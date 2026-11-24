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
                | models.Q(kind=StockMovementKind.CONSUMPTION.value, delta__lt=0, reason__isnull=True)
                | (models.Q(kind=StockMovementKind.ADJUSTMENT.value, reason__isnull=False)
                   & GreaterThan(Length(Trim("reason")), 0))
            ), name="inventory_movement_shape_valid"),
            models.CheckConstraint(condition=(
                models.Q(sequence=1, quantity_before=0, kind=StockMovementKind.RECEIPT.value)
                | models.Q(sequence__gt=1)
            ), name="inventory_movement_initial_valid"),
        ]


class StockLocation(models.Model):
    """A named physical place in the single pilot operation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, db_collation="C", unique=True)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    commercial_eligible = models.BooleanField(default=False)
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("manage_stock_locations", "Can manage stock locations")]
        constraints = [models.CheckConstraint(condition=models.Q(version__gt=0), name="inventory_location_version_positive")]


class StockLot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey("catalog.Variant", on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=80, db_collation="C")
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, db_collation="C", default="ACTIVE")
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("manage_stock_lots", "Can manage stock lots"),
            ("allocate_stock_lots", "Can allocate received stock to lots"),
            ("transfer_stock_lots", "Can transfer lots between locations"),
            ("correct_stock_lots", "Can correct physical lot quantities"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["variant", "code"], name="inventory_lot_variant_code_unique"),
            models.CheckConstraint(condition=models.Q(status__in=["ACTIVE", "BLOCKED", "QUARANTINE"]), name="inventory_lot_status_valid"),
            models.CheckConstraint(condition=models.Q(version__gt=0), name="inventory_lot_version_positive"),
        ]


class StockLotBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, related_name="balances")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["lot", "location"], name="inventory_lot_location_unique"),
            models.CheckConstraint(condition=models.Q(quantity__lte=MAX_STOCK_INTEGER), name="inventory_lot_quantity_valid"),
        ]


class StockLotEvent(models.Model):
    """Append-only explanation of lot/location mutations; Audit attributes each row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, null=True, related_name="events")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, null=True, related_name="+")
    other_location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, null=True, related_name="+")
    stock_movement_id = models.UUIDField(null=True)
    action = models.CharField(max_length=24, db_collation="C")
    quantity = models.BigIntegerField(default=0)
    reason = models.CharField(max_length=255, null=True)
    actor_id = models.CharField(max_length=128)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(action__in=[
            "location.created", "lot.created", "lot.status", "lot.received",
            "lot.transferred", "lot.corrected",
        ]), name="inventory_lot_event_action_valid")]


class StockReservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation_id = models.UUIDField(unique=True)
    source = models.CharField(max_length=40, db_collation="C")
    reference = models.CharField(max_length=120)
    actor_id = models.CharField(max_length=128)
    state = models.CharField(max_length=16, default="ACTIVE", db_collation="C")
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        permissions = [("reserve_stock", "Can reserve physical stock"),
                       ("release_reservation", "Can release reservations"),
                       ("expire_reservation", "Can expire reservations"),
                       ("consume_reservation", "Can consume reservations"),
                       ("classify_stock", "Can classify verified unallocated stock"),
                       ("set_location_eligibility", "Can set commercial location eligibility"),
                       ("read_availability", "Can read commercial availability")]
        constraints = [models.CheckConstraint(condition=models.Q(state__in=["ACTIVE", "RELEASED", "CONSUMED", "EXPIRED"]), name="inventory_reservation_state_valid"),
                       models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("created_at")), name="inventory_reservation_expiry_valid")]


class ReservationAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.ForeignKey(StockReservation, on_delete=models.PROTECT, related_name="allocations")
    balance = models.ForeignKey(StockLotBalance, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.PositiveBigIntegerField()
    stock_movement_id = models.UUIDField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["reservation", "balance"], name="inventory_reservation_balance_unique"),
                       models.CheckConstraint(condition=models.Q(quantity__gt=0, quantity__lte=MAX_STOCK_INTEGER), name="inventory_reservation_quantity_valid")]


class InventoryOperation(models.Model):
    operation_id = models.UUIDField(primary_key=True)
    action = models.CharField(max_length=32, db_collation="C")
    request_sha256 = models.CharField(max_length=64, db_collation="C")
    actor_id = models.CharField(max_length=128)
    reservation = models.ForeignKey(StockReservation, on_delete=models.PROTECT, null=True, related_name="operations")
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class ReservationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.ForeignKey(StockReservation, on_delete=models.PROTECT, null=True, related_name="events")
    operation_id = models.UUIDField(null=True)
    action = models.CharField(max_length=32, db_collation="C")
    actor_id = models.CharField(max_length=128)
    details = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(action__in=[
            "reservation.created", "reservation.allocated", "reservation.released",
            "reservation.expired", "reservation.consumed", "reservation.invalidated",
            "stock.classified", "location.eligibility",
        ]), name="inventory_reservation_event_valid")]
