
from django.contrib import admin
# Register your models here.
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "amount",
        "currency",
        "status",
        "reference",
        "paid_at",
        "created_at",
    )

    list_filter = (
        "status",
        "currency",
        "created_at",
    )

    search_fields = (
        "reference",
        "order__id",
        "order__user__username",
        "order__user__email",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "authorization_url",
    )
