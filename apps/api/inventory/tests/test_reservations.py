from datetime import timedelta
from uuid import UUID, uuid4
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from audit.models import InventoryMovementAuditEntry, ReservationAuditEntry
from identity.contracts import AccessDenied
from inventory import reservations as app
from inventory.application import adjust_stock, receive_stock
from inventory.contracts import InventoryUnit, InventoryValidationError, ReservationLine, StockConflictError
from inventory.lot_application import create_location, create_lot, correct_lot, set_lot_status, transfer_lot
from inventory.models import (InventoryOperation, ReservationAllocation, ReservationEvent, StockLotBalance,
                              StockMovement, StockPosition, StockReservation)
from inventory.tests.test_application import assert_test_database, make_principal, make_variant


class ReservationFixture:
    def setUp(self):
        assert_test_database(self)
        self.user, self.principal = make_principal(username="fictional-reservation-operator")
        self.variant = make_variant()
        self.location = create_location(principal=self.principal, code="FIC-ELIGIBLE", name="Fictional shelf")
        app.set_location_eligibility(principal=self.principal, location_id=self.location.pk,
                                    eligible=True, expected_version=1, reason="Fictional verified location")
        self.lot = self.physical(self.variant, 5)

    def physical(self, variant, quantity, *, expires=None, code=None, location=None):
        lot = create_lot(principal=self.principal, variant_id=variant.pk, code=code or "FIC-"+uuid4().hex, expires_on=expires)
        position = StockPosition.objects.filter(variant_id=variant.pk).first()
        stock = receive_stock(principal=self.principal, variant_id=variant.pk, unit=InventoryUnit.EACH,
                              quantity=quantity, expected_version=position.version if position else None)
        app.classify_stock(principal=self.principal, operation_id=uuid4(), lot_id=lot.pk,
                           location_id=(location or self.location).pk, quantity=quantity,
                           expected_stock_version=stock.version, evidence="Fictional verified count and lot label")
        return lot

    def reserve(self, quantity=5, **kwargs):
        values = dict(principal=self.principal, operation_id=uuid4(), source="fictional-review",
            reference="Fictional operation", expires_at=timezone.now()+timedelta(minutes=10),
            lines=(ReservationLine(self.variant.pk, quantity),))
        values.update(kwargs)
        return app.create_reservation(**values)

    def transition(self, reservation, action, **kwargs):
        return app.transition_reservation(principal=self.principal, operation_id=kwargs.get("operation_id",uuid4()),
                                          reservation_id=UUID(reservation["id"]), action=action)

    def available(self, variant=None):
        return app.query_availability(principal=self.principal, variant_id=(variant or self.variant).pk)

    def reconcile(self):
        for position in StockPosition.objects.all():
            movements = list(StockMovement.objects.filter(position=position).order_by("sequence"))
            self.assertEqual(position.on_hand, sum(m.delta for m in movements))
            allocated = sum(StockLotBalance.objects.filter(lot__variant_id=position.variant_id).values_list("quantity",flat=True))
            self.assertLessEqual(allocated, position.on_hand)
            for movement in movements:
                audit = InventoryMovementAuditEntry.objects.get(movement_id=movement.pk)
                self.assertEqual((audit.delta,audit.quantity_after),(movement.delta,movement.quantity_after))
        self.assertEqual(ReservationEvent.objects.count(),ReservationAuditEntry.objects.count())
        for event in ReservationEvent.objects.all():
            audit=ReservationAuditEntry.objects.get(event_id=event.pk)
            self.assertEqual((audit.actor_id,audit.action,audit.reservation_id),(event.actor_id,event.action,event.reservation_id))


