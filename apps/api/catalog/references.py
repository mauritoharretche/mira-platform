"""Read-only identity integration, called only after the consuming module authorizes.

This is not a public adapter or an authentication boundary. No ORM object or
commercial metadata leaves Catalog. Existing Catalog application contracts remain
unchanged; callers cannot use this reference lookup to authorize a mutation.
"""

from uuid import UUID

from .models import Variant


def variant_exists(*, variant_id: UUID) -> bool:
    if not isinstance(variant_id, UUID):
        raise ValueError("variant_id must be a UUID")
    return Variant.objects.using("default").filter(pk=variant_id).exists()
