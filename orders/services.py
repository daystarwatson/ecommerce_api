
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from inventory.models import Inventory, StockMovement
from orders.models import (
    Order,
    OrderItem,
    OrderItemAllocation,
    OrderItemAllocationHistory,
    OrderCancellationRequest,
)
from payments.models import Payment

from payments.services import (
    PaystackError,
    PaystackRefundUncertain,
    initiate_paystack_refund,
    fetch_paystack_refund,
    list_paystack_refunds,
    verify_paystack_payment,
)

class OrderCancellationError(Exception):
    """Raised when an order cannot be cancelled safely."""


@transaction.atomic
def create_order_item_allocation(
    *,
    order_item,
    inventory,
    quantity,
    actor=None,
    reason="",
):
    allocation = OrderItemAllocation.objects.create(
        order_item=order_item,
        inventory=inventory,
        quantity=quantity,
    )

    OrderItemAllocationHistory.objects.create(
        allocation_id_snapshot=allocation.pk,
        order_item_id_snapshot=order_item.pk,
        order_item_description=(
            f"Order item {order_item.pk}: "
            f"{order_item.product_name}"
        ),
        order_item=order_item,
        event_type=OrderItemAllocationHistory.EventType.CREATED,
        previous_inventory_id=None,
        new_inventory_id=inventory.pk,
        previous_quantity=None,
        new_quantity=quantity,
        reason=reason,
        performed_by_id_snapshot=str(actor.pk) if actor else "",
        performed_by_description=str(actor) if actor else "System",
        performed_by=actor,
    )

    return allocation


@transaction.atomic
def delete_order_item_allocation(
    *,
    allocation_id,
    actor=None,
    reason="",
):
    raise OrderCancellationError(
        "Order allocations cannot be deleted directly. "
        "Use a coordinated inventory adjustment workflow."
    )


@transaction.atomic
def update_order_item_allocation(
    *,
    allocation_id,
    actor=None,
    quantity=None,
    inventory=None,
    reason="",
):
    raise OrderCancellationError(
        "Order allocations cannot be changed directly. "
        "Use a coordinated inventory adjustment workflow."
    )


@transaction.atomic
def cancel_order(*, order_id, actor=None):
    from shipping.models import Shipment

    # Lock the order first, matching the dispatch service's
    # order-before-shipment locking order.
    try:
        order = (
            Order.objects
            .select_for_update()
            .get(pk=order_id)
        )
    except Order.DoesNotExist:
        raise OrderCancellationError("Order does not exist.")

    if actor is not None and order.user_id != actor.pk:
        raise OrderCancellationError("Order does not exist.")

    # Only pending orders can use direct cancellation.
    # Paid orders must use the cancellation and refund workflow.
    if order.status == Order.Status.CANCELLED:
        raise OrderCancellationError(
            "This order has already been cancelled."
        )

    if order.status != Order.Status.PENDING:
        raise OrderCancellationError(
            "Only pending orders can be cancelled directly. "
            "Paid orders must go through the cancellation and refund process."
        )

    # Lock shipments before checking whether dispatch has begun.
    shipments = list(
        Shipment.objects
        .select_for_update()
        .filter(order_id=order.pk)
        .order_by("id")
    )

    dispatched_statuses = {
        "shipped",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "dispatched",
    }

    if any(
        str(shipment.status).lower() in dispatched_statuses
        for shipment in shipments
    ):
        raise OrderCancellationError(
            "This order cannot be cancelled because dispatch has begun."
        )

    # Lock allocations in a consistent order.
    allocations = list(
        OrderItemAllocation.objects
        .select_for_update()
        .filter(order_item__order_id=order.pk)
        .order_by("inventory_id", "id")
    )

    # Confirm every order item is fully represented by its allocations.
    order_items = list(
        OrderItem.objects
        .filter(order_id=order.pk)
        .order_by("id")
    )

    if not order_items:
        raise OrderCancellationError(
            "The order has no items. No stock was restored."
        )

    allocated_quantity_by_item = {}

    for allocation in allocations:
        item_id = allocation.order_item_id
        allocated_quantity_by_item[item_id] = (
            allocated_quantity_by_item.get(item_id, 0)
            + allocation.quantity
        )

    for item in order_items:
        allocated_quantity = allocated_quantity_by_item.get(item.pk, 0)

        if allocated_quantity != item.quantity:
            raise OrderCancellationError(
                f"Stock allocations for order item {item.pk} "
                f"do not match its ordered quantity. "
                f"Expected {item.quantity}, allocated "
                f"{allocated_quantity}. No stock was restored."
            )

    # Lock affected inventory records in ID order.
    inventory_ids = sorted({
        allocation.inventory_id
        for allocation in allocations
    })

    locked_inventory = list(
        Inventory.objects
        .select_for_update()
        .filter(pk__in=inventory_ids)
        .order_by("id")
    )

    inventory_by_id = {
        inventory.pk: inventory
        for inventory in locked_inventory
    }

    if len(inventory_by_id) != len(inventory_ids):
        raise OrderCancellationError(
            "An inventory record is missing. "
            "No stock was restored and the order was not cancelled."
        )

    # Restore stock to the exact locations recorded at checkout.
    for allocation in allocations:
        inventory = inventory_by_id[allocation.inventory_id]

        inventory.quantity_on_hand += allocation.quantity
        inventory.save(
            update_fields=["quantity_on_hand", "updated_at"]
        )

        StockMovement.objects.create(
            inventory=inventory,
            movement_type=StockMovement.MovementType.RETURN,
            quantity=allocation.quantity,
            reason=f"Cancellation of order {order.pk}",
            reference=f"ORDER-{order.pk}",
        )

    # Update the order in the same transaction.
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])

    return order


