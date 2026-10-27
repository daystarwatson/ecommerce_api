
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cart.models import Cart
from inventory.models import Inventory, StockMovement
from .models import Order, OrderItem,  OrderItemAllocation

from accounts.models import Address
from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAdminUser

from orders.services import (
    create_order_item_allocation,
    cancel_order,
    request_order_cancellation,
    OrderCancellationError,
    review_order_cancellation_request,
    initiate_cancellation_refund,
    verify_and_finalize_cancellation_refund,
)



class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        # 1. Validate the selected delivery address.
        address_id = request.data.get("address_id")

        if not address_id:
            return Response(
                {"detail": "Please provide an address_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            address = Address.objects.get(
                id=address_id,
                user=request.user,
            )
        except (Address.DoesNotExist, ValueError, TypeError):
            return Response(
                {
                    "detail": (
                        "The selected delivery address does not exist "
                        "or does not belong to you."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Lock this customer's cart.

        try:
            cart = Cart.objects.select_for_update().get(
                user=request.user
            )
        except Cart.DoesNotExist:
            return Response(
                {"detail": "Your cart does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 3. Load the cart items and their products.
        cart_items = list(
            cart.items.select_related("product").all()
        )

        if not cart_items:
            return Response(
                {"detail": "Your cart is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Reject products that are no longer for sale.
        for item in cart_items:
            if not item.product.is_active:
                return Response(
                    {
                        "detail": (
                            f"{item.product.name} is no longer for sale."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 5. Lock all relevant active inventory records in a consistent order to reduce deadlock risk.
        product_ids = sorted(
            {item.product_id for item in cart_items}
        )

        locked_inventory = list(
            Inventory.objects.select_for_update()
            .filter(
                product_id__in=product_ids,
                location__active=True,
            )
            .order_by("product_id", "location_id", "id")
        )

        # Group the locked inventory records by product.
        inventory_by_product = {}

        for inventory in locked_inventory:
            inventory_by_product.setdefault(
                inventory.product_id, []
            ).append(inventory)

        # 6. Validate every item and plan the allocation before making any stock changes.
        allocations = []
        total_amount = Decimal("0.00")

        for item in cart_items:
            product = item.product
            remaining = item.quantity
            product_inventory = inventory_by_product.get(
                product.id, []
            )

            available_stock = sum(
                record.quantity_on_hand
                for record in product_inventory
            )

            if available_stock < item.quantity:
                return Response(
                    {
                        "detail": (
                            f"Insufficient stock for {product.name}. "
                            f"Available: {available_stock}; "
                            f"requested: {item.quantity}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for inventory in product_inventory:
                if remaining == 0:
                    break

                quantity_to_deduct = min(
                    inventory.quantity_on_hand,
                    remaining,
                )

                if quantity_to_deduct > 0:
                    allocations.append(
                        (item, inventory, quantity_to_deduct)
                    )
                    remaining -= quantity_to_deduct

            # Save the price snapshot and calculate the subtotal.
            total_amount += product.price * item.quantity

        
        
        # 7. Create the pending order and snapshot the delivery address.
        order = Order.objects.create(
            user=request.user,
            shipping_address={
                "line1": address.line1,
                "line2": address.line2,
                "city": address.city,
                "state": address.state,
                "postal_code": address.postal_code,
                "country": address.country,
            },
            status=Order.Status.PENDING,
            total_amount=total_amount,
        )


        # Create each OrderItem and remember which cart item
        # it belongs to.
        order_item_by_cart_item_id = {}

        for item in cart_items:
            order_item = OrderItem.objects.create(
                order=order,
                product=item.product,
                product_name=item.product.name,
                unit_price=item.product.price,
                quantity=item.quantity,
            )

            order_item_by_cart_item_id[item.pk] = order_item

        # 8. Deduct stock, record stock movements, and save
        # the warehouse allocation for each order item.
        for item, inventory, quantity_to_deduct in allocations:
            inventory.quantity_on_hand -= quantity_to_deduct
            inventory.save(
                update_fields=[
                    "quantity_on_hand",
                    "updated_at",
                ]
            )

            StockMovement.objects.create(
                inventory=inventory,
                movement_type=StockMovement.MovementType.SALE,
                quantity=quantity_to_deduct,
                reason=f"Checkout for order {order.pk}",
                reference=f"ORDER-{order.pk}",
            )

            create_order_item_allocation(
                order_item=order_item_by_cart_item_id[item.pk],
                inventory=inventory,
                quantity=quantity_to_deduct,
                actor=request.user,
                reason="Allocation created during checkout",
        )

 
        # 9. Clear the cart only after all prior operations succeed.
        cart.items.all().delete()

        return Response(
            {
                "detail": "Checkout successful.",
                "order_id": order.pk,
                "status": order.status,
                "total_amount": str(order.total_amount),
                "items": [
                    {
                        "product_name": item.product.name,
                        "unit_price": str(item.product.price),
                        "quantity": item.quantity,
                        "subtotal": str(
                            item.product.price * item.quantity
                        ),
                    }
                    for item in cart_items
                ],
            },
            status=status.HTTP_201_CREATED,
        )




class OrderCancellationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        # Check ownership before deciding how to handle cancellation.
        try:
            order = Order.objects.get(
                pk=order_id,
                user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            if order.status == Order.Status.PENDING:
                cancelled_order = cancel_order(
                    order_id=order.pk,
                    actor=request.user,
                )

                return Response(
                    {
                        "detail": (
                            "Unpaid order cancelled successfully."
                        ),
                        "order_id": cancelled_order.pk,
                        "status": cancelled_order.status,
                        "stock_restored": True,
                        "refund_status": "not_required",
                    },
                    status=status.HTTP_200_OK,
                )

            if order.status == Order.Status.PAID:
                cancellation_request = request_order_cancellation(
                    order_id=order.pk,
                    actor=request.user,
                    reason=str(
                        request.data.get("reason", "")
                    ),
                )

                return Response(
                    {
                        "detail": (
                            "Cancellation request submitted "
                            "for admin review."
                        ),
                        "order_id": order.pk,
                        "cancellation_request_id": (
                            cancellation_request.pk
                        ),
                        "request_status": (
                            cancellation_request.status
                        ),
                        "order_status": order.status,
                        "refund_status": (
                            cancellation_request.refund_status
                        ),
                    },
                    status=status.HTTP_201_CREATED,
                )

            return Response(
                {
                    "detail": (
                        "This order cannot be cancelled through "
                        "the ordinary cancellation process."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except OrderCancellationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )




class CancellationRequestReviewView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, request_id):
        decision = request.data.get("decision")
        review_note = request.data.get("review_note", "")

        try:
            cancellation_request = review_order_cancellation_request(
                request_id=request_id,
                actor=request.user,
                decision=decision,
                review_note=review_note,
            )
        except OrderCancellationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "Cancellation request reviewed.",
                "request_id": cancellation_request.id,
                "order_id": cancellation_request.order_id,
                "status": cancellation_request.status,
                "refund_status": cancellation_request.refund_status,
                "reviewed_at": cancellation_request.reviewed_at,
                "review_note": cancellation_request.review_note,
            },
            status=status.HTTP_200_OK,
        )



class CancellationRefundInitiationView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, request_id):
        try:
            cancellation_request = initiate_cancellation_refund(
                request_id=request_id,
                actor=request.user,
            )
        except OrderCancellationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "Refund initiation processed.",
                "request_id": cancellation_request.pk,
                "order_id": cancellation_request.order_id,
                "request_status": cancellation_request.status,
                "refund_status": cancellation_request.refund_status,
                "refund_reference": cancellation_request.refund_reference,
            },
            status=status.HTTP_200_OK,
        )



class CancellationRefundVerificationView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, request_id):
        try:
            cancellation_request = (
                verify_and_finalize_cancellation_refund(
                    request_id=request_id,
                )
            )
        except OrderCancellationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "Refund verification completed.",
                "request_id": cancellation_request.pk,
                "order_id": cancellation_request.order_id,
                "request_status": cancellation_request.status,
                "refund_status": cancellation_request.refund_status,
            },
            status=status.HTTP_200_OK,
        )



class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = (
            Order.objects
            .filter(user=request.user)
            .order_by("-created_at")
        )

        data = [
            {
                "id": order.id,
                "status": order.status,
                "total_amount": str(order.total_amount),
                "created_at": order.created_at,
                "updated_at": order.updated_at,
            }
            for order in orders
        ]

        return Response(data, status=status.HTTP_200_OK)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        order = get_object_or_404(
            Order.objects.filter(user=request.user),
            pk=order_id,
        )

        items = [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "unit_price": str(item.unit_price),
                "quantity": item.quantity,
                "subtotal": str(item.unit_price * item.quantity),
            }
            for item in order.items.all()
        ]

        return Response(
            {
                "id": order.id,
                "status": order.status,
                "total_amount": str(order.total_amount),
                "shipping_address": order.shipping_address,
                "created_at": order.created_at,
                "updated_at": order.updated_at,
                "items": items,
            },
            status=status.HTTP_200_OK,
        )
