from decimal import Decimal
from io import StringIO
from itertools import product
from uuid import uuid4

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.transaction import TransactionManagementError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from audit.application import record_variant_price_set
from audit.models import CatalogPriceAuditEntry
from identity.contracts import ActorContext


class PriceAuditContractTests(TestCase):
    def arguments(self, **overrides):
        return {
            "actor": ActorContext(actor_id="fictional-actor"), "variant_id": uuid4(),
            "previous_revision_id": None, "new_revision_id": uuid4(),
            "previous_amount": None, "new_amount": Decimal("20.00"),
            "currency": "ARS", "price_basis": "PER_VARIANT", **overrides,
        }

    def test_contract_records_typed_first_and_replacement_states_without_cross_module_relations(self):
        arguments = self.arguments(new_amount=Decimal("20.000"))
        first_id = record_variant_price_set(**arguments)
        second_arguments = self.arguments(
            variant_id=arguments["variant_id"], previous_revision_id=arguments["new_revision_id"],
            previous_amount=Decimal("20"), new_amount=Decimal("30"),
        )
        second_id = record_variant_price_set(**second_arguments)
        first = CatalogPriceAuditEntry.objects.get(pk=first_id)
        second = CatalogPriceAuditEntry.objects.get(pk=second_id)
        self.assertIsNone(first.previous_revision_id)
        self.assertIsNone(first.previous_amount)
        self.assertEqual(first.new_amount.as_tuple().exponent, -2)
        self.assertEqual(second.previous_revision_id, first.new_revision_id)
        self.assertEqual(second.previous_amount, first.new_amount)
        self.assertEqual(second.new_amount, Decimal("30.00"))
        self.assertEqual(second.actor_id, "fictional-actor")
        self.assertEqual(second.variant_id, first.variant_id)
        self.assertEqual(second.action, "catalog.variant_price_set")
        self.assertEqual(second.currency, "ARS")
        self.assertEqual(second.price_basis, "PER_VARIANT")
        self.assertEqual(second.result, "success")
        self.assertIsNotNone(second.occurred_at.utcoffset())
        self.assertEqual({field.name for field in second._meta.fields}, {
            "id", "actor_id", "action", "variant_id", "previous_revision_id", "new_revision_id",
            "previous_amount", "new_amount", "currency", "price_basis", "occurred_at", "result",
        })
        self.assertFalse(any(field.is_relation for field in second._meta.fields))

    def test_contract_rejects_invalid_shape_amounts_codes_and_identifiers(self):
        invalid = [
            {"previous_amount": Decimal("10")}, {"previous_revision_id": uuid4()},
            {"previous_revision_id": uuid4(), "previous_amount": Decimal("20.000")},
            {"variant_id": "fake"}, {"new_revision_id": None},
            {"currency": "USD"}, {"currency": "ars"}, {"price_basis": "KG"},
            {"previous_revision_id": "fake", "previous_amount": Decimal("10")},
        ]
        same = uuid4()
        invalid.append({"previous_revision_id": same, "new_revision_id": same, "previous_amount": Decimal("10")})
        for value in (None, 10, True, "1", 1.5, Decimal("0"), Decimal("-1"), Decimal("NaN"),
                      Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1.005"),
                      Decimal("1000000000000")):
            invalid.append({"new_amount": value})
            invalid.append({"previous_revision_id": uuid4(), "previous_amount": value})
        for values in invalid:
            with self.subTest(values=repr(values)), self.assertRaises(ValueError):
                record_variant_price_set(**self.arguments(**values))
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_contract_rejects_invalid_actor_context(self):
        for actor, error in (
            (None, TypeError), ({"actor_id": "fake"}, TypeError),
            (ActorContext(actor_id=" "), ValueError), (ActorContext(actor_id=None), ValueError),
            (ActorContext(actor_id="x" * 129), ValueError),
        ):
            with self.subTest(actor_type=type(actor).__name__), self.assertRaises(error):
                record_variant_price_set(**self.arguments(actor=actor))
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())


class PriceAuditDatabaseTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))

    def values(self, **overrides):
        return {
            "actor_id": "fictional-actor", "action": "catalog.variant_price_set", "variant_id": uuid4(),
            "previous_revision_id": None, "new_revision_id": uuid4(),
            "previous_amount": None, "new_amount": Decimal("20"),
            "currency": "ARS", "price_basis": "PER_VARIANT", "result": "success", **overrides,
        }

    def sql_insert(self, values):
        columns = ("id", "actor_id", "action", "variant_id", "previous_revision_id", "new_revision_id",
                   "previous_amount", "new_amount", "currency", "price_basis", "occurred_at", "result")
        data = {"id": uuid4(), "occurred_at": timezone.now(), **values}
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO audit_catalogpriceauditentry (' + ', '.join(columns) + ') VALUES ('
                + ', '.join(['%s'] * len(columns)) + ')', [data[column] for column in columns],
            )

    def test_postgresql_null_pair_and_distinct_states_matrix_through_orm_and_sql(self):
        valid_count = 0
        for use_sql in (False, True):
            for reference, amount in product(("none", "same", "other"), (None, Decimal("10"), Decimal("20"))):
                values = self.values(previous_amount=amount)
                values["previous_revision_id"] = (
                    None if reference == "none" else values["new_revision_id"] if reference == "same" else uuid4()
                )
                valid = (reference == "none" and amount is None) or (reference == "other" and amount == Decimal("10"))
                with self.subTest(sql=use_sql, reference=reference, amount=amount):
                    before = CatalogPriceAuditEntry.objects.count()
                    if valid:
                        if use_sql:
                            self.sql_insert(values)
                        else:
                            CatalogPriceAuditEntry.objects.create(**values)
                        valid_count += 1
                    else:
                        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
                            if use_sql:
                                self.sql_insert(values)
                            else:
                                CatalogPriceAuditEntry.objects.create(**values)
                        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "audit_price_shape_valid")
                        self.assertEqual(CatalogPriceAuditEntry.objects.count(), before)
        self.assertEqual(valid_count, 4)
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 4)

    def test_database_rejects_invalid_action_result_codes_and_nonpositive_amounts(self):
        cases = [
            ({"action": "catalog.unknown"}, "audit_price_action_valid"),
            ({"result": "failed"}, "audit_price_result_success"),
            ({"result": ""}, "audit_price_result_success"),
            ({"currency": "USD"}, "audit_price_currency_ars"),
            ({"currency": "ars"}, "audit_price_currency_ars"),
            ({"price_basis": "KG"}, "audit_price_basis_valid"),
            ({"price_basis": "per_variant"}, "audit_price_basis_valid"),
        ]
        for amount in (Decimal("0"), Decimal("-0.01")):
            cases.append(({"new_amount": amount}, "audit_price_new_amount_valid"))
            cases.append(({"previous_revision_id": uuid4(), "previous_amount": amount},
                          "audit_price_previous_amount_valid"))
        for overrides, constraint in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError) as failure, transaction.atomic():
                    CatalogPriceAuditEntry.objects.create(**self.values(**overrides))
                self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_direct_sql_nan_cannot_bypass_either_amount_check(self):
        cases = (
            ({"new_amount": "NaN"}, "audit_price_new_amount_valid"),
            ({"previous_revision_id": uuid4(), "previous_amount": "NaN"}, "audit_price_previous_amount_valid"),
        )
        for overrides, constraint in cases:
            with self.subTest(constraint=constraint):
                with self.assertRaises(IntegrityError) as failure, transaction.atomic():
                    self.sql_insert(self.values(**overrides))
                self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)

    def test_new_revision_reference_is_unique_and_required_fields_reject_null(self):
        existing = CatalogPriceAuditEntry.objects.create(**self.values())
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            CatalogPriceAuditEntry.objects.create(**self.values(new_revision_id=existing.new_revision_id))
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "audit_price_new_revision_unique")
        for field in ("actor_id", "action", "variant_id", "new_revision_id", "new_amount", "currency", "price_basis", "result"):
            with self.subTest(field=field), self.assertRaises(IntegrityError), transaction.atomic():
                CatalogPriceAuditEntry.objects.create(**self.values(**{field: None}))
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 1)

    def test_contract_requires_callers_transaction_and_participates_in_its_rollback(self):
        values = self.values()
        values.pop("actor_id")
        values.pop("action")
        values.pop("result")
        values["actor"] = ActorContext(actor_id="fictional-actor")
        self.assertTrue(connection.get_autocommit())
        with self.assertRaises(TransactionManagementError):
            record_variant_price_set(**values)
        with self.assertRaisesRegex(RuntimeError, "rollback requested"):
            with transaction.atomic():
                record_variant_price_set(**values)
                self.assertEqual(CatalogPriceAuditEntry.objects.count(), 1)
                raise RuntimeError("rollback requested")
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_generated_and_installed_shape_constraint_include_explicit_null_guards(self):
        output = StringIO()
        call_command("sqlmigrate", "audit", "0003_catalog_price_audit_entry", stdout=output)
        generated = output.getvalue()
        self.assertIn("numeric(14, 2)", generated)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
                           ["audit_price_shape_valid"])
            installed = cursor.fetchone()[0]
        for sql in (generated, installed):
            self.assertIn("previous_revision_id", sql)
            self.assertIn("previous_amount", sql)
            self.assertIn("IS NULL", sql)
            self.assertIn("IS NOT NULL", sql)
            self.assertIn("new_revision_id", sql)
            self.assertIn("new_amount", sql)
