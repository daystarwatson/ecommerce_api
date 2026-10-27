
import requests

from django.conf import settings


class PaystackError(Exception):
    pass


class PaystackInitializationUncertain(PaystackError):
    pass


def initialize_paystack_payment(
    email,
    amount_kobo,
    reference,
    callback_url=None,
):
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("Paystack is not configured.")

    payload = {
        "email": email,
        "amount": amount_kobo,
        "currency": "NGN",
        "reference": reference,
    }

    if callback_url:
        payload["callback_url"] = callback_url

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            json=payload,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

    except (requests.Timeout, requests.ConnectionError) as exc:
        raise PaystackInitializationUncertain(
            "The initialization outcome is uncertain. "
            "Check the existing payment reference before retrying."
        ) from exc

    except requests.HTTPError as exc:
        response = exc.response

        if response is not None:
            try:
                error_data = response.json()
                message = error_data.get(
                    "message",
                    "Paystack returned an HTTP error.",
                )
            except ValueError:
                message = "Paystack returned an HTTP error."
        else:
            message = "Paystack returned an HTTP error."

        raise PaystackError(message) from exc

    except requests.RequestException as exc:
        raise PaystackError(
            "Could not communicate with Paystack."
        ) from exc

    except ValueError as exc:
        raise PaystackError(
            "Paystack returned an invalid response."
        ) from exc

    if not result.get("status"):
        raise PaystackError(
            result.get("message", "Paystack initialization failed.")
        )

    data = result.get("data") or {}
    authorization_url = data.get("authorization_url")
    access_code = data.get("access_code")

    if not authorization_url or not access_code:
        raise PaystackError(
            "Paystack did not return the expected checkout details."
        )

    return {
        "authorization_url": authorization_url,
        "access_code": access_code,
        "reference": data.get("reference", reference),
    }


def verify_paystack_payment(reference):
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("Paystack is not configured.")

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

    except requests.RequestException as exc:
        raise PaystackError(
            "Could not communicate with Paystack."
        ) from exc

    except ValueError as exc:
        raise PaystackError(
            "Paystack returned an invalid response."
        ) from exc

    if not result.get("status"):
        raise PaystackError(
            result.get("message", "Paystack verification failed.")
        )

    return result.get("data") or {}


class PaystackRefundUncertain(PaystackError):
    """Paystack may have received the refund request, but the result is unknown."""


def initiate_paystack_refund(
    *,
    transaction_reference,
    amount_kobo,
    customer_note,
    merchant_note,
):
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("Paystack is not configured.")

    payload = {
        "transaction": transaction_reference,
        "amount": amount_kobo,
        "customer_note": customer_note,
        "merchant_note": merchant_note,
    }

    try:
        response = requests.post(
            "https://api.paystack.co/refund",
            headers={
                "Authorization": (
                    f"Bearer {settings.PAYSTACK_SECRET_KEY}"
                ),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PaystackRefundUncertain(
            "We could not confirm whether Paystack received "
            "the refund request. Check Paystack before retrying."
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise PaystackRefundUncertain(
            "Paystack returned an unreadable refund response. "
            "Check Paystack before retrying."
        ) from exc

    # A server error may occur after Paystack has accepted a request.
    # Do not blindly retry an outcome that may be uncertain.
    if response.status_code >= 500:
        raise PaystackRefundUncertain(
            "Paystack's refund outcome is uncertain. "
            "Check Paystack before retrying."
        )

    if not response.ok or not data.get("status"):
        raise PaystackError(
            data.get("message", "Paystack could not initiate the refund.")
        )

    refund_data = data.get("data")

    if not isinstance(refund_data, dict):
        raise PaystackRefundUncertain(
            "Paystack's refund response is incomplete. "
            "Check Paystack before retrying."
        )

    refund_id = refund_data.get("id")
    refund_status = refund_data.get("status")

    if refund_id is None or not refund_status:
        raise PaystackRefundUncertain(
            "Paystack did not provide enough refund details. "
            "Check Paystack before retrying."
        )

    return {
        "refund_id": str(refund_id),
        "status": str(refund_status).lower(),
        "amount_kobo": refund_data.get("amount"),
        "currency": refund_data.get("currency"),
    }


def fetch_paystack_refund(refund_id):
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("Paystack is not configured.")

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            f"https://api.paystack.co/refund/{refund_id}",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

    except requests.RequestException as exc:
        raise PaystackError(
            "Could not retrieve the refund status from Paystack."
        ) from exc

    except ValueError as exc:
        raise PaystackError(
            "Paystack returned an invalid refund response."
        ) from exc

    if not result.get("status"):
        raise PaystackError(
            result.get(
                "message",
                "Paystack refund status lookup failed.",
            )
        )

    refund_data = result.get("data") or {}

    if not isinstance(refund_data, dict):
        raise PaystackError(
            "Paystack returned invalid refund details."
        )

    return {
        "refund_id": str(refund_data.get("id", refund_id)),
        "status": str(refund_data.get("status", "")).lower(),
        "amount_kobo": refund_data.get("amount"),
        "currency": refund_data.get("currency"),
    }


def list_paystack_refunds(*, transaction_id):
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("Paystack is not configured.")

    url = "https://api.paystack.co/refund"

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params={"transaction": transaction_id},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PaystackError(
            "Could not retrieve refunds from Paystack. "
            "Check the refund status before taking further action."
        ) from exc

    try:
        result = response.json()
    except ValueError as exc:
        raise PaystackError(
            "Paystack returned an unreadable refund response."
        ) from exc

    if response.status_code >= 500:
        raise PaystackError(
            "Paystack could not retrieve refunds. "
            "Do not retry the refund automatically."
        )

    if not response.ok or not result.get("status"):
        raise PaystackError(
            result.get("message", "Could not retrieve Paystack refunds.")
        )

    refunds = result.get("data")

    if not isinstance(refunds, list):
        raise PaystackError(
            "Paystack returned an unexpected refund-list response."
        )

    return [
        {
            "refund_id": str(refund["id"]),
            "status": str(refund.get("status", "")).lower(),
            "amount_kobo": refund.get("amount"),
            "currency": refund.get("currency"),
            "transaction_id": refund.get("transaction"),
        }
        for refund in refunds
        if isinstance(refund, dict) and refund.get("id") is not None
    ]
