
from django.urls import path

from .views import (
    PaymentInitializationView,
    PaymentVerificationView,
)


urlpatterns = [
    path(
        "initialize/",
        PaymentInitializationView.as_view(),
        name="payment-initialize",
    ),
    path(
        "verify/",
        PaymentVerificationView.as_view(),
        name="payment-verify",
    ),
]
