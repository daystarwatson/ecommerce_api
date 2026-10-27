from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from .models import (
    Order,
    OrderItem,
    OrderItemAllocationHistory,
    OrderCancellationRequest,
)

from .services import (
    OrderCancellationError,
    initiate_cancellation_refund,
    review_order_cancellation_request,
    verify_and_finalize_cancellation_refund,
    recover_uncertain_cancellation_refund,
)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "customer_username",
        "customer_full_name",
        "status",
        "shipping_address_status",
        "total_amount",
        "created_at",
        "updated_at",
    )

    list_filter = ("status", "created_at")

    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
    )

    ordering = ("-created_at",)

    exclude = ("shipping_address",)
    readonly_fields = ("shipping_address_summary",)

    @admin.display(
        description="Customer username",
        ordering="user__username",
    )
    def customer_username(self, obj):
        return obj.user.username

    @admin.display(description="Customer full name")
    def customer_full_name(self, obj):
        return obj.user.get_full_name() or "(No name entered)"

    @admin.display(description="Delivery address")
    def shipping_address_status(self, obj):
        if not obj.shipping_address:
            return "Missing address"

        return "Address saved"

    @admin.display(description="Saved delivery address")
    def shipping_address_summary(self, obj):
        address = obj.shipping_address

        if not address:
            return format_html(
                '<strong style="color: #ba2121;">{}</strong>',
                "No shipping address saved for this order.",
            )

        fields = (
            ("Address line 1", "line1"),
            ("Address line 2", "line2"),
            ("City", "city"),
            ("State", "state"),
            ("Postal code", "postal_code"),
            ("Country", "country"),
        )

        rows = [
            (label, address[key])
            for label, key in fields
            if address.get(key)
        ]

        if not rows:
            return "An address record exists, but its details are empty."

        return format_html_join(
            "",
            "<strong>{}</strong>: {}<br>",
            rows,
        )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "product_name",
        "quantity",
        "unit_price",
    )

    search_fields = ("order__id", "product_name")


@admin.register(OrderItemAllocationHistory)
class OrderItemAllocationHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "allocation_id_snapshot",
        "order_item_id_snapshot",
        "event_type",
        "previous_inventory_id",
        "new_inventory_id",
        "previous_quantity",
        "new_quantity",
        "performed_by_description",
        "created_at",
    )

    list_filter = ("event_type", "created_at")

    search_fields = (
        "allocation_id_snapshot",
        "order_item_id_snapshot",
        "order_item_description",
        "performed_by_id_snapshot",
        "performed_by_description",
        "reason",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "allocation_id_snapshot",
        "order_item_id_snapshot",
        "order_item_description",
        "order_item",
        "event_type",
        "previous_inventory_id",
        "new_inventory_id",
        "previous_quantity",
        "new_quantity",
        "reason",
        "performed_by_id_snapshot",
        "performed_by_description",
        "performed_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderCancellationRequest)
class OrderCancellationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "requested_by",
        "status",
        "refund_status",
        "refund_reference",
        "refund_amount",
        "created_at",
        "reviewed_by",
    )

    list_filter = (
        "status",
        "refund_status",
        "created_at",
    )

    search_fields = (
        "order__id",
        "requested_by__username",
        "requested_by__email",
        "reason",
        "refund_reference",
    )

    ordering = ("-created_at",)

    readonly_fields = (
        "order",
        "requested_by",
        "reason",
        "status",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "refund_status",
        "refund_reference",
        "refund_amount",
        "refund_updated_at",
        "created_at",
        "updated_at",
    )

    actions = (
        "approve_cancellation_requests",
        "reject_cancellation_requests",
        "initiate_cancellation_refunds",
        "verify_refunds_and_finalize_cancellations",
        "recover_uncertain_cancellation_refunds",
    )

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve selected cancellation requests")
    def approve_cancellation_requests(self, request, queryset):
        self._review_requests(
            request,
            queryset,
            decision="approve",
        )

    @admin.action(description="Reject selected cancellation requests")
    def reject_cancellation_requests(self, request, queryset):
        self._review_requests(
            request,
            queryset,
            decision="reject",
        )


    
    @admin.action(
        description="Recover uncertain Paystack cancellation refunds"
    )
    def recover_uncertain_cancellation_refunds(
        self,
        request,
        queryset,
    ):
        for cancellation_request in queryset:
            try:
                result = recover_uncertain_cancellation_refund(
                    request_id=cancellation_request.pk
                )
            except OrderCancellationError as exc:
                self.message_user(
                    request,
                    f"Request #{cancellation_request.pk}: {exc}",
                    level=messages.ERROR,
                )
            except Exception:
                self.message_user(
                    request,
                    (
                        f"Request #{cancellation_request.pk}: "
                        "Recovery failed unexpectedly. Check Paystack "
                        "before taking further action."
                    ),
                    level=messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    (
                        f"Request #{result.pk}: recovery checked. "
                        f"Refund status: {result.refund_status}."
                    ),
                    level=messages.SUCCESS,
                )


    @admin.action(description="Initiate Paystack refunds for approved cancellations")
    def initiate_cancellation_refunds(self, request, queryset):
     for cancellation_request in queryset:
        try:
            initiate_cancellation_refund(
                request_id=cancellation_request.pk,
                actor=request.user,
            )
        except OrderCancellationError as exc:
            self.message_user(
                request,
                f"Cancellation request #{cancellation_request.pk}: {exc}",
                level=messages.ERROR,
            )
        except Exception:
            self.message_user(
                request,
                (
                    f"Cancellation request #{cancellation_request.pk}: "
                    "An unexpected error occurred. Check the refund status "
                    "before attempting another refund."
                ),
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                (
                    f"Refund initiation processed for cancellation request "
                    f"#{cancellation_request.pk}. Check its saved status."
                ),
                level=messages.SUCCESS,
            )




    @admin.action(
        description="Verify refunds and finalize eligible cancellations"
    )
    def verify_refunds_and_finalize_cancellations(
        self,
        request,
        queryset,
    ):
        for cancellation_request in queryset:
            try:
                result = verify_and_finalize_cancellation_refund(
                    request_id=cancellation_request.pk
                )

                self.message_user(
                    request,
                    (
                        f"Cancellation request #{result.pk}: "
                        f"refund status is {result.refund_status}."
                    ),
                    level=messages.SUCCESS,
                )

            except OrderCancellationError as exc:
                self.message_user(
                    request,
                    (
                        f"Cancellation request "
                        f"#{cancellation_request.pk}: {exc}"
                    ),
                    level=messages.ERROR,
                )

    def _review_requests(self, request, queryset, *, decision):
        reviewed = 0
        failed = 0

        for cancellation_request in queryset:
            try:
                review_order_cancellation_request(
                    request_id=cancellation_request.pk,
                    actor=request.user,
                    decision=decision,
                    review_note="",
                )
                reviewed += 1

            except OrderCancellationError as exc:
                failed += 1
                self.message_user(
                    request,
                    f"Request #{cancellation_request.pk}: {exc}",
                    level=messages.ERROR,
                )

        if reviewed:
            self.message_user(
                request,
                f"{reviewed} cancellation request(s) "
                f"{'approved' if decision == 'approve' else 'rejected'}.",
                level=messages.SUCCESS,
            )

        if failed:
            self.message_user(
                request,
                f"{failed} request(s) could not be reviewed.",
                level=messages.WARNING,
            )
