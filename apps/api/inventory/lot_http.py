"""Closed operator surface for physical stock attribution."""

from uuid import UUID

from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import lot_application
from .contracts import InventoryValidationError, InventoryVariantNotFoundError, StockConflictError, StockNotInitializedError


def _uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise serializers.ValidationError({"id": ["UUID required"]}) from None


def _positive(value):
    if not value.isascii() or not value.isdecimal() or value.startswith("0") or not 1 <= int(value) <= 2**63 - 1:
        raise serializers.ValidationError("Canonical positive integer required")
    return int(value)


def _nonzero(value):
    if not value.isascii() or not value or value[0] == "+" or value in {"0", "-0"}:
        raise serializers.ValidationError("Canonical nonzero signed integer required")
    digits = value[1:] if value.startswith("-") else value
    if not digits.isdecimal() or not digits.isascii() or digits.startswith("0") or abs(int(value)) > 2**63 - 1:
        raise serializers.ValidationError("Canonical nonzero signed integer required")
    return int(value)


def _respond(call):
    try:
        return call()
    except InventoryVariantNotFoundError:
        return error_response("not_found", 404)
    except (StockConflictError, StockNotInitializedError):
        return error_response("lot_conflict", 409)
    except InventoryValidationError:
        return error_response("validation_error", 400)


class LocationInput(StrictSerializer):
    code = StrictTextField(max_length=40)
    name = StrictTextField(max_length=120)


class LotInput(StrictSerializer):
    code = StrictTextField(max_length=80)
    expires_on = serializers.DateField(allow_null=True)


class StatusInput(StrictSerializer):
    status = StrictTextField(max_length=16)
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)
    reason = StrictTextField(max_length=255)

    def validate_expected_version(self, value):
        return _positive(value)


class TransferInput(StrictSerializer):
    source_id = serializers.UUIDField()
    destination_id = serializers.UUIDField()
    quantity = StrictTextField(max_length=19, trim_whitespace=False)
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)
    reason = StrictTextField(max_length=255)

    def validate_quantity(self, value):
        return _positive(value)

    def validate_expected_version(self, value):
        return _positive(value)


class CorrectionInput(StrictSerializer):
    location_id = serializers.UUIDField()
    delta = StrictTextField(max_length=20, trim_whitespace=False)
    expected_lot_version = StrictTextField(max_length=19, trim_whitespace=False)
    expected_stock_version = StrictTextField(max_length=19, trim_whitespace=False)
    reason = StrictTextField(max_length=255)

    def validate_delta(self, value):
        return _nonzero(value)

    def validate_expected_lot_version(self, value):
        return _positive(value)

    def validate_expected_stock_version(self, value):
        return _positive(value)


class LocationsView(OperatorAPIView):
    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_STOCK_LOTS)
        if request.query_params:
            raise serializers.ValidationError("Unexpected query parameters")
        locations = lot_application.list_locations(principal=request.user)
        return Response({"locations": [{"id": str(item.pk), "code": item.code, "name": item.name,
                                         "active": item.active} for item in locations[:100]],
                         "truncated": len(locations) > 100})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.MANAGE_STOCK_LOCATIONS)
        serializer = LocationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: lot_application.create_location(principal=request.user, **serializer.validated_data))
        if hasattr(result, "status_code"):
            return result
        return Response({"location": {"id": str(result.pk), "code": result.code, "name": result.name}}, status=201)


class LotsView(OperatorAPIView):
    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_STOCK_LOTS)
        if request.query_params:
            raise serializers.ValidationError("Unexpected query parameters")
        result = _respond(lambda: lot_application.list_lots(principal=request.user, variant_id=_uuid(variant_id)))
        return result if hasattr(result, "status_code") else Response(result)

    def post(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.MANAGE_STOCK_LOTS)
        identifier = _uuid(variant_id)
        serializer = LotInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: lot_application.create_lot(principal=request.user, variant_id=identifier,
                                                               **serializer.validated_data))
        if hasattr(result, "status_code"):
            return result
        return Response({"lot": {"id": str(result.pk), "code": result.code, "version": str(result.version)}}, status=201)


class LotStatusView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, lot_id):
        require_capability(principal=request.user, capability=Capability.MANAGE_STOCK_LOTS)
        serializer = StatusInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: lot_application.set_lot_status(principal=request.user, lot_id=_uuid(lot_id),
                                                                  **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response({"lot_id": str(result.pk), "version": str(result.version), "status": result.status})


class LotTransferView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, lot_id):
        require_capability(principal=request.user, capability=Capability.TRANSFER_STOCK_LOTS)
        serializer = TransferInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: lot_application.transfer_lot(principal=request.user, lot_id=_uuid(lot_id),
                                                                 **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response({"lot_id": str(result.pk), "version": str(result.version)})


class LotCorrectionView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, lot_id):
        require_capability(principal=request.user, capability=Capability.CORRECT_STOCK_LOTS)
        serializer = CorrectionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: lot_application.correct_lot(principal=request.user, lot_id=_uuid(lot_id),
                                                                **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response({"lot_id": str(result.pk), "version": str(result.version)})
