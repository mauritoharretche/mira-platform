import uuid
from decimal import Decimal

from django.db import models

from .contracts import CatalogAuditAction, CatalogChangeAuditAction, CatalogMeasurementAuditAction, CatalogPriceAuditAction


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


def _measurement_net_valid(prefix: str) -> models.Q:
    return models.Q(**{f"{prefix}net_content_amount__isnull": True, f"{prefix}net_content_unit__isnull": True}) | models.Q(**{
        f"{prefix}net_content_amount__isnull": False, f"{prefix}net_content_unit__isnull": False,
        f"{prefix}net_content_amount__gt": 0,
        f"{prefix}net_content_amount__lte": Decimal("999999999999.999999"),
        f"{prefix}net_content_unit__in": ["G", "KG"],
    })


def _measurement_field_differs(field: str) -> models.Q:
    previous, new = f"previous_{field}", f"new_{field}"
    return (
        models.Q(**{f"{previous}__isnull": True, f"{new}__isnull": False})
        | models.Q(**{f"{previous}__isnull": False, f"{new}__isnull": True})
        | (models.Q(**{f"{previous}__isnull": False, f"{new}__isnull": False})
           & ~models.Q(**{previous: models.F(new)}))
    )


class CatalogMeasurementAuditEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField(max_length=128)
    action = models.CharField(
        max_length=64, choices=[(action.value, action.name) for action in CatalogMeasurementAuditAction],
    )
    variant_id = models.UUIDField()
    spec_id = models.UUIDField()
    previous_version = models.PositiveBigIntegerField(null=True, blank=True)
    new_version = models.PositiveBigIntegerField()
    previous_sale_unit = models.CharField(max_length=8, null=True, blank=True, db_collation="C")
    new_sale_unit = models.CharField(max_length=8, null=True, blank=True, db_collation="C")
    previous_net_content_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    new_net_content_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    previous_net_content_unit = models.CharField(max_length=8, null=True, blank=True, db_collation="C")
    new_net_content_unit = models.CharField(max_length=8, null=True, blank=True, db_collation="C")
    occurred_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=16, default="success", editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["spec_id", "new_version"], name="audit_measurement_spec_version_unique"),
            models.CheckConstraint(
                condition=models.Q(action=CatalogMeasurementAuditAction.SPEC_CHANGED.value),
                name="audit_measurement_action_valid",
            ),
            models.CheckConstraint(condition=models.Q(result="success"), name="audit_measurement_result_success"),
            models.CheckConstraint(
                condition=models.Q(previous_sale_unit__isnull=True) | models.Q(previous_sale_unit__in=["EACH", "G", "KG"]),
                name="audit_measurement_previous_sale_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(new_sale_unit__isnull=True) | models.Q(new_sale_unit__in=["EACH", "G", "KG"]),
                name="audit_measurement_new_sale_valid",
            ),
            models.CheckConstraint(condition=_measurement_net_valid("previous_"), name="audit_measurement_previous_net_valid"),
            models.CheckConstraint(condition=_measurement_net_valid("new_"), name="audit_measurement_new_net_valid"),
            models.CheckConstraint(
                condition=(
                    models.Q(previous_version__isnull=True, new_version=1, previous_sale_unit__isnull=True,
                             previous_net_content_amount__isnull=True, previous_net_content_unit__isnull=True)
                    | models.Q(previous_version__isnull=False, previous_version__gt=0,
                               new_version=models.F("previous_version") + 1)
                ),
                name="audit_measurement_versions_valid",
            ),
            models.CheckConstraint(
                condition=_measurement_field_differs("sale_unit") | _measurement_field_differs("net_content_amount")
                | _measurement_field_differs("net_content_unit"),
                name="audit_measurement_state_changed",
            ),
        ]
