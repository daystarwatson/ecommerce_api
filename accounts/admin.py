from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, Profile, Address


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    pass


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "date_of_birth", "created_at")
    search_fields = ("user__username", "user__email")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "customer_username",
        "customer_full_name",
        "line1",
        "city",
        "country",
        "is_default",
    )

    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
        "line1",
        "city",
        "country",
    )

    @admin.display(description="Username", ordering="user__username")
    def customer_username(self, obj):
        return obj.user.username

    @admin.display(description="Full name", ordering="user__first_name")
    def customer_full_name(self, obj):
        return obj.user.get_full_name() or "(No name entered)"

