
from rest_framework import serializers
from .models import Cart
from .models import CartItem
from products.models import Product
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce


class CartItemSerializer(serializers.ModelSerializer):
    
    def get_available_stock(self, obj):
        result = obj.product.inventory_records.filter(
            location__is_active=True
        ).aggregate(
            total=Coalesce(
                Sum("quantity_on_hand"),
                Value(0),
            )
        )

        return result["total"]

    
    def get_is_available(self, obj):
        available_stock = self.get_available_stock(obj)

        return (
            obj.product.is_active
            and available_stock >= obj.quantity
        )

    
    def get_availability_message(self, obj):
        if not obj.product.is_active:
            return "This product is no longer for sale."

        available_stock = self.get_available_stock(obj)

        if available_stock == 0:
            return "This product is out of stock."

        if available_stock < obj.quantity:
            return (
                f"Only {available_stock} units are currently "
                "available. Please reduce your quantity."
            )

        return "This product is available."


    product_name = serializers.CharField(
        source="product.name",
        read_only=True,
    )
    product_price = serializers.DecimalField(
        source="product.price",
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    line_total = serializers.SerializerMethodField()

    available_stock = serializers.SerializerMethodField()

    is_available = serializers.SerializerMethodField()

    availability_message = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_price",
            "quantity",
            "line_total",
            "available_stock",
            "is_available",
            "availability_message",
        ]
        read_only_fields = [
            "id",
            "product_name",
            "product_price",
            "line_total",
        ]

    def get_line_total(self, obj):
        return obj.product.price * obj.quantity

    
class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "items",
            "total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "items",
            "total",
            "created_at",
            "updated_at",
        ]

    def get_total(self, obj):
        return sum(
            item.product.price * item.quantity
            for item in obj.items.all()
        )


class AddToCartSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all()
    )
    quantity = serializers.IntegerField(min_value=1)


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)
