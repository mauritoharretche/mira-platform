from decimal import Context, Decimal, Inexact, InvalidOperation, ROUND_HALF_EVEN, localcontext
from unicodedata import normalize
from uuid import UUID

from django.db import transaction

from audit import application as audit_application
from audit.contracts import CatalogAuditAction
from identity import application as identity_application
from identity.contracts import Capability

from .contracts import PriceBasis, PriceSnapshot
from .models import Category, Product, Variant, VariantPriceRevision


class CatalogValidationError(ValueError):
    """Raised when a Catalog application command receives invalid input."""


class ProductNotFoundError(LookupError):
    """Raised when a Catalog application command references no Product."""


class CategoryNotFoundError(LookupError):
    """Raised when a Catalog application command references no Category."""


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


def create_category(*, principal: object, name: str) -> Category:
    """Create through the trusted boundary; SQL uniqueness does not derive name_key."""
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.CREATE_CATEGORY
        )
        if not isinstance(name, str):
            raise CatalogValidationError("category name must be text")
        display_name = normalize("NFC", name.strip())
        name_key = normalize("NFC", display_name.casefold())
        if not display_name:
            raise CatalogValidationError("category name must not be empty")
        if len(display_name) > 255 or len(name_key) > 255:
            raise CatalogValidationError("category name exceeds technical limits")
        category = Category.objects.create(name=display_name, name_key=name_key)
        audit_application.record_category_created(actor=actor, category_id=category.id)

    return category


def list_categories(*, principal: object) -> list[Category]:
    """Return an internal administrative list, not a public storefront contract."""
    identity_application.require_capability(
        principal=principal, capability=Capability.LIST_CATEGORIES
    )
    return list(Category.objects.order_by("name_key", "id"))


def set_product_category(
    *, principal: object, product_id: UUID, category_id: UUID | None
) -> Product:
    """Serialize category and activation changes on the same Product row."""
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.SET_PRODUCT_CATEGORY
        )
        try:
            product = Product.objects.select_for_update().get(pk=product_id)
        except Product.DoesNotExist as error:
            raise ProductNotFoundError(f"product {product_id} does not exist") from error
        if category_id is not None:
            try:
                category = Category.objects.get(pk=category_id)
            except Category.DoesNotExist as error:
                raise CategoryNotFoundError(f"category {category_id} does not exist") from error
            category_id = category.id
        elif product.is_active:
            raise CatalogValidationError("an active product requires a category")
        if product.category_id == category_id:
            return product

        previous_category_id = product.category_id
        product.category_id = category_id
        product.save(update_fields=["category", "updated_at"])
        audit_application.record_product_category_changed(
            actor=actor, product_id=product.id,
            previous_category_id=previous_category_id, new_category_id=category_id,
        )

    return product


def set_product_active(*, principal: object, product_id: UUID, is_active: bool) -> Product:
    """Set administrative state only; active does not mean published or sellable."""
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.SET_PRODUCT_ACTIVE
        )
        if type(is_active) is not bool:
            raise CatalogValidationError("is_active must be a boolean")
        try:
            product = Product.objects.select_for_update().get(pk=product_id)
        except Product.DoesNotExist as error:
            raise ProductNotFoundError(f"product {product_id} does not exist") from error
        if is_active and product.category_id is None:
            raise CatalogValidationError("an active product requires a category")
        if product.is_active == is_active:
            return product

        previous_is_active = product.is_active
        product.is_active = is_active
        product.save(update_fields=["is_active", "updated_at"])
        audit_application.record_product_active_changed(
            actor=actor, product_id=product.id,
            previous_is_active=previous_is_active, new_is_active=is_active,
        )

    return product


class VariantNotFoundError(LookupError):
    """Raised when a pricing operation references no Variant."""


class PriceConflictError(RuntimeError):
    """The expected revision is stale, including a retry after a prior commit."""


