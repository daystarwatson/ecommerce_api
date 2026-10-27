from django.db import models

# Create your models here.
from django.db.models import Q

from orders.models import Order


class Payment(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INITIALIZATION_UNCERTAIN = (
           "initialization_uncertain",
           "Initialization Uncertain",
       )
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"
        ABANDONED = "abandoned", "Abandoned"
        REVERSED = "reversed", "Reversed"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="payments",
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    currency = models.CharField(
        max_length=3,
        default="NGN",
    )

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )

    reference = models.CharField(
        max_length=100,
        unique=True,
    )

    authorization_url = models.URLField(
    max_length=500,
    blank=True,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order"],
                condition=Q(status="successful"),
                name="one_successful_payment_per_order",
            ),
        ]

    def __str__(self):
        return (
            f"Payment {self.reference} - "
            f"{self.status} - Order {self.order_id}"
        )
