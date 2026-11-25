"""Typed commercial snapshots for POS; Catalog remains the commercial authority."""
from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Upper, Collate

from identity.application import require_capability
from identity.contracts import Capability
from audit import application as audit_application
from .application import _price_snapshot, CatalogValidationError, VariantNotFoundError
from .contracts import PriceSnapshot
from .models import Product, Variant, VariantMeasurementSpec, VariantPriceRevision


@dataclass(frozen=True)
class CommercialVariant:
    variant_id: UUID
    product_id: UUID
    product_name: str
    label: str
    sku: str
    barcode: str | None
    category_id: UUID | None
    product_active: bool
    variant_active: bool
    sale_unit: str | None
    measurement_version: int | None
    price: PriceSnapshot | None


def _snapshots(ids, *, lock):
    if not isinstance(ids, tuple) or not 1 <= len(ids) <= 30 or any(not isinstance(v, UUID) for v in ids) or len(set(ids)) != len(ids):
        raise CatalogValidationError("Between one and thirty distinct variant UUIDs required")
    product_ids = Variant.objects.filter(pk__in=ids).values_list("product_id", flat=True)
    products = Product.objects.filter(pk__in=product_ids).order_by("id")
    if lock: products = products.select_for_update()
    products = {p.pk: p for p in products}
    variants = Variant.objects.filter(pk__in=ids).order_by("id")
    if lock: variants = variants.select_for_update()
    variants = list(variants)
    if len(variants) != len(ids): raise VariantNotFoundError("Variant not found")
    specs = {s.variant_id:s for s in VariantMeasurementSpec.objects.filter(variant_id__in=ids)}
    prices = {p.variant_id:p for p in VariantPriceRevision.objects.filter(variant_id__in=ids).order_by("variant_id","-sequence").distinct("variant_id")}
    result = []
    for v in variants:
        p = products[v.product_id]
        spec = specs.get(v.pk)
        revision = prices.get(v.pk)
        result.append(CommercialVariant(v.pk, p.pk, p.name, v.label, v.sku, v.barcode, p.category_id,
            p.is_active, v.is_active, spec.sale_unit if spec else None, spec.version if spec else None,
            _price_snapshot(revision) if revision else None))
    return tuple(result)


def quote_variants(*, principal: object, variant_ids: tuple[UUID, ...]) -> tuple[CommercialVariant, ...]:
    require_capability(principal=principal, capability=Capability.POS_QUOTE)
    return _snapshots(variant_ids, lock=True)


def confirmation_variants(*, principal: object, variant_ids: tuple[UUID, ...]) -> tuple[CommercialVariant, ...]:
    require_capability(principal=principal, capability=Capability.POS_CONFIRM)
    return _snapshots(variant_ids, lock=True)


def discover_variants(*, principal: object, query: str = "", after: UUID | None = None, category_id: UUID | None = None) -> tuple[CommercialVariant, ...]:
    require_capability(principal=principal, capability=Capability.POS_DISCOVER)
    if not isinstance(query, str) or len(query) > 255 or (query and not query.isprintable()):
        raise CatalogValidationError("Bounded printable search required")
    if any(v is not None and not isinstance(v, UUID) for v in (after, category_id)):
        raise CatalogValidationError("UUID cursor/category required")
    q = Variant.objects.annotate(pos_label=Collate(Upper("label"),"C"), pos_product=Collate(Upper("product__name"),"C")).order_by("id")
    if query:
        if Variant.objects.filter(barcode=query).exists():
            q = q.filter(barcode=query)  # Exact barcode takes precedence over ambiguous prefix matches.
        else:
            q = q.filter(Q(sku__startswith=query) | Q(pos_label__startswith=query.upper()) | Q(pos_product__startswith=query.upper()))
    if category_id: q = q.filter(product__category_id=category_id)
    if after: q = q.filter(id__gt=after)
    ids = tuple(q.values_list("id", flat=True)[:51])
    # Public page is 50; internal batch snapshot has a strict 30-line command bound.
    return tuple(v for start in range(0,len(ids),30) for v in _snapshots(ids[start:start+30], lock=False))


def set_variant_active(*, principal: object, variant_id: UUID, is_active: bool, expected_active: bool) -> bool:
    actor = require_capability(principal=principal, capability=Capability.SET_VARIANT_ACTIVE)
    if not isinstance(variant_id, UUID) or type(is_active) is not bool or type(expected_active) is not bool:
        raise CatalogValidationError("Explicit variant activation and expectation required")
    from .application import PriceConflictError
    with transaction.atomic():
        v = Variant.objects.select_for_update().filter(pk=variant_id).first()
        if v is None: raise VariantNotFoundError("Variant not found")
        if v.is_active != expected_active: raise PriceConflictError("Variant activation changed")
        if v.is_active != is_active:
            before = v.is_active; v.is_active = is_active; v.save(update_fields=["is_active", "updated_at"])
            audit_application.record_variant_active_changed(actor=actor, variant_id=v.pk, previous_is_active=before, new_is_active=is_active)
    return is_active
