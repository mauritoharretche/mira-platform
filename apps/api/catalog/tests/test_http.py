import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase

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


class VariantHTTPTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator(permissions=("catalog.add_variant",))
        self.client = operator_client()
        self.token = sign_in(self.client).json()["csrf_token"]
        self.product = Product.objects.create(name="Fictional existing product")
        self.path = PREFIX + f"/catalog/products/{self.product.id}/variants"
        self.payload = {"label": " Second pack ", "sku": " VARIANT-HTTP ", "barcode": " 000456 "}

    def create(self, payload=None, path=None):
        return self.client.post(path or self.path, self.payload if payload is None else payload,
                                content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=self.token)

    def test_write_without_read_permission_has_minimal_confirmed_payload_and_audit(self):
        self.assertEqual(self.client.get(PREFIX + "/auth/session", secure=True).json()["capabilities"], ["catalog.add_variant_to_product"])
        response = self.create()
        self.assertEqual(response.status_code, 201)
        variant = Variant.objects.get()
        self.assertEqual(response.json(), {"id": str(variant.pk), "label": "Second pack", "sku": "VARIANT-HTTP", "barcode": "000456"})
        entry = CatalogAuditEntry.objects.get()
        self.assertEqual((entry.action, entry.actor_id, entry.product_id, entry.variant_id),
                         ("catalog.variant_added_to_product", str(self.user.pk), self.product.pk, variant.pk))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertIsNone(self.product.category_id)
        self.assertEqual(self.client.get(PREFIX + f"/catalog/products/{self.product.id}", secure=True).status_code, 403)

    def test_read_and_staff_do_not_grant_write_and_denial_precedes_validation(self):
        self.user.user_permissions.clear()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        for codename in ("view_product", "view_variant"):
            self.user.user_permissions.add(Permission.objects.get(content_type__app_label="catalog", codename=codename))
        with patch("catalog.application.add_variant_to_product") as add:
            self.assertEqual(self.create({"actor": "untrusted"}, PREFIX + "/catalog/products/invalid/variants").status_code, 403)
        add.assert_not_called()
        self.assertFalse(Variant.objects.exists())

    def test_strict_types_fields_lengths_identifiers_and_missing_product(self):
        for changes in ({"label": " "}, {"sku": False}, {"label": 42}, {"label": "x" * 256},
                        {"sku": "x" * 129}, {"barcode": 42}, {"barcode": "x" * 256}, {"principal": 1}):
            with self.subTest(fields=list(changes)):
                self.assertEqual(self.create(self.payload | changes).status_code, 400)
        self.assertEqual(self.create(path=PREFIX + "/catalog/products/invalid/variants").status_code, 400)
        self.assertEqual(self.create(path=PREFIX + f"/catalog/products/{uuid4()}/variants").status_code, 404)
        self.assertEqual(self.create(path=self.path + "?unknown=1").status_code, 400)
        self.assertFalse(Variant.objects.exists())

    def test_duplicates_and_committed_retry_do_not_append(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(self.create(self.payload | {"sku": "another"}).status_code, 409)
        self.assertEqual((Variant.objects.count(), CatalogAuditEntry.objects.count()), (1, 1))

    def test_audit_failure_before_and_after_insert_rolls_back_variant(self):
        original = audit_application.record_catalog_success
        for after in (False, True):
            def fail(**kwargs):
                if after:
                    original(**kwargs)
                raise DatabaseError("fictional private failure")
            with self.subTest(after=after), patch.object(audit_application, "record_catalog_success", side_effect=fail):
                response = self.create()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                self.assertFalse(Variant.objects.exists())
                self.assertFalse(CatalogAuditEntry.objects.exists())

    def test_csrf_anonymous_and_permission_revocation(self):
        self.assertEqual(self.client.post(self.path, self.payload, content_type="application/json", secure=True).status_code, 403)
        self.assertEqual(operator_client().post(self.path, self.payload, content_type="application/json", secure=True).status_code, 403)
        self.user.user_permissions.clear()
        self.assertEqual(self.create().status_code, 403)
        self.assertFalse(Variant.objects.exists())


# Adapter-only coverage is safe even when the isolated PostgreSQL service is down.
from rest_framework.test import APIRequestFactory, force_authenticate
from catalog.http import ProductVariantsView
from identity.contracts import AccessDenied, Capability
from catalog.application import ProductNotFoundError, CatalogValidationError


class VariantAdapterTests(SimpleTestCase):
    def call(self, payload, identifier=None, *, query=""):
        request = APIRequestFactory().post("/variants" + query, payload, format="json")
        self.principal = SimpleNamespace(is_authenticated=True)
        force_authenticate(request, user=self.principal)
        return ProductVariantsView.as_view()(request, product_id=identifier or str(uuid4()))

    def test_minimal_response_uses_original_command_and_fixed_capability(self):
        identifier, variant_id = uuid4(), uuid4()
        variant = SimpleNamespace(pk=variant_id, label="Pack", sku="FICTITIOUS", barcode=None)
        with patch("catalog.http.require_capability") as authorize, patch("catalog.application.add_variant_to_product", return_value=variant) as add:
            response = self.call({"label": "Pack", "sku": "FICTITIOUS"}, str(identifier))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, {"id": str(variant_id), "label": "Pack", "sku": "FICTITIOUS", "barcode": None})
        authorize.assert_called_once_with(principal=self.principal, capability=Capability.ADD_VARIANT_TO_PRODUCT)
        add.assert_called_once_with(principal=self.principal, product_id=identifier, label="Pack", sku="FICTITIOUS", barcode=None)

    def test_denial_precedes_uuid_input_and_command(self):
        with patch("catalog.http.require_capability", side_effect=AccessDenied), patch("catalog.application.add_variant_to_product") as add:
            self.assertEqual(self.call({"actor": 1}, "invalid").status_code, 403)
        add.assert_not_called()

    def test_strict_inputs_never_call_business_operation(self):
        invalid = ({"label": 42, "sku": "x"}, {"label": "x", "sku": False}, {"label": "x"},
                   {"label": "x", "sku": "x", "actor": 1}, {"label": "x", "sku": "x", "barcode": 42})
        with patch("catalog.http.require_capability"), patch("catalog.application.add_variant_to_product") as add:
            for payload in invalid:
                self.assertEqual(self.call(payload).status_code, 400)
            self.assertEqual(self.call({}, "invalid").status_code, 400)
            self.assertEqual(self.call({}, query="?unknown=1").status_code, 400)
        add.assert_not_called()

    def test_missing_validation_and_private_internal_errors_are_sanitized(self):
        for error, status, code in ((ProductNotFoundError("private"), 404, "not_found"),
                                    (CatalogValidationError("private"), 400, "validation_error"),
                                    (DatabaseError("private"), 503, "unavailable")):
            with self.subTest(status=status), patch("catalog.http.require_capability"), patch("catalog.application.add_variant_to_product", side_effect=error):
                response = self.call({"label": "x", "sku": "x"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(json.loads(response.content), {"error": {"code": code}})
