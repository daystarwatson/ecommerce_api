
from django.conf import settings
from django.db import models

from products.models import Product
# Create your models here.

class Order(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )

    shipping_address = models.JSONField(
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order {self.pk} - {self.user}"


class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )

    product_name = models.CharField(max_length=255)

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"



class OrderItemAllocationQuerySet(models.QuerySet):

    def update(self, **kwargs):
        raise ValueError(
            "Order allocations cannot be updated directly. "
            "Use a coordinated inventory adjustment workflow."
        )

    def delete(self):
        raise ValueError(
            "Order allocations cannot be deleted directly. "
            "Use a coordinated inventory adjustment workflow."
        )



class OrderItemAllocation(models.Model):
    
    objects = models.Manager.from_queryset(OrderItemAllocationQuerySet)()
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.PROTECT,
        related_name="allocations",
    )

    inventory = models.ForeignKey(
        "inventory.Inventory",
        on_delete=models.PROTECT,
        related_name="order_item_allocations",
    )

    quantity = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order_item", "inventory"],
                name="unique_order_item_inventory_allocation",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="order_item_allocation_quantity_positive",
            ),
        ]


    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError(
                "Existing order allocations cannot be modified directly. "
                "Use a coordinated inventory adjustment workflow."
            )

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Order allocations cannot be deleted directly. "
            "Use a coordinated inventory adjustment workflow."
        )


    def __str__(self):
        return (
            f"{self.order_item} - "
            f"Inventory {self.inventory_id} - "
            f"Quantity {self.quantity}"
        )


class OrderItemAllocationHistory(models.Model):

    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        REASSIGNED = "reassigned", "Reassigned"
        DELETED = "deleted", "Deleted"

    # Preserve the original allocation ID even after deletion.
    allocation_id_snapshot = models.PositiveBigIntegerField()

    # Preserve the order item reference and ID independently.
    order_item_id_snapshot = models.PositiveBigIntegerField()
    order_item_description = models.CharField(max_length=255)

    # Keep a link when the order item still exists.
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocation_history",
    )

    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
    )

    previous_inventory_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )

    new_inventory_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )

    previous_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    new_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    reason = models.TextField(blank=True)

    # Keep the actor's ID and description even if their account is deleted.
    performed_by_id_snapshot = models.CharField(
        max_length=255,
        blank=True,
    )

    performed_by_description = models.CharField(
        max_length=255,
        blank=True,
    )

    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocation_history_events",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["allocation_id_snapshot", "created_at"],
                name="alloc_hist_id_created_idx",
            ),
            models.Index(
                fields=["order_item_id_snapshot", "created_at"],
                name="alloc_hist_item_created_idx",
            ),
        ]

    def __str__(self):
        return (
            f"Allocation {self.allocation_id_snapshot} - "
            f"{self.get_event_type_display()} - "
            f"{self.created_at}"
        )


class OrderCancellationRequest(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    
    class RefundStatus(models.TextChoices):
       NOT_REQUIRED = "not_required", "Not Required"
       NOT_STARTED = "not_started", "Not Started"
       PENDING = "pending", "Pending"
       PROCESSING = "processing", "Processing"
       NEEDS_ATTENTION = "needs_attention", "Needs Attention"
       SUCCESSFUL = "successful", "Successful"
       FAILED = "failed", "Failed"
       UNKNOWN = "unknown", "Unknown"


    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="cancellation_requests",
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_cancellation_requests",
    )

    reason = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_cancellation_requests",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    review_note = models.TextField(blank=True)

    refund_status = models.CharField(
        max_length=20,
        choices=RefundStatus.choices,
        default=RefundStatus.NOT_STARTED,
    )

    refund_reference = models.CharField(
        max_length=100,
        blank=True,
    )

    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    refund_updated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["order", "status"],
                name="cancel_req_order_status_idx",
            ),
            models.Index(
                fields=["refund_status"],
                name="cancel_req_refund_status_idx",
            ),
        ]

    def __str__(self):
        return (
            f"Cancellation request {self.pk} "
            f"for order {self.order_id} - {self.status}"
        )