@transaction.atomic
def request_order_cancellation(*, order_id, actor, reason):
    from shipping.models import Shipment

    # Lock the order first.
    try:
        order = (
            Order.objects
            .select_for_update()
            .get(pk=order_id, user=actor)
        )
    except Order.DoesNotExist:
        raise OrderCancellationError("Order does not exist.")

    if order.status != Order.Status.PAID:
        raise OrderCancellationError(
            "Only paid orders can use the cancellation-request process."
        )

    # Lock shipments before checking dispatch.
    shipments = list(
        Shipment.objects
        .select_for_update()
        .filter(order_id=order.pk)
        .order_by("id")
    )

    dispatched_statuses = {
        "shipped",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "dispatched",
    }

    if any(
        str(shipment.status).lower() in dispatched_statuses
        for shipment in shipments
    ):
        raise OrderCancellationError(
            "This order cannot be cancelled because dispatch has begun."
        )

    # Only one pending cancellation request is allowed per order.
    existing_request = (
        OrderCancellationRequest.objects
        .filter(
            order=order,
            status=OrderCancellationRequest.Status.PENDING,
        )
        .exists()
    )

    if existing_request:
        raise OrderCancellationError(
            "A cancellation request is already pending for this order."
        )

    reason = reason.strip()

    if not reason:
        raise OrderCancellationError(
            "Please provide a reason for cancelling the order."
        )

    return OrderCancellationRequest.objects.create(
        order=order,
        requested_by=actor,
        reason=reason,
        status=OrderCancellationRequest.Status.PENDING,
        refund_status=(
            OrderCancellationRequest.RefundStatus.NOT_STARTED
        ),
    )


