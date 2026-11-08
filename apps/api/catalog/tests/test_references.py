from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalog.models import Product, Variant
from catalog.references import variant_exists


class VariantReferenceTests(TestCase):
    def test_reference_exposes_only_existence_without_changing_catalog(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        product = Product.objects.create(name="Fictional reference product")
        variant = Variant.objects.create(product=product, label="Fictional", sku="REF-EACH-001")
        self.assertIs(variant_exists(variant_id=variant.pk), True)
        self.assertIs(variant_exists(variant_id=uuid4()), False)
        self.assertEqual(Variant.objects.count(), 1)
        self.assertFalse(Product.objects.get(pk=product.pk).is_active)

    def test_invalid_identifier_is_rejected_before_any_query(self):
        for value in (None, "not-a-uuid", 1, True):
            with self.subTest(value=value), CaptureQueriesContext(connection) as queries:
                with self.assertRaises(ValueError):
                    variant_exists(variant_id=value)
            self.assertEqual(len(queries), 0)
