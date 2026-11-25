import json
from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from identity.tests.test_http_authentication import csrf, make_operator, operator_client, sign_in
from inventory.tests.test_reservations import ReservationFixture
from inventory.models import StockMovement, StockReservation


ROOT="/api/v1/operator/inventory"


class ReservationHttpTests(ReservationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.client=operator_client()
        self.assertEqual(sign_in(self.client,username=self.user.username,password="fictional-test-password-only").status_code,200)

    def send(self,path,payload):
        return self.client.post(ROOT+path,json.dumps(payload),content_type="application/json",
                                secure=True,HTTP_X_CSRFTOKEN=csrf(self.client))

    def read(self,path): return self.client.get(ROOT+path,secure=True)

    def test_availability_reservation_release_and_identity_lookup(self):
        availability=self.read(f"/variants/{self.variant.pk}/availability")
        self.assertEqual((availability.status_code,availability.json()["available"]),(200,"5"))
        payload={"operation_id":str(uuid4()),"source":"fictional-http","reference":"Fictional order",
                 "expires_at":(timezone.now()+timedelta(minutes=5)).isoformat(),
                 "lines":[{"variant_id":str(self.variant.pk),"quantity":"3"}]}
        created=self.send("/reservations",payload)
        self.assertEqual(created.status_code,201)
        reservation=created.json()["reservation"]
        self.assertEqual(self.send("/reservations",payload).json()["reservation"]["id"],reservation["id"])
        self.assertEqual(self.read("/operations/"+payload["operation_id"]).json()["action"],"reserve")
        self.assertEqual(self.read(f"/reservations?variant_id={self.variant.pk}").json()["reservations"][0]["effective_state"],"ACTIVE")
        operation=str(uuid4())
        result=self.send(f"/reservations/{reservation['id']}/release",{"operation_id":operation})
        self.assertEqual((result.status_code,result.json()["reservation"]["state"]),(200,"RELEASED"))
        self.assertEqual(self.send(f"/reservations/{reservation['id']}/release",{"operation_id":operation}).status_code,200)
        self.assertEqual(self.read(f"/variants/{self.variant.pk}/availability").json()["physical"],"5")

    def test_permissions_are_independent_revocable_and_precede_validation(self):
        self.user.user_permissions.clear()
        paths=[("/reservations",{}),("/reservations/invalid/consume",{}),
               ("/reservations/invalid/release",{}),("/reservations/invalid/expire",{}),
               ("/classifications",{}),("/locations/invalid/eligibility",{})]
        for path,body in paths:
            self.assertEqual(self.send(path,body).status_code,403)
        self.assertEqual(self.read("/variants/invalid/availability").status_code,403)
        self.assertEqual(self.read("/operations/invalid").status_code,403)
        permission=Permission.objects.get(content_type__app_label="inventory",codename="read_availability")
        self.user.user_permissions.add(permission)
        self.assertEqual(self.read(f"/variants/{self.variant.pk}/availability").status_code,200)
        capabilities=self.client.get("/api/v1/operator/auth/session",secure=True).json()["capabilities"]
        self.assertIn("inventory.read_availability",capabilities)
        self.assertNotIn("inventory.reserve_stock",capabilities)
        self.user.user_permissions.remove(permission)
        self.assertEqual(self.read(f"/variants/{self.variant.pk}/availability").status_code,403)

    def test_http_strict_integers_timezone_idempotency_conflict_and_csrf(self):
        payload={"operation_id":str(uuid4()),"source":"fictional-http","reference":"Fictional order",
                 "expires_at":(timezone.now()+timedelta(minutes=5)).isoformat(),
                 "lines":[{"variant_id":str(self.variant.pk),"quantity":"1"}]}
        self.assertEqual(self.client.post(ROOT+"/reservations",json.dumps(payload),content_type="application/json",secure=True).status_code,403)
        for quantity in (1,True,"01","1.0","0",str(2**63)):
            bad={**payload,"lines":[{"variant_id":str(self.variant.pk),"quantity":quantity}]}
            self.assertEqual(self.send("/reservations",bad).status_code,400)
        self.assertEqual(self.send("/reservations",{**payload,"expires_at":"2030-01-01T12:00:00"}).status_code,400)
        self.assertEqual(self.send("/reservations",{**payload,"unexpected":True}).status_code,400)
        self.assertEqual(self.send("/reservations",payload).status_code,201)
        self.assertEqual(self.send("/reservations",{**payload,"reference":"Different"}).status_code,409)

    def test_http_consumption_is_not_a_sale_or_adjustment(self):
        result=self.reserve(2)
        response=self.send(f"/reservations/{result['id']}/consume",{"operation_id":str(uuid4())})
        self.assertEqual((response.status_code,response.json()["reservation"]["state"]),(200,"CONSUMED"))
        self.assertEqual(StockMovement.objects.filter(kind="CONSUMPTION").get().delta,-2)
        self.assertEqual(self.read(f"/variants/{self.variant.pk}/availability").json()["physical"],"3")
