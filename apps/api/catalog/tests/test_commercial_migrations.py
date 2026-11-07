from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry
from catalog.models import Product, Variant


class CommercialSchemaCompatibilityTests(TestCase):
    def test_old_models_can_insert_and_preserve_identity_on_the_expanded_schema(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        state = MigrationLoader(connection).project_state([
            ("catalog", "0001_initial"), ("audit", "0001_initial"),
        ])
        OldProduct = state.apps.get_model("catalog", "Product")
        OldVariant = state.apps.get_model("catalog", "Variant")
        OldAudit = state.apps.get_model("audit", "CatalogAuditEntry")
        old_product = OldProduct.objects.create(name="Fictional legacy product")
        old_variant = OldVariant.objects.create(
            product=old_product, label="Fictional legacy variant", sku="Legacy-AbC", barcode="00123"
        )
        old_audit = OldAudit.objects.create(
            actor_id="fictional-legacy-actor", action="catalog.product_created_with_initial_variant",
            product_id=old_product.pk, variant_id=old_variant.pk,
        )

        current = Product.objects.get(pk=old_product.pk)
        current_variant = Variant.objects.get(pk=old_variant.pk)
        current_audit = CatalogAuditEntry.objects.get(pk=old_audit.pk)
        self.assertEqual(current.name, old_product.name)
        self.assertEqual(current.created_at, old_product.created_at)
        self.assertEqual(current.updated_at, old_product.updated_at)
        self.assertFalse(current.is_active)
        self.assertIsNone(current.category_id)
        self.assertEqual(current_variant.sku, "Legacy-AbC")
        self.assertEqual(current_variant.barcode, "00123")
        for field in ("id", "actor_id", "action", "product_id", "variant_id", "occurred_at", "result"):
            self.assertEqual(getattr(current_audit, field), getattr(old_audit, field))
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_database_default_and_applied_migrations_are_present_in_test_database(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
            cursor.execute(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                ["catalog_product", "is_active"],
            )
            default, nullable = cursor.fetchone()
            self.assertIn("false", default.lower())
            self.assertEqual(nullable, "NO")
            cursor.execute("SELECT app, name FROM django_migrations WHERE (app = %s AND name = %s) "
                           "OR (app = %s AND name = %s)",
                           ["catalog", "0002_categories_and_product_activation",
                            "audit", "0002_catalog_change_audit_entry"])
            self.assertEqual(set(cursor.fetchall()), {
                ("catalog", "0002_categories_and_product_activation"),
                ("audit", "0002_catalog_change_audit_entry"),
            })
