import uuid

from django.db import models

from .contracts import CatalogAuditAction


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
