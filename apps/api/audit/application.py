from uuid import UUID

from django.db import transaction
from django.db.transaction import TransactionManagementError

from identity.contracts import ActorContext

from .contracts import CatalogAuditAction, CatalogChangeAuditAction
from .models import CatalogAuditEntry, CatalogChangeAuditEntry


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


def _record_change(
    *, actor: ActorContext, action: CatalogChangeAuditAction, target_id: UUID,
    previous_category_id: UUID | None = None, new_category_id: UUID | None = None,
    previous_is_active: bool | None = None, new_is_active: bool | None = None,
) -> UUID:
    """Append typed attribution to the caller's transaction, not proof of authority."""
    if not transaction.get_connection("default").in_atomic_block:
        raise TransactionManagementError("Audit requires an active transaction")
    if not isinstance(actor, ActorContext):
        raise TypeError("ActorContext required")
    if not isinstance(actor.actor_id, str) or not actor.actor_id.strip():
        raise ValueError("Invalid actor identifier")
    entry = CatalogChangeAuditEntry.objects.using("default").create(
        actor_id=actor.actor_id, action=action.value, target_id=target_id,
        previous_category_id=previous_category_id, new_category_id=new_category_id,
        previous_is_active=previous_is_active, new_is_active=new_is_active,
        result="success",
    )
    return entry.id


def record_category_created(*, actor: ActorContext, category_id: UUID) -> UUID:
    return _record_change(
        actor=actor, action=CatalogChangeAuditAction.CATEGORY_CREATED, target_id=category_id,
    )


def record_product_category_changed(
    *, actor: ActorContext, product_id: UUID,
    previous_category_id: UUID | None, new_category_id: UUID | None,
) -> UUID:
    if previous_category_id == new_category_id:
        raise ValueError("A category change requires distinct states")
    return _record_change(
        actor=actor, action=CatalogChangeAuditAction.PRODUCT_CATEGORY_CHANGED,
        target_id=product_id,
        previous_category_id=previous_category_id, new_category_id=new_category_id,
    )


def record_product_active_changed(
    *, actor: ActorContext, product_id: UUID,
    previous_is_active: bool, new_is_active: bool,
) -> UUID:
    if type(previous_is_active) is not bool or type(new_is_active) is not bool:
        raise ValueError("An active state change requires booleans")
    if previous_is_active == new_is_active:
        raise ValueError("An active state change requires distinct states")
    return _record_change(
        actor=actor, action=CatalogChangeAuditAction.PRODUCT_ACTIVE_CHANGED,
        target_id=product_id,
        previous_is_active=previous_is_active, new_is_active=new_is_active,
    )
