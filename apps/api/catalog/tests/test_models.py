from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase

from catalog.models import Product, Variant


class CatalogDatabaseConstraintTests(TransactionTestCase):
    def create_product(self, name: str) -> Product:
        return Product.objects.create(name=name)

    def create_variant(
        self, *, product: Product, sku: str, barcode: str | None = None
    ) -> Variant:
        return Variant.objects.create(
            product=product,
            label=f"Label for {sku}",
            sku=sku,
            barcode=barcode,
        )

    def test_database_rejects_exact_duplicate_sku(self) -> None:
        self.create_variant(product=self.create_product("One"), sku="ABC-01")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_variant(product=self.create_product("Two"), sku="ABC-01")

    def test_database_rejects_exact_duplicate_barcode(self) -> None:
        self.create_variant(product=self.create_product("One"), sku="SKU-001", barcode="00123")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_variant(product=self.create_product("Two"), sku="SKU-002", barcode="00123")

    def test_database_allows_case_distinct_skus(self) -> None:
        self.create_variant(product=self.create_product("One"), sku="ABC-01")
        self.create_variant(product=self.create_product("Two"), sku="abc-01")

        self.assertEqual(Variant.objects.count(), 2)

    def test_database_allows_case_distinct_barcodes(self) -> None:
        self.create_variant(product=self.create_product("One"), sku="SKU-001", barcode="ABC-01")
        self.create_variant(product=self.create_product("Two"), sku="SKU-002", barcode="abc-01")

        self.assertEqual(Variant.objects.count(), 2)

    def test_database_allows_multiple_null_barcodes(self) -> None:
        self.create_variant(product=self.create_product("One"), sku="SKU-001")
        self.create_variant(product=self.create_product("Two"), sku="SKU-002")

        self.assertEqual(Variant.objects.filter(barcode__isnull=True).count(), 2)

    def test_database_rejects_variant_without_product(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Variant.objects.create(label="Orphan variant", sku="SKU-001")

    def test_database_protects_product_with_variants_from_deletion(self) -> None:
        product = self.create_product("Protected product")
        self.create_variant(product=product, sku="SKU-001")

        with self.assertRaises(ProtectedError):
            product.delete()
