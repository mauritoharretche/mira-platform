import base64
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit import application as audit_application
from audit.models import CatalogChangeAuditEntry
from catalog.models import Category, Product, Variant
from identity.tests.test_http_authentication import PREFIX, make_operator, operator_client, sign_in
from inventory.tests.test_application import assert_test_database

PERMISSIONS = ("catalog.view_product", "catalog.view_variant", "catalog.view_category", "catalog.add_category",
               "catalog.set_product_category", "catalog.set_product_active")


class CatalogManagementHTTPTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator(permissions=PERMISSIONS)
        self.client = operator_client()
        self.token = sign_in(self.client).json()["csrf_token"]
        self.product = Product.objects.create(name="Fictional operational product")
        self.variant = Variant.objects.create(product=self.product, label="Fictional pack", sku="FICTIONAL-CATALOG-HTTP")
        self.path = PREFIX + f"/catalog/products/{self.product.id}"

    def post(self, path, data, client=None):
        return (client or self.client).post(path, data, content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=self.token)

    def category(self, name="Fictional Café"):
        response = self.post(PREFIX + "/catalog/categories", {"name": name})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def test_navigation_classification_activation_and_exact_audit(self):
        self.assertEqual(self.client.get(PREFIX + "/catalog/products?category=uncategorized", secure=True).json()["products"][0]["id"], str(self.product.id))
        category = self.category("  FiCtiOnAl Cafe\u0301  ")
        result = self.post(self.path + "/category", {"category_id": category})
        self.assertEqual(result.json(), {"id": str(self.product.id), "category_id": category})
        self.assertEqual(self.post(self.path + "/active", {"is_active": True}).status_code, 200)
        detail = self.client.get(self.path, secure=True).json()
        self.assertEqual((detail["category_id"], detail["is_active"]), (category, True))
        self.assertEqual(self.client.get(PREFIX + "/catalog/products?category=" + category, secure=True).json()["products"][0]["id"], str(self.product.id))
        entries = list(CatalogChangeAuditEntry.objects.order_by("occurred_at"))
        self.assertEqual([e.action for e in entries], ["catalog.category_created", "catalog.product_category_changed", "catalog.product_active_changed"])
        self.assertTrue(all(e.actor_id == str(self.user.pk) for e in entries))
        self.assertEqual((entries[1].previous_category_id, str(entries[1].new_category_id)), (None, category))
        self.assertEqual((entries[2].previous_is_active, entries[2].new_is_active), (False, True))
        self.assertEqual(self.variant.sku, "FICTIONAL-CATALOG-HTTP")

    def test_noops_activation_requirement_reassignment_and_removal(self):
        self.assertEqual(self.post(self.path + "/active", {"is_active": True}).status_code, 400)
        category = self.category()
        self.post(self.path + "/category", {"category_id": category})
        self.post(self.path + "/active", {"is_active": True})
        self.product.refresh_from_db()
        before, count = self.product.updated_at, CatalogChangeAuditEntry.objects.count()
        self.post(self.path + "/active", {"is_active": True})
        self.post(self.path + "/category", {"category_id": category})
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at, before)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), count)
        self.assertEqual(self.post(self.path + "/category", {"category_id": None}).status_code, 400)
        other = self.category("Fictional replacement")
        self.assertEqual(self.post(self.path + "/category", {"category_id": other}).status_code, 200)
        self.post(self.path + "/active", {"is_active": False})
        self.assertEqual(self.post(self.path + "/category", {"category_id": None}).status_code, 200)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.category_id)
        self.assertFalse(self.product.is_active)

    def test_category_normalization_duplicate_and_limits(self):
        self.category(" Café ")
        self.assertEqual(self.post(PREFIX + "/catalog/categories", {"name": "CAFE\u0301"}).status_code, 409)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(CatalogChangeAuditEntry.objects.count(), 1)
        self.category("Cafe")  # Accents stay significant.
        self.category("A" * 255)
        for value in (42, True, None, " ", "a" * 256, "ß" * 128):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(self.post(PREFIX + "/catalog/categories", {"name": value}).status_code, 400)

    def test_pages_cursor_roundtrip_and_invalid_or_incompatible_parameters(self):
        Category.objects.bulk_create([Category(name=f"Fictional {i:03}", name_key=f"fictional {i:03}") for i in range(51)])
        page = self.client.get(PREFIX + "/catalog/categories", secure=True).json()
        self.assertEqual(len(page["categories"]), 50)
        cursor = page["next_cursor"]
        last = self.client.get(PREFIX + "/catalog/categories", {"after": cursor}, secure=True).json()
        self.assertEqual(len(last["categories"]), 1)
        self.assertIsNone(last["next_cursor"])
        invalid = ["/catalog/products?category=", "/catalog/products?category=invalid", "/catalog/products?unknown=1",
                   "/catalog/products?category=all&category=uncategorized", "/catalog/products?after=" + cursor,
                   "/catalog/categories?after=" + str(uuid4()), "/catalog/categories?limit=100", "/catalog/categories?after=",
                   "/catalog/categories?after=%FF", "/catalog/categories?after=" + "a" * 2049,
                   "/catalog/categories?after=a&after=b"]
        # Syntactically encodable cursors with invalid keys still fail before Catalog queries.
        for key in ("", "UPPER", " a", "a" * 256, "a\x00b"):
            encoded = base64.urlsafe_b64encode(f"{uuid4()}:{key}".encode()).decode().rstrip("=")
            invalid.append("/catalog/categories?after=" + encoded)
        for suffix in invalid:
            with self.subTest(suffix=suffix[:80]), CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(PREFIX + suffix, secure=True).status_code, 400)
            self.assertFalse(any('FROM "catalog_' in q["sql"] for q in queries))

    def test_authorization_before_bad_input_existence_and_business_access(self):
        self.user.user_permissions.clear()
        with patch("catalog.application.list_products") as products, patch("catalog.application.list_category_page") as categories:
            self.assertEqual(self.client.get(PREFIX + "/catalog/products?category=invalid", secure=True).status_code, 403)
            self.assertEqual(self.client.get(PREFIX + "/catalog/categories?after=invalid", secure=True).status_code, 403)
        products.assert_not_called()
        categories.assert_not_called()
        for suffix, data in (("/catalog/categories", {"principal": 1}),
                             ("/catalog/products/invalid/category", {"category_id": "invalid"}),
                             ("/catalog/products/invalid/active", {"is_active": "true"})):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.post(PREFIX + suffix, data).status_code, 403)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_write_permissions_do_not_imply_reads_or_other_writes(self):
        self.user.user_permissions.clear()
        for code, path, data in (("add_category", PREFIX + "/catalog/categories", {"name": "Fictional private creation"}),
                                 ("set_product_category", self.path + "/category", {"category_id": None}),
                                 ("set_product_active", self.path + "/active", {"is_active": False})):
            permission = Permission.objects.get(content_type__app_label="catalog", codename=code)
            self.user.user_permissions.add(permission)
            response = self.post(path, data)
            self.assertIn(response.status_code, (200, 201))
            self.assertNotIn("variants", response.json())
            self.assertEqual(self.client.get(self.path, secure=True).status_code, 403)
            self.assertEqual(self.client.get(PREFIX + "/catalog/categories", secure=True).status_code, 403)
            self.user.user_permissions.remove(permission)
            self.assertEqual(self.post(path, data).status_code, 403)

    def test_native_session_csrf_and_strict_payloads(self):
        anonymous = operator_client()
        self.assertEqual(anonymous.get(PREFIX + "/catalog/categories", secure=True).status_code, 403)
        for path, good in ((PREFIX + "/catalog/categories", {"name": "Fictional"}),
                           (self.path + "/category", {"category_id": None}), (self.path + "/active", {"is_active": False})):
            self.assertEqual(self.client.post(path, good, content_type="application/json", secure=True).status_code, 403)
            for data in ({}, {**good, "actor": "spoofed"}, {**good, "principal": self.user.pk}):
                self.assertEqual(self.post(path, data).status_code, 400)
            self.assertEqual(self.client.delete(path, secure=True, HTTP_X_CSRFTOKEN=self.token).status_code, 405)
        for value in ("true", 1, None, [], "false"):
            self.assertEqual(self.post(self.path + "/active", {"is_active": value}).status_code, 400)
        self.assertEqual(self.post(self.path + "/category", {"category_id": str(uuid4())}).status_code, 404)
        self.assertEqual(self.post(PREFIX + f"/catalog/products/{uuid4()}/active", {"is_active": False}).status_code, 404)

    def test_each_http_write_rolls_back_audit_failure_before_and_after_insert(self):
        category = Category.objects.create(name="Fictional", name_key="fictional")
        cases = [("record_category_created", PREFIX + "/catalog/categories", {"name": "Other fictional"}),
                 ("record_product_category_changed", self.path + "/category", {"category_id": None}),
                 ("record_product_active_changed", self.path + "/active", {"is_active": True})]
        self.product.category = category
        self.product.save(update_fields=["category"])
        for contract, path, data in cases:
            original = getattr(audit_application, contract)
            for after in (False, True):
                def fail(**kwargs):
                    if after:
                        original(**kwargs)
                    raise DatabaseError("fictional hidden audit failure")
                with self.subTest(contract=contract, after=after), patch.object(audit_application, contract, side_effect=fail):
                    response = self.post(path, data)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                self.product.refresh_from_db()
                self.assertEqual(self.product.category_id, category.id)
                self.assertFalse(self.product.is_active)
                self.assertEqual(Category.objects.count(), 1)
                self.assertFalse(CatalogChangeAuditEntry.objects.exists())
