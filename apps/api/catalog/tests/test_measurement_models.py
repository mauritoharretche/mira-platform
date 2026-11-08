from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.utils.timezone import now

from catalog.models import Product, Variant, VariantMeasurementSpec


class MeasurementPostgreSQLTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_setting('server_version')")
            database, version = cursor.fetchone()
        self.assertEqual(database, "test_mira")
        self.assertEqual(version.split()[0], "18.6")
        product = Product.objects.create(name="Fictional constraint product")
        self.variant = Variant.objects.create(product=product, label="Presentation", sku="DB-MEASURE-001")

    def insert(self, **overrides):
        values = dict(variant_id=self.variant.id, version=1, sale_unit=None,
                      net_content_amount=None, net_content_unit=None)
        values.update(overrides)
        return VariantMeasurementSpec.objects.create(**values)

    def assert_constraint(self, expected, **overrides):
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert(**overrides)
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, expected)
        self.assertFalse(VariantMeasurementSpec.objects.exists())

    def raw_amount(self, value):
        identifier = uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO catalog_variantmeasurementspec '
                '(id, variant_id, version, sale_unit, net_content_amount, net_content_unit, created_at, updated_at) '
                'VALUES (%s, %s, %s, %s, %s::numeric, %s, %s, %s)',
                [identifier, self.variant.id, 1, None, value, "G", now(), now()],
            )
        return identifier

    def test_nullable_states_and_independent_sale_unit_are_valid(self):
        for sale in (None, "EACH", "G", "KG"):
            for amount, unit in ((None, None), (Decimal("1"), "G"), (Decimal("0.001"), "KG")):
                with self.subTest(sale=sale, unit=unit), transaction.atomic():
                    spec = self.insert(sale_unit=sale, net_content_amount=amount, net_content_unit=unit)
                    self.assertEqual(spec.sale_unit, sale)
                    # Normal test transaction rollback, not a manual data reset.
                    transaction.set_rollback(True)

    def test_version_must_be_positive_in_postgresql(self):
        self.assert_constraint("catalog_measurement_version_positive", version=0)

    def test_closed_sale_and_net_unit_codes_are_case_sensitive(self):
        for sale in ("", "g", "kg", "UNIT", "ML"):
            with self.subTest(sale=sale):
                self.assert_constraint("catalog_measurement_sale_unit_valid", sale_unit=sale)
        for unit in ("EACH", "g", "kg", "", "ML"):
            with self.subTest(unit=unit):
                self.assert_constraint("catalog_measurement_net_valid", net_content_amount=Decimal("1"), net_content_unit=unit)

    def test_null_pair_guards_reject_unknown_check_results(self):
        self.assert_constraint("catalog_measurement_net_valid", net_content_amount=Decimal("1"))
        self.assert_constraint("catalog_measurement_net_valid", net_content_unit="G")

    def test_amount_bounds_are_enforced_without_full_clean(self):
        for amount in (Decimal("0"), Decimal("-1")):
            with self.subTest(amount=amount):
                self.assert_constraint("catalog_measurement_net_valid", net_content_amount=amount, net_content_unit="G")
        for amount in (Decimal("0.000001"), Decimal("999999999999.999999")):
            with self.subTest(amount=amount), transaction.atomic():
                spec = self.insert(net_content_amount=amount, net_content_unit="G")
                spec.refresh_from_db()
                self.assertEqual(spec.net_content_amount, amount)
                transaction.set_rollback(True)

    def test_raw_sql_rejects_nan_infinities_and_numeric_overflow(self):
        for value in ("NaN", "Infinity", "-Infinity", "1000000000000"):
            with self.subTest(value=value):
                # NUMERIC itself rejects overflow/infinity for this typmod;
                # the CHECK must also reject PostgreSQL's representable NaN.
                from django.db import DatabaseError
                with self.assertRaises(DatabaseError) as failure, transaction.atomic():
                    self.raw_amount(value)
                if value == "NaN":
                    self.assertEqual(failure.exception.__cause__.diag.constraint_name, "catalog_measurement_net_valid")
                self.assertFalse(VariantMeasurementSpec.objects.exists())

    def test_direct_sql_scaled_numeric_rounds_bypassed_overprecision(self):
        identifier = self.raw_amount("1.0000004")
        self.assertEqual(VariantMeasurementSpec.objects.get(pk=identifier).net_content_amount, Decimal("1.000000"))
        # Exact-precision rejection is the application contract, not a NUMERIC claim.

    def test_variant_is_required_and_missing_foreign_key_cannot_commit(self):
        for identifier in (None, uuid4()):
            with self.subTest(identifier=identifier):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.insert(variant_id=identifier)
                self.assertFalse(VariantMeasurementSpec.objects.exists())

    def test_one_spec_per_variant_and_protected_variant_deletion(self):
        spec = self.insert()
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.insert()
        self.assertIn("variant_id", failure.exception.__cause__.diag.constraint_name)
        with self.assertRaises(ProtectedError):
            self.variant.delete()
        self.assertTrue(VariantMeasurementSpec.objects.filter(pk=spec.pk).exists())

    def test_generated_sql_and_installed_constraints_preserve_null_guards_and_collation(self):
        output = StringIO()
        call_command("sqlmigrate", "catalog", "0004_variant_measurement_spec", stdout=output)
        sql = output.getvalue()
        self.assertIn("numeric(18, 6)", sql)
        self.assertIn('COLLATE "C"', sql)
        self.assertIn('"net_content_amount" IS NOT NULL', sql)
        self.assertIn('"net_content_unit" IS NOT NULL', sql)
        self.assertIn('"net_content_amount" IS NULL', sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass",
                           ["catalog_variantmeasurementspec"])
            constraints = dict(cursor.fetchall())
        net = constraints["catalog_measurement_net_valid"]
        self.assertIn("net_content_amount IS NOT NULL", net)
        self.assertIn("net_content_unit IS NOT NULL", net)
        self.assertIn("net_content_amount IS NULL", net)
        self.assertIn("net_content_unit IS NULL", net)
