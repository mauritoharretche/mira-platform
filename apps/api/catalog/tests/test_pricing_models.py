from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.db import DataError, IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.utils import timezone

from catalog.models import Product, Variant, VariantPriceRevision


class PriceRevisionDatabaseTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        self.product = Product.objects.create(name="Fictional product")
        self.variant = Variant.objects.create(product=self.product, label="Presentation", sku="FICT-001")

    def values(self, **overrides):
        return dict(variant=self.variant, sequence=1, amount=Decimal("10.00"),
                    currency="ARS", price_basis="PER_VARIANT", **overrides)

    def assert_rejected(self, overrides, constraint):
        values = self.values()
        values.update(overrides)
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            VariantPriceRevision.objects.create(**values)
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, constraint)

    def sql_insert(self, amount):
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO catalog_variantpricerevision '
                '(id, variant_id, sequence, amount, currency, price_basis, recorded_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING amount',
                [uuid4(), self.variant.id, 1, amount, "ARS", "PER_VARIANT", timezone.now()],
            )
            return cursor.fetchone()[0]

    def test_unique_sequence_is_per_variant_and_zero_is_rejected(self):
        VariantPriceRevision.objects.create(**self.values())
        self.assert_rejected({}, "catalog_price_variant_sequence_unique")
        self.assert_rejected({"sequence": 0}, "catalog_price_sequence_positive")
        sibling = Variant.objects.create(product=self.product, label="Sibling", sku="FICT-002")
        values = self.values()
        values["variant"] = sibling
        VariantPriceRevision.objects.create(**values)
        self.assertEqual(VariantPriceRevision.objects.count(), 2)

    def test_positive_bounds_currency_and_basis_are_database_constraints(self):
        for amount in (Decimal("0"), Decimal("-0.01")):
            self.assert_rejected({"amount": amount}, "catalog_price_amount_valid")
        for currency in ("USD", "ars", ""):
            self.assert_rejected({"currency": currency}, "catalog_price_currency_ars")
        for basis in ("KG", "per_variant", ""):
            self.assert_rejected({"price_basis": basis}, "catalog_price_basis_valid")
        for sequence, amount in enumerate((Decimal("0.01"), Decimal("999999999999.99")), start=1):
            values = self.values()
            values.update(sequence=sequence, amount=amount)
            VariantPriceRevision.objects.create(**values)
        self.assertEqual(VariantPriceRevision.objects.count(), 2)

    def test_postgresql_rejects_nan_and_infinities_or_overflow_without_application_validation(self):
        with self.assertRaises(IntegrityError) as failure, transaction.atomic():
            self.sql_insert("NaN")
        self.assertEqual(failure.exception.__cause__.diag.constraint_name, "catalog_price_amount_valid")
        for amount in ("Infinity", "-Infinity", "1000000000000"):
            with self.subTest(amount=amount), self.assertRaises(DataError), transaction.atomic():
                self.sql_insert(amount)
        self.assertFalse(VariantPriceRevision.objects.exists())

    def test_numeric_coercion_can_round_direct_sql_and_is_not_the_application_scale_policy(self):
        self.assertEqual(self.sql_insert(Decimal("1.005")), Decimal("1.01"))
        self.assertEqual(VariantPriceRevision.objects.get().amount, Decimal("1.01"))

    def test_required_fields_and_foreign_key_are_enforced(self):
        for field in ("variant", "sequence", "amount", "currency", "price_basis"):
            values = self.values()
            values[field] = None
            with self.subTest(field=field), self.assertRaises(IntegrityError), transaction.atomic():
                VariantPriceRevision.objects.create(**values)
        values = self.values()
        del values["variant"]
        values["variant_id"] = uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            VariantPriceRevision.objects.create(**values)
        self.assertFalse(VariantPriceRevision.objects.exists())

    def test_revisions_protect_their_variant_from_deletion(self):
        VariantPriceRevision.objects.create(**self.values())
        with self.assertRaises(ProtectedError):
            self.variant.delete()

    def test_generated_sql_has_numeric_type_collations_and_required_constraints(self):
        output = StringIO()
        call_command("sqlmigrate", "catalog", "0003_variant_price_revision", stdout=output)
        sql = output.getvalue()
        self.assertIn("numeric(14, 2)", sql)
        self.assertIn('COLLATE "C"', sql)
        for name in ("catalog_price_variant_sequence_unique", "catalog_price_sequence_positive",
                     "catalog_price_amount_valid", "catalog_price_currency_ars", "catalog_price_basis_valid"):
            self.assertIn(name, sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass",
                           ["catalog_variantpricerevision"])
            self.assertIn("catalog_price_amount_valid", {row[0] for row in cursor.fetchall()})
