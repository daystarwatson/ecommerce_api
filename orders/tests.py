from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from cart.models import Cart, CartItem
from inventory.models import Inventory, StockLocation, StockMovement
from products.models import Category, Product
from .models import Order

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connection


class CheckoutInsufficientStockTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="test_customer",
            email="test@example.com",
            password="TestPassword123!",
        )

        category = Category.objects.create(name="Test Phones")
        self.product = Product.objects.create(
            category=category,
            name="Test Phone",
            price=Decimal("100000.00"),
            is_active=True,
        )

        location = StockLocation.objects.create(
            name="Test Store",
            is_active=True,
        )

        self.inventory = Inventory.objects.create(
            product=self.product,
            location=location,
            quantity_on_hand=2,
        )

        self.cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=5,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_checkout_rejects_quantity_above_stock(self):
        response = self.client.post("/api/orders/checkout/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, 2)

        self.assertTrue(
            CartItem.objects.filter(
                pk=self.cart_item.pk,
                quantity=5,
            ).exists()
        )


class CheckoutRollbackTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="rollback_customer",
            email="rollback@example.com",
            password="TestPassword123!",
        )

        category = Category.objects.create(
            name="Rollback Test Phones"
        )
        self.product = Product.objects.create(
            category=category,
            name="Rollback Test Phone",
            price=Decimal("100000.00"),
            stock_quantity=2,
            is_active=True,
        )

        location = StockLocation.objects.create(
            name="Rollback Test Store",
            is_active=True,
        )

        self.inventory = Inventory.objects.create(
            product=self.product,
            location=location,
            quantity_on_hand=2,
        )

        self.cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            product=self.product,
            quantity=2,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_database_error_rolls_back_checkout(self):
        with patch(
            "orders.views.StockMovement.objects.create",
            side_effect=IntegrityError("Simulated database error"),
        ):
            with self.assertRaises(IntegrityError):
                self.client.post("/api/orders/checkout/")

        self.assertEqual(Order.objects.count(), 0)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, 2)

        self.assertTrue(
            CartItem.objects.filter(
                pk=self.cart_item.pk,
                quantity=2,
            ).exists()
        )

        self.assertEqual(StockMovement.objects.count(), 0)


class ConcurrentCheckoutTest(TransactionTestCase):
    def setUp(self):
        User = get_user_model()

        self.customer_a = User.objects.create_user(
            username="concurrent_customer_a",
            email="concurrent_a@example.com",
            password="TestPassword123!",
        )

        self.customer_b = User.objects.create_user(
            username="concurrent_customer_b",
            email="concurrent_b@example.com",
            password="TestPassword123!",
        )

        category = Category.objects.create(
            name="Concurrency Test Phones"
        )

        self.product = Product.objects.create(
            category=category,
            name="Last Test Phone",
            price=Decimal("100000.00"),
            stock_quantity=1,
            is_active=True,
        )

        location = StockLocation.objects.create(
            name="Concurrency Test Store",
            is_active=True,
        )

        self.inventory = Inventory.objects.create(
            product=self.product,
            location=location,
            quantity_on_hand=1,
        )

        self.cart_a = Cart.objects.create(user=self.customer_a)
        self.cart_b = Cart.objects.create(user=self.customer_b)

        CartItem.objects.create(
            cart=self.cart_a,
            product=self.product,
            quantity=1,
        )

        CartItem.objects.create(
            cart=self.cart_b,
            product=self.product,
            quantity=1,
        )

    def test_only_one_customer_buys_the_last_phone(self):
        barrier = Barrier(2)

        def checkout(customer):
            close_old_connections()

            try:
                client = APIClient()
                client.force_authenticate(user=customer)

                # Make both workers ready before either sends its request.
                barrier.wait(timeout=10)

                response = client.post("/api/orders/checkout/")
                return response.status_code

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(
                checkout, self.customer_a
            )
            future_b = executor.submit(
                checkout, self.customer_b
            )

            status_a = future_a.result(timeout=30)
            status_b = future_b.result(timeout=30)

        statuses = [status_a, status_b]

        self.assertEqual(statuses.count(201), 1)
        self.assertEqual(statuses.count(400), 1)

        self.assertEqual(Order.objects.count(), 1)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity_on_hand, 0)

        remaining_cart_items = CartItem.objects.filter(
            cart__in=[self.cart_a, self.cart_b]
        ).count()

        self.assertEqual(remaining_cart_items, 1)