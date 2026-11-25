"""Narrow HTTP adapters; Catalog application services own all business writes."""
import base64
import binascii
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import application
from . import references
from .contracts import CategoryCursor, MeasurementUnit


class ProductInput(StrictSerializer):
    product_name = StrictTextField(max_length=255)
    variant_label = StrictTextField(max_length=255)
    sku = StrictTextField(max_length=128)
    barcode = StrictTextField(max_length=255, allow_null=True, allow_blank=True, required=False, default=None)


def _product_payload(product):
    return {"id": str(product.pk), "name": product.name, "is_active": product.is_active,
            "category_id": str(product.category_id) if product.category_id else None,
            "variants": [{"id": str(variant.pk), "label": variant.label,
                          "sku": variant.sku, "barcode": variant.barcode}
                         for variant in product.variants.all().order_by("id")]}


class ProductCreateView(OperatorAPIView):
    http_method_names = ["get", "post", "options"]

    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_PRODUCT)
        _query(request, {"category", "after"})
        category = request.query_params.get("category", "all")
        category_id = None if category in {"all", "uncategorized"} else _uuid(category, "category")
        after_id = _uuid(request.query_params["after"], "after") if "after" in request.query_params else None
        page = application.list_products(
            principal=request.user, category_id=category_id,
            only_uncategorized=category == "uncategorized", after_id=after_id,
        )
        return Response({"products": [{"id": str(item.id), "name": item.name,
                                      "category_id": str(item.category_id) if item.category_id else None,
                                      "is_active": item.is_active} for item in page.items],
                         "next_cursor": str(page.next_after_id) if page.next_after_id else None})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT)
        serializer = ProductInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic(using="default"):
                product = application.create_product_with_initial_variant(principal=request.user, **serializer.validated_data)
                payload = _product_payload(product)
        except IntegrityError as error:
            name = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
            if name in {"catalog_variant_sku_unique", "catalog_variant_barcode_unique"}:
                return error_response("identifier_conflict", 409)
            raise
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        return Response(payload, status=201)


class ProductDetailView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, product_id):
        require_capability(principal=request.user, capability=Capability.READ_PRODUCT)
        try:
            identifier = UUID(product_id)
        except ValueError:
            raise serializers.ValidationError({"product_id": ["A UUID is required."]}) from None
        try:
            product = application.get_product(principal=request.user, product_id=identifier)
        except application.ProductNotFoundError:
            return error_response("not_found", 404)
        return Response(_product_payload(product))


class VariantReferenceSearchView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request):
        require_capability(principal=request.user, capability=Capability.READ_PRODUCT)
        _query(request, {"q"})
        query = request.query_params.get("q", "")
        if len(query) > 120 or not query.isprintable():
            raise serializers.ValidationError({"q": ["Invalid search text."]})
        matches = references.search_variant_references(query=query)
        return Response({"variants": matches[:50], "truncated": len(matches) > 50})


def _query(request, allowed):
    if set(request.query_params) - allowed or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise serializers.ValidationError({"query": ["Unsupported or repeated query parameter."]})


def _uuid(value, field):
    try:
        parsed = UUID(value)
        if str(parsed) != value.lower():
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise serializers.ValidationError({field: ["A canonical UUID is required."]}) from None


def _encode_cursor(cursor):
    if cursor is None:
        return None
    return base64.urlsafe_b64encode(f"{cursor.id}:{cursor.name_key}".encode()).decode().rstrip("=")


def _decode_cursor(value):
    try:
        if not 1 <= len(value) <= 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True).decode("utf-8")
        identifier, separator, key = raw.partition(":")
        if not separator or not key:
            raise ValueError
        cursor = CategoryCursor(id=_uuid(identifier, "after"), name_key=key)
        if _encode_cursor(cursor) != value:
            raise ValueError
        return cursor
    except (ValueError, binascii.Error, UnicodeError):
        raise serializers.ValidationError({"after": ["Invalid category cursor."]}) from None


class CategoryInput(StrictSerializer):
    name = StrictTextField(max_length=255)


class StrictUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return _uuid(data, "category_id")


class CategoryAssignmentInput(StrictSerializer):
    category_id = StrictUUIDField(allow_null=True)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid")
        return data


class ActivationInput(StrictSerializer):
    is_active = StrictBooleanField()


