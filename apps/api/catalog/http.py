"""Narrow HTTP adapters; Catalog application services own all business writes."""
from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response

from . import application


class ProductInput(StrictSerializer):
    product_name = StrictTextField(max_length=255)
    variant_label = StrictTextField(max_length=255)
    sku = StrictTextField(max_length=128)
    barcode = StrictTextField(max_length=255, allow_null=True, allow_blank=True, required=False, default=None)


def _product_payload(product):
    return {"id": str(product.pk), "name": product.name, "is_active": product.is_active,
            "variants": [{"id": str(variant.pk), "label": variant.label,
                          "sku": variant.sku, "barcode": variant.barcode}
                         for variant in product.variants.all().order_by("id")]}


class ProductCreateView(OperatorAPIView):
    http_method_names = ["post", "options"]

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