@transaction.atomic
def review_order_cancellation_request(
    *,
    request_id,
    actor,
    decision,
    review_note="",
):
    if decision not in {"approve", "reject"}:
        raise OrderCancellationError(
            "Decision must be approve or reject."
        )

    if not isinstance(review_note, str):
        raise OrderCancellationError(
            "Review note must be text."
        )

    # Read the order ID without locking the request yet.
    try:
        order_id = (
            OrderCancellationRequest.objects
            .values_list("order_id", flat=True)
            .get(pk=request_id)
        )
    except OrderCancellationRequest.DoesNotExist:
        raise OrderCancellationError(
            "Cancellation request does not exist."
        )

    # Lock the order first, consistent with shipping operations.
    try:
        order = (
            Order.objects
            .select_for_update()
            .get(pk=order_id)
        )
    except Order.DoesNotExist:
        raise OrderCancellationError(
            "The order associated with this cancellation request "
            "does not exist."
        )

    # Lock the cancellation request second.
    try:
        cancellation_request = (
            OrderCancellationRequest.objects
            .select_for_update()
            .get(
                pk=request_id,
                order_id=order.pk,
            )
        )
    except OrderCancellationRequest.DoesNotExist:
        raise OrderCancellationError(
            "Cancellation request does not exist."
        )

    if cancellation_request.status != (
        OrderCancellationRequest.Status.PENDING
    ):
        raise OrderCancellationError(
            "This cancellation request has already been reviewed."
        )

    if decision == "approve":
        if order.status != Order.Status.PAID:
            raise OrderCancellationError(
                "Only paid orders can have cancellation requests approved."
            )

        from shipping.models import Shipment

        shipments = list(
            Shipment.objects
            .select_for_update()
            .filter(order_id=order.pk)
            .order_by("id")
        )

        dispatched_statuses = {
            "shipped",
            "in_transit",
            "out_for_delivery",
            "delivered",
            "dispatched",
        }

        if any(
            str(shipment.status).lower() in dispatched_statuses
            for shipment in shipments
        ):
            raise OrderCancellationError(
                "This cancellation request cannot be approved "
                "because dispatch has begun."
            )

    cancellation_request.reviewed_by = actor
    cancellation_request.reviewed_at = timezone.now()
    cancellation_request.review_note = review_note.strip()

    if decision == "reject":
        cancellation_request.status = (
            OrderCancellationRequest.Status.REJECTED
        )
    else:
        cancellation_request.status = (
            OrderCancellationRequest.Status.APPROVED
        )
        cancellation_request.refund_status = (
            OrderCancellationRequest.RefundStatus.NOT_STARTED
        )

    cancellation_request.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "refund_status",
            "updated_at",
        ]
    )

    return cancellation_request


@transaction.atomic
def prepare_cancellation_refund(*, request_id, actor):
    from shipping.models import Shipment

    # Find the order ID without locking the cancellation request yet.
    try:
        order_id = (
            OrderCancellationRequest.objects
            .values_list("order_id", flat=True)
            .get(pk=request_id)
        )
    except OrderCancellationRequest.DoesNotExist:
        raise OrderCancellationError(
            "Cancellation request does not exist."
        )

    # Lock the order first to coordinate with shipping operations.
    try:
        order = (
            Order.objects
            .select_for_update()
            .get(pk=order_id)
        )
    except Order.DoesNotExist:
        raise OrderCancellationError(
            "The order associated with this cancellation request "
            "does not exist."
        )

    # Lock the cancellation request second.
    try:
        cancellation_request = (
            OrderCancellationRequest.objects
            .select_for_update()
            .get(
                pk=request_id,
                order_id=order.pk,
            )
        )
    except OrderCancellationRequest.DoesNotExist:
        raise OrderCancellationError(
            "Cancellation request does not exist."
        )

    if cancellation_request.status != (
        OrderCancellationRequest.Status.APPROVED
    ):
        raise OrderCancellationError(
            "The cancellation request must be approved first."
        )

    if cancellation_request.refund_status != (
        OrderCancellationRequest.RefundStatus.NOT_STARTED
    ):
        raise OrderCancellationError(
            "This refund has already been started or requires review."
        )

    if order.status != Order.Status.PAID:
        raise OrderCancellationError(
            "Only paid orders can enter the refund workflow."
        )

    # Check dispatch before preparing the refund.
    dispatched_statuses = [
        Shipment.Status.SHIPPED,
        Shipment.Status.IN_TRANSIT,
        Shipment.Status.DELIVERED,
        "out_for_delivery",
        "dispatched",
    ]

    shipment_dispatched = (
        Shipment.objects
        .select_for_update()
        .filter(
            order_id=order.pk,
            status__in=dispatched_statuses,
        )
        .exists()
    )

    if shipment_dispatched:
        raise OrderCancellationError(
            "A refund cannot be initiated because a shipment "
            "has already been dispatched."
        )

    successful_payments = list(
        Payment.objects
        .select_for_update()
        .filter(
            order_id=order.pk,
            status=Payment.Status.SUCCESSFUL,
        )
        .order_by("pk")
    )

    if len(successful_payments) != 1:
        raise OrderCancellationError(
            "The order must have exactly one successful payment "
            "before a refund can be initiated."
        )

    payment = successful_payments[0]

    if payment.currency != "NGN":
        raise OrderCancellationError(
            "This refund workflow currently supports NGN payments only."
        )

    if payment.amount <= Decimal("0.00"):
        raise OrderCancellationError(
            "The successful payment has an invalid refund amount."
        )

    amount_kobo = int(payment.amount * 100)

    if amount_kobo <= 0:
        raise OrderCancellationError(
            "The refund amount is invalid."
        )

    cancellation_request.refund_amount = payment.amount
    cancellation_request.refund_status = (
        OrderCancellationRequest.RefundStatus.PROCESSING
    )
    cancellation_request.refund_updated_at = timezone.now()

    cancellation_request.save(
        update_fields=[
            "refund_amount",
            "refund_status",
            "refund_updated_at",
            "updated_at",
        ]
    )

    return {
        "request_id": cancellation_request.pk,
        "order_id": order.pk,
        "payment_reference": payment.reference,
        "amount_kobo": amount_kobo,
    }


