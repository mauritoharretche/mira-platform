import json
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase

from catalog.models import Product, Variant
from identity.tests.test_http_authentication import csrf, make_operator, operator_client, sign_in
from procurement.models import GoodsReceipt, PurchaseOrder, Supplier


ROOT = "/api/v1/operator"


class ProcurementHttpTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        self.user = make_operator(username="fictional-proc-http", permissions=())
        self.client = operator_client()
        self.assertEqual(sign_in(self.client, username=self.user.username).status_code, 200)
        product = Product.objects.create(name="Fictional HTTP procurement product")
        self.variant = Variant.objects.create(product=product, label="Fictional pack", sku="FICTIONAL-HTTP-PROC")

    def allow(self, *names):
        for name in names:
            app, codename = name.split(".")
            self.user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))

    def send(self, path, value):
        return self.client.post(ROOT + path, json.dumps(value), content_type="application/json",
                                secure=True, HTTP_X_CSRFTOKEN=csrf(self.client))

    def read(self, path):
        return self.client.get(ROOT + path, secure=True)

    def test_authorization_precedes_payload_and_existence_and_session_hints_are_granular(self):
        malformed = {"unexpected": "fictional"}
        self.assertEqual(self.send("/procurement/suppliers", malformed).status_code, 403)
        self.assertEqual(self.send("/procurement/orders/not-a-uuid/receipts", malformed).status_code, 403)
        self.assertEqual(self.read("/procurement/suppliers/not-a-uuid").status_code, 403)
        self.assertEqual(self.read("/catalog/variants/search?q=fictional").status_code, 403)
        self.allow("procurement.view_supplier")
        state = self.read("/auth/session").json()["capabilities"]
        self.assertIn("procurement.read_supplier", state)
        self.assertNotIn("procurement.manage_supplier", state)
        self.assertNotIn("procurement.receive_purchase_order", state)
        self.allow("procurement.receive_purchase_order")
        self.assertNotIn("procurement.receive_purchase_order", self.read("/auth/session").json()["capabilities"])
        self.allow("inventory.receive_stock")
        self.assertIn("procurement.receive_purchase_order", self.read("/auth/session").json()["capabilities"])

    def test_http_supplier_order_receipt_and_idempotent_lookup(self):
        self.allow("procurement.view_supplier", "procurement.manage_supplier", "procurement.view_purchaseorder",
                   "procurement.create_purchase_order", "procurement.edit_purchase_order", "procurement.confirm_purchase_order",
                   "procurement.receive_purchase_order", "inventory.receive_stock", "catalog.view_product", "catalog.view_variant",
                   "inventory.view_stockposition")
        self.assertEqual(self.send("/procurement/suppliers", {"name": "Fictional HTTP Supplier", "extra": "no"}).status_code, 400)
        supplier_response = self.send("/procurement/suppliers", {"name": "Fictional HTTP Supplier"})
        self.assertEqual(supplier_response.status_code, 201)
        supplier_id = supplier_response.json()["supplier"]["id"]
        self.assertEqual(self.send("/procurement/suppliers", {"name": "fictional http supplier"}).status_code, 409)
        self.assertEqual(len(self.read("/procurement/suppliers?search=HTTP").json()["suppliers"]), 1)
        self.assertEqual(len(self.read("/catalog/variants/search?q=FICTIONAL-HTTP-PROC").json()["variants"]), 1)
        line = {"variant_id": str(self.variant.pk), "quantity": "5", "unit": "EACH", "unit_cost_ars": "12.30"}
        self.assertEqual(self.send("/procurement/orders", {"supplier_id": supplier_id, "lines": [dict(line, quantity=5)]}).status_code, 400)
        created = self.send("/procurement/orders", {"supplier_id": supplier_id, "lines": [line]})
        self.assertEqual(created.status_code, 201)
        order = created.json()["order"]
        self.assertEqual(order["total_cost_ars"], "61.50")
        self.assertEqual(order["lines"][0]["variant_sku"], "FICTIONAL-HTTP-PROC")
        order_id = order["id"]
        changed = self.send(f"/procurement/orders/{order_id}", {"supplier_id": supplier_id,
                            "expected_version": "1", "lines": [dict(line, quantity="6")]})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.send(f"/procurement/orders/{order_id}/confirm", {"expected_version": "1"}).status_code, 409)
        confirmed = self.send(f"/procurement/orders/{order_id}/confirm", {"expected_version": "2"})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["order"]["status"], "CONFIRMED")
        order_line = confirmed.json()["order"]["lines"][0]
        operation = str(uuid4())
        payload = {"operation_id": operation, "expected_version": "3", "lines": [{
            "order_line_id": order_line["id"], "delivered_quantity": "2", "accepted_quantity": "2",
            "difference_reason": None, "expected_stock_version": None,
        }]}
        accepted = self.send(f"/procurement/orders/{order_id}/receipts", payload)
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.json()["order"]["status"], "PARTIAL")
        self.assertEqual(accepted.json()["order"]["lines"][0]["pending_quantity"], "4")
        replay = self.send(f"/procurement/orders/{order_id}/receipts", payload)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["receipt"]["id"], accepted.json()["receipt"]["id"])
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertEqual(self.read(f"/procurement/receipts/operations/{operation}").json()["receipt"]["id"],
                         accepted.json()["receipt"]["id"])
        self.assertEqual(self.send(f"/procurement/orders/{order_id}/receipts", dict(payload, operation_id=str(uuid4()),
                         lines=[dict(payload["lines"][0], delivered_quantity="7", accepted_quantity="7")])).status_code, 409)
        self.assertEqual((Supplier.objects.count(), PurchaseOrder.objects.count()), (1, 1))
