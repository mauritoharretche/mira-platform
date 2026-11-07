import uuid

from django.db import models

from .contracts import CatalogAuditAction, CatalogChangeAuditAction


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
