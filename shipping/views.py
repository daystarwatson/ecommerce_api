from django.db import transaction
from django.utils import timezone

from requests import request
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import (
    Order,
    OrderItemAllocation,
    OrderCancellationRequest,
)

from .models import (
    Shipment,
    ShipmentItem,
    ShipmentEvent,
    AddressCorrectionRequest,
)

from .services import calculate_review_deadline

from .serializers import (
    ShipmentSerializer,
    AddressCorrectionRequestSerializer,
    CustomerShipmentEventSerializer,
)

from inventory.models import StockLocation


def order_has_blocking_cancellation(order):
    return OrderCancellationRequest.objects.filter(
        order=order,
        status__in=[
            OrderCancellationRequest.Status.PENDING,
            OrderCancellationRequest.Status.APPROVED,
        ],
    ).exists()


class ShipmentCreateView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request):
        order_id = request.data.get("order_id")
        location_id = request.data.get("location_id")
        allocation_ids = request.data.get("allocation_ids")

        if not isinstance(allocation_ids, list) or not allocation_ids:
            return Response(
                {
                    "detail": (
                        "Provide a non-empty list of allocation_ids."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(allocation_ids) != len(set(allocation_ids)):
            return Response(
                {"detail": "Duplicate allocation IDs are not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )


        
        # Require every allocation for this order and warehouse
        # to be included in the shipment.
        expected_allocation_ids = set(
            OrderItemAllocation.objects.filter(
                order_item__order=order,
                inventory__location=location,
            ).values_list("id", flat=True)
        )

        submitted_allocation_ids = set(allocation_ids)

        missing_allocation_ids = sorted(
            expected_allocation_ids - submitted_allocation_ids
        )

        if missing_allocation_ids:
            return Response(
                {
                    "detail": (
                        "All allocations for this order and location "
                        "must be included in the shipment."
                    ),
                    "missing_allocation_ids": missing_allocation_ids,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


        if not all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            for value in allocation_ids
        ):
            return Response(
                {"detail": "Every allocation ID must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order_id = int(order_id)
            location_id = int(location_id)

            if order_id <= 0 or location_id <= 0:
                raise ValueError

        except (TypeError, ValueError):
            return Response(
                {
                    "detail": (
                        "order_id and location_id must be positive integers."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = Order.objects.select_for_update().filter(
            pk=order_id
        ).first()

        if order is None:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status not in [
            Order.Status.PAID,
            Order.Status.SHIPPED,
        ]:
            return Response(
                {
                    "detail": (
                        "A shipment can only be created for a paid "
                        "or shipped order."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order_has_blocking_cancellation(order):
            return Response(
                {
                    "detail": (
                        "A shipment cannot be created while an order "
                        "cancellation request is pending or approved."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not order.shipping_address:
            return Response(
                {
                    "detail": (
                        "The order does not have a saved shipping address."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        location = StockLocation.objects.filter(
            pk=location_id,
            active=True,
        ).first()

        if location is None:
            return Response(
                {"detail": "Active stock location not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        allocations = list(
            OrderItemAllocation.objects.select_for_update()
            .filter(
                pk__in=allocation_ids,
                order_item__order=order,
                inventory__location=location,
            )
            .select_related("order_item", "inventory")
        )

        if len(allocations) != len(allocation_ids):
            return Response(
                {
                    "detail": (
                        "One or more allocations do not belong to this "
                        "order and location."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        already_assigned = [
            allocation.pk
            for allocation in allocations
            if ShipmentItem.objects.filter(
                allocation_id=allocation.pk
            ).exists()
        ]

        if already_assigned:
            return Response(
                {
                    "detail": (
                        "Some allocations already belong to a shipment."
                    ),
                    "allocation_ids": already_assigned,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Shipment.objects.filter(
            order=order,
            location=location,
        ).exists():
            return Response(
                {
                    "detail": (
                        "A shipment already exists for this order "
                        "and location."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        shipment = Shipment.objects.create(
            order=order,
            location=location,
            shipping_address=order.shipping_address,
            carrier=request.data.get("carrier", ""),
            delivery_agent_name=request.data.get(
                "delivery_agent_name", ""
            ),
        )

        ShipmentItem.objects.bulk_create(
            [
                ShipmentItem(
                    shipment=shipment,
                    allocation=allocation,
                )
                for allocation in allocations
            ]
        )

        ShipmentEvent.objects.create(
            shipment=shipment,
            event_type=ShipmentEvent.EventType.CONFIRMED,
            description="The shipment has been created and confirmed.",
            created_by=request.user,
            customer_visible=True,
        )

        return Response(
            {
                "detail": "Shipment created successfully.",
                "shipment_id": shipment.id,
                "order_id": order.id,
                "location_id": location.id,
                "tracking_number": str(shipment.tracking_number),
                "status": shipment.status,
                "allocation_ids": allocation_ids,
            },
            status=status.HTTP_201_CREATED,
        )


class ShipmentListView(generics.ListAPIView):
    serializer_class = ShipmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Shipment.objects.filter(
                order__user=self.request.user
            )
            .select_related("order")
            .order_by("-created_at")
        )


class CustomerOrderTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        try:
            order = Order.objects.get(
                pk=order_id,
                user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        shipments = (
            Shipment.objects.filter(order=order)
            .prefetch_related(
                "items__allocation__order_item",
                "events",
            )
            .order_by("created_at", "id")
        )

        shipment_data = []

        for shipment in shipments:
            items = []

            for shipment_item in shipment.items.all():
                allocation = shipment_item.allocation
                order_item = allocation.order_item

                items.append({
                    "product_name": order_item.product_name,
                    "quantity": allocation.quantity,
                })

            customer_events = shipment.events.filter(
                customer_visible=True,
            ).order_by("created_at", "id")

            shipment_data.append({
                "tracking_number": str(
                    shipment.tracking_number
                ),
                "status": shipment.status,
                "carrier": shipment.carrier,
                "delivery_agent_name": (
                    shipment.delivery_agent_name
                ),
                "shipping_address": shipment.shipping_address,
                "created_at": shipment.created_at,
                "updated_at": shipment.updated_at,
                "shipped_at": shipment.shipped_at,
                "delivered_at": shipment.delivered_at,
                "items": items,
                "events": CustomerShipmentEventSerializer(
                    customer_events,
                    many=True,
                ).data,
            })

        return Response({
            "order_id": order.pk,
            "order_status": order.status,
            "shipments": shipment_data,
        })


class AddressCorrectionRequestCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, order_id):
        try:
            order = (
                Order.objects.select_for_update()
                .get(pk=order_id, user=request.user)
            )
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not order.shipping_address:
            return Response(
                {
                    "detail": (
                        "This order has no saved shipping address."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.status in (
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
            Order.Status.CANCELLED,
        ):
            return Response(
                {
                    "detail": (
                        "This order is no longer eligible "
                        "for an address correction."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Shipment.objects.filter(
            order=order,
            status__in=[
                Shipment.Status.SHIPPED,
                Shipment.Status.IN_TRANSIT,
                Shipment.Status.DELIVERED,
            ],
        ).exists():
            return Response(
                {
                    "detail": (
                        "A shipment has already been dispatched. "
                        "Address corrections are no longer allowed."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_count = (
            AddressCorrectionRequest.objects.filter(
                order=order,
            )
            .exclude(
                status=AddressCorrectionRequest.Status.WITHDRAWN,
            )
            .count()
        )

        if request_count >= 3:
            return Response(
                {
                    "detail": (
                        "This order has reached the maximum "
                        "of 3 address-correction requests."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AddressCorrectionRequestSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)

        correction = serializer.save(
            order=order,
            requested_by=request.user,
            original_address=order.shipping_address,
            review_due_at=calculate_review_deadline(
                timezone.now()
            ),
        )

        return Response(
            AddressCorrectionRequestSerializer(
                correction
            ).data,
            status=status.HTTP_201_CREATED,
        )


class AddressCorrectionRequestListView(generics.ListAPIView):
    serializer_class = AddressCorrectionRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            AddressCorrectionRequest.objects.filter(
                requested_by=self.request.user
            )
            .select_related("order")
        )


class ShipmentUpdateView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def patch(self, request, shipment_id):
        # First find which order this shipment belongs to.
        try:
            shipment_info = Shipment.objects.only("order_id").get(
                pk=shipment_id
            )
        except Shipment.DoesNotExist:
            return Response(
                {"detail": "Shipment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Lock the order first to keep the locking order consistent.
        try:
            order = Order.objects.select_for_update().get(
                pk=shipment_info.order_id
            )
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Then lock the shipment.
        try:
            shipment = (
                Shipment.objects.select_for_update()
                .select_related("order")
                .get(pk=shipment_id)
            )
        except Shipment.DoesNotExist:
            return Response(
                {"detail": "Shipment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        allowed_fields = {
            "carrier",
            "delivery_agent_name",
            "status",
            "description",
            "reason_code",
            "internal_note",
            "event_type",
        }

        unexpected_fields = set(request.data.keys()) - allowed_fields

        if unexpected_fields:
            return Response(
                {
                    "detail": (
                        "Unexpected fields: "
                        + ", ".join(sorted(unexpected_fields))
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = request.data.get("status", shipment.status)
        description = request.data.get("description", "")
        reason_code = request.data.get("reason_code", "")
        internal_note = request.data.get("internal_note", "")
        requested_event_type = request.data.get("event_type", "")

        for field_name, value in (
            ("description", description),
            ("reason_code", reason_code),
            ("internal_note", internal_note),
            ("event_type", requested_event_type),
        ):
            if not isinstance(value, str):
                return Response(
                    {"detail": f"{field_name} must be text."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        description = description.strip()
        reason_code = reason_code.strip()
        internal_note = internal_note.strip()
        requested_event_type = requested_event_type.strip()

        valid_statuses = {
            choice[0] for choice in Shipment.Status.choices
        }

        if new_status not in valid_statuses:
            return Response(
                {"detail": "Invalid shipment status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type_by_status = {
            Shipment.Status.SHIPPED: ShipmentEvent.EventType.DISPATCHED,
            Shipment.Status.IN_TRANSIT: ShipmentEvent.EventType.IN_TRANSIT,
            Shipment.Status.DELIVERED: ShipmentEvent.EventType.DELIVERED,
            Shipment.Status.FAILED: ShipmentEvent.EventType.FAILED,
        }

        extra_event_types = {
            ShipmentEvent.EventType.DELAYED,
            ShipmentEvent.EventType.DELIVERY_ATTEMPT_FAILED,
        }

        if (
            requested_event_type
            and requested_event_type not in extra_event_types
        ):
            return Response(
                {
                    "detail": (
                        "event_type must be 'delayed' or "
                        "'delivery_attempt_failed'."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_retry = (
            shipment.status == Shipment.Status.FAILED
            and new_status == Shipment.Status.PENDING
        )

        requires_description = (
            new_status == Shipment.Status.FAILED
            or bool(requested_event_type)
            or is_retry
        )

        if requires_description and not description:
            return Response(
                {
                    "detail": (
                        "Provide a customer-facing description for a "
                        "failure, delay, or failed delivery attempt."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_transitions = {
            Shipment.Status.PENDING: {
                Shipment.Status.PENDING,
                Shipment.Status.SHIPPED,
                Shipment.Status.FAILED,
            },
            Shipment.Status.SHIPPED: {
                Shipment.Status.SHIPPED,
                Shipment.Status.IN_TRANSIT,
                Shipment.Status.FAILED,
            },
            Shipment.Status.IN_TRANSIT: {
                Shipment.Status.IN_TRANSIT,
                Shipment.Status.DELIVERED,
                Shipment.Status.FAILED,
            },
            Shipment.Status.DELIVERED: {
                Shipment.Status.DELIVERED,
            },
            Shipment.Status.FAILED: {
                Shipment.Status.FAILED,
                Shipment.Status.PENDING,
            },
        }

        if new_status not in valid_transitions[shipment.status]:
            return Response(
                {
                    "detail": (
                        f"Cannot change shipment status from "
                        f"{shipment.status} to {new_status}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        carrier = request.data.get("carrier", shipment.carrier)
        agent_name = request.data.get(
            "delivery_agent_name",
            shipment.delivery_agent_name,
        )

        if not isinstance(carrier, str):
            return Response(
                {"detail": "Carrier must be text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(agent_name, str):
            return Response(
                {"detail": "Delivery agent name must be text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        carrier = carrier.strip()
        agent_name = agent_name.strip()

        if new_status == Shipment.Status.SHIPPED:
            if not carrier:
                return Response(
                    {"detail": "Assign a carrier before shipping."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not agent_name:
                return Response(
                    {
                        "detail": (
                            "Assign a delivery agent before shipping."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if order_has_blocking_cancellation(order):
                return Response(
                    {
                        "detail": (
                            "Dispatch is blocked while an order "
                            "cancellation request is pending or approved."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            cancellation_pending = (
                OrderCancellationRequest.objects.filter(
                    order=order,
                    status=OrderCancellationRequest.Status.PENDING,
                ).exists()
            )

            if cancellation_pending:
                return Response(
                    {
                        "detail": (
                            "Dispatch is blocked because an order "
                            "cancellation request is awaiting review."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        old_status = shipment.status

        shipment.carrier = carrier
        shipment.delivery_agent_name = agent_name

        if new_status != old_status:
            shipment.status = new_status

            if new_status == Shipment.Status.SHIPPED:
                shipment.shipped_at = timezone.now()

            elif new_status == Shipment.Status.DELIVERED:
                shipment.delivered_at = timezone.now()

        shipment.save()

        # Record when a failed shipment is reset for another attempt.
        if is_retry:
            ShipmentEvent.objects.create(
                shipment=shipment,
                event_type=ShipmentEvent.EventType.EXCEPTION_RESOLVED,
                description=description,
                reason_code=reason_code,
                internal_note=internal_note,
                created_by=request.user,
                customer_visible=True,
            )

        # Record the event associated with a status change.
        if new_status != old_status:
            event_type = event_type_by_status.get(new_status)

            if event_type:
                ShipmentEvent.objects.create(
                    shipment=shipment,
                    event_type=event_type,
                    description=(
                        description
                        or (
                            "Shipment status updated to "
                            f"{shipment.get_status_display()}."
                        )
                    ),
                    reason_code=reason_code,
                    internal_note=internal_note,
                    created_by=request.user,
                    customer_visible=True,
                )

        # Optional additional event, such as a delay.
        if requested_event_type:
            ShipmentEvent.objects.create(
                shipment=shipment,
                event_type=requested_event_type,
                description=description,
                reason_code=reason_code,
                internal_note=internal_note,
                created_by=request.user,
                customer_visible=True,
            )

        # Synchronize the order's status with all its shipments.
        # The order is already locked above.
        if order.status not in (
            Order.Status.DELIVERED,
            Order.Status.CANCELLED,
        ):
            shipments = Shipment.objects.filter(order=order)
            total_shipments = shipments.count()

            if (
                total_shipments > 0
                and not shipments.exclude(
                    status=Shipment.Status.DELIVERED
                ).exists()
            ):
                order.status = Order.Status.DELIVERED
                order.save(update_fields=["status", "updated_at"])

            elif shipments.filter(
                status__in=[
                    Shipment.Status.SHIPPED,
                    Shipment.Status.IN_TRANSIT,
                ]
            ).exists():
                if order.status != Order.Status.SHIPPED:
                    order.status = Order.Status.SHIPPED
                    order.save(update_fields=["status", "updated_at"])

        return Response(
            ShipmentSerializer(shipment).data,
            status=status.HTTP_200_OK,
        )


class AddressCorrectionRequestReviewView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def patch(self, request, correction_id):
        try:
            correction = (
                AddressCorrectionRequest.objects
                .select_for_update()
                .get(pk=correction_id)
            )
        except AddressCorrectionRequest.DoesNotExist:
            return Response(
                {
                    "detail": (
                        "Address-correction request not found."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            order = (
                Order.objects.select_for_update()
                .get(pk=correction.order_id)
            )
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if correction.status != AddressCorrectionRequest.Status.PENDING:
            return Response(
                {
                    "detail": (
                        "Only pending address-correction requests "
                        "can be reviewed."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        decision = request.data.get("status")
        review_note = request.data.get("review_note", "")

        if decision not in (
            AddressCorrectionRequest.Status.APPROVED,
            AddressCorrectionRequest.Status.REJECTED,
        ):
            return Response(
                {
                    "detail": (
                        "Status must be either 'approved' or 'rejected'."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(review_note, str):
            return Response(
                {"detail": "Review note must be text."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if decision == AddressCorrectionRequest.Status.APPROVED:
            if order.status in (
                Order.Status.SHIPPED,
                Order.Status.DELIVERED,
                Order.Status.CANCELLED,
            ):
                return Response(
                    {
                        "detail": (
                            "This order is no longer eligible "
                            "for an address correction."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            dispatched = Shipment.objects.filter(
                order=order,
                status__in=[
                    Shipment.Status.SHIPPED,
                    Shipment.Status.IN_TRANSIT,
                    Shipment.Status.DELIVERED,
                ],
            ).exists()

            if dispatched:
                return Response(
                    {
                        "detail": (
                            "The address cannot be changed because "
                            "a shipment has already been dispatched."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            order.shipping_address = correction.proposed_address
            order.save(
                update_fields=["shipping_address", "updated_at"]
            )

            Shipment.objects.filter(
                order=order,
                status=Shipment.Status.PENDING,
            ).update(
                shipping_address=correction.proposed_address
            )

        correction.status = decision
        correction.reviewed_by = request.user
        correction.reviewed_at = timezone.now()
        correction.review_note = review_note.strip()

        correction.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_note",
            ]
        )

        return Response(
            AddressCorrectionRequestSerializer(
                correction
            ).data,
            status=status.HTTP_200_OK,
        )