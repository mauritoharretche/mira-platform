from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import TestCase

from audit import application as audit_application
from audit.models import CatalogAuditEntry
from catalog.models import Product, Variant
from identity.tests.test_http_authentication import PREFIX, make_operator, operator_client, sign_in
from inventory.tests.test_application import assert_test_database


class CatalogHTTPTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator()
        self.client = operator_client()
        self.token = sign_in(self.client).json()["csrf_token"]
        self.payload = {"product_name": " Fictional grain ", "variant_label": " Initial pack ", "sku": " AbC-HTTP ", "barcode": " 000123 "}

    def create(self, payload=None):
        return self.client.post(PREFIX + "/catalog/products", self.payload if payload is None else payload,
                                content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=self.token)

    def test_creation_retrieval_and_existing_domain_attribution(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.content)
        product = response.json()
        self.assertEqual((product["name"], product["is_active"]), ("Fictional grain", False))
        self.assertEqual(len(product["variants"]), 1)
        self.assertEqual((product["variants"][0]["sku"], product["variants"][0]["barcode"]), ("AbC-HTTP", "000123"))
        detail = self.client.get(PREFIX + "/catalog/products/" + product["id"], secure=True)
        self.assertEqual(detail.json(), product)
        audit = CatalogAuditEntry.objects.get()
        self.assertEqual((audit.actor_id, str(audit.product_id), str(audit.variant_id)),
                         (str(self.user.pk), product["id"], product["variants"][0]["id"]))

    def test_authorize_before_validation_existence_and_business_queries(self):
        self.user.user_permissions.clear()
        with patch("catalog.application.create_product_with_initial_variant") as create:
            self.assertEqual(self.create({"principal": self.user.pk}).status_code, 403)
        create.assert_not_called()
        self.assertEqual(self.client.get(PREFIX + "/catalog/products/not-a-uuid", secure=True).status_code, 403)
        self.assertFalse(Product.objects.exists())

    def test_each_required_permission_is_enforced_without_staff_shortcut(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        for codename in ("add_product", "add_variant"):
            permission = Permission.objects.get(content_type__app_label="catalog", codename=codename)
            self.user.user_permissions.remove(permission)
            self.assertEqual(self.create().status_code, 403)
            self.user.user_permissions.add(permission)
        self.assertFalse(Product.objects.exists())

    def test_product_read_requires_both_permissions_before_uuid_validation(self):
        for codename in ("view_product", "view_variant"):
            permission = Permission.objects.get(content_type__app_label="catalog", codename=codename)
            self.user.user_permissions.remove(permission)
            self.assertEqual(self.client.get(PREFIX + "/catalog/products/invalid", secure=True).status_code, 403)
            self.user.user_permissions.add(permission)

    def test_invalid_fields_types_lengths_blank_and_authority_claims_are_rejected(self):
        invalid = ({"principal": 1}, {"actor": {"actor_id": "1"}}, {"is_active": True},
                   {"product_name": 42}, {"product_name": " "}, {"variant_label": "x" * 256},
                   {"product_name": "x" * 256}, {"sku": "x" * 129}, {"sku": False},
                   {"barcode": 123}, {"barcode": "x" * 256})
        for changes in invalid:
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.create(self.payload | changes).status_code, 400)
        self.assertFalse(Product.objects.exists())

    def test_known_duplicates_conflict_without_partial_product(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.create(self.payload | {"sku": "different"}).status_code, 409)
        self.assertEqual((Product.objects.count(), Variant.objects.count(), CatalogAuditEntry.objects.count()), (1, 1, 1))

    def test_audit_failure_before_and_after_insert_is_safe_and_atomic_over_http(self):
        original = audit_application.record_catalog_success
        for after in (False, True):
            def fail(**kwargs):
                if after:
                    original(**kwargs)
                raise DatabaseError("fictional private exception")
            with self.subTest(after=after), patch.object(audit_application, "record_catalog_success", side_effect=fail):
                response = self.create()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                self.assertFalse(Product.objects.exists())
                self.assertFalse(Variant.objects.exists())
                self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_missing_product_and_invalid_uuid_are_safe(self):
        self.assertEqual(self.client.get(PREFIX + "/catalog/products/" + str(uuid4()), secure=True).status_code, 404)
        self.assertEqual(self.client.get(PREFIX + "/catalog/products/invalid", secure=True).status_code, 400)

    def test_unsafe_request_without_csrf_and_anonymous_requests_are_denied(self):
        self.assertEqual(self.client.post(PREFIX + "/catalog/products", self.payload, content_type="application/json", secure=True).status_code, 403)
        anonymous = operator_client()
        self.assertEqual(anonymous.get(PREFIX + "/catalog/products/" + str(uuid4()), secure=True).status_code, 403)
        self.assertFalse(Product.objects.exists())
