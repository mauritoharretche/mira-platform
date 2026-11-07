from uuid import UUID

from django.db import transaction

from audit import application as audit_application
from audit.contracts import CatalogAuditAction
from identity import application as identity_application
from identity.contracts import Capability

from .models import Product, Variant


class CatalogValidationError(ValueError):
    """Raised when a Catalog application command receives invalid input."""


class ProductNotFoundError(LookupError):
    """Raised when a Catalog application command references no Product."""


def _normalize_required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise CatalogValidationError(f"{field_name} must not be empty")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_variant_values(
    *, label: str, sku: str, barcode: str | None
) -> tuple[str, str, str | None]:
    return (
        _normalize_required(label, "variant label"),
        _normalize_required(sku, "variant sku"),
        _normalize_optional(barcode),
    )


def create_product_with_initial_variant(
    *,
    principal: object,
    product_name: str,
    variant_label: str,
    sku: str,
    barcode: str | None = None,
) -> Product:
    """Create and audit atomically; principal must come from trusted authentication.

    A User instance alone does not prove authentication. The caller owns that
    trust boundary. An enclosing transaction can still roll back this operation.
    """
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT
        )
        normalized_product_name = _normalize_required(product_name, "product name")
        normalized_label, normalized_sku, normalized_barcode = _normalize_variant_values(
            label=variant_label, sku=sku, barcode=barcode
        )
        product = Product.objects.create(name=normalized_product_name)
        variant = Variant.objects.create(
            product=product,
            label=normalized_label,
            sku=normalized_sku,
            barcode=normalized_barcode,
        )
        audit_application.record_catalog_success(
            actor=actor,
            action=CatalogAuditAction.PRODUCT_CREATED_WITH_INITIAL_VARIANT,
            product_id=product.id,
            variant_id=variant.id,
        )

    return product


def add_variant_to_product(
    *,
    principal: object,
    product_id: UUID,
    label: str,
    sku: str,
    barcode: str | None = None,
) -> Variant:
    """Authorize a trusted principal and atomically add a variant with its audit."""
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.ADD_VARIANT_TO_PRODUCT
        )
        normalized_label, normalized_sku, normalized_barcode = _normalize_variant_values(
            label=label, sku=sku, barcode=barcode
        )

        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist as error:
            raise ProductNotFoundError(f"product {product_id} does not exist") from error

        variant = Variant.objects.create(
            product=product,
            label=normalized_label,
            sku=normalized_sku,
            barcode=normalized_barcode,
        )
        audit_application.record_catalog_success(
            actor=actor,
            action=CatalogAuditAction.VARIANT_ADDED_TO_PRODUCT,
            product_id=product.id,
            variant_id=variant.id,
        )

    return variant


def get_product(*, principal: object, product_id: UUID) -> Product:
    """Authorize a trusted principal before querying; reads do not create audit rows."""
    identity_application.require_capability(
        principal=principal, capability=Capability.READ_PRODUCT
    )
    try:
        return Product.objects.prefetch_related("variants").get(pk=product_id)
    except Product.DoesNotExist as error:
        raise ProductNotFoundError(f"product {product_id} does not exist") from error
