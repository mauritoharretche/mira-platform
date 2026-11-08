from io import StringIO
import os
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from audit.models import CatalogAuditEntry, CatalogChangeAuditEntry, CatalogMeasurementAuditEntry, CatalogPriceAuditEntry, InventoryMovementAuditEntry
from catalog.models import Category, Product, Variant, VariantMeasurementSpec, VariantPriceRevision
from inventory.models import StockMovement, StockPosition
from inventory.tests.test_application import assert_test_database


class InventoryMigrationTests(TestCase):
    def setUp(self):
        assert_test_database(self)

    def test_previous_model_state_remains_compatible_and_no_inventory_is_backfilled(self):
        state = MigrationLoader(connection).project_state([
            ("catalog", "0004_variant_measurement_spec"), ("audit", "0004_catalog_measurement_audit_entry"),
        ])
        old_product = state.apps.get_model("catalog", "Product").objects.create(name="Fictional legacy inventory product")
        old_variant = state.apps.get_model("catalog", "Variant").objects.create(
            product=old_product, label="Legacy", sku="LEGACY-INVENTORY-001", barcode="000123")
        for model in (Category, Product, Variant, VariantMeasurementSpec, VariantPriceRevision,
                      CatalogAuditEntry, CatalogChangeAuditEntry, CatalogPriceAuditEntry, CatalogMeasurementAuditEntry):
            historic = state.apps.get_model(model._meta.app_label, model.__name__)
            self.assertEqual({f.name for f in historic._meta.fields}, {f.name for f in model._meta.fields})
        variant = Variant.objects.get(pk=old_variant.pk)
        self.assertEqual((variant.sku, variant.barcode), ("LEGACY-INVENTORY-001", "000123"))
        self.assertFalse(StockPosition.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(InventoryMovementAuditEntry.objects.exists())

    def test_migration_drift_sql_and_installed_constraints_use_only_the_test_connection(self):
        call_command("makemigrations", check=True, dry_run=True, interactive=False, stdout=StringIO())
        output_dir = os.environ.get("MIRA_TEST_SQL_OUTPUT")
        for app, migration in (("inventory", "0001_initial"), ("audit", "0005_inventory_movement_audit_entry")):
            output = StringIO()
            call_command("sqlmigrate", app, migration, stdout=output)
            sql = output.getvalue()
            self.assertIn("bigint", sql)
            self.assertIn('COLLATE "C"', sql)
            self.assertIn("CHECK", sql)
            self.assertIn("numeric(20, 0)", sql)
            if output_dir:
                directory = Path(output_dir)
                self.assertTrue(directory.is_dir())
                self.assertFalse(directory.is_symlink())
                self.assertFalse(directory.resolve().is_relative_to(Path(__file__).resolve().parents[4]))
                path = directory / f"{app}-{migration}.sql"
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "w") as handle:
                    handle.write(sql)
        with connection.cursor() as cursor:
            for table in ("inventory_stockposition", "inventory_stockmovement", "audit_inventorymovementauditentry"):
                cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass", [table])
                constraints = dict(cursor.fetchall())
                self.assertTrue(constraints)
                if table == "inventory_stockmovement":
                    self.assertIn("reason IS NULL", constraints["inventory_movement_shape_valid"])
                    self.assertIn("reason IS NOT NULL", constraints["inventory_movement_shape_valid"])
                    self.assertIn("numeric(20,0)", constraints["inventory_movement_equation_valid"].replace(" ", ""))
                    self.assertIn("UNIQUE", constraints["inventory_movement_sequence_unique"])
                if table == "audit_inventorymovementauditentry":
                    self.assertIn("UNIQUE", constraints["audit_inventory_movement_unique"])
