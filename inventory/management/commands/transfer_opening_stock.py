from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from products.models import Product
from inventory.models import StockLocation, Inventory, StockMovement


class Command(BaseCommand):
    help = "Transfer existing product stock into the inventory system."

    @transaction.atomic
    def handle(self, *args, **options):
        location = StockLocation.objects.get(name="main store")

        if Inventory.objects.exists():
            raise CommandError(
                "Inventory records already exist. Transfer stopped."
            )

        products = Product.objects.all()
        total_quantity = 0
        products_transferred = 0

        for product in products:
            inventory = Inventory.objects.create(
                product=product,
                location=location,
                quantity_on_hand=product.stock_quantity,
            )

            if product.stock_quantity > 0:
                StockMovement.objects.create(
                    inventory=inventory,
                    movement_type=StockMovement.MovementType.OPENING,
                    quantity=product.stock_quantity,
                    reason="Initial stock transferred from Product.stock_quantity",
                    reference=f"OPENING-PRODUCT-{product.id}",
                )

            total_quantity += product.stock_quantity
            products_transferred += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Transfer complete. Products: {products_transferred}. "
                f"Total stock: {total_quantity}."
            )
        )
