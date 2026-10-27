
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import Address


User = get_user_model()


class AddressDefaultTest(TestCase):
    def test_new_default_address_replaces_old_default(self):
        # 1. Create a test customer.
        customer = User.objects.create_user(
            username="customer_test",
            password="TestPassword123!"
        )

        # 2. Create the customer's existing default address.
        first_address = Address.objects.create(
            user=customer,
            line1="25 Aba Road",
            city="Port Harcourt",
            state="Rivers",
            postal_code="500001",
            country="Nigeria",
            is_default=True,
        )

        # 3. Create a test client and authenticate as this customer.
        client = APIClient()
        client.force_authenticate(user=customer)

        # 4. Send a POST request to the real Address API.
        response = client.post(
            "/addresses/",
            {
                "line1": "15 Olu Obasanjo Road",
                "line2": "",
                "city": "Port Harcourt",
                "state": "Rivers",
                "postal_code": "500001",
                "country": "Nigeria",
                "is_default": True,
            },
            format="json",
        )

        # 5. Confirm that the API created the address successfully.
        self.assertEqual(response.status_code, 201)

        # 6. Reload the original address from the database.
        first_address.refresh_from_db()

        # 7. Check that the old address is no longer the default.
        self.assertFalse(first_address.is_default)

        # 8. Check that the new address is the only default.
        default_addresses = Address.objects.filter(
            user=customer,
            is_default=True,
        )

        self.assertEqual(default_addresses.count(), 1)
        self.assertEqual(
            default_addresses.first().line1,
            "15 Olu Obasanjo Road",
        )
# Create your tests here.


class AddressOwnershipTest(TestCase):
    def test_customer_cannot_retrieve_another_customers_address(self):
        # 1. Create two different customers.
        customer1 = User.objects.create_user(
            username="customer1",
            password="secret123"
        )

        customer2 = User.objects.create_user(
            username="customer2",
            password="secret456"
        )

        # 2. Create an address owned by Customer 2.
        address = Address.objects.create(
            user=customer2,
            line1="25 Aba Road",
            city="Port Harcourt",
            state="Rivers",
            postal_code="500001",
            country="Nigeria",
            is_default=True,
        )

        # 3. Authenticate the API client as Customer 1.
        client = APIClient()
        client.force_authenticate(user=customer1)

        # 4. Customer 1 tries to retrieve Customer 2's address.
        response = client.get(f"/addresses/{address.id}/")

        # 5. Confirm that access is denied through a 404 response.
        self.assertEqual(response.status_code, 404)