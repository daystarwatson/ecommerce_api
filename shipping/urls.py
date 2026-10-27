
from django.urls import path

from .views import (
    ShipmentCreateView,
    ShipmentListView,
    AddressCorrectionRequestCreateView,
    AddressCorrectionRequestListView,
    ShipmentUpdateView,
    AddressCorrectionRequestReviewView,
    CustomerOrderTrackingView,
)


urlpatterns = [
    path(
        "",
        ShipmentListView.as_view(),
        name="shipment-list",
    ),
    path(
        "orders/<int:order_id>/address-corrections/",
        AddressCorrectionRequestCreateView.as_view(),
        name="address-correction-create",
    ),

    path(
    "address-corrections/",
    AddressCorrectionRequestListView.as_view(),
    name="address-correction-list",
    ),


    path(
    "<int:shipment_id>/",
    ShipmentUpdateView.as_view(),
    name="shipment-update",
  ),

    path(
    "address-corrections/<int:correction_id>/review/",
    AddressCorrectionRequestReviewView.as_view(),
    name="address-correction-review",
  ),


    path(
    "orders/<int:order_id>/tracking/",
    CustomerOrderTrackingView.as_view(),
    name="customer-order-tracking",
),

    path(
    "create/",
    ShipmentCreateView.as_view(),
    name="shipment-create",
   ),


]
