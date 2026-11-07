from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogPriceAuditEntry
from catalog.models import Product, Variant, VariantPriceRevision


class PricingSchemaCompatibilityTests(TestCase):
    def test_phase_19_models_and_audits_still_work_on_expanded_schema_without_fabricated_prices(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        state = MigrationLoader(connection).project_state([
            ("catalog", "0002_categories_and_product_activation"),
            ("audit", "0002_catalog_change_audit_entry"),
        ])
        OldCategory = state.apps.get_model("catalog", "Category")
        OldProduct = state.apps.get_model("catalog", "Product")
        OldVariant = state.apps.get_model("catalog", "Variant")
        OldAudit = state.apps.get_model("audit", "CatalogAuditEntry")
        OldChangeAudit = state.apps.get_model("audit", "CatalogChangeAuditEntry")
        category = OldCategory.objects.create(name="Fictional category", name_key="fictional category")
        product = OldProduct.objects.create(name="Fictional legacy product", category=category, is_active=True)
        variant = OldVariant.objects.create(product=product, label="Legacy presentation", sku="Legacy-AbC", barcode="00123")
        audit = OldAudit.objects.create(
            actor_id="fictional-legacy-actor", action="catalog.variant_added_to_product",
            product_id=product.id, variant_id=variant.id,
        )
        change = OldChangeAudit.objects.create(
            actor_id="fictional-legacy-actor", action="catalog.product_active_changed",
            target_id=product.id, previous_is_active=False, new_is_active=True,
        )
        for old, model in ((product, Product), (variant, Variant), (audit, CatalogAuditEntry),
                           (change, CatalogChangeAuditEntry)):
            current = model.objects.get(pk=old.pk)
            self.assertEqual({field.name for field in old._meta.fields}, {field.name for field in current._meta.fields})
            for field in old._meta.fields:
                self.assertEqual(getattr(current, field.attname), getattr(old, field.attname))
        self.assertTrue(Product.objects.get(pk=product.id).is_active)
        self.assertEqual(Variant.objects.get(pk=variant.id).barcode, "00123")
        self.assertFalse(VariantPriceRevision.objects.exists())
        self.assertFalse(CatalogPriceAuditEntry.objects.exists())

    def test_new_migrations_and_numeric_column_are_installed_only_in_the_test_database(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
            cursor.execute("SELECT app, name FROM django_migrations WHERE (app = %s AND name = %s) "
                           "OR (app = %s AND name = %s)",
                           ["catalog", "0003_variant_price_revision", "audit", "0003_catalog_price_audit_entry"])
            self.assertEqual(set(cursor.fetchall()), {
                ("catalog", "0003_variant_price_revision"), ("audit", "0003_catalog_price_audit_entry"),
            })
            cursor.execute(
                "SELECT numeric_precision, numeric_scale, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = %s AND column_name = %s", ["catalog_variantpricerevision", "amount"],
            )
            self.assertEqual(cursor.fetchone(), (14, 2, "NO", None))
