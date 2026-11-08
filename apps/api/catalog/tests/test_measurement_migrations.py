from decimal import Decimal

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogMeasurementAuditEntry, CatalogPriceAuditEntry
from catalog.models import Category, Product, Variant, VariantMeasurementSpec, VariantPriceRevision


class MeasurementMigrationCompatibilityTests(TestCase):
    def test_phase_110_historical_models_remain_compatible_without_backfilled_measurements(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        state = MigrationLoader(connection).project_state([
            ("catalog", "0003_variant_price_revision"), ("audit", "0003_catalog_price_audit_entry"),
        ])
        old = {model.__name__: state.apps.get_model(model._meta.app_label, model.__name__)
               for model in (Category, Product, Variant, VariantPriceRevision,
                             CatalogAuditEntry, CatalogChangeAuditEntry, CatalogPriceAuditEntry)}
        category = old["Category"].objects.create(name="Fictional legacy category", name_key="fictional legacy category")
        product = old["Product"].objects.create(name="Fictional legacy product", category=category, is_active=True)
        variant = old["Variant"].objects.create(product=product, label="Legacy package", sku="LEGACY-AbC", barcode="00123")
        revision = old["VariantPriceRevision"].objects.create(
            variant=variant, sequence=1, amount=Decimal("12.34"), currency="ARS", price_basis="PER_VARIANT",
        )
        identity = old["CatalogAuditEntry"].objects.create(
            actor_id="fictional-legacy-actor", action="catalog.variant_added_to_product", product_id=product.id, variant_id=variant.id,
        )
        change = old["CatalogChangeAuditEntry"].objects.create(
            actor_id="fictional-legacy-actor", action="catalog.product_active_changed", target_id=product.id,
            previous_is_active=False, new_is_active=True,
        )
        price = old["CatalogPriceAuditEntry"].objects.create(
            actor_id="fictional-legacy-actor", action="catalog.variant_price_set", variant_id=variant.id,
            new_revision_id=revision.id, new_amount=revision.amount, currency="ARS", price_basis="PER_VARIANT",
        )
        for historic, model in ((category, Category), (product, Product), (variant, Variant),
                                (revision, VariantPriceRevision), (identity, CatalogAuditEntry),
                                (change, CatalogChangeAuditEntry), (price, CatalogPriceAuditEntry)):
            current = model.objects.get(pk=historic.pk)
            self.assertEqual({f.name for f in historic._meta.fields}, {f.name for f in current._meta.fields})
            for field in historic._meta.fields:
                self.assertEqual(getattr(current, field.attname), getattr(historic, field.attname))
        self.assertFalse(VariantMeasurementSpec.objects.exists())
        self.assertFalse(CatalogMeasurementAuditEntry.objects.exists())

    def test_additive_migrations_and_nullable_numeric_schema_are_installed_only_in_test_database(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
            cursor.execute("SELECT app, name FROM django_migrations WHERE (app = %s AND name = %s) OR (app = %s AND name = %s)",
                           ["catalog", "0004_variant_measurement_spec", "audit", "0004_catalog_measurement_audit_entry"])
            self.assertEqual(set(cursor.fetchall()), {
                ("catalog", "0004_variant_measurement_spec"), ("audit", "0004_catalog_measurement_audit_entry"),
            })
            cursor.execute("SELECT numeric_precision, numeric_scale, is_nullable, column_default FROM information_schema.columns "
                           "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                           ["catalog_variantmeasurementspec", "net_content_amount"])
            self.assertEqual(cursor.fetchone(), (18, 6, "YES", None))
            cursor.execute("SELECT is_nullable, column_default FROM information_schema.columns "
                           "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                           ["catalog_variantmeasurementspec", "sale_unit"])
            self.assertEqual(cursor.fetchone(), ("YES", None))
