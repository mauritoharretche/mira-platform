from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.contrib.auth.models import Permission
from django.test import TestCase

from audit import application as audit_application
from audit.models import InventoryMovementAuditEntry
from catalog.models import Product, Variant
from identity.tests.test_http_authentication import PREFIX, make_operator, operator_client, sign_in
from inventory.application import receive_stock
from inventory.contracts import InventoryUnit, MAX_STOCK_INTEGER
from inventory.models import StockMovement, StockPosition
from inventory.tests.test_application import assert_test_database


class InventoryHTTPTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user = make_operator()
        self.client = operator_client()
        self.token = sign_in(self.client).json()["csrf_token"]
        product = Product.objects.create(name="Fictional HTTP inventory")
        self.variant = Variant.objects.create(product=product, label="Initial", sku="fictional-inventory-http")
        self.path = PREFIX + "/inventory/variants/" + str(self.variant.pk)

    def receipt(self, payload=None):
        return self.client.post(self.path + "/initial-receipt", {"quantity": "10", "unit": "EACH", "expected_version": None} if payload is None else payload,
                                content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=self.token)

    def test_absence_initial_receipt_history_attribution_and_conflict(self):
        self.assertEqual(self.client.get(self.path + "/stock", secure=True).json(), {"stock": None})
        self.assertEqual(self.client.get(self.path + "/movements", secure=True).json(), {"movements": [], "next_cursor": None})
        response = self.receipt()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["stock"]["on_hand"], response.json()["stock"]["version"]), ("10", "1"))
        history = self.client.get(self.path + "/movements", secure=True).json()["movements"]
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]["quantity_before"], history[0]["delta"], history[0]["quantity_after"]), ("0", "10", "10"))
        audit = InventoryMovementAuditEntry.objects.get()
        self.assertEqual((audit.actor_id, str(audit.movement_id)), (str(self.user.pk), history[0]["movement_id"]))
        self.assertEqual(self.receipt().status_code, 409)
        self.assertEqual((StockPosition.objects.get().on_hand, StockMovement.objects.count(), InventoryMovementAuditEntry.objects.count()), (10, 1, 1))

    def test_bigint_quantity_version_and_sequence_are_lossless_strings(self):
        response = self.receipt({"quantity": str(MAX_STOCK_INTEGER), "unit": "EACH", "expected_version": None})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["stock"]["on_hand"], "9223372036854775807")
        movement = self.client.get(self.path + "/movements", secure=True).json()["movements"][0]
        self.assertEqual(movement["delta"], "9223372036854775807")
        self.assertIsInstance(movement["sequence"], str)

    def test_invalid_types_overflow_units_missing_expectation_and_authority_fields(self):
        base = {"quantity": "1", "unit": "EACH", "expected_version": None}
        changes = [{"quantity": value} for value in (True, 1, 1.5, "0", "-1", "01", " 1", "1.0", "１", str(MAX_STOCK_INTEGER + 1))]
        changes += [{"unit": value} for value in ("KG", "each", 1)]
        changes += [{"expected_version": value} for value in (0, "1", 1, False)]
        changes += [{"principal": 1}, {"actor": "fictional"}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(self.receipt(base | change).status_code, 400)
        self.assertEqual(self.receipt({"quantity": "1", "unit": "EACH"}).status_code, 400)
        self.assertFalse(StockPosition.objects.exists())

    def test_authorization_precedes_business_validation_and_missing_variant_checks(self):
        self.user.user_permissions.clear()
        with patch("inventory.application.receive_stock") as receive:
            self.assertEqual(self.receipt({"quantity": "invalid"}).status_code, 403)
        receive.assert_not_called()
        for operation in ("stock", "movements"):
            self.assertEqual(self.client.get(PREFIX + "/inventory/variants/invalid/" + operation, secure=True).status_code, 403)

    def test_each_inventory_route_enforces_its_independent_capability(self):
        for operation, codename in (("initial-receipt", "receive_stock"), ("stock", "view_stockposition"), ("movements", "view_stockmovement")):
            permission = Permission.objects.get(content_type__app_label="inventory", codename=codename)
            self.user.user_permissions.remove(permission)
            path = PREFIX + "/inventory/variants/invalid/" + operation
            response = (self.client.post(path, {}, content_type="application/json", secure=True, HTTP_X_CSRFTOKEN=self.token)
                        if operation == "initial-receipt" else self.client.get(path, secure=True))
            self.assertEqual(response.status_code, 403)
            self.user.user_permissions.add(permission)

    def test_missing_variant_is_not_uninitialized_stock(self):
        self.path = PREFIX + "/inventory/variants/" + str(uuid4())
        for operation in ("stock", "movements"):
            self.assertEqual(self.client.get(self.path + "/" + operation, secure=True).status_code, 404)
        self.assertEqual(self.receipt().status_code, 404)

    def test_audit_failure_before_and_after_insertion_rolls_back_all_inventory(self):
        original = audit_application.record_inventory_movement
        for after in (False, True):
            def fail(**kwargs):
                if after:
                    original(**kwargs)
                raise DatabaseError("fictional private inventory detail")
            with self.subTest(after=after), patch.object(audit_application, "record_inventory_movement", side_effect=fail):
                response = self.receipt()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": "unavailable"}})
                self.assertFalse(StockPosition.objects.exists())
                self.assertFalse(StockMovement.objects.exists())
                self.assertFalse(InventoryMovementAuditEntry.objects.exists())

    def test_cursor_pagination_is_stable_and_does_not_change_internal_contract(self):
        for index in range(53):
            receive_stock(principal=self.user, variant_id=self.variant.pk, unit=InventoryUnit.EACH,
                          quantity=1, expected_version=index or None)
        first = self.client.get(self.path + "/movements", secure=True).json()
        self.assertEqual((len(first["movements"]), first["next_cursor"]), (50, "4"))
        second = self.client.get(self.path + "/movements?before_sequence=4", secure=True).json()
        self.assertEqual([item["sequence"] for item in second["movements"]], ["3", "2", "1"])
        self.assertIsNone(second["next_cursor"])
        for query in ("before_sequence=0", "before_sequence=01", "before_sequence=1&before_sequence=2", "actor=1"):
            self.assertEqual(self.client.get(self.path + "/movements?" + query, secure=True).status_code, 400)