def initiate_cancellation_refund(*, request_id, actor):
    from orders.models import OrderCancellationRequest
    from payments.services import (
        PaystackError,
        PaystackRefundUncertain,
        initiate_paystack_refund,
    )

    # Reserve the approved request before making the network call.
    refund_details = prepare_cancellation_refund(
        request_id=request_id,
        actor=actor,
    )

    try:
        result = initiate_paystack_refund(
            transaction_reference=refund_details["payment_reference"],
            amount_kobo=refund_details["amount_kobo"],
            customer_note=(
                f"Refund for order {refund_details['order_id']}"
            ),
            merchant_note=(
                f"Cancellation request #{refund_details['request_id']}"
            ),
        )

    except PaystackRefundUncertain:
        # Paystack may have received the request.
        # Never automatically submit it again.
        with transaction.atomic():
            cancellation_request = (
                OrderCancellationRequest.objects
                .select_for_update()
                .get(pk=request_id)
            )
            cancellation_request.refund_status = (
                OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
            )
            cancellation_request.refund_updated_at = timezone.now()
            cancellation_request.save(
                update_fields=[
                    "refund_status",
                    "refund_updated_at",
                    "updated_at",
                ]
            )

        raise OrderCancellationError(
            "The refund outcome is uncertain. Check Paystack before "
            "taking further action."
        )

    except PaystackError as exc:
        # Paystack definitively rejected the request.
        # Keep this request from being automatically retried.
        with transaction.atomic():
            cancellation_request = (
                OrderCancellationRequest.objects
                .select_for_update()
                .get(pk=request_id)
            )
            cancellation_request.refund_status = (
                OrderCancellationRequest.RefundStatus.FAILED
            )
            cancellation_request.refund_updated_at = timezone.now()
            cancellation_request.review_note = (
                f"{cancellation_request.review_note}\n"
                f"Refund initiation failed: {exc}"
            ).strip()
            cancellation_request.save(
                update_fields=[
                    "refund_status",
                    "refund_updated_at",
                    "review_note",
                    "updated_at",
                ]
            )

        raise OrderCancellationError(
            "Paystack did not initiate the refund. "
            "Review the request before taking further action."
        )

    # Save the refund identifier and Paystack's returned status.
    returned_status = result["status"].lower().replace("-", "_")

    status_map = {
        "pending": OrderCancellationRequest.RefundStatus.PENDING,
        "processing": OrderCancellationRequest.RefundStatus.PROCESSING,
        "processed": OrderCancellationRequest.RefundStatus.SUCCESSFUL,
        "successful": OrderCancellationRequest.RefundStatus.UNKNOWN,
        "failed": OrderCancellationRequest.RefundStatus.FAILED,
        "needs_attention": (
            OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
        ),
    }

    mapped_status = status_map.get(
        returned_status,
        OrderCancellationRequest.RefundStatus.UNKNOWN,
    )

    with transaction.atomic():
        cancellation_request = (
            OrderCancellationRequest.objects
            .select_for_update()
            .get(pk=request_id)
        )
        cancellation_request.refund_reference = result["refund_id"]
        cancellation_request.refund_status = mapped_status
        cancellation_request.refund_updated_at = timezone.now()
        cancellation_request.save(
            update_fields=[
                "refund_reference",
                "refund_status",
                "refund_updated_at",
                "updated_at",
            ]
        )

    return cancellation_request




