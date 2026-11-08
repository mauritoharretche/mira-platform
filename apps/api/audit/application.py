from decimal import Context, Decimal, Inexact, InvalidOperation, ROUND_HALF_EVEN, localcontext
from uuid import UUID

from django.db import transaction
from django.db.transaction import TransactionManagementError

from identity.contracts import ActorContext

from .contracts import CatalogAuditAction, CatalogChangeAuditAction, CatalogMeasurementAuditAction, CatalogPriceAuditAction
from .models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogMeasurementAuditEntry, CatalogPriceAuditEntry


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


def _normalize_measurement_state(
    sale_unit: str | None, amount: Decimal | None, unit: str | None,
) -> tuple[str | None, Decimal | None, str | None]:
    if sale_unit is not None and (type(sale_unit) is not str or sale_unit not in ("EACH", "G", "KG")):
        raise ValueError("Invalid audit sale unit")
    if amount is None and unit is None:
        return sale_unit, None, None
    if type(unit) is not str or unit not in ("G", "KG"):
        raise ValueError("Audit net content requires a mass unit and an amount")
    if (
        not isinstance(amount, Decimal) or not amount.is_finite()
        or not Decimal("0") < amount <= Decimal("999999999999.999999")
    ):
        raise ValueError("Invalid audit net content")
    try:
        with localcontext(Context(prec=18, rounding=ROUND_HALF_EVEN, traps=[Inexact, InvalidOperation])):
            normalized = amount.quantize(Decimal("0.000001"))
    except (Inexact, InvalidOperation) as error:
        raise ValueError("Audit net content must be exactly representable at six decimal places") from error
    return sale_unit, normalized, unit


def record_variant_measurement_spec_changed(
    *, actor: ActorContext, variant_id: UUID, spec_id: UUID,
    previous_version: int | None, new_version: int,
    previous_sale_unit: str | None, new_sale_unit: str | None,
    previous_net_content_amount: Decimal | None, new_net_content_amount: Decimal | None,
    previous_net_content_unit: str | None, new_net_content_unit: str | None,
) -> UUID:
    """Append typed before/after attribution to the caller's default transaction.

    Catalog supplies the authorized state transition. This contract does not
    authenticate actors or independently prove correspondence with Catalog rows.
    """
    if not transaction.get_connection("default").in_atomic_block:
        raise TransactionManagementError("Audit requires an active transaction")
    if not isinstance(actor, ActorContext):
        raise TypeError("ActorContext required")
    if not isinstance(actor.actor_id, str) or not actor.actor_id.strip() or len(actor.actor_id) > 128:
        raise ValueError("Invalid actor identifier")
    if not isinstance(variant_id, UUID) or not isinstance(spec_id, UUID):
        raise ValueError("UUID references required")
    if type(new_version) is not int or new_version <= 0:
        raise ValueError("A positive integer version is required")
    if previous_version is not None and (type(previous_version) is not int or previous_version <= 0):
        raise ValueError("Invalid previous version")
    if new_version != (1 if previous_version is None else previous_version + 1):
        raise ValueError("Audit versions must advance by one")
    previous = _normalize_measurement_state(
        previous_sale_unit, previous_net_content_amount, previous_net_content_unit,
    )
    new = _normalize_measurement_state(new_sale_unit, new_net_content_amount, new_net_content_unit)
    if previous_version is None and previous != (None, None, None):
        raise ValueError("Initial attribution has no previous state")
    if previous == new:
        raise ValueError("Measurement attribution requires an actual change")
    entry = CatalogMeasurementAuditEntry.objects.using("default").create(
        actor_id=actor.actor_id, action=CatalogMeasurementAuditAction.SPEC_CHANGED.value,
        variant_id=variant_id, spec_id=spec_id, previous_version=previous_version, new_version=new_version,
        previous_sale_unit=previous[0], new_sale_unit=new[0],
        previous_net_content_amount=previous[1], new_net_content_amount=new[1],
        previous_net_content_unit=previous[2], new_net_content_unit=new[2], result="success",
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


def _normalize_audit_price(amount: Decimal) -> Decimal:
    if (
        not isinstance(amount, Decimal) or not amount.is_finite()
        or not Decimal("0") < amount <= Decimal("999999999999.99")
    ):
        raise ValueError("Invalid audit price")
    try:
        with localcontext(Context(prec=14, rounding=ROUND_HALF_EVEN, traps=[Inexact, InvalidOperation])):
            return amount.quantize(Decimal("0.01"))
    except (Inexact, InvalidOperation) as error:
        raise ValueError("Audit price must be exactly representable at two decimal places") from error


def record_variant_price_set(
    *, actor: ActorContext, variant_id: UUID,
    previous_revision_id: UUID | None, new_revision_id: UUID,
    previous_amount: Decimal | None, new_amount: Decimal,
    currency: str, price_basis: str,
) -> UUID:
    """Append attribution to the caller's transaction, without authenticating it.

    The controlled Catalog writer supplies before/after values. SQL constraints
    do not independently verify correspondence with another module's revision.
    """
    if not transaction.get_connection("default").in_atomic_block:
        raise TransactionManagementError("Audit requires an active transaction")
    if not isinstance(actor, ActorContext):
        raise TypeError("ActorContext required")
    if not isinstance(actor.actor_id, str) or not actor.actor_id.strip() or len(actor.actor_id) > 128:
        raise ValueError("Invalid actor identifier")
    if not isinstance(variant_id, UUID) or not isinstance(new_revision_id, UUID):
        raise ValueError("UUID references required")
    if currency != "ARS" or price_basis != "PER_VARIANT":
        raise ValueError("Unsupported audit currency or price basis")
    normalized_new = _normalize_audit_price(new_amount)
    normalized_previous = None
    if previous_revision_id is None:
        if previous_amount is not None:
            raise ValueError("Previous revision and amount must both be absent")
    else:
        if not isinstance(previous_revision_id, UUID) or previous_revision_id == new_revision_id:
            raise ValueError("Distinct UUID revision references required")
        normalized_previous = _normalize_audit_price(previous_amount)
        if normalized_previous == normalized_new:
            raise ValueError("A price change requires distinct amounts")

    entry = CatalogPriceAuditEntry.objects.using("default").create(
        actor_id=actor.actor_id, action=CatalogPriceAuditAction.VARIANT_PRICE_SET.value,
        variant_id=variant_id, previous_revision_id=previous_revision_id,
        new_revision_id=new_revision_id, previous_amount=normalized_previous,
        new_amount=normalized_new, currency=currency, price_basis=price_basis, result="success",
    )
    return entry.id
