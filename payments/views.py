
import uuid

from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Order
from shipping.services import create_shipments_for_order

from .models import Payment
from .serializers import PaymentInitializationSerializer
from .services import (
    PaystackError,
    PaystackInitializationUncertain,
    initialize_paystack_payment,
    verify_paystack_payment,
)


class PaymentInitializationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentInitializationSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data["order_id"]
        email = request.user.email

        if not email:
            return Response(
                {
                    "detail": (
                        "Your account needs an email address "
                        "before you can pay."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(
                    id=order_id,
                    user=request.user,
                )

                if order.status != Order.Status.PENDING:
                    return Response(
                        {
                            "detail": (
                                "Only pending orders can start "
                                "a payment."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if order.total_amount <= 0:
                    return Response(
                        {
                            "detail": (
                                "This order does not have a valid "
                                "payment amount."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                existing_payment = Payment.objects.filter(
                    order=order,
                    status=Payment.Status.PENDING,
                ).order_by("-created_at").first()

                if existing_payment:
                    if existing_payment.authorization_url:
                        return Response(
                            {
                                "detail": (
                                    "A pending payment already exists. "
                                    "Continue using the existing "
                                    "checkout link."
                                ),
                                "payment_id": existing_payment.id,
                                "order_id": order.id,
                                "amount": str(existing_payment.amount),
                                "currency": existing_payment.currency,
                                "reference": existing_payment.reference,
                                "status": existing_payment.status,
                                "authorization_url": (
                                    existing_payment.authorization_url
                                ),
                            },
                            status=status.HTTP_200_OK,
                        )

                    return Response(
    {
        "detail": (
            "This payment may already be processing. "
            "Check its status before retrying."
        ),
        "payment_id": existing_payment.id,
        "reference": existing_payment.reference,
        "status": existing_payment.status,
    },
    status=status.HTTP_409_CONFLICT,
)

                else:
                    reference = f"PAY-{uuid.uuid4().hex}"

                    payment = Payment.objects.create(
                        order=order,
                        amount=order.total_amount,
                        currency="NGN",
                        reference=reference,
                        status=Payment.Status.PENDING,
                    )

        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Call Paystack after releasing the database lock.
        amount_kobo = int(payment.amount * 100)

        try:
            paystack_data = initialize_paystack_payment(
                email=email,
                amount_kobo=amount_kobo,
                reference=payment.reference,
            )

        except PaystackInitializationUncertain:
            return Response(
                {
                    "detail": (
                        "We could not confirm the outcome "
                        "of payment initialization. "
                        "Check the payment reference before retrying."
                    ),
                    "payment_id": payment.id,
                    "reference": payment.reference,
                    "status": payment.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        except PaystackError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "payment_id": payment.id,
                    "reference": payment.reference,
                    "status": payment.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if paystack_data.get("reference") != payment.reference:
            return Response(
                {
                    "detail": (
                        "Paystack returned a different payment "
                        "reference. Please contact support."
                    ),
                    "payment_id": payment.id,
                    "reference": payment.reference,
                    "status": payment.status,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        payment.authorization_url = paystack_data[
            "authorization_url"
        ]
        payment.save(
            update_fields=["authorization_url", "updated_at"]
        )

        return Response(
            {
                "detail": (
                    "Payment initialized. Continue using "
                    "the Paystack checkout link."
                ),
                "payment_id": payment.id,
                "order_id": order.id,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "reference": payment.reference,
                "status": payment.status,
                "authorization_url": payment.authorization_url,
            },
            status=status.HTTP_200_OK,
        )


class PaymentVerificationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reference = request.data.get("reference")

        if not isinstance(reference, str) or not reference.strip():
            return Response(
                {"detail": "A payment reference is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reference = reference.strip()

        try:
            payment = Payment.objects.select_related(
                "order",
                "order__user",
            ).get(
                reference=reference,
                order__user=request.user,
            )
        except Payment.DoesNotExist:
            return Response(
                {"detail": "Payment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        
        if payment.status == Payment.Status.SUCCESSFUL:
            with transaction.atomic():
                # Always lock the order first, then the payment.
                order = Order.objects.select_for_update().get(
                    pk=payment.order_id
                )

                payment = Payment.objects.select_for_update().get(
                    pk=payment.pk
                )

                # Another request may have changed the payment
                # while this request was waiting for the locks.
                if payment.status != Payment.Status.SUCCESSFUL:
                    return Response(
                        {
                            "detail": (
                                "Payment status changed. "
                                "Please verify the payment again."
                            ),
                            "payment_status": payment.status,
                            "order_status": order.status,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                # Retry shipment creation for an already-paid order.
                if order.status == Order.Status.PAID:
                    create_shipments_for_order(order)

            return Response(
                {
                    "detail": "Payment has already been verified.",
                    "reference": payment.reference,
                    "payment_status": payment.status,
                    "order_status": order.status,
                }
            )

        try:
            paystack_data = verify_paystack_payment(
                payment.reference
            )
        except PaystackError:
            return Response(
                {
                    "detail": (
                        "We could not verify this payment "
                        "with Paystack. Please try again."
                    )
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if paystack_data.get("reference") != payment.reference:
            return Response(
                {"detail": "Payment reference does not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        paystack_status = paystack_data.get("status")

        # Handle unsuccessful or incomplete transactions safely.
        if paystack_status != "success":
            with transaction.atomic():
                # Lock the order first, then the payment.
                order = Order.objects.select_for_update().get(
                    pk=payment.order_id
                )

                payment = Payment.objects.select_for_update().get(
                    pk=payment.pk
                )

                # Never overwrite a payment already confirmed successful.
                if payment.status == Payment.Status.SUCCESSFUL:
                    return Response(
                        {
                            "detail": (
                                "Payment has already been verified."
                            ),
                            "reference": payment.reference,
                            "payment_status": payment.status,
                            "order_status": order.status,
                        }
                    )

                # Only update these statuses when Paystack explicitly
                # reports them.
                if paystack_status == "abandoned":
                    payment.status = Payment.Status.ABANDONED
                    payment.save(
                        update_fields=["status", "updated_at"]
                    )

                elif paystack_status == "failed":
                    payment.status = Payment.Status.FAILED
                    payment.save(
                        update_fields=["status", "updated_at"]
                    )

                return Response(
                    {
                        "detail": (
                            "Paystack has not confirmed a "
                            "successful payment."
                        ),
                        "paystack_status": paystack_status,
                        "payment_status": payment.status,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Validate the amount reported by Paystack.
        try:
            paid_amount_kobo = int(paystack_data.get("amount"))
        except (TypeError, ValueError):
            return Response(
                {
                    "detail": (
                        "Paystack returned an invalid "
                        "payment amount."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        expected_amount_kobo = int(payment.amount * 100)

        if paid_amount_kobo != expected_amount_kobo:
            return Response(
                {
                    "detail": (
                        "The payment amount does not match "
                        "the order."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate the currency reported by Paystack.
        if paystack_data.get("currency") != payment.currency:
            return Response(
                {
                    "detail": (
                        "The payment currency does not match."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Lock the order first so payment attempts for the
            # same order must take turns finalizing payment.
            order = Order.objects.select_for_update().get(
                pk=payment.order_id
            )

            # Lock and reload this particular payment attempt.
            payment = Payment.objects.select_for_update().get(
                pk=payment.pk
            )

            # Check whether another request already completed
            # this payment.
            if payment.status == Payment.Status.SUCCESSFUL:
                return Response(
                    {
                        "detail": "Payment has already been verified.",
                        "reference": payment.reference,
                        "payment_status": payment.status,
                        "order_status": order.status,
                    }
                )

            # Do not finalize another payment if the order is
            # no longer pending.
            if order.status != Order.Status.PENDING:
                return Response(
                    {
                        "detail": "This order is no longer pending.",
                        "payment_status": payment.status,
                        "order_status": order.status,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Record the verified payment as successful.
            payment.status = Payment.Status.SUCCESSFUL
            payment.paid_at = timezone.now()
            payment.save(
                update_fields=[
                    "status",
                    "paid_at",
                    "updated_at",
                ]
            )

            
            # Mark the associated order as paid.
            order.status = Order.Status.PAID
            order.save(
                update_fields=["status", "updated_at"]
            )

            # Create one shipment per warehouse.
            # This remains inside the same database transaction.
            create_shipments_for_order(order)


        return Response(
            {
                "detail": "Payment verified successfully.",
                "reference": payment.reference,
                "payment_status": payment.status,
                "order_id": order.id,
                "order_status": order.status,
                "amount": str(payment.amount),
                "currency": payment.currency,
            }
        )