class CategoriesView(OperatorAPIView):
    http_method_names = ["get", "post", "options"]

    def get(self, request):
        require_capability(principal=request.user, capability=Capability.LIST_CATEGORIES)
        _query(request, {"after"})
        after = _decode_cursor(request.query_params["after"]) if "after" in request.query_params else None
        try:
            page = application.list_category_page(principal=request.user, after=after)
        except application.CatalogValidationError:
            raise serializers.ValidationError({"after": ["Invalid category cursor."]}) from None
        return Response({"categories": [{"id": str(item.id), "name": item.name} for item in page.items],
                         "next_cursor": _encode_cursor(page.next_cursor)})

    def post(self, request):
        require_capability(principal=request.user, capability=Capability.CREATE_CATEGORY)
        _query(request, set())
        serializer = CategoryInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            category = application.create_category(principal=request.user, **serializer.validated_data)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        except IntegrityError as error:
            if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) == "catalog_category_name_key_unique":
                return error_response("category_conflict", 409)
            raise
        return Response({"id": str(category.id), "name": category.name}, status=201)


class ProductCategoryView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, product_id):
        require_capability(principal=request.user, capability=Capability.SET_PRODUCT_CATEGORY)
        _query(request, set())
        identifier = _uuid(product_id, "product_id")
        serializer = CategoryAssignmentInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            product = application.set_product_category(principal=request.user, product_id=identifier, **serializer.validated_data)
        except (application.ProductNotFoundError, application.CategoryNotFoundError):
            return error_response("not_found", 404)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        return Response({"id": str(product.id), "category_id": str(product.category_id) if product.category_id else None})


class ProductActiveView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, product_id):
        require_capability(principal=request.user, capability=Capability.SET_PRODUCT_ACTIVE)
        _query(request, set())
        identifier = _uuid(product_id, "product_id")
        serializer = ActivationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            product = application.set_product_active(principal=request.user, product_id=identifier, **serializer.validated_data)
        except application.ProductNotFoundError:
            return error_response("not_found", 404)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        return Response({"id": str(product.id), "is_active": product.is_active})


class VariantInput(StrictSerializer):
    label = StrictTextField(max_length=255)
    sku = StrictTextField(max_length=128)
    barcode = StrictTextField(max_length=255, allow_null=True, allow_blank=True, required=False, default=None)


class ProductVariantsView(OperatorAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, product_id):
        require_capability(principal=request.user, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        _query(request, set())
        identifier = _uuid(product_id, "product_id")
        serializer = VariantInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            variant = application.add_variant_to_product(
                principal=request.user, product_id=identifier, **serializer.validated_data,
            )
        except application.ProductNotFoundError:
            return error_response("not_found", 404)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        except IntegrityError as error:
            name = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
            if name in {"catalog_variant_sku_unique", "catalog_variant_barcode_unique"}:
                return error_response("identifier_conflict", 409)
            raise
        return Response({"id": str(variant.pk), "label": variant.label,
                         "sku": variant.sku, "barcode": variant.barcode}, status=201)


def _price_payload(price):
    if price is None:
        return None
    return {"revision_id": str(price.revision_id), "variant_id": str(price.variant_id),
            "sequence": str(price.sequence), "amount": format(price.amount, ".2f"),
            "currency": price.currency, "price_basis": price.price_basis.value,
            "recorded_at": price.recorded_at.isoformat()}


class PriceInput(StrictSerializer):
    price_basis = StrictTextField(required=False, default="PER_VARIANT")

    def validate_price_basis(self, value):
        from .contracts import PriceBasis
        try: return PriceBasis(value)
        except ValueError: raise serializers.ValidationError("PER_VARIANT or PER_KG required") from None

    amount = StrictTextField(max_length=64, trim_whitespace=False)
    currency = StrictTextField(max_length=3, trim_whitespace=False)
    expected_revision_id = StrictTextField(max_length=36, trim_whitespace=False, allow_null=True)

    def validate_amount(self, value):
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]+)?", value, flags=re.ASCII):
            raise serializers.ValidationError("A plain decimal amount is required.")
        try:
            return Decimal(value)
        except InvalidOperation:
            raise serializers.ValidationError("Invalid decimal amount.") from None

    def validate_currency(self, value):
        if value != "ARS":
            raise serializers.ValidationError("Only ARS is supported.")
        return value

    def validate_expected_revision_id(self, value):
        return _uuid(value, "expected_revision_id") if value is not None else None


class VariantPriceView(OperatorAPIView):
    http_method_names = ["get", "post", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_VARIANT_PRICE)
        _query(request, set())
        identifier = _uuid(variant_id, "variant_id")
        try:
            price = application.get_current_variant_price(principal=request.user, variant_id=identifier)
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        return Response({"price": _price_payload(price)})

    def post(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.SET_VARIANT_PRICE)
        _query(request, set())
        identifier = _uuid(variant_id, "variant_id")
        serializer = PriceInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            price = application.set_variant_price(
                principal=request.user, variant_id=identifier, **serializer.validated_data,
            )
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        except application.PriceConflictError:
            return error_response("price_conflict", 409)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        return Response({"price": _price_payload(price)})


