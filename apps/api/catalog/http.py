"""Narrow HTTP adapters; Catalog application services own all business writes."""
import base64
import binascii
import re
from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import application
from .contracts import CategoryCursor


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
