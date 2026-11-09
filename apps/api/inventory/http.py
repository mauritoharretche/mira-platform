"""Explicit first receipt and authorized administrative reads, without ORM writes."""
from uuid import UUID

from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import application
from .contracts import MAX_STOCK_INTEGER, InventoryUnit, InventoryValidationError, InventoryVariantNotFoundError, StockConflictError


class ReceiptInput(StrictSerializer):
    quantity = StrictTextField(max_length=19, trim_whitespace=False)
    unit = StrictTextField(trim_whitespace=False)
    expected_version = serializers.JSONField(allow_null=True)

    def validate_quantity(self, value):
        if not value.isascii() or not value.isdecimal() or value.startswith("0") or not 1 <= int(value) <= MAX_STOCK_INTEGER:
            raise serializers.ValidationError("Use a positive canonical integer within technical bounds.")
        return int(value)

    def validate_unit(self, value):
        if value != "EACH":
            raise serializers.ValidationError("Explicit EACH is required.")
        return InventoryUnit.EACH

    def validate_expected_version(self, value):
        if value is not None:
            raise serializers.ValidationError("An initial receipt must explicitly expect no prior version.")
        return None


def _variant_id(value):
    try:
        return UUID(value)
    except ValueError:
        raise serializers.ValidationError({"variant_id": ["A UUID is required."]}) from None


def _stock_payload(stock):
    if stock is None:
        return None
    return {"position_id": str(stock.position_id), "variant_id": str(stock.variant_id),
            "unit": stock.unit.value, "on_hand": str(stock.on_hand), "version": str(stock.version),
            "created_at": stock.created_at.isoformat(), "updated_at": stock.updated_at.isoformat()}


class InitialReceiptView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.RECEIVE_STOCK)
        identifier = _variant_id(variant_id)
        serializer = ReceiptInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            stock = application.receive_stock(principal=request.user, variant_id=identifier, **serializer.validated_data)
        except InventoryVariantNotFoundError:
            return error_response("not_found", 404)
        except StockConflictError:
            return error_response("stock_conflict", 409)
        except InventoryValidationError:
            return error_response("validation_error", 400)
        return Response({"stock": _stock_payload(stock)}, status=201)


class StockView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_STOCK)
        identifier = _variant_id(variant_id)
        try:
            stock = application.get_stock(principal=request.user, variant_id=identifier)
        except InventoryVariantNotFoundError:
            return error_response("not_found", 404)
        return Response({"stock": _stock_payload(stock)})


class MovementsView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_STOCK_MOVEMENTS)
        identifier = _variant_id(variant_id)
        if set(request.query_params) - {"before_sequence"} or len(request.query_params.getlist("before_sequence")) > 1:
            raise serializers.ValidationError("Unexpected query parameters.")
        before = request.query_params.get("before_sequence")
        if before is not None:
            if not (before.isascii() and before.isdecimal() and not before.startswith("0") and len(before) <= 19 and int(before) <= MAX_STOCK_INTEGER):
                raise serializers.ValidationError({"before_sequence": ["A canonical positive sequence is required."]})
            before = int(before)
        try:
            movements = application.list_stock_movements(principal=request.user, variant_id=identifier)
        except InventoryVariantNotFoundError:
            return error_response("not_found", 404)
        # The existing contract evaluates the whole history. This bounds the wire
        # response only, not the database query; no scalability claim is made.
        page = [entry for entry in movements if before is None or entry.sequence < before][:51]
        result = [{"movement_id": str(entry.movement_id), "sequence": str(entry.sequence),
                   "kind": entry.kind.value, "unit": entry.unit.value,
                   "quantity_before": str(entry.quantity_before), "delta": str(entry.delta),
                   "quantity_after": str(entry.quantity_after), "reason": entry.reason,
                   "recorded_at": entry.recorded_at.isoformat()} for entry in page[:50]]
        return Response({"movements": result, "next_cursor": str(page[49].sequence) if len(page) > 50 else None})
