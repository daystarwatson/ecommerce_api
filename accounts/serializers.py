from django.db import transaction
from rest_framework import serializers
from .models import Profile, User, Address, Profile


class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User

        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "phone",
            "is_verified",
        ]

        read_only_fields = ["id", "is_verified", "is_superuser", "is_staff"]

        extra_kwargs = {
             "password": {"write_only": True}
        }


    def create(self, validated_data):
        with transaction.atomic():
            password = validated_data.pop("password")
            user = User(**validated_data)
            user.set_password(password)
            user.save()
            Profile.objects.create(user=user)
        return user



class AddressSerializer(serializers.ModelSerializer):

    class Meta:
        model = Address

        fields = [
            "id",
            "line1",
            "line2",
            "city",
            "state",
            "postal_code",
            "country",
            "is_default",
        ]
        
        read_only_fields = ["id"]    