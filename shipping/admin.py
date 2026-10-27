
from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from orders.models import Order
from django.utils import timezone

from .models import Shipment, AddressCorrectionRequest
from .services import DispatchBlocked, dispatch_shipment

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        "tracking_number",
        "order",
        "carrier",
        "delivery_agent_name",
        "status",
        "shipped_at",
        "delivered_at",
        "created_at",
    )

    list_filter = (
        "status",
        "carrier",
        "created_at",
    )

    actions = ("dispatch_selected_shipments",)

    search_fields = (
        "tracking_number",
        "order__id",
        "carrier",
        "delivery_agent_name",
    )

    readonly_fields = (
        "tracking_number",
        "status",
        "shipping_address",
        "shipped_at",
        "delivered_at",
        "created_at",
        "updated_at",
    )


    @admin.action(description="Dispatch selected shipments")
    def dispatch_selected_shipments(self, request, queryset):
        dispatched = 0
        skipped = 0

        shipment_ids = list(
            queryset.values_list("pk", flat=True)
        )

        for shipment_id in shipment_ids:
            try:
                dispatch_shipment(shipment_id)
                dispatched += 1
            except DispatchBlocked as exc:
                skipped += 1
                self.message_user(
                    request,
                    f"Shipment {shipment_id} was not dispatched: {exc}",
                    level=messages.WARNING,
                )

        if dispatched:
            self.message_user(
                request,
                f"{dispatched} shipment(s) dispatched successfully.",
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                f"{skipped} shipment(s) could not be dispatched.",
                level=messages.WARNING,
            )

            

@admin.register(AddressCorrectionRequest)
class AddressCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "requested_by",
        "status",
        "requested_at",
        "reviewed_by",
        "reviewed_at",
        "review_due_at",
        "review_deadline_status",
    )

    list_filter = (
        "status",
        "requested_at",
    )

    search_fields = (
        "order__id",
        "requested_by__username",
        "requested_by__email",
    )

    readonly_fields = (
        "order",
        "requested_by",
        "original_address",
        "proposed_address",
        "reason",
        "status",
        "requested_at",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "review_due_at"
    )

    actions = (
        "approve_corrections",
        "reject_corrections",
    )

    @admin.action(description="Approve selected address corrections")
    def approve_corrections(self, request, queryset):
        approved = 0
        skipped = 0

        for correction_id in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                try:
                    initial = AddressCorrectionRequest.objects.get(
                        pk=correction_id
                    )

                    # Lock the order first, matching the request
                    # creation workflow's lock order.
                    order = Order.objects.select_for_update().get(
                        pk=initial.order_id
                    )

                    correction = (
                        AddressCorrectionRequest.objects
                        .select_for_update()
                        .get(pk=correction_id)
                    )
                except (
                    Order.DoesNotExist,
                    AddressCorrectionRequest.DoesNotExist,
                ):
                    skipped += 1
                    continue

                if correction.status != AddressCorrectionRequest.Status.PENDING:
                    skipped += 1
                    continue

                if order.status in (
                    Order.Status.SHIPPED,
                    Order.Status.DELIVERED,
                    Order.Status.CANCELLED,
                ):
                    skipped += 1
                    continue

                shipments = list(
                    Shipment.objects.select_for_update()
                    .filter(order=order)
                    .order_by("pk")
                )

                # Until a dedicated dispatch workflow exists,
                # approve only while every shipment is pending.
                if any(
                    shipment.status != Shipment.Status.PENDING
                    for shipment in shipments
                ):
                    skipped += 1
                    continue

                order.shipping_address = correction.proposed_address
                order.save(update_fields=["shipping_address"])

                for shipment in shipments:
                    shipment.shipping_address = correction.proposed_address
                    shipment.save(update_fields=["shipping_address"])

                correction.status = AddressCorrectionRequest.Status.APPROVED
                correction.reviewed_by = request.user
                correction.reviewed_at = timezone.now()
                correction.review_note = "Approved by admin."
                correction.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "review_note",
                    ]
                )

                approved += 1

        if approved:
            self.message_user(
                request,
                f"{approved} address correction(s) approved.",
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                f"{skipped} request(s) skipped. They may no longer be "
                "pending, or a shipment may no longer be eligible.",
                level=messages.WARNING,
            )

    @admin.action(description="Reject selected address corrections")
    def reject_corrections(self, request, queryset):
        rejected = 0
        skipped = 0

        for correction_id in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                try:
                    initial = AddressCorrectionRequest.objects.get(
                        pk=correction_id
                    )

                    Order.objects.select_for_update().get(
                        pk=initial.order_id
                    )

                    correction = (
                        AddressCorrectionRequest.objects
                        .select_for_update()
                        .get(pk=correction_id)
                    )
                except (
                    Order.DoesNotExist,
                    AddressCorrectionRequest.DoesNotExist,
                ):
                    skipped += 1
                    continue

                if correction.status != AddressCorrectionRequest.Status.PENDING:
                    skipped += 1
                    continue

                correction.status = AddressCorrectionRequest.Status.REJECTED
                correction.reviewed_by = request.user
                correction.reviewed_at = timezone.now()
                correction.review_note = "Rejected by admin."
                correction.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "review_note",
                    ]
                )

                rejected += 1

        if rejected:
            self.message_user(
                request,
                f"{rejected} address correction(s) rejected.",
                level=messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                f"{skipped} request(s) skipped because they are no longer pending.",
                level=messages.WARNING,
            )


    @admin.display(description="Review deadline")
    def review_deadline_status(self, obj):
        if obj.status != AddressCorrectionRequest.Status.PENDING:
           return obj.get_status_display()

        if not obj.review_due_at:
           return "Deadline not set"

        if timezone.now() > obj.review_due_at:
            return "OVERDUE — escalate"

        return "Within review target"