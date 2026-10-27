from django.test import TestCase

# Create your tests here.

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class ProductAPITests(APITestCase):

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="testadmin",
            email="testadmin@example.com",
            password="TestPassword123!",
        )
        self.client.force_authenticate(user=self.admin)

    def test_create_product_with_invalid_category(self):
        url = reverse("product-list")

        data = {
            "category": 999,
            "name": "Test Product",
            "description": "Testing invalid category",
            "price": "50000.00",
            "stock_quantity": 5,
            "image": "",
            "is_active": True,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )


    
    def test_unauthenticated_user_cannot_create_product(self):
        self.client.force_authenticate(user=None)

        url = reverse("product-list")

        data = {
            "category": 1,
            "name": "Unauthorized Test Product",
            "description": "Testing product permissions",
            "price": "50000.00",
            "stock_quantity": 5,
            "image": "",
            "is_active": True,
        }

        response = self.client.post(url, data, format="json")

        self.assertIn(
            response.status_code,
            [
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ],
        )

