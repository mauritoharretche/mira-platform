"""Versioned operator adapters; every route authorizes before parsing identifiers or payloads."""

import re
from decimal import Decimal
from uuid import UUID

from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability, AccessDenied, AuthenticationRequired
from inventory.contracts import InventoryValidationError, InventoryVariantNotFoundError
from inventory.quantities import parse_quantity
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import application
from .contracts import ProcurementConflictError, ProcurementNotFoundError, ProcurementValidationError


_POSITIVE = re.compile(r"[1-9][0-9]{0,18}\Z")
_NONNEGATIVE = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_COST = re.compile(r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}\Z")
_MAX = 2**63 - 1


def _uuid(value):
    try:
        parsed = UUID(value)
        if str(parsed) != value.lower():
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise serializers.ValidationError({"id": ["A UUID is required."]}) from None


def _positive(value):
    if not _POSITIVE.fullmatch(value) or int(value) > _MAX:
        raise serializers.ValidationError("A canonical positive integer is required.")
    return int(value)


def _nonnegative(value):
    if not _NONNEGATIVE.fullmatch(value) or int(value) > _MAX:
        raise serializers.ValidationError("A canonical nonnegative integer is required.")
    return int(value)


class SupplierCreateInput(StrictSerializer):
    name = StrictTextField(max_length=120, trim_whitespace=False)


class SupplierUpdateInput(SupplierCreateInput):
    active = serializers.BooleanField()
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)

    def validate_expected_version(self, value):
        return _positive(value)

    def validate_active(self, value):
        if type(self.initial_data.get("active")) is not bool:
            raise serializers.ValidationError("A JSON boolean is required.")
        return value


class OrderLineInput(StrictSerializer):
    variant_id = serializers.UUIDField()
    quantity = StrictTextField(max_length=30, trim_whitespace=False)
    unit = StrictTextField(max_length=8, trim_whitespace=False)
    unit_cost_ars = StrictTextField(max_length=15, trim_whitespace=False)

    cost_basis = StrictTextField(required=False, default="PER_VARIANT")

    def validate(self, attrs):
        try: attrs["quantity"] = parse_quantity(attrs["quantity"], attrs["unit"], positive=True)
        except InventoryValidationError: raise serializers.ValidationError("Exact quantity/unit required") from None
        return attrs

    def validate_unit_cost_ars(self, value):
        if not _COST.fullmatch(value):
            raise serializers.ValidationError("Canonical ARS amount with two decimals required.")
        return Decimal(value)


class OrderCreateInput(StrictSerializer):
    supplier_id = serializers.UUIDField()
    lines = OrderLineInput(many=True, allow_empty=False)


class OrderUpdateInput(OrderCreateInput):
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)

    def validate_expected_version(self, value):
        return _positive(value)


class ConfirmInput(StrictSerializer):
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)

    def validate_expected_version(self, value):
        return _positive(value)


class ReceiptAllocationInput(StrictSerializer):
    lot_id = serializers.UUIDField()
    location_id = serializers.UUIDField()
    quantity = StrictTextField(max_length=30, trim_whitespace=False)

    def validate_quantity(self, value):
        return value


class ReceiptLineInput(StrictSerializer):
    unit = StrictTextField(required=False, default="EACH")
    order_line_id = serializers.UUIDField()
    delivered_quantity = StrictTextField(max_length=30, trim_whitespace=False)
    accepted_quantity = StrictTextField(max_length=30, trim_whitespace=False)
    difference_reason = StrictTextField(max_length=255, allow_null=True, trim_whitespace=False)
    expected_stock_version = StrictTextField(max_length=19, allow_null=True, trim_whitespace=False)
    allocations = ReceiptAllocationInput(many=True, required=False, allow_empty=False)

    def validate(self, attrs):
        try:
            attrs["delivered_quantity"] = parse_quantity(attrs["delivered_quantity"], attrs["unit"], positive=True)
            attrs["accepted_quantity"] = parse_quantity(attrs["accepted_quantity"], attrs["unit"])
            for item in attrs.get("allocations", []):
                item["quantity"] = parse_quantity(item["quantity"], attrs["unit"], positive=True)
        except InventoryValidationError:
            raise serializers.ValidationError("Exact matching receipt quantities required") from None
        return attrs

    def validate_expected_stock_version(self, value):
        return _positive(value) if value is not None else None


class ReceiptInput(StrictSerializer):
    operation_id = serializers.UUIDField()
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)
    lines = ReceiptLineInput(many=True, allow_empty=False)

    def validate_expected_version(self, value):
        return _positive(value)


def _respond(call):
    try:
        return call()
    except ProcurementNotFoundError:
        return error_response("not_found", 404)
    except (ProcurementConflictError,):
        return error_response("procurement_conflict", 409)
    except (ProcurementValidationError, InventoryValidationError):
        return error_response("validation_error", 400)
    except InventoryVariantNotFoundError:
        return error_response("not_found", 404)


def _can_read_lots(request):
    try:
        require_capability(principal=request.user, capability=Capability.READ_STOCK_LOTS)
        return True
    except (AccessDenied, AuthenticationRequired):
        return False


