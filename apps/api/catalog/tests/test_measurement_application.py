from dataclasses import FrozenInstanceError, fields
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils.timezone import is_aware

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogMeasurementAuditEntry, CatalogPriceAuditEntry
from catalog.application import (
    CatalogValidationError, MeasurementSpecConflictError, VariantNotFoundError,
    get_variant_measurement_spec, set_variant_measurement_spec, set_variant_price,
)
from catalog.contracts import MeasurementUnit, PriceBasis, PriceSnapshot, VariantMeasurementSpecSnapshot
from catalog.models import Product, Variant, VariantMeasurementSpec, VariantPriceRevision


class MeasurementApplicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(username="fictional-measurement-editor", password=password)
        cls.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        cls.principal = authenticate(username=cls.user.username, password=password)
        cls.product = Product.objects.create(name="Fictional product")
        cls.variant = Variant.objects.create(product=cls.product, label="Fictional presentation", sku="MEASURE-001")

    def write(self, *, sale=MeasurementUnit.EACH, amount=None, unit=None, expected=None, **overrides):
        args = dict(principal=self.principal, variant_id=self.variant.id, sale_unit=sale,
                    net_content_amount=amount, net_content_unit=unit, expected_version=expected)
        args.update(overrides)
        return set_variant_measurement_spec(**args)

    def read(self):
        return get_variant_measurement_spec(principal=self.principal, variant_id=self.variant.id)

    def test_missing_and_initial_unknown_are_absent_without_fabricated_audit(self):
        self.assertIsNone(self.read())
        self.assertIsNone(self.write(sale=None))
        self.assertFalse(VariantMeasurementSpec.objects.exists())
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_representative_declarations_keep_sale_unit_and_net_content_independent(self):
        states = ((MeasurementUnit.EACH, Decimal("250"), MeasurementUnit.G),
                  (MeasurementUnit.G, None, None), (MeasurementUnit.KG, None, None),
                  (MeasurementUnit.EACH, None, None), (None, Decimal("0.25"), MeasurementUnit.KG))
        expected = None
        for sale, amount, unit in states:
            snapshot = self.write(sale=sale, amount=amount, unit=unit, expected=expected)
            self.assertEqual((snapshot.sale_unit, snapshot.net_content_amount, snapshot.net_content_unit),
                             (sale, amount, unit))
            self.assertEqual(self.read(), snapshot)
            expected = snapshot.version
        sibling = Variant.objects.create(product=self.product, label="Sibling", sku="MEASURE-002")
        self.assertIsNone(get_variant_measurement_spec(principal=self.principal, variant_id=sibling.id))

    def test_clear_retains_identity_and_is_distinct_from_missing(self):
        first = self.write(amount=Decimal("250"), unit=MeasurementUnit.G)
        cleared = self.write(sale=None, expected=first.version)
        self.assertEqual(cleared.spec_id, first.spec_id)
        self.assertEqual(cleared.created_at, first.created_at)
        self.assertEqual(cleared.version, 2)
        self.assertEqual((cleared.sale_unit, cleared.net_content_amount, cleared.net_content_unit), (None, None, None))
        self.assertEqual(self.read(), cleared)
        self.assertEqual(VariantMeasurementSpec.objects.count(), 1)
        with self.assertRaises(MeasurementSpecConflictError):
            self.write(sale=None, expected=None)
        self.assertEqual(self.write(sale=None, expected=2), cleared)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 2)

    def test_noop_preserves_version_timestamps_and_audit_after_exact_normalization(self):
        first = self.write(amount=Decimal("250.0000000"), unit=MeasurementUnit.G)
        self.assertEqual(first.net_content_amount.as_tuple().exponent, -6)
        result = self.write(amount=Decimal("250"), unit=MeasurementUnit.G, expected=1)
        self.assertEqual(first, result)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 1)

    def test_stale_expectations_conflict_even_when_submitted_state_matches(self):
        first = self.write()
        for expected in (None, 2):
            with self.assertRaises(MeasurementSpecConflictError):
                self.write(expected=expected)
        second = self.write(sale=MeasurementUnit.KG, expected=first.version)
        with self.assertRaises(MeasurementSpecConflictError):
            self.write(sale=MeasurementUnit.KG, expected=first.version)
        self.assertEqual(self.read(), second)
        self.assertEqual(CatalogMeasurementAuditEntry.objects.count(), 2)

    def test_snapshot_is_frozen_and_earlier_values_do_not_change(self):
        first = self.write()
        self.assertIsInstance(first, VariantMeasurementSpecSnapshot)
        self.assertEqual({f.name for f in fields(first)}, {
            "spec_id", "variant_id", "version", "sale_unit", "net_content_amount",
            "net_content_unit", "created_at", "updated_at",
        })
        self.assertTrue(is_aware(first.created_at))
        self.assertTrue(is_aware(first.updated_at))
        with self.assertRaises(FrozenInstanceError):
            first.version = 99
        self.write(sale=MeasurementUnit.G, expected=1)
        self.assertEqual(first.sale_unit, MeasurementUnit.EACH)
        self.assertEqual(first.version, 1)

    def test_net_content_pair_rejects_partial_values_and_non_mass_units(self):
        for amount, unit in ((Decimal("1"), None), (None, MeasurementUnit.G),
                             (Decimal("1"), MeasurementUnit.EACH), (Decimal("1"), "G"),
                             (Decimal("1"), "ML")):
            with self.subTest(amount=amount, unit=unit), self.assertRaises(CatalogValidationError):
                self.write(amount=amount, unit=unit)
        self.assertFalse(VariantMeasurementSpec.objects.exists())
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_net_content_rejects_non_decimal_nonfinite_nonpositive_and_rounding(self):
        invalid = (True, False, 1, 1.0, "1", object(), Decimal("NaN"), Decimal("sNaN"),
                   Decimal("Infinity"), Decimal("-Infinity"), Decimal("0"), Decimal("-1"),
                   Decimal("1000000000000"), Decimal("1.0000001"), Decimal("0.0000001"))
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(CatalogValidationError):
                self.write(amount=value, unit=MeasurementUnit.G)
        self.assertFalse(VariantMeasurementSpec.objects.exists())

    def test_exact_bounds_and_trailing_zeroes_with_hostile_ambient_decimal_context(self):
        expected = None
        for value in (Decimal("0.000001"), Decimal("999999999999.999999"), Decimal("1.200000000")):
            with localcontext() as context:
                context.prec, context.Emax, context.Emin = 1, 2, -2
                context.traps[Inexact] = context.traps[Rounded] = True
                result = self.write(amount=value, unit=MeasurementUnit.G, expected=expected)
            self.assertEqual(result.net_content_amount, value)
            expected = result.version

    def test_sale_units_and_versions_require_the_closed_types(self):
        for value in ("EACH", "G", "KG", "", 1, True, object()):
            with self.subTest(sale=value), self.assertRaises(CatalogValidationError):
                self.write(sale=value)
        for value in (True, False, 0, -1, "1", Decimal("1"), uuid4()):
            with self.subTest(version=value), self.assertRaises(CatalogValidationError):
                self.write(expected=value)

    def test_missing_variant_and_invalid_uuid_are_not_absent_specifications(self):
        for operation in (lambda value: self.write(variant_id=value),
                          lambda value: get_variant_measurement_spec(principal=self.principal, variant_id=value)):
            with self.assertRaises(VariantNotFoundError):
                operation(uuid4())
            for value in (str(self.variant.pk), None, 1):
                with self.subTest(value=value), self.assertRaises(CatalogValidationError):
                    operation(value)

    def test_full_replacement_requires_all_fields_and_none_clears_them(self):
        args = dict(principal=self.principal, variant_id=self.variant.pk, sale_unit=MeasurementUnit.EACH,
                    net_content_amount=None, net_content_unit=None, expected_version=None)
        for field in ("sale_unit", "net_content_amount", "net_content_unit", "expected_version"):
            incomplete = dict(args)
            del incomplete[field]
            with self.subTest(field=field), self.assertRaises(TypeError):
                set_variant_measurement_spec(**incomplete)
        self.write(amount=Decimal("250"), unit=MeasurementUnit.G)
        result = self.write(sale=None, amount=Decimal("0.250"), unit=MeasurementUnit.KG, expected=1)
        self.assertEqual((result.sale_unit, result.net_content_amount, result.net_content_unit),
                         (None, Decimal("0.250"), MeasurementUnit.KG))
        entry = CatalogMeasurementAuditEntry.objects.get(new_version=2)
        self.assertEqual((entry.previous_net_content_amount, entry.previous_net_content_unit), (Decimal("250"), "G"))
        self.assertEqual((entry.new_net_content_amount, entry.new_net_content_unit), (Decimal("0.250"), "KG"))

    def test_measurement_changes_leave_identity_activation_pricing_and_old_audits_untouched(self):
        price = set_variant_price(principal=self.principal, variant_id=self.variant.id,
                                  amount=Decimal("12.34"), currency="ARS", expected_revision_id=None)
        models = (Product, Variant, VariantPriceRevision, CatalogAuditEntry, CatalogChangeAuditEntry, CatalogPriceAuditEntry)
        before = [list(model.objects.values()) for model in models]
        self.write(sale=MeasurementUnit.KG, amount=Decimal("250"), unit=MeasurementUnit.G)
        self.write(sale=None, expected=1)
        self.assertEqual([list(model.objects.values()) for model in models], before)
        self.assertEqual(price.price_basis, PriceBasis.PER_VARIANT)
        self.assertEqual({f.name for f in fields(PriceSnapshot)}, {
            "revision_id", "variant_id", "sequence", "amount", "currency", "price_basis", "recorded_at",
        })
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertIsNone(self.product.category_id)
        self.assertEqual({unit.value for unit in MeasurementUnit}, {"EACH", "G", "KG"})
