from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, ROUND_DOWN, Rounded, localcontext
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogPriceAuditEntry
from catalog.application import (
    CatalogValidationError, PriceConflictError, VariantNotFoundError,
    add_variant_to_product, create_product_with_initial_variant, get_current_variant_price,
    get_product, list_variant_price_history, set_variant_price,
)
from catalog.contracts import PriceBasis, PriceSnapshot
from catalog.models import Category, Product, Variant, VariantPriceRevision


class PricingApplicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "fictional-test-password-only"
        cls.user = get_user_model().objects.create_user(username="fictional-pricing-operator", password=cls.password)
        cls.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="catalog"))
        cls.product = Product.objects.create(name="Fictional unclassified product")
        cls.variant = Variant.objects.create(product=cls.product, label="Presentation", sku="FICT-001", barcode="00123")

    def setUp(self):
        self.principal = authenticate(username=self.user.username, password=self.password)
        self.assertIsNotNone(self.principal)

    def set_price(self, amount=Decimal("12.30"), expected=None, variant=None):
        return set_variant_price(
            principal=self.principal, variant_id=(variant or self.variant).id,
            amount=amount, currency="ARS", expected_revision_id=expected,
        )

    def test_first_assignment_returns_immutable_snapshot_and_exact_audit(self):
        before_variant = Variant.objects.values().get(pk=self.variant.pk)
        before_product = Product.objects.values().get(pk=self.product.pk)
        result = self.set_price(Decimal("12.3000"))
        self.assertIsInstance(result, PriceSnapshot)
        self.assertEqual({field.name for field in fields(result)}, {
            "revision_id", "variant_id", "sequence", "amount", "currency", "price_basis", "recorded_at",
        })
        self.assertEqual(result.variant_id, self.variant.id)
        self.assertEqual(result.sequence, 1)
        self.assertEqual(result.amount, Decimal("12.30"))
        self.assertEqual(result.amount.as_tuple().exponent, -2)
        self.assertEqual(result.currency, "ARS")
        self.assertIs(result.price_basis, PriceBasis.PER_VARIANT)
        self.assertIsNotNone(result.recorded_at.utcoffset())
        with self.assertRaises(FrozenInstanceError):
            result.amount = Decimal("99.99")
        entry = CatalogPriceAuditEntry.objects.get()
        self.assertEqual(entry.actor_id, str(self.user.pk))
        self.assertEqual(entry.action, "catalog.variant_price_set")
        self.assertEqual(entry.variant_id, self.variant.id)
        self.assertIsNone(entry.previous_revision_id)
        self.assertIsNone(entry.previous_amount)
        self.assertEqual(entry.new_revision_id, result.revision_id)
        self.assertEqual(entry.new_amount, result.amount)
        self.assertEqual(entry.currency, result.currency)
        self.assertEqual(entry.price_basis, result.price_basis.value)
        self.assertEqual(entry.result, "success")
        self.assertEqual(Variant.objects.values().get(pk=self.variant.pk), before_variant)
        self.assertEqual(Product.objects.values().get(pk=self.product.pk), before_product)
        self.assertFalse(CatalogAuditEntry.objects.exists())
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_missing_and_unpriced_variants_are_distinct(self):
        self.assertIsNone(get_current_variant_price(principal=self.principal, variant_id=self.variant.id))
        self.assertEqual(list_variant_price_history(principal=self.principal, variant_id=self.variant.id), ())
        for read in (get_current_variant_price, list_variant_price_history):
            with self.subTest(read=read.__name__), self.assertRaises(VariantNotFoundError):
                read(principal=self.principal, variant_id=uuid4())
        with self.assertRaises(VariantNotFoundError):
            set_variant_price(principal=self.principal, variant_id=uuid4(), amount=Decimal("1"),
                              currency="ARS", expected_revision_id=None)
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_history_preserves_earlier_values_and_return_to_an_older_amount_is_new(self):
        first = self.set_price(Decimal("10"))
        original = VariantPriceRevision.objects.values().get(pk=first.revision_id)
        second = self.set_price(Decimal("20"), expected=first.revision_id)
        third = self.set_price(Decimal("10"), expected=second.revision_id)
        self.assertEqual([first.sequence, second.sequence, third.sequence], [1, 2, 3])
        self.assertNotEqual(first.revision_id, third.revision_id)
        self.assertEqual(VariantPriceRevision.objects.values().get(pk=first.revision_id), original)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), third)
        self.assertEqual(list_variant_price_history(principal=self.principal, variant_id=self.variant.id),
                         (third, second, first))
        entries = list(CatalogPriceAuditEntry.objects.order_by("occurred_at"))
        self.assertEqual([(entry.previous_amount, entry.new_amount) for entry in entries],
                         [(None, Decimal("10")), (Decimal("10"), Decimal("20")), (Decimal("20"), Decimal("10"))])
        self.assertEqual([(entry.previous_revision_id, entry.new_revision_id) for entry in entries],
                         [(None, first.revision_id), (first.revision_id, second.revision_id),
                          (second.revision_id, third.revision_id)])

    def test_sequence_determines_current_and_history_when_recording_clock_repeats_or_moves_back(self):
        same = datetime(2026, 1, 2, tzinfo=timezone.utc)
        earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("django.utils.timezone.now", return_value=same):
            first = self.set_price(Decimal("1"))
            second = self.set_price(Decimal("2"), expected=first.revision_id)
        with patch("django.utils.timezone.now", return_value=earlier):
            third = self.set_price(Decimal("3"), expected=second.revision_id)
        self.assertEqual(first.recorded_at, second.recorded_at)
        self.assertLess(third.recorded_at, second.recorded_at)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), third)
        self.assertEqual(list_variant_price_history(principal=self.principal, variant_id=self.variant.id),
                         (third, second, first))

    def test_matching_noop_preserves_revision_timestamps_and_audit(self):
        first = self.set_price()
        before = list(VariantPriceRevision.objects.values())
        before_audit = list(CatalogPriceAuditEntry.objects.values())
        self.assertEqual(self.set_price(Decimal("12.30000"), expected=first.revision_id), first)
        self.assertEqual(list(VariantPriceRevision.objects.values()), before)
        self.assertEqual(list(CatalogPriceAuditEntry.objects.values()), before_audit)

    def test_committed_retries_conflict_even_when_amount_matches_current(self):
        first = self.set_price()
        with self.assertRaises(PriceConflictError):
            self.set_price(expected=None)
        second = self.set_price(Decimal("20"), expected=first.revision_id)
        for stale in (None, first.revision_id, uuid4()):
            with self.subTest(stale=stale), self.assertRaises(PriceConflictError):
                self.set_price(Decimal("20.000"), expected=stale)
        self.assertEqual(VariantPriceRevision.objects.count(), 2)
        self.assertEqual(CatalogPriceAuditEntry.objects.count(), 2)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), second)

    def test_rejects_invalid_money_without_any_revision_or_audit(self):
        values = (None, True, False, 1, 1.25, "1.25", Decimal("NaN"), Decimal("sNaN"),
                  Decimal("Infinity"), Decimal("-Infinity"), Decimal("0"), Decimal("-0"),
                  Decimal("-0.01"), Decimal("1000000000000"), Decimal("999999999999.991"),
                  Decimal("0.001"), Decimal("1.005"))
        for value in values:
            with self.subTest(value=repr(value)), self.assertRaises(CatalogValidationError):
                self.set_price(value)
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_accepts_bounds_and_ignores_ambient_decimal_precision_rounding_and_traps(self):
        with localcontext() as context:
            context.prec = 1
            context.Emax, context.Emin = 2, -2
            context.rounding = ROUND_DOWN
            for signal in (Inexact, InvalidOperation, Rounded):
                context.traps[signal] = True
            first = self.set_price(Decimal("0.01000"))
            second = self.set_price(Decimal("999999999999.9900"), expected=first.revision_id)
            with self.assertRaises(CatalogValidationError):
                self.set_price(Decimal("1.005"), expected=second.revision_id)
        self.assertEqual(first.amount, Decimal("0.01"))
        self.assertEqual(second.amount, Decimal("999999999999.99"))
        self.assertEqual(VariantPriceRevision.objects.count(), 2)

    def test_rejects_other_currency_codes_and_invalid_uuid_arguments(self):
        for currency in ("USD", "ars", " ARS ", "", None):
            with self.subTest(currency=currency), self.assertRaises(CatalogValidationError):
                set_variant_price(principal=self.principal, variant_id=self.variant.id,
                                  amount=Decimal("1"), currency=currency, expected_revision_id=None)
        for value in (str(self.variant.id), None, 1):
            with self.subTest(variant_id=value):
                for read in (get_current_variant_price, list_variant_price_history):
                    with self.assertRaises(CatalogValidationError):
                        read(principal=self.principal, variant_id=value)
                with self.assertRaises(CatalogValidationError):
                    set_variant_price(principal=self.principal, variant_id=value, amount=Decimal("1"),
                                      currency="ARS", expected_revision_id=None)
        for expected in (str(uuid4()), False, 0):
            with self.assertRaises(CatalogValidationError):
                self.set_price(expected=expected)
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_each_presentation_has_independent_prices_without_product_fallback(self):
        sibling = Variant.objects.create(product=self.product, label="Other", sku="FICT-002")
        first = self.set_price()
        self.assertIsNone(get_current_variant_price(principal=self.principal, variant_id=sibling.id))
        self.assertEqual(list_variant_price_history(principal=self.principal, variant_id=sibling.id), ())
        second = self.set_price(Decimal("40"), variant=sibling)
        self.assertEqual(second.sequence, 1)
        self.assertEqual(get_current_variant_price(principal=self.principal, variant_id=self.variant.id), first)
        self.assertEqual(list_variant_price_history(principal=self.principal, variant_id=sibling.id), (second,))

    def test_active_product_can_remain_unpriced_and_pricing_does_not_change_state(self):
        category = Category.objects.create(name="Fictional", name_key="fictional")
        self.product.category = category
        self.product.is_active = True
        self.product.save()
        before = Product.objects.values().get(pk=self.product.pk)
        self.assertIsNone(get_current_variant_price(principal=self.principal, variant_id=self.variant.id))
        self.set_price()
        self.assertEqual(Product.objects.values().get(pk=self.product.pk), before)

    def test_existing_creation_and_reads_do_not_assign_price(self):
        product = create_product_with_initial_variant(
            principal=self.principal, product_name="Fictional new product", variant_label="First", sku="NEW-001"
        )
        second = add_variant_to_product(principal=self.principal, product_id=product.id, label="Second", sku="NEW-002")
        self.assertEqual(len(list(get_product(principal=self.principal, product_id=product.id).variants.all())), 2)
        self.assertIsNone(get_current_variant_price(principal=self.principal, variant_id=second.id))
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_expectation_is_required_and_scheduling_or_withdrawal_arguments_are_not_supported(self):
        arguments = dict(principal=self.principal, variant_id=self.variant.id, amount=Decimal("1"), currency="ARS")
        with self.assertRaises(TypeError):
            set_variant_price(**arguments)
        for keyword in ("valid_from", "valid_to", "recorded_at", "price_basis", "withdraw"):
            with self.subTest(keyword=keyword), self.assertRaises(TypeError):
                set_variant_price(**arguments, expected_revision_id=None, **{keyword: None})
        self.assertFalse(VariantPriceRevision.objects.exists())
