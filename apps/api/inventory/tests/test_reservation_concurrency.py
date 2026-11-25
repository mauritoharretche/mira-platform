from datetime import timedelta
from queue import Queue
from threading import Barrier, Thread
from uuid import UUID, uuid4
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from inventory import reservations as app
from inventory.contracts import StockConflictError
from inventory.models import StockMovement, StockReservation
from inventory.tests.test_reservations import ReservationFixture
from inventory.lot_application import set_lot_status, transfer_lot, create_location
from inventory.application import receive_stock_for_purchase
from inventory.contracts import InventoryUnit


class ReservationConcurrencyTests(ReservationFixture, TransactionTestCase):
    def race(self, left, right):
        barrier=Barrier(2,timeout=10); results=Queue()
        def run(call):
            close_old_connections()
            try:
                barrier.wait()
                results.put(("ok",call()))
            except StockConflictError:
                results.put(("conflict",None))
            except BaseException as error:
                results.put(("error",type(error).__name__))
            finally: close_old_connections()
        threads=[Thread(target=run,args=(call,)) for call in (left,right)]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(),"Inventory command failed to finish")
        outcome=[results.get_nowait() for _ in threads]
        self.assertFalse(any(result[0]=="error" for result in outcome),outcome)
        return outcome

    def test_concurrent_last_units_cannot_oversell(self):
        outcome=self.race(lambda:self.reserve(5),lambda:self.reserve(5))
        self.assertCountEqual([r[0] for r in outcome],["ok","conflict"])
        self.assertEqual(StockReservation.objects.count(),1)
        self.assertEqual(self.available().reserved,5)
        self.reconcile()

    def test_duplicate_consumption_uses_one_movement(self):
        reservation=self.reserve(5); operation=uuid4()
        outcome=self.race(lambda:self.transition(reservation,"consume",operation_id=operation),
                          lambda:self.transition(reservation,"consume",operation_id=operation))
        self.assertEqual([r[0] for r in outcome],["ok","ok"])
        self.assertEqual(StockMovement.objects.filter(kind="CONSUMPTION").count(),1)
        self.assertEqual(self.available().physical,0)
        self.reconcile()

    def test_release_against_consumption_has_one_terminal_winner(self):
        reservation=self.reserve(5)
        outcome=self.race(lambda:self.transition(reservation,"release"),lambda:self.transition(reservation,"consume"))
        self.assertCountEqual([r[0] for r in outcome],["ok","conflict"])
        state=StockReservation.objects.get().state
        self.assertIn(state,("RELEASED","CONSUMED"))
        self.assertEqual(self.available().physical,0 if state=="CONSUMED" else 5)
        self.reconcile()

    def test_expiration_against_consumption_at_deadline_never_consumes(self):
        expiry=timezone.now()+timedelta(seconds=10)
        reservation=self.reserve(5,expires_at=expiry)
        with patch("inventory.reservations.timezone.now",return_value=expiry):
            outcome=self.race(lambda:self.transition(reservation,"expire"),lambda:self.transition(reservation,"consume"))
        self.assertEqual([r[0] for r in outcome],["ok","ok"])
        self.assertEqual(StockReservation.objects.get().state,"EXPIRED")
        self.assertFalse(StockMovement.objects.filter(kind="CONSUMPTION").exists())
        self.reconcile()

    def test_safety_quarantine_and_reservation_race_never_leaves_ineligible_active_claim(self):
        outcome=self.race(lambda:self.reserve(5),lambda:set_lot_status(principal=self.principal,
            lot_id=self.lot.pk,status="QUARANTINE",expected_version=1,reason="Fictional safety check"))
        self.assertEqual(self.available().available,0)
        self.assertFalse(StockReservation.objects.filter(state="ACTIVE").exists())
        self.reconcile()

    def test_transfer_and_reservation_race_preserves_exact_units(self):
        target=create_location(principal=self.principal,code="FIC-NOT-ELIGIBLE",name="Fictional receiving shelf")
        self.race(lambda:self.reserve(5),lambda:transfer_lot(principal=self.principal,lot_id=self.lot.pk,
            source_id=self.location.pk,destination_id=target.pk,quantity=5,expected_version=1,reason="Fictional relocation"))
        self.assertIn(self.available().reserved,(0,5))
        self.assertEqual(self.available().physical,5)
        self.reconcile()

    def test_receipt_contract_and_reservation_race_reconcile(self):
        self.race(lambda:self.reserve(5),lambda:receive_stock_for_purchase(principal=self.principal,
            variant_id=self.variant.pk,unit=InventoryUnit.EACH,quantity=2,expected_version=1))
        self.assertEqual((self.available().physical,self.available().unallocated,self.available().reserved),(7,2,5))
        self.reconcile()
