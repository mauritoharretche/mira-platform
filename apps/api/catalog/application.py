from uuid import UUID

from django.db import transaction

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
    *, product_name: str, variant_label: str, sku: str, barcode: str | None = None
) -> Product:
    normalized_product_name = _normalize_required(product_name, "product name")
    normalized_label, normalized_sku, normalized_barcode = _normalize_variant_values(
        label=variant_label, sku=sku, barcode=barcode
    )

    with transaction.atomic():
        product = Product.objects.create(name=normalized_product_name)
        Variant.objects.create(
            product=product,
            label=normalized_label,
            sku=normalized_sku,
            barcode=normalized_barcode,
        )

    return product


def add_variant_to_product(
    *, product_id: UUID, label: str, sku: str, barcode: str | None = None
) -> Variant:
    normalized_label, normalized_sku, normalized_barcode = _normalize_variant_values(
        label=label, sku=sku, barcode=barcode
    )

    try:
        product = Product.objects.get(pk=product_id)
    except Product.DoesNotExist as error:
        raise ProductNotFoundError(f"product {product_id} does not exist") from error

    with transaction.atomic():
        return Variant.objects.create(
            product=product,
            label=normalized_label,
            sku=normalized_sku,
            barcode=normalized_barcode,
        )


def get_product(*, product_id: UUID) -> Product:
    try:
        return Product.objects.prefetch_related("variants").get(pk=product_id)
    except Product.DoesNotExist as error:
        raise ProductNotFoundError(f"product {product_id} does not exist") from error
