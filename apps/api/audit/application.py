from uuid import UUID

from django.db import transaction
from django.db.transaction import TransactionManagementError

from identity.contracts import ActorContext

from .contracts import CatalogAuditAction
from .models import CatalogAuditEntry


def record_catalog_success(
    *,
    actor: ActorContext,
    action: CatalogAuditAction,
    product_id: UUID,
    variant_id: UUID,
) -> UUID:
    """Append to the caller's default transaction, without authenticating the actor.

    Only a trusted, already authorized Catalog operation may supply this context.
    ActorContext is attribution, not a credential. The caller owns commit/rollback;
    this interface does not make the underlying table tamper-proof.
    """
    if not transaction.get_connection("default").in_atomic_block:
        raise TransactionManagementError("Audit requires an active transaction")
    if not isinstance(actor, ActorContext):
        raise TypeError("ActorContext required")
    if not isinstance(actor.actor_id, str) or not actor.actor_id.strip():
        raise ValueError("Invalid actor identifier")
    if not isinstance(action, CatalogAuditAction):
        raise ValueError("Unknown audit action")

    entry = CatalogAuditEntry.objects.using("default").create(
        actor_id=actor.actor_id,
        action=action.value,
        product_id=product_id,
        variant_id=variant_id,
        result="success",
    )
    return entry.id
