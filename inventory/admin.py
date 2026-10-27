from django.contrib import admin

from .models import StockLocation, Inventory, StockMovement


admin.site.register(StockLocation)
admin.site.register(Inventory)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "get_product",
        "inventory",
        "movement_type",
        "quantity",
        "reason",
        "reference",
        "created_at",
    )

    ordering = ("-created_at",)

    @admin.display(description="Product")
    def get_product(self, obj):
        return obj.inventory.product.name
