import ast
import inspect
from importlib.util import resolve_name
from pathlib import Path

from django.test import SimpleTestCase

from audit import application as audit_application
from catalog import application as catalog_application


class CatalogModuleBoundaryTests(SimpleTestCase):
    def test_public_operations_require_principal_and_expose_no_capability_override(self) -> None:
        operations = {
            name: function
            for name, function in inspect.getmembers(catalog_application, inspect.isfunction)
            if function.__module__ == catalog_application.__name__ and not name.startswith("_")
        }
        self.assertEqual(
            set(operations),
            {
                "create_product_with_initial_variant", "add_variant_to_product", "get_product",
                "create_category", "list_categories", "set_product_category", "set_product_active",
                "set_variant_price", "get_current_variant_price", "list_variant_price_history",
            },
        )
        for name, operation in operations.items():
            with self.subTest(operation=name):
                parameters = inspect.signature(operation).parameters
                principal = parameters["principal"]
                self.assertEqual(principal.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(principal.default, inspect.Parameter.empty)
                self.assertNotIn("actor", parameters)
                self.assertNotIn("capability", parameters)

    def test_audit_exposes_only_the_append_contract(self) -> None:
        public_functions = {
            name
            for name, function in inspect.getmembers(audit_application, inspect.isfunction)
            if function.__module__ == audit_application.__name__ and not name.startswith("_")
        }
        self.assertEqual(public_functions, {
            "record_catalog_success", "record_category_created",
            "record_product_category_changed", "record_product_active_changed",
            "record_variant_price_set",
        })

    def test_explicit_imports_respect_model_ownership_and_dependency_direction(self) -> None:
        api_root = Path(__file__).resolve().parents[2]
        allowed_dependencies = {
            "catalog": {"identity", "audit"},
            "audit": {"identity"},
            "identity": set(),
        }
        violations = []
        for package in api_root.iterdir():
            if not package.is_dir() or package.name.startswith("."):
                continue
            for source in package.rglob("*.py"):
                relative = source.relative_to(api_root)
                if "tests" in relative.parts or "migrations" in relative.parts:
                    continue
                owner = relative.parts[0]
                module_package = ".".join(relative.parts[:-1])
                tree = ast.parse(source.read_text(), filename=str(relative))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        if node.level:
                            module = resolve_name("." * node.level + module, module_package)
                        imports = [module] + [f"{module}.{alias.name}" for alias in node.names]
                    else:
                        continue
                    for imported in imports:
                        dependency = imported.split(".")[0]
                        if dependency not in allowed_dependencies or dependency == owner:
                            continue
                        imports_model = imported == f"{dependency}.models" or imported.startswith(
                            f"{dependency}.models."
                        )
                        wrong_direction = (
                            owner in allowed_dependencies
                            and dependency not in allowed_dependencies[owner]
                        )
                        if imports_model or wrong_direction:
                            violations.append(f"{relative}:{node.lineno}: {imported}")

        self.assertEqual(violations, [], "Explicit imports violate module boundaries")
