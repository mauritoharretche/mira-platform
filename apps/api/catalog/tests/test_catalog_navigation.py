from dataclasses import FrozenInstanceError
from unicodedata import normalize
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit.models import CatalogChangeAuditEntry
from catalog import application
from catalog.contracts import CategoryCursor
from catalog.models import Category, Product
from identity.contracts import AccessDenied, AuthenticationRequired
from identity.tests.test_http_authentication import PASSWORD, make_operator
from inventory.tests.test_application import assert_test_database


class CatalogNavigationTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator(permissions=("catalog.view_product", "catalog.view_variant", "catalog.view_category"))
        self.principal = authenticate(username=self.user.username, password=PASSWORD)
        self.assertIsNotNone(self.principal)

    def test_product_pages_are_bounded_deterministic_and_immutable(self):
        Product.objects.bulk_create([Product(id=UUID(int=i + 1), name=f"Fictional {i}") for i in range(103)])
        seen, after = [], None
        for expected_size in (50, 50, 3):
            with CaptureQueriesContext(connection) as queries:
                page = application.list_products(principal=self.principal, after_id=after)
            business = [q["sql"] for q in queries if 'FROM "catalog_product"' in q["sql"]]
            self.assertEqual(len(business), 1)
            self.assertIn("LIMIT 51", business[0])
            self.assertEqual(len(page.items), expected_size)
            self.assertIsInstance(page.items, tuple)
            seen.extend(item.id for item in page.items)
            after = page.next_after_id
        self.assertIsNone(after)
        self.assertEqual(seen, sorted(set(seen)))
        with self.assertRaises(FrozenInstanceError):
            page.items[0].name = "forbidden"
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_category_and_uncategorized_filters_include_inactive_products(self):
        category = Category.objects.create(name="Fictional category", name_key="fictional category")
        other = Category.objects.create(name="Other fictional", name_key="other fictional")
        a = Product.objects.create(name="A", category=category)
        b = Product.objects.create(name="B", category=category, is_active=True)
        c = Product.objects.create(name="C", category=other)
        d = Product.objects.create(name="D")
        read = lambda **kwargs: {item.id for item in application.list_products(principal=self.principal, **kwargs).items}
        self.assertEqual(read(), {a.id, b.id, c.id, d.id})
        self.assertEqual(read(category_id=category.id), {a.id, b.id})
        self.assertEqual(read(only_uncategorized=True), {d.id})
        self.assertEqual(read(category_id=uuid4()), set())

    def test_category_keyset_uses_database_collation_and_limits_not_full_list(self):
        names = [f"Fictional {i:03}" for i in range(104)] + ["Café", "Cafe", "Árbol"]
        Category.objects.bulk_create([Category(name=name, name_key=normalize("NFC", name.casefold())) for name in names])
        after, seen = None, []
        with patch.object(application, "list_categories", side_effect=AssertionError("Full list must not be used")):
            while True:
                with CaptureQueriesContext(connection) as queries:
                    page = application.list_category_page(principal=self.principal, after=after)
                sql = [q["sql"] for q in queries if 'FROM "catalog_category"' in q["sql"]]
                self.assertEqual(len(sql), 1)
                self.assertIn("LIMIT 51", sql[0])
                self.assertLessEqual(len(page.items), 50)
                seen.extend(item.id for item in page.items)
                after = page.next_cursor
                if after is None:
                    break
        self.assertEqual(seen, list(Category.objects.order_by("name_key", "id").values_list("id", flat=True)))
        self.assertEqual(len(seen), 107)
        self.assertFalse(CatalogChangeAuditEntry.objects.exists())

    def test_empty_and_exact_page_boundaries_have_no_false_continuation(self):
        self.assertEqual(application.list_products(principal=self.principal).items, ())
        self.assertEqual(application.list_category_page(principal=self.principal).items, ())
        Product.objects.bulk_create([Product(name=f"Fictional {i}") for i in range(50)])
        Category.objects.bulk_create([Category(name=f"Fictional {i:03}", name_key=f"fictional {i:03}") for i in range(50)])
        self.assertIsNone(application.list_products(principal=self.principal).next_after_id)
        self.assertIsNone(application.list_category_page(principal=self.principal).next_cursor)

    def test_invalid_filters_and_cursors_do_not_query_business_tables(self):
        invalid_products = ({"category_id": "invalid"}, {"after_id": "invalid"}, {"only_uncategorized": 1},
                            {"only_uncategorized": True, "category_id": uuid4()})
        invalid_categories = ("invalid", CategoryCursor(name_key="", id=uuid4()), CategoryCursor(name_key="UPPER", id=uuid4()),
                              CategoryCursor(name_key="a" * 256, id=uuid4()), CategoryCursor(name_key="a", id="invalid"),
                              CategoryCursor(name_key="a\x00b", id=uuid4()), CategoryCursor(name_key="a\ud800b", id=uuid4()))
        with CaptureQueriesContext(connection) as queries:
            for kwargs in invalid_products:
                with self.assertRaises(application.CatalogValidationError):
                    application.list_products(principal=self.principal, **kwargs)
            for cursor in invalid_categories:
                with self.assertRaises(application.CatalogValidationError):
                    application.list_category_page(principal=self.principal, after=cursor)
        self.assertFalse(any('FROM "catalog_' in q["sql"] for q in queries))

    def test_authorization_precedes_validation_and_no_cursor_grants_access(self):
        self.user.user_permissions.clear()
        for operation, args in ((application.list_products, {"category_id": "invalid"}),
                                (application.list_category_page, {"after": CategoryCursor("a", uuid4())})):
            with self.assertRaises(AccessDenied):
                operation(principal=self.principal, **args)
            for invalid in (self.user.pk, {"id": self.user.pk}, None):
                with self.assertRaises(AuthenticationRequired):
                    operation(principal=invalid, **args)

    def test_permission_pairs_groups_and_fresh_revocation(self):
        self.user.user_permissions.clear()
        group = Group.objects.create(name="fictional-catalog-readers")
        self.user.groups.add(group)
        permissions = Permission.objects.filter(content_type__app_label="catalog", codename__in=("view_product", "view_variant"))
        group.permissions.add(*permissions)
        application.list_products(principal=self.principal)
        with self.assertRaises(AccessDenied):
            application.list_category_page(principal=self.principal)
        group.permissions.remove(Permission.objects.get(content_type__app_label="catalog", codename="view_variant"))
        with self.assertRaises(AccessDenied):
            application.list_products(principal=self.principal)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with self.assertRaises(AccessDenied):
            application.list_products(principal=self.principal)
