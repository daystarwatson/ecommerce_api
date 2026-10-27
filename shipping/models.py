
import uuid

from django.conf import settings
from django.db import models


class Shipment(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SHIPPED = "shipped", "Shipped"
        IN_TRANSIT = "in_transit", "In Transit"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="shipments",
    )

    location = models.ForeignKey(
        "inventory.StockLocation",
        on_delete=models.PROTECT,
        related_name="shipments",
        null=True,
        blank=True,
    )

    tracking_number = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    shipping_address = models.JSONField()

    carrier = models.CharField(max_length=100, blank=True)

    delivery_agent_name = models.CharField(
        max_length=150,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "location"],
                name="unique_shipment_per_order_location",
            ),
        ]

    def __str__(self):
        return f"Shipment {self.tracking_number} - Order {self.order_id}"


class ShipmentItem(models.Model):

    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        related_name="items",
    )

    allocation = models.OneToOneField(
        "orders.OrderItemAllocation",
        on_delete=models.PROTECT,
        related_name="shipment_item",
    )

    def __str__(self):
        return (
            f"Shipment {self.shipment_id} - "
            f"Allocation {self.allocation_id}"
        )


class AddressCorrectionRequest(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="address_correction_requests",
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_address_corrections",
    )

    original_address = models.JSONField()
    proposed_address = models.JSONField()

    reason = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    requested_at = models.DateTimeField(auto_now_add=True)

    
    review_due_at = models.DateTimeField(
        null=True,
        blank=True,
    )


    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_address_corrections",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return (
            f"Address correction for Order {self.order_id} "
            f"({self.status})"
        )



class ShipmentEvent(models.Model):

    class EventType(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        DISPATCHED = "dispatched", "Dispatched"
        IN_TRANSIT = "in_transit", "In Transit"
        DELAYED = "delayed", "Delayed"
        DELIVERY_ATTEMPT_FAILED = (
            "delivery_attempt_failed",
            "Delivery Attempt Failed",
        )
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        RETURNED = "returned", "Returned"
        EXCEPTION_RESOLVED = (
            "exception_resolved",
            "Exception Resolved",
        )

    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        related_name="events",
    )

    event_type = models.CharField(
        max_length=30,
        choices=EventType.choices,
    )

    # Explanation intended for the customer.
    description = models.TextField()

    # Optional category for filtering and reporting.
    reason_code = models.CharField(
        max_length=50,
        blank=True,
    )

    # Private investigation notes for administrators.
    internal_note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_shipment_events",
    )

    customer_visible = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return (
            f"{self.get_event_type_display()} - "
            f"Shipment {self.shipment_id}"
        )
