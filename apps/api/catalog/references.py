"""Read-only identity integration, called only after the consuming module authorizes.

This is not a public adapter or an authentication boundary. No ORM object or
commercial metadata leaves Catalog. Existing Catalog application contracts remain
unchanged; callers cannot use this reference lookup to authorize a mutation.
"""

from uuid import UUID

from django.db.models import Q

from .models import Variant


def variant_exists(*, variant_id: UUID) -> bool:
    if not isinstance(variant_id, UUID):
        raise ValueError("variant_id must be a UUID")
    return Variant.objects.using("default").filter(pk=variant_id).exists()


def variant_reference(*, variant_id: UUID) -> dict | None:
    """Read-only identity snapshot for an already authorized consuming command."""
    if not isinstance(variant_id, UUID):
        raise ValueError("variant_id must be a UUID")
    variant = Variant.objects.using("default").select_related("product").filter(pk=variant_id).first()
    if variant is None:
        return None
    return {"id": str(variant.pk), "label": variant.label, "sku": variant.sku,
            "product_name": variant.product.name}


def search_variant_references(*, query: str) -> tuple[dict, ...]:
    """Bounded read-only picker data; the HTTP adapter authorizes separately."""
    if not isinstance(query, str) or len(query) > 120 or not query.isprintable():
        raise ValueError("invalid variant search")
    matches = Variant.objects.using("default").select_related("product").order_by("sku", "id")
    if query.strip():
        term = query.strip()
        matches = matches.filter(Q(sku__icontains=term) | Q(label__icontains=term) | Q(product__name__icontains=term))
    return tuple({"id": str(item.pk), "label": item.label, "sku": item.sku,
                  "product_name": item.product.name} for item in matches[:51])
