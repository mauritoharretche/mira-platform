from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase
from django.utils.timezone import now

from audit.application import record_inventory_movement
from audit.contracts import InventoryMovementAuditAction
from audit.models import InventoryMovementAuditEntry
from identity.contracts import ActorContext
from inventory.tests.test_application import assert_test_database


def contract_values(**changes):
    values = dict(actor=ActorContext("fictional-stock-actor"), action=InventoryMovementAuditAction.STOCK_RECEIVED,
                  variant_id=uuid4(), position_id=uuid4(), movement_id=uuid4(), sequence=1,
                  unit="EACH", quantity_before=0, delta=5, quantity_after=5)
    values.update(changes)
    return values


class InventoryAuditContractTests(TestCase):
    def setUp(self):
        assert_test_database(self)

    def test_typed_attribution_contains_exact_fields_without_cross_module_foreign_keys(self):
        values = contract_values()
        identifier = record_inventory_movement(**values)
        entry = InventoryMovementAuditEntry.objects.get(pk=identifier)
        self.assertEqual(entry.actor_id, values["actor"].actor_id)
        self.assertEqual(entry.action, values["action"].value)
        for name, value in values.items():
            if name not in ("actor", "action"):
                self.assertEqual(getattr(entry, name), value)
        self.assertEqual(entry.result, "success")
        self.assertEqual(len(entry._meta.fields), 13)
        self.assertFalse(any(f.is_relation for f in entry._meta.fields))
        self.assertNotIn("reason", {f.name for f in entry._meta.fields})
        record_inventory_movement(**contract_values(action=InventoryMovementAuditAction.STOCK_ADJUSTED,
                                  sequence=2, quantity_before=5, delta=-5, quantity_after=0))

    def test_invalid_actor_action_references_and_codes_are_rejected(self):
        for changes, error in (
            ({"actor": object()}, TypeError), ({"actor": {"actor_id": "fictional"}}, TypeError),
            ({"actor": ActorContext("")}, ValueError), ({"actor": ActorContext(" ")}, ValueError),
            ({"actor": ActorContext("a" * 129)}, ValueError), ({"actor": ActorContext(1)}, ValueError),
            ({"action": "inventory.stock_received"}, ValueError), ({"action": None}, ValueError),
            ({"unit": "each"}, ValueError), ({"unit": None}, ValueError),
            ({"variant_id": "not-uuid"}, ValueError), ({"position_id": None}, ValueError),
            ({"movement_id": 1}, ValueError),
        ):
            with self.subTest(changes=changes), self.assertRaises(error):
                record_inventory_movement(**contract_values(**changes))
        self.assertFalse(InventoryMovementAuditEntry.objects.exists())

    def test_types_overflow_equations_and_initial_states_are_validated(self):
        changes = [{name: value} for name in ("sequence", "quantity_before", "quantity_after", "delta")
                   for value in (None, True, "1", 1.0, 2**63)]
        changes += [{"sequence": 0}, {"quantity_before": -1}, {"quantity_after": -1}, {"delta": -(2**63)},
                    {"delta": 0}, {"quantity_after": 7},
                    {"quantity_before": 1, "quantity_after": 6},
                    {"action": InventoryMovementAuditAction.STOCK_ADJUSTED},
                    {"sequence": 2, "quantity_before": 5, "delta": -1, "quantity_after": 4}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                record_inventory_movement(**contract_values(**change))
        self.assertFalse(InventoryMovementAuditEntry.objects.exists())


class InventoryAuditTransactionContextTests(TransactionTestCase):
    def test_contract_requires_active_default_transaction(self):
        assert_test_database(self)
        self.assertTrue(connection.get_autocommit())
        with self.assertRaises(TransactionManagementError):
            record_inventory_movement(**contract_values())
        self.assertFalse(InventoryMovementAuditEntry.objects.exists())


class InventoryAuditDatabaseTests(TransactionTestCase):
    def setUp(self):
        assert_test_database(self)

    def insert(self, *, raw=False, **changes):
        values = contract_values()
        values["actor_id"] = values.pop("actor").actor_id
        values["action"] = values["action"].value
        values.update(id=uuid4(), occurred_at=now(), result="success")
        values.update(changes)
        if raw:
            columns = ", ".join(connection.ops.quote_name(k) for k in values)
            with connection.cursor() as cursor:
                cursor.execute(f'INSERT INTO audit_inventorymovementauditentry ({columns}) VALUES ({", ".join(["%s"] * len(values))})', list(values.values()))
        else:
            InventoryMovementAuditEntry.objects.create(**values)
        return values["id"]

    def reject(self, constraint=None, **changes):
        before = InventoryMovementAuditEntry.objects.count()
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert(**changes)
        if constraint:
            self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), before)

    def test_direct_database_checks_reject_invalid_actions_results_codes_and_state_shapes(self):
        cases = (
            ("audit_inventory_action_valid", {"action": "inventory.unknown"}),
            ("audit_inventory_action_valid", {"action": "INVENTORY.STOCK_RECEIVED"}),
            ("audit_inventory_result_success", {"result": "failure"}),
            ("audit_inventory_unit_valid", {"unit": "each"}),
            ("audit_inventory_actor_nonempty", {"actor_id": "   "}),
            ("audit_inventory_equation_valid", {"quantity_after": 6}),
            ("audit_inventory_initial_valid", {"action": "inventory.stock_adjusted"}),
            ("audit_inventory_initial_valid", {"quantity_before": 1, "quantity_after": 6}),
            ("audit_inventory_delta_valid", {"action": "inventory.stock_adjusted", "sequence": 2, "delta": 0, "quantity_after": 0}),
            ("audit_inventory_action_valid", {"sequence": 2, "quantity_before": 5, "delta": -1, "quantity_after": 4}),
        )
        for raw in (False, True):
            for constraint, changes in cases:
                with self.subTest(raw=raw, changes=changes):
                    self.reject(constraint, raw=raw, **changes)

    def test_null_does_not_bypass_any_required_audit_field_and_uniqueness_is_real(self):
        for field in ("actor_id", "action", "variant_id", "position_id", "movement_id", "sequence", "unit",
                      "quantity_before", "delta", "quantity_after", "occurred_at", "result"):
            with self.subTest(field=field):
                self.reject(raw=True, **{field: None})
        identifier = uuid4()
        self.insert(movement_id=identifier)
        self.reject("audit_inventory_movement_unique", movement_id=identifier)

    def test_bigint_boundaries_and_overflow_and_negative_quantities(self):
        maximum = 2**63 - 1
        self.insert(raw=True, delta=maximum, quantity_after=maximum)
        self.insert(raw=True, sequence=2, action="inventory.stock_adjusted",
                    quantity_before=maximum, delta=-maximum, quantity_after=0)
        for changes in ({"sequence": maximum + 1}, {"delta": maximum + 1},
                        {"delta": -(2**63)}, {"quantity_before": maximum + 1}, {"quantity_after": maximum + 1}):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(raw=True, **changes)
        self.reject(raw=True, sequence=2, action="inventory.stock_adjusted", quantity_before=-1, delta=2, quantity_after=1)
        self.assertEqual(InventoryMovementAuditEntry.objects.count(), 2)
