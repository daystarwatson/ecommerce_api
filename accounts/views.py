# Create your views here.

from django.db import transaction
from rest_framework import generics, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import Address, User
from .serializers import AddressSerializer, UserSerializer


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        with transaction.atomic():
            if serializer.validated_data.get("is_default", False):
                Address.objects.filter(
                    user=self.request.user,
                    is_default=True,
                ).update(is_default=False)

            serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            if serializer.validated_data.get("is_default", False):
                Address.objects.filter(
                    user=self.request.user,
                    is_default=True,
                ).exclude(pk=self.get_object().pk).update(is_default=False)

            serializer.save()


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]