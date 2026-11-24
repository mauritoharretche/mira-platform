import json

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase

from catalog.models import Product, Variant
from identity.tests.test_http_authentication import csrf, make_operator, operator_client, sign_in


ROOT = "/api/v1/operator/inventory"


class LotHttpTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_mira")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone(), ("test_mira",))
        self.user = make_operator(username="fictional-lot-http", permissions=())
        self.client = operator_client()
        self.assertEqual(sign_in(self.client, username=self.user.username).status_code, 200)
        product = Product.objects.create(name="Fictional HTTP lot product")
        self.variant = Variant.objects.create(product=product, label="Fictional pack", sku="FICTIONAL-LOT-HTTP")

    def allow(self, *names):
        for name in names:
            app, code = name.split(".")
            self.user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))

    def send(self, path, payload):
        return self.client.post(ROOT + path, json.dumps(payload), content_type="application/json",
                                secure=True, HTTP_X_CSRFTOKEN=csrf(self.client))

    def read(self, path):
        return self.client.get(ROOT + path, secure=True)

    def test_denial_precedes_payload_and_existence_and_session_is_granular(self):
        self.assertEqual(self.send("/locations", {"unexpected": 1}).status_code, 403)
        self.assertEqual(self.send("/lots/not-a-uuid/status", {"unexpected": 1}).status_code, 403)
        self.assertEqual(self.read("/variants/not-a-uuid/lots").status_code, 403)
        self.allow("inventory.view_stocklot")
        self.assertNotIn("inventory.read_stock_lots", self.client.get(
            "/api/v1/operator/auth/session", secure=True).json()["capabilities"])
        self.allow("inventory.view_stocklocation")
        self.assertIn("inventory.read_stock_lots", self.client.get("/api/v1/operator/auth/session", secure=True).json()["capabilities"])
        self.assertEqual(self.read(f"/variants/{self.variant.pk}/lots").json()["unallocated"], "0")
        self.assertEqual(self.send("/locations", {"code": "FIC", "name": "Fictional"}).status_code, 403)

    def test_create_location_lot_and_status_with_conflict_and_strict_payload(self):
        self.allow("inventory.view_stocklot", "inventory.view_stocklocation",
                   "inventory.manage_stock_locations", "inventory.manage_stock_lots")
        self.assertEqual(self.send("/locations", {"code": "FIC", "name": "Fictional", "extra": 1}).status_code, 400)
        location = self.send("/locations", {"code": "FIC", "name": "Fictional shelf"})
        self.assertEqual(location.status_code, 201)
        self.assertEqual(self.send("/locations", {"code": "fic", "name": "Duplicate"}).status_code, 409)
        created = self.send(f"/variants/{self.variant.pk}/lots", {"code": "FIC-LOT", "expires_on": None})
        self.assertEqual(created.status_code, 201)
        lot = created.json()["lot"]
        state = self.read(f"/variants/{self.variant.pk}/lots")
        self.assertEqual((state.status_code, state.json()["lots"][0]["code"]), (200, "FIC-LOT"))
        changed = self.send(f"/lots/{lot['id']}/status", {"status": "QUARANTINE", "reason": "Fictional inspection",
                                                         "expected_version": "1"})
        self.assertEqual((changed.status_code, changed.json()["version"]), (200, "2"))
        self.assertEqual(self.send(f"/lots/{lot['id']}/status", {"status": "ACTIVE", "reason": "Stale",
                                                              "expected_version": "1"}).status_code, 409)