class VariantPriceHistoryView(OperatorAPIView):
    http_method_names = ["get", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_VARIANT_PRICE_HISTORY)
        _query(request, {"before_sequence"})
        identifier = _uuid(variant_id, "variant_id")
        before = request.query_params.get("before_sequence")
        if before is not None:
            if not re.fullmatch(r"[1-9][0-9]{0,18}", before, flags=re.ASCII) or int(before) > 9223372036854775807:
                raise serializers.ValidationError({"before_sequence": ["A canonical positive sequence is required."]})
            before = int(before)
        try:
            page = application.list_variant_price_history_page(
                principal=request.user, variant_id=identifier, before_sequence=before,
            )
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        return Response({"revisions": [_price_payload(price) for price in page.items],
                         "next_cursor": str(page.next_before_sequence) if page.next_before_sequence else None})


def _measurement_payload(spec):
    if spec is None:
        return None
    return {"spec_id": str(spec.spec_id), "variant_id": str(spec.variant_id),
            "version": str(spec.version),
            "sale_unit": spec.sale_unit.value if spec.sale_unit is not None else None,
            "net_content_amount": format(spec.net_content_amount, ".6f") if spec.net_content_amount is not None else None,
            "net_content_unit": spec.net_content_unit.value if spec.net_content_unit is not None else None,
            "created_at": spec.created_at.isoformat(), "updated_at": spec.updated_at.isoformat()}


class MeasurementUnitField(serializers.Field):
    def __init__(self, *, mass_only=False, **kwargs):
        self.mass_only = mass_only
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        allowed = {"G", "KG"} if self.mass_only else {"EACH", "G", "KG"}
        if data is None and self.allow_null:
            return None
        if type(data) is not str or data not in allowed:
            raise serializers.ValidationError("A supported measurement unit is required.")
        return MeasurementUnit(data)


class MeasurementInput(StrictSerializer):
    sale_unit = MeasurementUnitField(allow_null=True)
    net_content_amount = StrictTextField(allow_null=True, trim_whitespace=False)
    net_content_unit = MeasurementUnitField(allow_null=True, mass_only=True)
    expected_version = StrictTextField(allow_null=True, trim_whitespace=False)

    def validate_net_content_amount(self, value):
        if value is None:
            return None
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value, flags=re.ASCII):
            raise serializers.ValidationError("A plain decimal amount is required.")
        try:
            return Decimal(value)
        except InvalidOperation:
            raise serializers.ValidationError("Invalid decimal amount.") from None

    def validate_expected_version(self, value):
        if value is None:
            return None
        if not re.fullmatch(r"[1-9][0-9]{0,18}", value, flags=re.ASCII) or int(value) > 9223372036854775807:
            raise serializers.ValidationError("A canonical positive version is required.")
        return int(value)


class VariantMeasurementSpecView(OperatorAPIView):
    http_method_names = ["get", "post", "options"]

    def get(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.READ_VARIANT_MEASUREMENT_SPEC)
        _query(request, set())
        identifier = _uuid(variant_id, "variant_id")
        try:
            spec = application.get_variant_measurement_spec(principal=request.user, variant_id=identifier)
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        return Response({"spec": _measurement_payload(spec)})

    def post(self, request, variant_id):
        require_capability(principal=request.user, capability=Capability.SET_VARIANT_MEASUREMENT_SPEC)
        _query(request, set())
        identifier = _uuid(variant_id, "variant_id")
        serializer = MeasurementInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            spec = application.set_variant_measurement_spec(
                principal=request.user, variant_id=identifier, **serializer.validated_data,
            )
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        except application.MeasurementSpecConflictError:
            return error_response("measurement_conflict", 409)
        except application.CatalogValidationError:
            return error_response("validation_error", 400)
        return Response({"spec": _measurement_payload(spec)})


class VariantActiveView(OperatorAPIView):
    def post(self, request, variant_id):
        from .sales import set_variant_active
        require_capability(principal=request.user, capability=Capability.SET_VARIANT_ACTIVE)
        from rest_framework import serializers
        data = request.data
        if not isinstance(data, dict) or set(data) != {"is_active", "expected_active"} or any(type(data[k]) is not bool for k in data):
            raise serializers.ValidationError("Explicit boolean activation and expectation required")
        try:
            identifier = UUID(variant_id)
            if str(identifier) != variant_id.lower(): raise ValueError
        except (ValueError, TypeError):
            raise serializers.ValidationError("UUID required") from None
        try:
            active = set_variant_active(principal=request.user, variant_id=identifier, **data)
        except application.VariantNotFoundError:
            return error_response("not_found", 404)
        except application.PriceConflictError:
            return error_response("variant_conflict", 409)
        return Response({"variant_id": str(identifier), "is_active": active})