class ReservationTests(ReservationFixture, TestCase):
    def test_last_unit_reserve_then_release_preserves_physical(self):
        reservation=self.reserve(5)
        self.assertEqual((self.available().available,self.available().reserved,self.available().physical),(0,5,5))
        with self.assertRaises(StockConflictError): self.reserve(1)
        result=self.transition(reservation,"release")
        self.assertEqual(result["state"],"RELEASED")
        self.assertEqual(self.available().available,5)
        self.assertEqual(StockMovement.objects.count(),1)
        self.reconcile()

    def test_multi_variant_all_or_nothing(self):
        other=make_variant()
        self.physical(other,2)
        before=StockReservation.objects.count()
        with self.assertRaises(StockConflictError):
            self.reserve(lines=(ReservationLine(self.variant.pk,3),ReservationLine(other.pk,3)))
        self.assertEqual(StockReservation.objects.count(),before)
        self.assertEqual(self.available().available,5)
        result=self.reserve(lines=(ReservationLine(self.variant.pk,3),ReservationLine(other.pk,2)))
        self.assertEqual(sum(int(a["quantity"]) for a in result["allocations"]),5)
        self.assertEqual(self.available(other).available,0)

    def test_fefo_expiry_boundary_and_undated_across_locations(self):
        now=timezone.now(); today=timezone.localdate(now)
        soon=self.physical(self.variant,2,expires=today,code="FIC-SOON")
        later=self.physical(self.variant,2,expires=today+timedelta(days=1),code="FIC-LATER")
        second=create_location(principal=self.principal,code="FIC-SECOND",name="Fictional second shelf")
        app.set_location_eligibility(principal=self.principal,location_id=second.pk,eligible=True,expected_version=1,reason="verified")
        first=self.physical(self.variant,1,expires=today,code="FIC-A-FIRST",location=second)
        self.physical(self.variant,3,expires=today-timedelta(days=1),code="FIC-EXPIRED")
        with patch("inventory.reservations.timezone.now",return_value=now):
            result=self.reserve(4)
        allocations=list(ReservationAllocation.objects.filter(reservation_id=result["id"]).select_related("balance__lot"))
        self.assertEqual({a.balance.lot_id: a.quantity for a in allocations},
                         {first.pk:1,soon.pk:2,later.pk:1})
        self.assertEqual(self.available().eligible,10)
        self.assertTrue(all(a.balance.lot.expires_on is not None for a in allocations))

    def test_more_than_100_lots_do_not_truncate_availability(self):
        # Build through controlled commands, without fabricated provenance.
        for i in range(101): self.physical(self.variant,1,code=f"FIC-{i:03}")
        self.assertEqual(self.available().available,106)
        result=self.reserve(106)
        self.assertEqual(sum(int(a["quantity"]) for a in result["allocations"]),106)
        self.assertEqual(self.available().available,0)

    def test_ineligible_unallocated_blocked_and_quarantined_excluded_without_double_subtraction(self):
        stock=StockPosition.objects.get(variant_id=self.variant.pk)
        receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.EACH,quantity=2,expected_version=stock.version)
        ineligible=create_location(principal=self.principal,code="FIC-UNVERIFIED",name="Fictional unverified shelf")
        self.physical(self.variant,3,location=ineligible)
        blocked=self.physical(self.variant,4)
        quarantine=self.physical(self.variant,6)
        set_lot_status(principal=self.principal,lot_id=blocked.pk,status="BLOCKED",expected_version=1,reason="Fictional hold")
        set_lot_status(principal=self.principal,lot_id=quarantine.pk,status="QUARANTINE",expected_version=1,reason="Fictional check")
        result=self.reserve(2)
        a=self.available()
        self.assertEqual((a.physical,a.allocated,a.unallocated,a.eligible,a.reserved,a.available,a.unavailable,a.safety_stock),
                         (20,18,2,5,2,3,17,0))
        self.assertEqual(a.unavailable,a.physical-a.available)

    def test_idempotency_canonical_request_and_operation_lookup(self):
        operation=uuid4(); expiry=timezone.now()+timedelta(minutes=5)
        first=self.reserve(2,operation_id=operation,expires_at=expiry)
        second=self.reserve(2,operation_id=operation,expires_at=expiry)
        self.assertEqual(first,second)
        self.assertEqual(app.query_operation(principal=self.principal,operation_id=operation)["result"],first)
        with self.assertRaises(StockConflictError): self.reserve(3,operation_id=operation,expires_at=expiry)
        with self.assertRaises(StockConflictError): self.transition(first,"release",operation_id=operation)
        self.assertEqual(StockReservation.objects.count(),1)

    def test_release_and_consumption_each_exactly_once(self):
        result=self.reserve(3)
        operation=uuid4()
        consumed=self.transition(result,"consume",operation_id=operation)
        self.assertEqual(consumed["state"],"CONSUMED")
        self.assertEqual(self.transition(result,"consume",operation_id=operation),consumed)
        self.assertEqual(self.transition(result,"consume")["state"],"CONSUMED")
        with self.assertRaises(StockConflictError): self.transition(result,"release")
        self.assertEqual((self.available().physical,self.available().available),(2,2))
        self.assertEqual(StockMovement.objects.filter(kind="CONSUMPTION").count(),1)
        self.assertEqual(InventoryMovementAuditEntry.objects.filter(action="inventory.stock_consumed").count(),1)
        self.reconcile()

    def test_expiry_effective_without_cleanup_and_cleanup_idempotent(self):
        expires=timezone.now()+timedelta(minutes=1)
        reservation=self.reserve(5,expires_at=expires)
        with patch("inventory.reservations.timezone.now",return_value=expires):
            self.assertEqual(self.available().available,5)
            self.assertEqual(app.read_reservation(principal=self.principal,reservation_id=UUID(reservation["id"]))["effective_state"],"EXPIRED")
            self.assertEqual(self.transition(reservation,"consume")["state"],"EXPIRED")
            self.assertEqual(app.expire_reservations(principal=self.principal),0)
            self.assertEqual(app.expire_reservations(principal=self.principal),0)
        self.assertEqual(StockMovement.objects.filter(kind="CONSUMPTION").count(),0)
        self.assertEqual(ReservationEvent.objects.filter(action="reservation.expired").count(),1)
        self.reconcile()

    def test_explicit_cleanup_expires_due_only_once(self):
        expires=timezone.now()+timedelta(seconds=1)
        self.reserve(expires_at=expires)
        with patch("inventory.reservations.timezone.now",return_value=expires):
            self.assertEqual(app.expire_reservations(principal=self.principal),1)
            self.assertEqual(app.expire_reservations(principal=self.principal),0)

    def test_safety_status_invalidates_whole_multi_variant_reservation(self):
        other=make_variant(); self.physical(other,3)
        reservation=self.reserve(lines=(ReservationLine(self.variant.pk,5),ReservationLine(other.pk,3)))
        set_lot_status(principal=self.principal,lot_id=self.lot.pk,status="QUARANTINE",expected_version=1,reason="Fictional safety recall")
        read=app.read_reservation(principal=self.principal,reservation_id=UUID(reservation["id"]))
        self.assertEqual(read["state"],"RELEASED")
        self.assertEqual(self.available(other).available,3)
        self.assertEqual(self.available().available,0)
        self.assertEqual(ReservationEvent.objects.filter(action="reservation.invalidated").count(),1)
        self.reconcile()

    def test_location_ineligibility_releases_reservations(self):
        self.reserve()
        app.set_location_eligibility(principal=self.principal,location_id=self.location.pk,
                                    eligible=False,expected_version=2,reason="Fictional location hold")
        self.assertEqual(StockReservation.objects.get().state,"RELEASED")
        self.assertEqual(self.available().eligible,0)

    def test_transfer_and_correction_cannot_remove_reserved_units(self):
        self.reserve(4)
        second=create_location(principal=self.principal,code="FIC-B",name="Fictional B")
        with self.assertRaises(StockConflictError):
            transfer_lot(principal=self.principal,lot_id=self.lot.pk,source_id=self.location.pk,destination_id=second.pk,
                         quantity=2,expected_version=1,reason="Fictional transfer")
        with self.assertRaises(StockConflictError):
            correct_lot(principal=self.principal,lot_id=self.lot.pk,location_id=self.location.pk,
                        delta=-2,expected_lot_version=1,expected_stock_version=1,reason="Fictional count")
        with self.assertRaises(StockConflictError):
            adjust_stock(principal=self.principal,variant_id=self.variant.pk,new_on_hand=4,expected_version=1,reason="Fictional aggregate")
        transfer_lot(principal=self.principal,lot_id=self.lot.pk,source_id=self.location.pk,destination_id=second.pk,
                     quantity=1,expected_version=1,reason="Fictional transfer of unreserved unit")
        self.assertEqual(self.available().reserved,4)
        self.reconcile()

    def test_consume_revalidates_expired_lot_and_outer_transaction_rolls_back(self):
        today=timezone.localdate()
        dated=self.physical(self.variant,2,expires=today,code="FIC-DATED")
        reservation=self.reserve(2,expires_at=timezone.now()+timedelta(days=3))
        with patch("inventory.reservations.timezone.now",return_value=timezone.now()+timedelta(days=1)):
            self.assertEqual(self.transition(reservation,"consume")["state"],"RELEASED")
        other=self.reserve(2)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.transition(other,"consume")
                raise RuntimeError("Fictional outer POS rollback")
        self.assertEqual(app.read_reservation(principal=self.principal,reservation_id=UUID(other["id"]))["state"],"ACTIVE")
        self.assertFalse(StockMovement.objects.filter(kind="CONSUMPTION").exists())
        self.reconcile()

    def test_audit_failure_rolls_back_create_consume_and_classification(self):
        with patch("audit.application.record_reservation_event",side_effect=RuntimeError("Fictional audit failure")):
            with self.assertRaises(RuntimeError): self.reserve()
        self.assertEqual(StockReservation.objects.count(),0)
        reservation=self.reserve()
        with patch("audit.application.record_reservation_event",side_effect=RuntimeError("Fictional audit failure")):
            with self.assertRaises(RuntimeError): self.transition(reservation,"consume")
        self.assertEqual(self.available().physical,5)
        self.assertFalse(StockMovement.objects.filter(kind="CONSUMPTION").exists())
        self.reconcile()

    def test_strict_integer_limits_and_permissions(self):
        for value in (True,0,-1,1.0,"1",2**63):
            with self.subTest(value=value),self.assertRaises(InventoryValidationError): self.reserve(value)
        with self.assertRaises(InventoryValidationError): self.reserve(lines=(ReservationLine(self.variant.pk,1),)*2)
        unused,denied=make_principal(username="fictional-denied-reservation",grant=False)
        with self.assertRaises(AccessDenied): app.query_availability(principal=denied,variant_id=uuid4())
        with self.assertRaises(AccessDenied): self.reserve(principal=denied)
        with self.assertRaises(AccessDenied): app.query_operation(principal=denied,operation_id=uuid4())

    def test_classification_does_not_increase_stock_or_invent_history(self):
        stock=receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.EACH,quantity=2,expected_version=1)
        lot=create_lot(principal=self.principal,variant_id=self.variant.pk,code="FIC-VERIFIED",expires_on=None)
        before=StockMovement.objects.count(); operation=uuid4()
        kwargs=dict(principal=self.principal,operation_id=operation,lot_id=lot.pk,location_id=self.location.pk,
                    quantity=2,expected_stock_version=stock.version,evidence="Fictional counted units; label physically verified")
        result=app.classify_stock(**kwargs)
        self.assertEqual(app.classify_stock(**kwargs),result)
        self.assertEqual((StockMovement.objects.count(),self.available().physical,self.available().unallocated),(before,7,0))
        with self.assertRaises(StockConflictError): app.classify_stock(**{**kwargs,"operation_id":uuid4(),"quantity":1})
        self.reconcile()

    def test_maximum_each_consumption_and_overflow_preserve_nonnegative_ledger(self):
        variant=make_variant()
        self.physical(variant,2**63-1)
        reservation=self.reserve(lines=(ReservationLine(variant.pk,2**63-1),))
        self.transition(reservation,"consume")
        self.assertEqual(self.available(variant).physical,0)
        self.assertEqual(StockMovement.objects.get(position__variant_id=variant.pk,kind="CONSUMPTION").delta,-(2**63-1))
        self.reconcile()

    def test_classification_audit_failure_after_insert_rolls_back_only_new_classification(self):
        receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.EACH,quantity=1,expected_version=1)
        before=(self.available().unallocated,ReservationEvent.objects.count(),ReservationAuditEntry.objects.count())
        from audit import application as audit
        original=audit.record_reservation_event
        def fail(**kwargs):
            original(**kwargs)
            raise RuntimeError("Fictional failure after attribution insert")
        with patch.object(audit,"record_reservation_event",side_effect=fail):
            with self.assertRaises(RuntimeError):
                app.classify_stock(principal=self.principal,operation_id=uuid4(),lot_id=self.lot.pk,
                    location_id=self.location.pk,quantity=1,expected_stock_version=2,evidence="Fictional verified label")
        self.assertEqual((self.available().unallocated,ReservationEvent.objects.count(),ReservationAuditEntry.objects.count()),before)
        self.reconcile()
