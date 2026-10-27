
from django.db import transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from products.models import Product
from inventory.models import Inventory
from .models import Cart, CartItem
from .serializers import (
    CartSerializer,
    AddToCartSerializer,
    UpdateCartItemSerializer,
)


class CartDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cart, created = Cart.objects.get_or_create(
            user=request.user
        )

        serializer = CartSerializer(cart)

        return Response(serializer.data)


class AddToCartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = serializer.validated_data["product"]
        quantity = serializer.validated_data["quantity"]

        if not product.is_active:
            return Response(
                {"detail": "This product is not available."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        available_stock = Inventory.objects.filter(
            product=product,
            location__is_active=True,
        ).aggregate(
            total=Coalesce(
                Sum("quantity_on_hand"),
                Value(0),
            )
        )["total"]

        cart, created = Cart.objects.get_or_create(
            user=request.user
        )

        with transaction.atomic():
            cart_item = CartItem.objects.filter(
                cart=cart,
                product=product,
            ).first()

            requested_total = quantity

            if cart_item:
                requested_total += cart_item.quantity

            if requested_total > available_stock:
                return Response(
                    {
                        "detail": (
                            "Insufficient stock. "
                            f"Available: {available_stock}. "
                            f"Requested total: {requested_total}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if cart_item:
                cart_item.quantity = requested_total
                cart_item.save(update_fields=["quantity"])
            else:
                CartItem.objects.create(
                    cart=cart,
                    product=product,
                    quantity=quantity,
                )

        cart.refresh_from_db()
        response_serializer = CartSerializer(cart)

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )


class UpdateCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, item_id):
        serializer = UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_quantity = serializer.validated_data["quantity"]
        
        cart_item = CartItem.objects.filter(
            pk=item_id,
            cart__user=request.user,
        ).first()

        if cart_item is None:
            return Response(
                {"detail": "Cart item not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        
        if not cart_item.product.is_active:
            return Response(
                {
                    "detail": (
                        "This product is no longer for sale."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if the updated quantity is available
        available_stock = Inventory.objects.filter(
            product=cart_item.product,
            location__is_active=True,
        ).aggregate(
            total=Coalesce(
                Sum("quantity_on_hand"),
                Value(0),
            )
        )["total"]

        if new_quantity > available_stock:
            return Response(
                {
                    "detail": (
                        "Insufficient stock. "
                        f"Available: {available_stock}. "
                        f"Requested: {new_quantity}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item.quantity = new_quantity
        cart_item.save(update_fields=["quantity"])

        cart = cart_item.cart
        cart.refresh_from_db()

        response_serializer = CartSerializer(cart)

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

    
class RemoveCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, item_id):
        cart_item = CartItem.objects.filter(
            pk=item_id,
            cart__user=request.user,
        ).first()

        if cart_item is None:
            return Response(
                {"detail": "Cart item not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        cart_item.delete()

        return Response(
            {"detail": "Item removed from cart."},
            status=status.HTTP_200_OK,
        )