from django.shortcuts import render

# Create your views here.
from django.db.models.deletion import ProtectedError
from rest_framework.response import Response
from rest_framework import status
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAdminUser

from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer


from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all().order_by("name")
    serializer_class = CategorySerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [AllowAny()]
        return [IsAdminUser()]

    def destroy(self, request, *args, **kwargs):
      try:
          return super().destroy(request, *args, **kwargs)
      except ProtectedError:
          return Response(
            {
                "detail": (
                    "Cannot delete this category because "
                    "products are still assigned to it."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )



class ProductViewSet(viewsets.ModelViewSet):
    queryset = (
        Product.objects
        .select_related("category")
        .annotate(
            total_stock=Coalesce(
                Sum(
                    "inventory_records__quantity_on_hand",
                    filter=Q(
                        inventory_records__location__is_active=True
                    ),
                ),
                Value(0),
            )
        )
    )
    serializer_class = ProductSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [AllowAny()]
        return [IsAdminUser()]

