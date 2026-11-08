from decimal import Decimal, Inexact, Rounded, localcontext
from io import StringIO
from itertools import product
from uuid import uuid4

from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase
from django.utils.timezone import now

from audit.application import record_variant_measurement_spec_changed
from audit.contracts import CatalogMeasurementAuditAction
from audit.models import CatalogMeasurementAuditEntry
from identity.contracts import ActorContext


def contract_values(**overrides):
    values = dict(actor=ActorContext("fictional-measurement-actor"), variant_id=uuid4(), spec_id=uuid4(),
                  previous_version=None, new_version=1, previous_sale_unit=None, new_sale_unit="EACH",
                  previous_net_content_amount=None, new_net_content_amount=None,
                  previous_net_content_unit=None, new_net_content_unit=None)
    values.update(overrides)
    return values


def row_values(**overrides):
    values = contract_values()
    values["actor_id"] = values.pop("actor").actor_id
    values.update(action=CatalogMeasurementAuditAction.SPEC_CHANGED.value, result="success")
    values.update(overrides)
    return values


class MeasurementAuditContractTests(TestCase):
    def test_typed_creation_update_and_clear_preserve_exact_fields_without_foreign_keys(self):
        values = contract_values(new_net_content_amount=Decimal("250"), new_net_content_unit="G")
        identifier = record_variant_measurement_spec_changed(**values)
        entry = CatalogMeasurementAuditEntry.objects.get(pk=identifier)
        self.assertEqual(entry.actor_id, values["actor"].actor_id)
        for name, value in values.items():
            if name != "actor":
                self.assertEqual(getattr(entry, name), value)
        self.assertEqual(entry.action, "catalog.variant_measurement_spec_changed")
        self.assertEqual(entry.result, "success")
        self.assertEqual(len(entry._meta.fields), 15)
        self.assertFalse(any(field.is_relation for field in entry._meta.fields))
        cleared = record_variant_measurement_spec_changed(**contract_values(
            spec_id=values["spec_id"], variant_id=values["variant_id"], previous_version=1, new_version=2,
            previous_sale_unit="EACH", previous_net_content_amount=Decimal("250"), previous_net_content_unit="G",
            new_sale_unit=None,
        ))
        self.assertIsNone(CatalogMeasurementAuditEntry.objects.get(pk=cleared).new_sale_unit)

    def test_contract_rejects_untrusted_actor_shapes_and_non_uuid_references(self):
        for override, error in (({"actor": object()}, TypeError), ({"actor": {"actor_id": "fictional"}}, TypeError),
                                ({"actor": ActorContext("")}, ValueError), ({"actor": ActorContext(" ")}, ValueError),
                                ({"actor": ActorContext("a" * 129)}, ValueError), ({"actor": ActorContext(1)}, ValueError),
                                ({"variant_id": "not-a-uuid"}, ValueError), ({"spec_id": None}, ValueError)):
            with self.subTest(override=override), self.assertRaises(error):
                record_variant_measurement_spec_changed(**contract_values(**override))
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_contract_rejects_invalid_versions_initial_state_and_noop(self):
        overrides = ({"new_version": True}, {"new_version": 0}, {"new_version": 2},
                     {"previous_version": False}, {"previous_version": -1}, {"previous_version": "1"},
                     {"previous_version": 2, "new_version": 2},
                     {"previous_sale_unit": "KG"}, {"new_sale_unit": None},
                     {"previous_version": 1, "new_version": 2, "previous_sale_unit": "EACH"})
        for override in overrides:
            with self.subTest(override=override), self.assertRaises(ValueError):
                record_variant_measurement_spec_changed(**contract_values(**override))
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_contract_rejects_malformed_before_and_after_states(self):
        for prefix in ("previous_", "new_"):
            base = dict(previous_version=1, new_version=2, previous_sale_unit="G")
            changes = ({"sale_unit": "g"}, {"sale_unit": 1}, {"net_content_amount": Decimal("1")},
                       {"net_content_unit": "G"}, {"net_content_amount": Decimal("1"), "net_content_unit": "EACH"},
                       {"net_content_amount": Decimal("1"), "net_content_unit": "g"})
            for change in changes:
                override = {**base, **{prefix + key: value for key, value in change.items()}}
                with self.subTest(prefix=prefix, change=change), self.assertRaises(ValueError):
                    record_variant_measurement_spec_changed(**contract_values(**override))
            for amount in (1, True, "1", Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1"),
                           Decimal("1000000000000"), Decimal("1.0000001")):
                override = {**base, prefix + "net_content_amount": amount, prefix + "net_content_unit": "G"}
                with self.subTest(prefix=prefix, amount=amount), self.assertRaises(ValueError):
                    record_variant_measurement_spec_changed(**contract_values(**override))
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_audit_normalization_is_independent_of_ambient_decimal_context(self):
        with localcontext() as context:
            context.prec, context.Emax, context.Emin = 1, 2, -2
            context.traps[Inexact] = context.traps[Rounded] = True
            identifier = record_variant_measurement_spec_changed(**contract_values(
                new_net_content_amount=Decimal("250.000000000"), new_net_content_unit="G",
            ))
        self.assertEqual(CatalogMeasurementAuditEntry.objects.get(pk=identifier).new_net_content_amount, Decimal("250"))

    def test_canonical_equal_states_are_noop_even_with_different_decimal_exponents(self):
        with self.assertRaises(ValueError):
            record_variant_measurement_spec_changed(**contract_values(
                previous_version=1, new_version=2, previous_sale_unit="EACH",
                previous_net_content_amount=Decimal("1.0000000"), previous_net_content_unit="G",
                new_net_content_amount=Decimal("1"), new_net_content_unit="G",
            ))
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())


class MeasurementAuditPostgreSQLTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))

    def insert(self, raw=False, **overrides):
        values = row_values(**overrides)
        if not raw:
            return CatalogMeasurementAuditEntry.objects.create(**values).id
        values.update(id=uuid4(), occurred_at=now())
        columns = ", ".join(connection.ops.quote_name(key) for key in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO audit_catalogmeasurementauditentry ({columns}) VALUES ({placeholders})", list(values.values()))
        return values["id"]

    def reject(self, constraint, raw=False, **overrides):
        count = CatalogMeasurementAuditEntry.objects.count()
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert(raw=raw, **overrides)
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), count)

    def test_contract_requires_default_transaction_and_outer_rollback_removes_insert(self):
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(TransactionManagementError):
            record_variant_measurement_spec_changed(**contract_values())
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                record_variant_measurement_spec_changed(**contract_values())
                raise RuntimeError("rollback requested")
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_database_null_safe_change_check_accepts_only_actual_metadata_changes(self):
        states = tuple((sale, amount, unit) for sale in (None, "EACH", "G", "KG")
                       for amount, unit in ((None, None), (Decimal("1"), "G"), (Decimal("0.001"), "KG")))
        for state in states:
            values = dict(new_sale_unit=state[0], new_net_content_amount=state[1], new_net_content_unit=state[2])
            with self.subTest(initial=state):
                if state == (None, None, None):
                    self.reject("audit_measurement_state_changed", **values)
                else:
                    self.insert(**values)
        for previous, new in product(states, repeat=2):
            values = dict(previous_version=1, new_version=2, previous_sale_unit=previous[0], new_sale_unit=new[0],
                          previous_net_content_amount=previous[1], new_net_content_amount=new[1],
                          previous_net_content_unit=previous[2], new_net_content_unit=new[2])
            with self.subTest(previous=previous, new=new):
                if previous == new:
                    self.reject("audit_measurement_state_changed", **values)
                else:
                    self.insert(**values)
        # Representative SQL bypasses all application and ORM validation.
        self.reject("audit_measurement_state_changed", raw=True, new_sale_unit=None)
        self.reject("audit_measurement_state_changed", raw=True, previous_version=1, new_version=2, previous_sale_unit="EACH")

    def test_database_rejects_partial_null_pairs_and_invalid_unit_codes_in_each_state(self):
        for prefix in ("previous_", "new_"):
            base = dict(previous_version=1, new_version=2, previous_sale_unit="G")
            for raw in (False, True):
                for change in ({"net_content_amount": Decimal("1")}, {"net_content_unit": "G"},
                               {"net_content_amount": Decimal("1"), "net_content_unit": "EACH"},
                               {"net_content_amount": Decimal("1"), "net_content_unit": "g"}):
                    self.reject(f"audit_measurement_{prefix}net_valid", raw=raw,
                                **{**base, **{prefix + key: value for key, value in change.items()}})
                for sale in ("", "g", "ML"):
                    self.reject(f"audit_measurement_{prefix}sale_valid", raw=raw, **{**base, prefix + "sale_unit": sale})

    def test_database_versions_require_initial_absence_and_contiguous_positive_increment(self):
        for raw in (False, True):
            for override in ({"new_version": 0}, {"new_version": 2}, {"previous_sale_unit": "G"},
                             {"previous_net_content_amount": Decimal("1"), "previous_net_content_unit": "G"},
                             {"previous_version": 0, "new_version": 1},
                             {"previous_version": 1, "new_version": 1}, {"previous_version": 1, "new_version": 3}):
                with self.subTest(raw=raw, override=override):
                    self.reject("audit_measurement_versions_valid", raw=raw, **override)
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert(new_version=None)
        self.assertEqual(failure.exception.__cause__.diag.column_name, "new_version")

    def test_database_action_result_and_spec_version_uniqueness(self):
        self.reject("audit_measurement_action_valid", action="catalog.unknown")
        self.reject("audit_measurement_result_success", result="failure")
        spec_id = uuid4()
        self.insert(spec_id=spec_id)
        self.reject("audit_measurement_spec_version_unique", spec_id=spec_id, new_sale_unit="KG")
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 1)

    def test_database_amount_checks_and_numeric_conversion_are_distinct_guarantees(self):
        for prefix in ("previous_", "new_"):
            base = dict(previous_version=1, new_version=2, previous_sale_unit="G")
            for value in ("0", "-1", "NaN"):
                self.reject(f"audit_measurement_{prefix}net_valid", raw=True,
                            **{**base, prefix + "net_content_amount": value, prefix + "net_content_unit": "G"})
            for value in ("Infinity", "-Infinity", "1000000000000"):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    self.insert(raw=True, **{**base, prefix + "net_content_amount": value, prefix + "net_content_unit": "G"})
        identifier = self.insert(raw=True, new_net_content_amount="1.0000004", new_net_content_unit="G")
        self.assertEqual(CatalogMeasurementAuditEntry.objects.get(pk=identifier).new_net_content_amount, Decimal("1.000000"))

    def test_generated_sql_and_installed_checks_have_explicit_null_and_version_guards(self):
        output = StringIO()
        call_command("sqlmigrate", "audit", "0004_catalog_measurement_audit_entry", stdout=output)
        sql = output.getvalue()
        self.assertIn("numeric(18, 6)", sql)
        self.assertIn('COLLATE "C"', sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass",
                           ["audit_catalogmeasurementauditentry"])
            constraints = dict(cursor.fetchall())
        changed = constraints["audit_measurement_state_changed"]
        for field in ("sale_unit", "net_content_amount", "net_content_unit"):
            for prefix in ("previous_", "new_"):
                self.assertIn(f"{prefix}{field} IS NULL", changed)
                self.assertIn(f"{prefix}{field} IS NOT NULL", changed)
        versions = constraints["audit_measurement_versions_valid"]
        self.assertIn("previous_version IS NULL", versions)
        self.assertIn("previous_version IS NOT NULL", versions)
        for prefix in ("previous_", "new_"):
            net = constraints[f"audit_measurement_{prefix}net_valid"]
            self.assertIn(f"{prefix}net_content_amount IS NOT NULL", net)
            self.assertIn(f"{prefix}net_content_unit IS NOT NULL", net)
