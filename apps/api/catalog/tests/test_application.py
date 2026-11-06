from uuid import uuid4

from django.db import IntegrityError
from django.test import TestCase

from catalog.application import (
    CatalogValidationError,
    ProductNotFoundError,
    add_variant_to_product,
    create_product_with_initial_variant,
    get_product,
)
from catalog.models import Product, Variant


class CatalogApplicationTests(TestCase):
    def test_creates_product_with_normalized_initial_variant(self) -> None:
        product = create_product_with_initial_variant(
            product_name="  Trail Mix  ",
            variant_label="  Small pack  ",
            sku="  ABC-01  ",
            barcode="  00123  ",
        )

        variant = product.variants.get()
        self.assertEqual(product.name, "Trail Mix")
        self.assertEqual(variant.label, "Small pack")
        self.assertEqual(variant.sku, "ABC-01")
        self.assertEqual(variant.barcode, "00123")
        self.assertIsNotNone(product.created_at)
        self.assertIsNotNone(product.updated_at)
        self.assertIsNotNone(variant.created_at)
        self.assertIsNotNone(variant.updated_at)

    def test_adds_variant_and_gets_product_with_variants(self) -> None:
        product = create_product_with_initial_variant(
            product_name="Fictional cereal",
            variant_label="Standard",
            sku="CAT-001",
        )
        added_variant = add_variant_to_product(
            product_id=product.id,
            label="Large",
            sku="CAT-002",
        )

        loaded_product = get_product(product_id=product.id)

        self.assertEqual(added_variant.product_id, product.id)
        self.assertEqual({variant.sku for variant in loaded_product.variants.all()}, {"CAT-001", "CAT-002"})

    def test_rejects_unknown_product_when_adding_variant(self) -> None:
        with self.assertRaises(ProductNotFoundError):
            add_variant_to_product(
                product_id=uuid4(),
                label="Fictional variant",
                sku="CAT-001",
            )

    def test_rejects_empty_required_values_after_normalization(self) -> None:
        for arguments in (
            {"product_name": "   ", "variant_label": "Variant", "sku": "CAT-001"},
            {"product_name": "Product", "variant_label": "   ", "sku": "CAT-001"},
            {"product_name": "Product", "variant_label": "Variant", "sku": "   "},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(CatalogValidationError):
                    create_product_with_initial_variant(**arguments)

    def test_normalizes_barcodes_without_changing_case(self) -> None:
        product = create_product_with_initial_variant(
            product_name="Fictional product",
            variant_label="Fictional label",
            sku="AbC-01",
            barcode=" AbC-01 ",
        )
        product_with_empty_barcode = create_product_with_initial_variant(
            product_name="Another fictional product",
            variant_label="Another fictional label",
            sku="AbC-02",
            barcode="   ",
        )

        variant = product.variants.get()
        variant_with_empty_barcode = product_with_empty_barcode.variants.get()
        self.assertEqual(variant.sku, "AbC-01")
        self.assertEqual(variant.barcode, "AbC-01")
        self.assertIsNone(variant_with_empty_barcode.barcode)

    def test_rolls_back_product_when_initial_variant_violates_sku_constraint(self) -> None:
        create_product_with_initial_variant(
            product_name="Existing product",
            variant_label="Existing variant",
            sku="CAT-001",
        )

        with self.assertRaises(IntegrityError):
            create_product_with_initial_variant(
                product_name="Partial product",
                variant_label="Conflicting variant",
                sku="CAT-001",
            )

        self.assertFalse(Product.objects.filter(name="Partial product").exists())
        self.assertEqual(Variant.objects.count(), 1)
