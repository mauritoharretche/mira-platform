"""HTTP pricing adapter over the existing Catalog and Audit contracts."""
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit import application as audit_application
from audit.models import CatalogPriceAuditEntry
from catalog.models import Product, Variant, VariantPriceRevision
from identity.tests.test_http_authentication import PREFIX, make_operator, operator_client, sign_in
from inventory.tests.test_application import assert_test_database


class VariantPriceHTTPTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator(permissions=("catalog.set_variant_price", "catalog.view_variantpricerevision",
                                               "catalog.view_variant_price_history"))
        self.client = operator_client()
        self.token = sign_in(self.client).json()["csrf_token"]
        self.variant = Variant.objects.create(product=Product.objects.create(name="Fictional price item"),
                                              label="Fictional pack", sku="FICTIONAL-PRICE-HTTP")
        self.path = PREFIX + f"/catalog/variants/{self.variant.id}/price"

    def write(self, amount, expected=None, *, client=None, token=None, extra=None):
        data = {"amount": amount, "currency": "ARS", "expected_revision_id": expected}
        return (client or self.client).post(self.path, {**data, **(extra or {})}, content_type="application/json",
                                            secure=True, HTTP_X_CSRFTOKEN=token or self.token)

    def test_absence_first_price_noop_replacement_and_exact_audit(self):
        self.assertEqual(self.client.get(self.path, secure=True).json(), {"price": None})
        first = self.write("12.3000")
        self.assertEqual(first.status_code, 200)
        original = first.json()["price"]
        self.assertEqual((original["amount"], original["currency"], original["price_basis"], original["sequence"]),
                         ("12.30", "ARS", "PER_VARIANT", "1"))
        self.assertEqual(self.write("12.30", original["revision_id"]).json()["price"], original)
        self.assertEqual((VariantPriceRevision.objects.count(), CatalogPriceAuditEntry.objects.count()), (1, 1))
        second = self.write("13.40", original["revision_id"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["price"]["sequence"], "2")
        self.assertEqual((VariantPriceRevision.objects.count(), CatalogPriceAuditEntry.objects.count()), (2, 2))
        audits = list(CatalogPriceAuditEntry.objects.order_by("occurred_at"))
        self.assertEqual([entry.actor_id for entry in audits], [str(self.user.pk)] * 2)
        self.assertEqual(str(audits[1].previous_revision_id), original["revision_id"])
        self.assertEqual(audits[1].new_amount, Decimal("13.40"))

    def test_stale_two_operators_and_explicit_conflict_without_retry(self):
        other = make_operator(username="fictional-second-price-writer", permissions=("catalog.set_variant_price",))
        second_client = operator_client()
        second_token = sign_in(second_client, username=other.username).json()["csrf_token"]
        first = self.write("1.00").json()["price"]
        self.assertEqual(self.write("2.00", first["revision_id"], client=second_client, token=second_token).status_code, 200)
        stale = self.write("2.00", first["revision_id"])
        self.assertEqual((stale.status_code, stale.json()), (409, {"error": {"code": "price_conflict"}}))
        self.assertEqual(self.write("2.00").status_code, 409)
        self.assertEqual((VariantPriceRevision.objects.count(), CatalogPriceAuditEntry.objects.count()), (2, 2))
        self.assertEqual(self.client.get(self.path, secure=True).json()["price"]["amount"], "2.00")

    def test_history_uses_database_limit_and_exclusive_cursor(self):
        previous = None
        for number in range(52):
            response = self.write(str(number + 1), previous)
            self.assertEqual(response.status_code, 200)
            previous = response.json()["price"]["revision_id"]
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.path + "/revisions", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["revisions"]), 50)
        self.assertEqual(response.json()["next_cursor"], "3")
        self.assertTrue(any("LIMIT 51" in item["sql"] and '"catalog_variantpricerevision"' in item["sql"] for item in queries))
        last = self.client.get(self.path + "/revisions?before_sequence=3", secure=True).json()
        self.assertEqual([item["sequence"] for item in last["revisions"]], ["2", "1"])
        self.assertIsNone(last["next_cursor"])
        for query in ("?before_sequence=0", "?before_sequence=01", "?before_sequence=9223372036854775808",
                      "?before_sequence=3&before_sequence=2", "?unknown=1"):
            self.assertEqual(self.client.get(self.path + "/revisions" + query, secure=True).status_code, 400)

    def test_authentication_permissions_revocation_and_validation_order(self):
        self.assertEqual(operator_client().get(self.path, secure=True).status_code, 403)
        self.user.user_permissions.clear()
        with patch("catalog.application.get_current_variant_price") as current, patch("catalog.application.set_variant_price") as write, \
                patch("catalog.application.list_variant_price_history_page") as history:
            self.assertEqual(self.client.get(PREFIX + "/catalog/variants/bad/price", secure=True).status_code, 403)
            self.assertEqual(self.write("bad").status_code, 403)
            self.assertEqual(self.client.get(self.path + "/revisions?before_sequence=bad", secure=True).status_code, 403)
        current.assert_not_called(); write.assert_not_called(); history.assert_not_called()
        grant = lambda name: Permission.objects.get(content_type__app_label="catalog", codename=name)
        self.user.user_permissions.add(grant("set_variant_price"))
        self.assertEqual(self.write("1.00").status_code, 200)  # Write does not require read.
        self.assertEqual(self.client.get(self.path, secure=True).status_code, 403)
        self.user.user_permissions.add(grant("view_variant_price_history"))
        self.assertEqual(self.client.get(self.path + "/revisions", secure=True).status_code, 403)
        group = Group.objects.create(name="Fictional price viewers")
        group.permissions.add(grant("view_variantpricerevision"))
        self.user.groups.add(group)
        self.assertEqual(self.client.get(self.path, secure=True).status_code, 200)
        self.assertEqual(self.client.get(self.path + "/revisions", secure=True).status_code, 200)
        group.permissions.clear()
        self.assertEqual(self.client.get(self.path, secure=True).status_code, 403)

    def test_strict_decimal_body_csrf_and_missing_variant(self):
        self.assertEqual(self.client.post(self.path, {"amount": "1.00", "currency": "ARS", "expected_revision_id": None},
                                          content_type="application/json", secure=True).status_code, 403)
        for value in (1, 1.5, True, None, "0", "-1", ".50", "1e2", " 1.00", "1,00", "1.001", "1000000000000", "１.００"):
            with self.subTest(value=value):
                self.assertEqual(self.write(value).status_code, 400)
        self.assertEqual(self.write("1.00", extra={"actor": "spoofed"}).status_code, 400)
        self.assertEqual(self.client.post(self.path, {"amount": "1.00", "currency": "ARS"},
                                          content_type="application/json", secure=True,
                                          HTTP_X_CSRFTOKEN=self.token).status_code, 400)
        self.assertEqual(self.write("1.00", str(uuid4())).status_code, 409)
        self.assertEqual(self.client.get(PREFIX + f"/catalog/variants/{uuid4()}/price", secure=True).status_code, 404)
        self.assertFalse(VariantPriceRevision.objects.exists())

    def test_audit_failure_rolls_back_http_write(self):
        original = audit_application.record_variant_price_set
        self.client.raise_request_exception = False
        for after in (False, True):
            def fail(**kwargs):
                if after:
                    original(**kwargs)
                raise DatabaseError("fictional private audit detail")
            with self.subTest(after=after), patch.object(audit_application, "record_variant_price_set", side_effect=fail):
                response = self.write("1.00")
                self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                self.assertEqual(response.status_code, 503)
            self.assertFalse(VariantPriceRevision.objects.exists())
            self.assertFalse(CatalogPriceAuditEntry.objects.exists())
