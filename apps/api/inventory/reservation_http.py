"""Versioned operator adapters. Authorization always precedes validation."""
from dataclasses import asdict
from uuid import UUID

from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField
from . import reservations
from .contracts import ReservationLine, InventoryValidationError, InventoryUnit
from .quantities import parse_quantity, format_quantity, unit as accounting_unit
from .lot_http import _positive, _respond, _uuid


class ReservationLineInput(StrictSerializer):
    unit = StrictTextField(required=False, default="EACH")
    variant_id = serializers.UUIDField()
    quantity = StrictTextField(max_length=30, trim_whitespace=False)

    def validate(self, attrs):
        try:
            attrs["unit"] = accounting_unit(attrs["unit"])
            attrs["quantity"] = parse_quantity(attrs["quantity"], attrs["unit"], positive=True)
        except InventoryValidationError:
            raise serializers.ValidationError("Exact matching quantity/unit required") from None
        return attrs


class ReservationInput(StrictSerializer):
    operation_id = serializers.UUIDField()
    source = StrictTextField(max_length=40)
    reference = StrictTextField(max_length=120)
    expires_at = serializers.DateTimeField()
    lines = ReservationLineInput(many=True, allow_empty=False, max_length=30)

    def validate_expires_at(self, value):
        raw = self.initial_data.get("expires_at")
        if not isinstance(raw, str) or not (raw.endswith("Z") or "+" in raw[10:] or "-" in raw[10:]):
            raise serializers.ValidationError("An explicit UTC offset is required")
        return value


class OperationInput(StrictSerializer):
    operation_id = serializers.UUIDField()


class ClassificationInput(OperationInput):
    unit = StrictTextField(required=False, default="EACH")
    lot_id = serializers.UUIDField()
    location_id = serializers.UUIDField()
    quantity = StrictTextField(max_length=30, trim_whitespace=False)
    expected_stock_version = StrictTextField(max_length=19, trim_whitespace=False)
    evidence = StrictTextField(max_length=255)

    def validate(self, attrs):
        try:
            attrs["unit"] = accounting_unit(attrs["unit"])
            attrs["quantity"] = parse_quantity(attrs["quantity"], attrs["unit"], positive=True)
        except InventoryValidationError:
            raise serializers.ValidationError("Exact matching quantity/unit required") from None
        return attrs

    def validate_expected_stock_version(self, value):
        return _positive(value)


class EligibilityInput(StrictSerializer):
    eligible = serializers.BooleanField()
    expected_version = StrictTextField(max_length=19, trim_whitespace=False)
    reason = StrictTextField(max_length=255)

    def validate_eligible(self, value):
        if type(self.initial_data.get("eligible")) is not bool:
            raise serializers.ValidationError("JSON boolean required")
        return value

    def validate_expected_version(self, value):
        return _positive(value)


def _payload(snapshot):
    value = asdict(snapshot)
    value["variant_id"] = str(snapshot.variant_id)
    value["as_of"] = snapshot.as_of.isoformat()
    for field in ("physical", "allocated", "unallocated", "eligible", "reserved", "unavailable", "available", "safety_stock"):
        value[field] = format_quantity(value[field], snapshot.unit or InventoryUnit.EACH)
    value["unit"] = snapshot.unit.value if snapshot.unit else None
    for row in value["balances"]:
        for field in ("balance_id", "lot_id", "location_id"):
            row[field] = str(row[field])
        for field in ("physical", "reserved", "available"):
            row[field] = format_quantity(row[field], snapshot.unit or InventoryUnit.EACH)
    return value


class AvailabilityView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_AVAILABILITY)
        if request.query_params:
            raise serializers.ValidationError("Unexpected query parameters")
        result = _respond(lambda: reservations.query_availability(principal=request.user, variant_id=_uuid(variant_id)))
        return result if hasattr(result, "status_code") else Response(_payload(result))


class ReservationsView(OperatorAPIView):
    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_RESERVATION)
        if set(request.query_params) - {"variant_id"} or len(request.query_params.getlist("variant_id")) > 1:
            raise serializers.ValidationError("Unexpected query parameters")
        rows = _respond(lambda: reservations.list_reservations(principal=request.user,
            variant_id=_uuid(request.query_params["variant_id"]) if "variant_id" in request.query_params else None))
        return rows if hasattr(rows, "status_code") else Response({"reservations": rows[:100], "truncated": len(rows) > 100})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.RESERVE_STOCK)
        serializer = ReservationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["lines"] = tuple(ReservationLine(**line) for line in data["lines"])
        result = _respond(lambda: reservations.create_reservation(principal=request.user, **data))
        return result if hasattr(result, "status_code") else Response({"reservation": result}, status=201)


class ReservationView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, reservation_id):
        require_capability(principal=request.user, capability=Capability.READ_RESERVATION)
        result = _respond(lambda: reservations.read_reservation(principal=request.user, reservation_id=_uuid(reservation_id)))
        return result if hasattr(result, "status_code") else Response({"reservation": result})


class ReservationTransitionView(OperatorAPIView):
    http_method_names = ["post", "options"]
    capability = None
    action = None

    def post(self, request, reservation_id):
        require_capability(principal=request.user, capability=self.capability)
        serializer = OperationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: reservations.transition_reservation(principal=request.user,
            reservation_id=_uuid(reservation_id), action=self.action, **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response({"reservation": result})


class ReleaseReservationView(ReservationTransitionView):
    capability, action = Capability.RELEASE_RESERVATION, "release"


class ExpireReservationView(ReservationTransitionView):
    capability, action = Capability.EXPIRE_RESERVATION, "expire"


class ConsumeReservationView(ReservationTransitionView):
    capability, action = Capability.CONSUME_RESERVATION, "consume"


class InventoryOperationView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, operation_id):
        require_capability(principal=request.user, capability=Capability.READ_RESERVATION)
        result = _respond(lambda: reservations.query_operation(principal=request.user, operation_id=_uuid(operation_id)))
        return result if hasattr(result, "status_code") else Response(result)


class ClassificationView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.CLASSIFY_STOCK)
        serializer = ClassificationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: reservations.classify_stock(principal=request.user, **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response(result, status=201)


class LocationEligibilityView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, location_id):
        require_capability(principal=request.user, capability=Capability.SET_LOCATION_ELIGIBILITY)
        serializer = EligibilityInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = _respond(lambda: reservations.set_location_eligibility(principal=request.user,
            location_id=_uuid(location_id), **serializer.validated_data))
        return result if hasattr(result, "status_code") else Response(result)
