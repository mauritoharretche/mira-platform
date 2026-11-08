import uuid

from django.db import models
from django.db.models.functions import Cast, Length, Trim
from django.db.models.lookups import Exact, GreaterThan

from .contracts import MAX_STOCK_INTEGER, InventoryUnit, StockMovementKind


class StockPosition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey("catalog.Variant", on_delete=models.PROTECT, related_name="+")
    accounting_unit = models.CharField(max_length=8, db_collation="C")
    on_hand = models.PositiveBigIntegerField()
    version = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("receive_stock", "Can receive stock"), ("adjust_stock", "Can adjust stock")]
        constraints = [
            models.UniqueConstraint(fields=["variant"], name="inventory_position_variant_unique"),
            models.CheckConstraint(condition=models.Q(accounting_unit=InventoryUnit.EACH.value), name="inventory_position_unit_valid"),
            models.CheckConstraint(condition=models.Q(on_hand__gte=0, on_hand__lte=MAX_STOCK_INTEGER), name="inventory_position_quantity_valid"),
            models.CheckConstraint(condition=models.Q(version__gt=0, version__lte=MAX_STOCK_INTEGER), name="inventory_position_version_valid"),
        ]


class StockMovement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    position = models.ForeignKey(StockPosition, on_delete=models.PROTECT, related_name="movements")
    sequence = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=16, db_collation="C")
    unit = models.CharField(max_length=8, db_collation="C")
    quantity_before = models.PositiveBigIntegerField()
    delta = models.BigIntegerField()
    quantity_after = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=255, null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["position", "sequence"], name="inventory_movement_sequence_unique"),
            models.CheckConstraint(condition=models.Q(sequence__gt=0, sequence__lte=MAX_STOCK_INTEGER), name="inventory_movement_sequence_valid"),
            models.CheckConstraint(condition=models.Q(unit=InventoryUnit.EACH.value), name="inventory_movement_unit_valid"),
            models.CheckConstraint(condition=models.Q(quantity_before__gte=0, quantity_before__lte=MAX_STOCK_INTEGER, quantity_after__gte=0, quantity_after__lte=MAX_STOCK_INTEGER), name="inventory_movement_quantities_valid"),
            models.CheckConstraint(condition=models.Q(delta__gte=-MAX_STOCK_INTEGER, delta__lte=MAX_STOCK_INTEGER) & ~models.Q(delta=0), name="inventory_movement_delta_valid"),
            # Cast only CHECK arithmetic: even malformed signed bigint operands
            # cannot overflow before PostgreSQL evaluates the other constraints.
            models.CheckConstraint(condition=Exact(
                Cast("quantity_after", models.DecimalField(max_digits=20, decimal_places=0))
                - Cast("quantity_before", models.DecimalField(max_digits=20, decimal_places=0)),
                models.F("delta"),
            ), name="inventory_movement_equation_valid"),
            models.CheckConstraint(condition=(
                models.Q(kind=StockMovementKind.RECEIPT.value, delta__gt=0, reason__isnull=True)
                | (models.Q(kind=StockMovementKind.ADJUSTMENT.value, reason__isnull=False)
                   & GreaterThan(Length(Trim("reason")), 0))
            ), name="inventory_movement_shape_valid"),
            models.CheckConstraint(condition=(
                models.Q(sequence=1, quantity_before=0, kind=StockMovementKind.RECEIPT.value)
                | models.Q(sequence__gt=1)
            ), name="inventory_movement_initial_valid"),
        ]
