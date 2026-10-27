
from rest_framework import serializers

from .models import Shipment, AddressCorrectionRequest

from .models import ShipmentEvent

class ShipmentSerializer(serializers.ModelSerializer):

    class Meta:
        model = Shipment
        fields = [
            "id",
            "order",
            "tracking_number",
            "status",
            "shipping_address",
            "carrier",
            "delivery_agent_name",
            "created_at",
            "updated_at",
            "shipped_at",
            "delivered_at",
        ]

        read_only_fields = fields


class AddressCorrectionRequestSerializer(serializers.ModelSerializer):

    class Meta:
        model = AddressCorrectionRequest
        fields = [
            "id",
            "order",
            "original_address",
            "proposed_address",
            "reason",
            "status",
            "requested_at",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "review_due_at",
            
        ]

        read_only_fields = [
            "id",
            "order",
            "original_address",
            "status",
            "requested_at",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "review_due_at",
        ]

    def validate_proposed_address(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "The proposed address must be an object."
            )

        required_fields = [
            "line1",
            "city",
            "state",
            "country",
        ]

        for field in required_fields:
            field_value = value.get(field)

            if not isinstance(field_value, str) or not field_value.strip():
                raise serializers.ValidationError(
                    f"'{field}' is required and must not be empty."
                )

        allowed_fields = {
            "line1",
            "line2",
            "city",
            "state",
            "postal_code",
            "country",
        }

        unexpected_fields = set(value) - allowed_fields

        if unexpected_fields:
            raise serializers.ValidationError(
                "Unsupported address fields: "
                + ", ".join(sorted(unexpected_fields))
            )

        return {
            field: value.get(field, "").strip()
            for field in allowed_fields
        }

    def validate_reason(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Please explain why the address needs correcting."
            )

        return value



    
class CustomerShipmentEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(
        source="get_event_type_display",
        read_only=True,
    )

    class Meta:
        model = ShipmentEvent
        fields = [
            "event_type",
            "event_type_display",
            "description",
            "reason_code",
            "created_at",
        ]
        read_only_fields = fields
