
from django.urls import path

from .views import (
    CheckoutView,
    OrderListView,
    OrderDetailView,
    OrderCancellationView,
    CancellationRequestReviewView,
    CancellationRefundInitiationView,
    CancellationRefundVerificationView,
)

urlpatterns = [
    path("", OrderListView.as_view(), name="order-list"),
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path(
        "<int:order_id>/",
        OrderDetailView.as_view(),
        name="order-detail",
    ),
    path(
        "<int:order_id>/cancel/",
        OrderCancellationView.as_view(),
        name="order-cancel",
    ),
    path(
        "cancellation-requests/<int:request_id>/review/",
        CancellationRequestReviewView.as_view(),
        name="cancellation-request-review",
    ),
    path(
        "cancellation-requests/<int:request_id>/refund/",
        CancellationRefundInitiationView.as_view(),
        name="cancellation-refund-initiate",
    ),
    path(
        "cancellation-requests/<int:request_id>/refunds/verify/",
        CancellationRefundVerificationView.as_view(),
        name="cancellation-refund-verify",
    ),
]