def _order_detail(order, *, include_allocations=False):
    payload = application.order_snapshot(order)
    payload["history"] = [{"action": event.action, "version": str(event.version),
                           "actor_id": event.actor_id, "occurred_at": event.occurred_at.isoformat(),
                           "snapshot": event.snapshot, "receipt_id": str(event.receipt_id) if event.receipt_id else None}
                          for event in order.events.order_by("version")]
    payload["receipts"] = [application.receipt_snapshot(receipt, include_allocations=include_allocations)
                           for receipt in order.receipts.order_by("recorded_at", "id")]
    return payload


class SuppliersView(OperatorAPIView):
    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_SUPPLIER)
        if set(request.query_params) - {"search"} or len(request.query_params.getlist("search")) > 1:
            raise serializers.ValidationError("Unexpected search parameters.")
        suppliers = _respond(lambda: application.list_suppliers(principal=request.user,
                                                                 search=request.query_params.get("search", "")))
        if isinstance(suppliers, Response) or hasattr(suppliers, "status_code"):
            return suppliers
        return Response({"suppliers": [application.supplier_snapshot(item) for item in suppliers[:50]],
                         "truncated": len(suppliers) > 50})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.MANAGE_SUPPLIER)
        serializer = SupplierCreateInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        supplier = _respond(lambda: application.create_supplier(principal=request.user, **serializer.validated_data))
        if hasattr(supplier, "status_code"):
            return supplier
        return Response({"supplier": application.supplier_snapshot(supplier)}, status=201)


class SupplierView(OperatorAPIView):
    def get(self, request, supplier_id):
        require_capability(principal=request.user, capability=Capability.READ_SUPPLIER)
        supplier = _respond(lambda: application.get_supplier(principal=request.user, supplier_id=_uuid(supplier_id)))
        if hasattr(supplier, "status_code"):
            return supplier
        return Response({"supplier": application.supplier_snapshot(supplier)})

    def post(self, request, supplier_id):
        require_capability(principal=request.user, capability=Capability.MANAGE_SUPPLIER)
        identifier = _uuid(supplier_id)
        serializer = SupplierUpdateInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        supplier = _respond(lambda: application.update_supplier(principal=request.user, supplier_id=identifier,
                                                                 **serializer.validated_data))
        if hasattr(supplier, "status_code"):
            return supplier
        return Response({"supplier": application.supplier_snapshot(supplier)})


class OrdersView(OperatorAPIView):
    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_PURCHASE_ORDER)
        if request.query_params:
            raise serializers.ValidationError("Unexpected query parameters.")
        orders = application.list_orders(principal=request.user)
        return Response({"orders": [application.order_snapshot(item) for item in orders[:50]],
                         "truncated": len(orders) > 50})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.CREATE_PURCHASE_ORDER)
        serializer = OrderCreateInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = _respond(lambda: application.create_order(principal=request.user, **serializer.validated_data))
        if hasattr(order, "status_code"):
            return order
        return Response({"order": _order_detail(order, include_allocations=_can_read_lots(request))}, status=201)


class OrderView(OperatorAPIView):
    def get(self, request, order_id):
        require_capability(principal=request.user, capability=Capability.READ_PURCHASE_ORDER)
        order = _respond(lambda: application.get_order(principal=request.user, order_id=_uuid(order_id)))
        if hasattr(order, "status_code"):
            return order
        return Response({"order": _order_detail(order, include_allocations=_can_read_lots(request))})

    def post(self, request, order_id):
        require_capability(principal=request.user, capability=Capability.EDIT_PURCHASE_ORDER)
        identifier = _uuid(order_id)
        serializer = OrderUpdateInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = _respond(lambda: application.update_order(principal=request.user, order_id=identifier,
                                                           **serializer.validated_data))
        if hasattr(order, "status_code"):
            return order
        return Response({"order": _order_detail(order, include_allocations=_can_read_lots(request))})


class ConfirmOrderView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, order_id):
        require_capability(principal=request.user, capability=Capability.CONFIRM_PURCHASE_ORDER)
        identifier = _uuid(order_id)
        serializer = ConfirmInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = _respond(lambda: application.confirm_order(principal=request.user, order_id=identifier,
                                                            **serializer.validated_data))
        if hasattr(order, "status_code"):
            return order
        return Response({"order": _order_detail(order, include_allocations=_can_read_lots(request))})


class ReceiveOrderView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, order_id):
        require_capability(principal=request.user, capability=Capability.RECEIVE_PURCHASE_ORDER)
        identifier = _uuid(order_id)
        serializer = ReceiptInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: application.receive_order(principal=request.user, order_id=identifier,
                                                              **serializer.validated_data))
        if hasattr(result, "status_code"):
            return result
        order, receipt = result
        include_allocations = _can_read_lots(request)
        return Response({"order": _order_detail(order, include_allocations=include_allocations),
                         "receipt": application.receipt_snapshot(receipt, include_allocations=include_allocations)}, status=201)


class ReceiptOperationView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, operation_id):
        require_capability(principal=request.user, capability=Capability.READ_PURCHASE_ORDER)
        receipt = _respond(lambda: application.get_receipt_by_operation(principal=request.user,
                                                                         operation_id=_uuid(operation_id)))
        if hasattr(receipt, "status_code"):
            return receipt
        return Response({"receipt": application.receipt_snapshot(receipt, include_allocations=_can_read_lots(request))})