def recover_uncertain_cancellation_refund(*, request_id):
    from payments.models import Payment

    cancellation_request = OrderCancellationRequest.objects.get(
        pk=request_id
    )

    if (
        cancellation_request.status
        != OrderCancellationRequest.Status.APPROVED
    ):
        raise OrderCancellationError(
            "Only approved cancellation requests can be recovered."
        )

    if (
        cancellation_request.refund_status
        != OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
    ):
        raise OrderCancellationError(
            "This request is not marked as needing refund attention."
        )

    # If Paystack's refund ID is already saved, verify that refund.
    if cancellation_request.refund_reference:
        return verify_and_finalize_cancellation_refund(
            request_id=request_id
        )

    payments = Payment.objects.filter(
        order_id=cancellation_request.order_id,
        status=Payment.Status.SUCCESSFUL,
    )

    if payments.count() != 1:
        raise OrderCancellationError(
            "Exactly one successful payment is required for recovery."
        )

    payment = payments.first()

    try:
        transaction_data = verify_paystack_payment(payment.reference)
    except PaystackError as exc:
        raise OrderCancellationError(
            "Could not verify the original Paystack transaction. "
            "No new refund was submitted."
        ) from exc

    transaction_id = transaction_data.get("id")

    if not transaction_id:
        raise OrderCancellationError(
            "Paystack did not return the original transaction ID."
        )

    try:
        refunds = list_paystack_refunds(
            transaction_id=transaction_id
        )
    except PaystackError as exc:
        raise OrderCancellationError(
            "Could not retrieve existing Paystack refunds. "
            "No new refund was submitted."
        ) from exc

    expected_amount_kobo = int(
        cancellation_request.refund_amount * 100
    )

    matching_refunds = []

    for refund in refunds:
        refund_transaction = refund.get("transaction_id")

        if isinstance(refund_transaction, dict):
            refund_transaction = refund_transaction.get("id")

        try:
            refund_amount_kobo = int(refund.get("amount_kobo") or 0)
        except (TypeError, ValueError):
            continue

        if (
            str(refund_transaction) == str(transaction_id)
            and refund_amount_kobo == expected_amount_kobo
            and str(refund.get("currency", "")).upper() == "NGN"
            and refund.get("refund_id")
        ):
            matching_refunds.append(refund)

    if len(matching_refunds) != 1:
        raise OrderCancellationError(
            "Could not identify exactly one matching Paystack refund. "
            "The request remains under review. Do not initiate another "
            "refund until the existing refund has been investigated."
        )

    refund = matching_refunds[0]

    status_map = {
        "pending": OrderCancellationRequest.RefundStatus.PENDING,
        "processing": OrderCancellationRequest.RefundStatus.PROCESSING,
        "processed": OrderCancellationRequest.RefundStatus.SUCCESSFUL,
        "failed": OrderCancellationRequest.RefundStatus.FAILED,
        "needs-attention": (
            OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
        ),
        "needs_attention": (
            OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
        ),
    }

    refund_status = str(refund.get("status", "")).lower()

    cancellation_request.refund_reference = refund["refund_id"]
    cancellation_request.refund_status = status_map.get(
        refund_status,
        OrderCancellationRequest.RefundStatus.UNKNOWN,
    )
    cancellation_request.save(
        update_fields=[
            "refund_reference",
            "refund_status",
            "updated_at",
        ]
    )

    # Verify the recovered refund. Stock is restored only when the
    # existing refund is confirmed as processed.
    return verify_and_finalize_cancellation_refund(
        request_id=request_id
    )



