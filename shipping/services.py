
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays

from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItemAllocation

from .models import (
    AddressCorrectionRequest,
    Shipment,
    ShipmentItem,
)


NIGERIA_TZ = ZoneInfo("Africa/Lagos")

WORK_START = time(9, 0)
WORK_END = time(17, 0)


@transaction.atomic
def create_shipments_for_order(order):
    # Lock the order so concurrent calls cannot create shipments
    # for the same order at the same time.
    order = type(order).objects.select_for_update().get(
        pk=order.pk
    )

    if not order.shipping_address:
        raise ValueError(
            "The order does not have a saved shipping address."
        )

    allocations = list(
        OrderItemAllocation.objects.filter(
            order_item__order=order
        )
        .select_related("inventory__location")
        .order_by("inventory__location_id", "id")
    )

    if not allocations:
        raise ValueError(
            "This order has no inventory allocations to ship."
        )

    # Group allocations by warehouse.
    allocations_by_location = {}

    for allocation in allocations:
        location_id = allocation.inventory.location_id

        if location_id is None:
            raise ValueError(
                "An order allocation has no warehouse."
            )

        allocations_by_location.setdefault(
            location_id, []
        ).append(allocation)

    shipments = []

    for location_id, location_allocations in (
        allocations_by_location.items()
    ):
        shipment, created = Shipment.objects.get_or_create(
            order=order,
            location_id=location_id,
            defaults={
                "shipping_address": order.shipping_address,
            },
        )

        # Reuse existing shipment items on repeated calls.
        for allocation in location_allocations:
            ShipmentItem.objects.get_or_create(
                allocation=allocation,
                defaults={"shipment": shipment},
            )

        # Never silently attach an allocation to the wrong shipment.
        for allocation in location_allocations:
            shipment_item = ShipmentItem.objects.get(
                allocation=allocation
            )

            if shipment_item.shipment_id != shipment.id:
                raise ValueError(
                    "An order allocation is already assigned "
                    "to a different shipment."
                )

        shipments.append(shipment)

    return shipments


def calculate_review_deadline(start_at, hours=4):
    """
    Calculate a review deadline using Nigerian working hours:
    Monday-Friday, 9 AM-5 PM, excluding public holidays.
    """
    if timezone.is_naive(start_at):
        start_at = timezone.make_aware(
            start_at,
            NIGERIA_TZ,
        )

    current = start_at.astimezone(NIGERIA_TZ)
    remaining = timedelta(hours=hours)

    public_holidays = holidays.country_holidays(
        "NG",
        years=range(current.year, current.year + 3),
    )

    def is_working_day(day):
        return (
            day.weekday() < 5
            and day not in public_holidays
        )

    def next_working_start(day):
        next_day = day + timedelta(days=1)

        while not is_working_day(next_day):
            next_day += timedelta(days=1)

        return datetime.combine(
            next_day,
            WORK_START,
            tzinfo=NIGERIA_TZ,
        )

    while True:
        if not is_working_day(current.date()):
            current = next_working_start(current.date())
            continue

        opening = datetime.combine(
            current.date(),
            WORK_START,
            tzinfo=NIGERIA_TZ,
        )
        closing = datetime.combine(
            current.date(),
            WORK_END,
            tzinfo=NIGERIA_TZ,
        )

        if current < opening:
            current = opening

        if current >= closing:
            current = next_working_start(current.date())
            continue

        available = closing - current

        if remaining <= available:
            return current + remaining

        remaining -= available
        current = next_working_start(current.date())


class DispatchBlocked(Exception):
    """Raised when a shipment is not eligible for dispatch."""


@transaction.atomic
def dispatch_shipment(shipment_id):
    # Read the order ID so we know which order to lock first.
    try:
        initial_shipment = Shipment.objects.only(
            "order_id"
        ).get(pk=shipment_id)
    except Shipment.DoesNotExist:
        raise DispatchBlocked("Shipment not found.")

    # Lock the order first. Address-correction requests use this
    # same lock, preventing a request from racing with dispatch.
    try:
        order = Order.objects.select_for_update().get(
            pk=initial_shipment.order_id
        )
    except Order.DoesNotExist:
        raise DispatchBlocked(
            "The shipment's order was not found."
        )

    # Lock the shipment after locking its order.
    try:
        shipment = Shipment.objects.select_for_update().get(
            pk=shipment_id,
            order_id=order.pk,
        )
    except Shipment.DoesNotExist:
        raise DispatchBlocked("Shipment not found.")

    if shipment.status != Shipment.Status.PENDING:
        raise DispatchBlocked(
            "Only pending shipments can be dispatched."
        )

    if order.status != Order.Status.PAID:
        raise DispatchBlocked(
            "Only paid orders can be dispatched."
       )

    if not order.shipping_address:
        raise DispatchBlocked(
            "The order has no saved delivery address."
        )

    if AddressCorrectionRequest.objects.filter(
        order=order,
        status=AddressCorrectionRequest.Status.PENDING,
    ).exists():
        raise DispatchBlocked(
            "Dispatch blocked: an address-correction request "
            "is awaiting admin review."
        )

    # Ensure the shipment uses the order's latest confirmed address.
    shipment.shipping_address = order.shipping_address
    shipment.status = Shipment.Status.SHIPPED
    shipment.shipped_at = timezone.now()

    shipment.save(
        update_fields=[
            "shipping_address",
            "status",
            "shipped_at",
            "updated_at",
        ]
    )

    return shipment
