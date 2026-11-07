import uuid
from decimal import Decimal

from django.db import models

from .contracts import CatalogAuditAction, CatalogChangeAuditAction, CatalogPriceAuditAction


class CatalogAuditEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField(max_length=128)
    action = models.CharField(
        max_length=64,
        choices=[(action.value, action.name) for action in CatalogAuditAction],
    )
    product_id = models.UUIDField()
    variant_id = models.UUIDField()
    occurred_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=16, default="success", editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=[action.value for action in CatalogAuditAction]),
                name="audit_catalog_action_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(result="success"),
                name="audit_catalog_result_success",
            ),
        ]


class CatalogChangeAuditEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField(max_length=128)
    action = models.CharField(
        max_length=64,
        choices=[(action.value, action.name) for action in CatalogChangeAuditAction],
    )
    target_id = models.UUIDField()
    previous_category_id = models.UUIDField(null=True, blank=True)
    new_category_id = models.UUIDField(null=True, blank=True)
    previous_is_active = models.BooleanField(null=True, blank=True)
    new_is_active = models.BooleanField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=16, default="success", editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=[action.value for action in CatalogChangeAuditAction]),
                name="audit_change_action_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(result="success"), name="audit_change_result_success",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        action="catalog.category_created",
                        previous_category_id__isnull=True, new_category_id__isnull=True,
                        previous_is_active__isnull=True, new_is_active__isnull=True,
                    )
                    | (
                        models.Q(
                            action="catalog.product_category_changed",
                            previous_is_active__isnull=True, new_is_active__isnull=True,
                        )
                        & (
                            models.Q(previous_category_id__isnull=True, new_category_id__isnull=False)
                            | models.Q(previous_category_id__isnull=False, new_category_id__isnull=True)
                            | (
                                models.Q(previous_category_id__isnull=False, new_category_id__isnull=False)
                                & ~models.Q(previous_category_id=models.F("new_category_id"))
                            )
                        )
                    )
                    | (
                        models.Q(
                            action="catalog.product_active_changed",
                            previous_category_id__isnull=True, new_category_id__isnull=True,
                            previous_is_active__isnull=False, new_is_active__isnull=False,
                        )
                        & ~models.Q(previous_is_active=models.F("new_is_active"))
                    )
                ),
                name="audit_change_shape_valid",
            ),
        ]


class CatalogPriceAuditEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField(max_length=128)
    action = models.CharField(
        max_length=64, choices=[(action.value, action.name) for action in CatalogPriceAuditAction],
    )
    variant_id = models.UUIDField()
    previous_revision_id = models.UUIDField(null=True, blank=True)
    new_revision_id = models.UUIDField()
    previous_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    new_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, db_collation="C")
    price_basis = models.CharField(max_length=16, db_collation="C")
    occurred_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=16, default="success", editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["new_revision_id"], name="audit_price_new_revision_unique"),
            models.CheckConstraint(
                condition=models.Q(action=CatalogPriceAuditAction.VARIANT_PRICE_SET.value),
                name="audit_price_action_valid",
            ),
            models.CheckConstraint(condition=models.Q(result="success"), name="audit_price_result_success"),
            models.CheckConstraint(condition=models.Q(currency="ARS"), name="audit_price_currency_ars"),
            models.CheckConstraint(
                condition=models.Q(price_basis="PER_VARIANT"), name="audit_price_basis_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(new_amount__gt=0, new_amount__lte=Decimal("999999999999.99")),
                name="audit_price_new_amount_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(previous_amount__isnull=True) | models.Q(
                    previous_amount__gt=0, previous_amount__lte=Decimal("999999999999.99")
                ),
                name="audit_price_previous_amount_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(previous_revision_id__isnull=True, previous_amount__isnull=True)
                    | (
                        models.Q(previous_revision_id__isnull=False, previous_amount__isnull=False)
                        & ~models.Q(previous_revision_id=models.F("new_revision_id"))
                        & ~models.Q(previous_amount=models.F("new_amount"))
                    )
                ),
                name="audit_price_shape_valid",
            ),
        ]