def verify_and_finalize_cancellation_refund(*, request_id):
    from shipping.models import Shipment
    from payments.services import (
        PaystackError,
        fetch_paystack_refund,
    )

    # Read the cancellation request and its saved refund.
    try:
        cancellation_request = (
            OrderCancellationRequest.objects
            .select_related("order")
            .get(pk=request_id)
        )
    except OrderCancellationRequest.DoesNotExist:
        raise OrderCancellationError(
            "Cancellation request does not exist."
        )

    if cancellation_request.status != (
        OrderCancellationRequest.Status.APPROVED
    ):
        raise OrderCancellationError(
            "The cancellation request must be approved first."
        )

    if not cancellation_request.refund_reference:
        raise OrderCancellationError(
            "No Paystack refund reference is saved for this request."
        )

    if cancellation_request.refund_amount is None:
        raise OrderCancellationError(
            "The expected refund amount is missing."
        )

    saved_refund_reference = cancellation_request.refund_reference
    expected_amount_kobo = int(
        cancellation_request.refund_amount * 100
    )

    # Query Paystack outside the database transaction.
    try:
        result = fetch_paystack_refund(saved_refund_reference)
    except PaystackError as exc:
        raise OrderCancellationError(
            "Could not verify the refund with Paystack. "
            "No stock was restored. Check again later."
        ) from exc

    # Verify the refund identity, amount, and currency.
    if str(result["refund_id"]) != str(saved_refund_reference):
        raise OrderCancellationError(
            "Paystack returned a different refund ID. "
            "No cancellation or stock restoration was performed."
        )

    if int(result["amount_kobo"]) != expected_amount_kobo:
        raise OrderCancellationError(
            "Paystack's refund amount does not match the expected "
            "amount. No cancellation or stock restoration was performed."
        )

    if str(result["currency"]).upper() != "NGN":
        raise OrderCancellationError(
            "Paystack's refund currency is not NGN. "
            "No cancellation or stock restoration was performed."
        )

    returned_status = (
        str(result["status"]).lower().replace("-", "_")
    )

    status_map = {
        "pending": OrderCancellationRequest.RefundStatus.PENDING,
        "processing": (
            OrderCancellationRequest.RefundStatus.PROCESSING
        ),
        "processed": (
            OrderCancellationRequest.RefundStatus.SUCCESSFUL
        ),
        "failed": OrderCancellationRequest.RefundStatus.FAILED,
        "needs_attention": (
            OrderCancellationRequest.RefundStatus.NEEDS_ATTENTION
        ),
    }

    mapped_status = status_map.get(
        returned_status,
        OrderCancellationRequest.RefundStatus.UNKNOWN,
    )

    # Only an explicitly processed refund can finalize cancellation.
    if returned_status != "processed":
        with transaction.atomic():
            try:
                order = (
                    Order.objects
                    .select_for_update()
                    .get(pk=cancellation_request.order_id)
                )
            except Order.DoesNotExist:
                raise OrderCancellationError(
                    "The associated order does not exist."
                )

            current_request = (
                OrderCancellationRequest.objects
                .select_for_update()
                .get(pk=request_id)
            )

            if (
                current_request.refund_reference
                != saved_refund_reference
            ):
                raise OrderCancellationError(
                    "The refund reference changed. "
                    "Review the request before continuing."
                )

            if current_request.status != (
                OrderCancellationRequest.Status.APPROVED
            ):
                raise OrderCancellationError(
                    "The cancellation request is no longer approved."
                )

            # Never overwrite a completed refund with an older result.
            if (
                order.status == Order.Status.CANCELLED
                and current_request.refund_status
                == OrderCancellationRequest.RefundStatus.SUCCESSFUL
            ):
                return current_request

            if order.status != Order.Status.PAID:
                raise OrderCancellationError(
                    "The order is no longer paid. "
                    "Review its status before continuing."
                )

            current_request.refund_status = mapped_status
            current_request.refund_updated_at = timezone.now()
            current_request.save(
                update_fields=[
                    "refund_status",
                    "refund_updated_at",
                    "updated_at",
                ]
            )

        return current_request

    # Finalize a processed refund in one database transaction.
    # Lock the order first, then the cancellation request.
    with transaction.atomic():
        try:
            order = (
                Order.objects
                .select_for_update()
                .get(pk=cancellation_request.order_id)
            )
        except Order.DoesNotExist:
            raise OrderCancellationError(
                "The associated order does not exist."
            )

        current_request = (
            OrderCancellationRequest.objects
            .select_for_update()
            .get(pk=request_id)
        )

        if (
            current_request.refund_reference
            != saved_refund_reference
        ):
            raise OrderCancellationError(
                "The refund reference changed. "
                "Review the request before continuing."
            )

        if current_request.status != (
            OrderCancellationRequest.Status.APPROVED
        ):
            raise OrderCancellationError(
                "The cancellation request is no longer approved."
            )

        # Repeated verification must never restore stock twice.
        if (
            order.status == Order.Status.CANCELLED
            and current_request.refund_status
            == OrderCancellationRequest.RefundStatus.SUCCESSFUL
        ):
            return current_request

        if order.status != Order.Status.PAID:
            raise OrderCancellationError(
                "The order is not paid. "
                "Review its status before finalizing the refund."
            )

        shipments = list(
            Shipment.objects
            .select_for_update()
            .filter(order_id=order.pk)
            .order_by("id")
        )

        dispatched_statuses = {
            "shipped",
            "in_transit",
            "out_for_delivery",
            "delivered",
            "dispatched",
        }

        if any(
            str(shipment.status).lower() in dispatched_statuses
            for shipment in shipments
        ):
            raise OrderCancellationError(
                "Dispatch has begun. Do not restore stock through "
                "the cancellation workflow; review the return process."
            )

        allocations = list(
            OrderItemAllocation.objects
            .select_for_update()
            .filter(order_item__order_id=order.pk)
            .order_by("inventory_id", "id")
        )


        # Every order item must have allocations covering its full quantity.
        order_items = list(
            OrderItem.objects
            .filter(order_id=order.pk)
            .order_by("id")
        )

        if not order_items:
            raise OrderCancellationError(
                "The order has no items. No stock was restored."
            )

        allocated_quantity_by_item = {}

        for allocation in allocations:
            item_id = allocation.order_item_id
            allocated_quantity_by_item[item_id] = (
                allocated_quantity_by_item.get(item_id, 0)
                + allocation.quantity
            )

        for item in order_items:
            allocated_quantity = allocated_quantity_by_item.get(
                item.pk, 0
            )

            if allocated_quantity != item.quantity:
                raise OrderCancellationError(
                    f"Stock allocations for order item {item.pk} "
                    f"do not match its ordered quantity. "
                    f"Expected {item.quantity}, allocated "
                    f"{allocated_quantity}. No stock was restored."
                )



        inventory_ids = sorted({
            allocation.inventory_id
            for allocation in allocations
        })

        locked_inventory = list(
            Inventory.objects
            .select_for_update()
            .filter(pk__in=inventory_ids)
            .order_by("id")
        )

        inventory_by_id = {
            inventory.pk: inventory
            for inventory in locked_inventory
        }

        if len(inventory_by_id) != len(inventory_ids):
            raise OrderCancellationError(
                "An inventory record is missing. "
                "No stock was restored."
            )

        for allocation in allocations:
            inventory = inventory_by_id[allocation.inventory_id]

            inventory.quantity_on_hand += allocation.quantity
            inventory.save(
                update_fields=["quantity_on_hand", "updated_at"]
            )

            StockMovement.objects.create(
                inventory=inventory,
                movement_type=StockMovement.MovementType.RETURN,
                quantity=allocation.quantity,
                reason=f"Refunded cancellation of order {order.pk}",
                reference=f"ORDER-{order.pk}",
            )

        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])

        current_request.refund_status = (
            OrderCancellationRequest.RefundStatus.SUCCESSFUL
        )
        current_request.refund_updated_at = timezone.now()
        current_request.save(
            update_fields=[
                "refund_status",
                "refund_updated_at",
                "updated_at",
            ]
        )

        return current_request