def _normalize_price_amount(amount: Decimal) -> Decimal:
    if (
        not isinstance(amount, Decimal) or not amount.is_finite()
        or not Decimal("0") < amount <= Decimal("999999999999.99")
    ):
        raise CatalogValidationError("price must be a finite positive Decimal within technical bounds")
    try:
        with localcontext(Context(prec=14, rounding=ROUND_HALF_EVEN, traps=[Inexact, InvalidOperation])):
            return amount.quantize(Decimal("0.01"))
    except (Inexact, InvalidOperation) as error:
        raise CatalogValidationError("price must be exactly representable at two decimal places") from error


def _get_price_variant(variant_id: UUID, *, lock: bool = False) -> Variant:
    if not isinstance(variant_id, UUID):
        raise CatalogValidationError("variant_id must be a UUID")
    variants = Variant.objects.using("default")
    if lock:
        variants = variants.select_for_update()
    try:
        return variants.get(pk=variant_id)
    except Variant.DoesNotExist as error:
        raise VariantNotFoundError(f"variant {variant_id} does not exist") from error


def _latest_price_revision(variant_id: UUID) -> VariantPriceRevision | None:
    return VariantPriceRevision.objects.using("default").filter(
        variant_id=variant_id
    ).order_by("-sequence").first()


def _price_snapshot(revision: VariantPriceRevision) -> PriceSnapshot:
    return PriceSnapshot(
        revision_id=revision.id, variant_id=revision.variant_id, sequence=revision.sequence,
        amount=revision.amount, currency=revision.currency,
        price_basis=PriceBasis(revision.price_basis), recorded_at=revision.recorded_at,
    )


def set_variant_price(
    *, principal: object, variant_id: UUID, amount: Decimal, currency: str,
    expected_revision_id: UUID | None,
) -> PriceSnapshot:
    """Append a revision and audit atomically, rejecting stale expectations first.

    Principal provenance belongs to the trusted authentication boundary. Retrying
    an already committed change with its old expectation is a conflict even when
    the amount matches. This is not request-level idempotency or a sales quote.
    An enclosing transaction may still roll back the returned revision.
    """
    with transaction.atomic(using="default"):
        actor = identity_application.require_capability(
            principal=principal, capability=Capability.SET_VARIANT_PRICE
        )
        normalized_amount = _normalize_price_amount(amount)
        if currency != "ARS":
            raise CatalogValidationError("only ARS is supported")
        if expected_revision_id is not None and not isinstance(expected_revision_id, UUID):
            raise CatalogValidationError("expected_revision_id must be a UUID or None")
        variant = _get_price_variant(variant_id, lock=True)
        previous = _latest_price_revision(variant.id)
        current_id = previous.id if previous is not None else None
        if expected_revision_id != current_id:
            raise PriceConflictError("price revision has changed")
        if previous is not None and normalized_amount == previous.amount:
            return _price_snapshot(previous)

        revision = VariantPriceRevision.objects.using("default").create(
            variant=variant, sequence=previous.sequence + 1 if previous is not None else 1,
            amount=normalized_amount, currency="ARS", price_basis=PriceBasis.PER_VARIANT.value,
        )
        audit_application.record_variant_price_set(
            actor=actor, variant_id=variant.id,
            previous_revision_id=current_id, new_revision_id=revision.id,
            previous_amount=previous.amount if previous is not None else None,
            new_amount=revision.amount, currency=revision.currency, price_basis=revision.price_basis,
        )

    return _price_snapshot(revision)


def get_current_variant_price(*, principal: object, variant_id: UUID) -> PriceSnapshot | None:
    """Read the highest visible sequence; absence is distinct from a zero price."""
    identity_application.require_capability(
        principal=principal, capability=Capability.READ_VARIANT_PRICE
    )
    variant = _get_price_variant(variant_id)
    revision = _latest_price_revision(variant.id)
    return _price_snapshot(revision) if revision is not None else None


def list_variant_price_history(*, principal: object, variant_id: UUID) -> tuple[PriceSnapshot, ...]:
    """Return evaluated immutable revisions; recording times are not validity intervals."""
    identity_application.require_capability(
        principal=principal, capability=Capability.READ_VARIANT_PRICE_HISTORY
    )
    variant = _get_price_variant(variant_id)
    revisions = VariantPriceRevision.objects.using("default").filter(
        variant_id=variant.id
    ).order_by("-sequence")
    return tuple(_price_snapshot(revision) for revision in revisions)
