from django.urls import path
from .views import (CartDetailView,AddToCartView, UpdateCartItemView, RemoveCartItemView,)


urlpatterns = [
    path("cart/", CartDetailView.as_view(), name="cart-detail"),
    path("cart/items/", AddToCartView.as_view(), name="add-to-cart"),
    path( "cart/items/<int:item_id>/update/", UpdateCartItemView.as_view(),name="update-cart-item",),
    path("cart/items/<int:item_id>/remove/", RemoveCartItemView.as_view(), name="remove-cart-item"),
]

