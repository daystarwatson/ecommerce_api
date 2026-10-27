from django.db import models
from products.models import Product

# Create your models here.
class StockLocation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Inventory(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_records",
    )
    location = models.ForeignKey(
        StockLocation,
        on_delete=models.PROTECT,
        related_name="inventory_records",
    )
    quantity_on_hand = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "location"],
                name="unique_product_inventory_per_location",
            ),
        ]

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.location.name}: {self.quantity_on_hand}"
        )


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        OPENING = "OPENING", "Opening Stock"
        RECEIPT = "RECEIPT", "Stock Receipt"
        SALE = "SALE", "Sale"
        RETURN = "RETURN", "Customer Return"
        ADJUSTMENT = "ADJUSTMENT", "Stock Adjustment"

    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
    )
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="stock_movement_quantity_at_least_one",
            ),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return (
            f"{self.movement_type}: "
            f"{self.inventory.product.name} x {self.quantity}"
        )