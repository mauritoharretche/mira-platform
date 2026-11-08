import ast
import inspect
from pathlib import Path

from django.test import SimpleTestCase

from inventory import application
from inventory.models import StockMovement, StockPosition


class InventoryBoundaryTests(SimpleTestCase):
    def test_only_four_authorized_internal_operations_are_exposed(self):
        operations = {name: value for name, value in inspect.getmembers(application, inspect.isfunction)
                      if value.__module__ == application.__name__ and not name.startswith("_")}
        self.assertEqual(set(operations), {"receive_stock", "adjust_stock", "get_stock", "list_stock_movements"})
        for operation in operations.values():
            params = inspect.signature(operation).parameters
            self.assertEqual(params["principal"].kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(params["principal"].default, inspect.Parameter.empty)
            self.assertNotIn("actor", params)
            self.assertNotIn("capability", params)

    def test_inventory_reads_catalog_only_through_its_reference_contract(self):
        root = Path(__file__).resolve().parents[1]
        for source in root.glob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module == "catalog":
                    self.assertEqual([n.name for n in node.names], ["references"])
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith("catalog."))
                elif isinstance(node, ast.Import):
                    self.assertFalse(any(n.name == "catalog" or n.name.startswith("catalog.") for n in node.names))
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "variant")
        self.assertEqual(StockPosition._meta.get_field("variant").remote_field.related_name, "+")

    def test_no_model_or_application_delete_or_history_update_contract(self):
        self.assertFalse(any(name.startswith(("delete_", "update_movement")) for name in vars(application)))
        self.assertEqual(StockMovement._meta.get_field("position").remote_field.model, StockPosition)
        self.assertFalse(any(field.name in ("created_by", "username", "email") for field in StockMovement._meta.fields))
