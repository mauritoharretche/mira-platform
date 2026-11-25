import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from unittest.mock import patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from audit.models import InventoryMovementAuditEntry, PhysicalInventoryAuditEntry
from catalog.calculations import MassMeasurement, calculate_amount
from catalog.contracts import MeasurementUnit, PriceBasis
from catalog.application import set_variant_price
from inventory import counting, reservations
from inventory.application import receive_stock, adjust_stock
from inventory.contracts import InventoryUnit, InventoryValidationError, ReservationLine, StockConflictError
from inventory.lot_application import create_location, create_lot, transfer_lot, correct_lot
from inventory.models import PhysicalCount, PhysicalCountLine, PhysicalInventoryEvent, PhysicalLoss, StockLotBalance, StockMovement, StockPosition
from inventory.quantities import parse_quantity, format_quantity, convert_mass
from inventory.tests.test_application import assert_test_database, make_principal, make_variant
from procurement import application as procurement


class ExactMeasurementTests(SimpleTestCase):
    def test_exact_grams_kilograms_and_bounds(self):
        self.assertEqual(convert_mass("0.245",from_unit="KG",to_unit="G"),"245.000")
        self.assertEqual(convert_mass("0.001",from_unit="G",to_unit="KG"),"0.000001")
        self.assertEqual(parse_quantity("245.001",InventoryUnit.G),245001)
        self.assertEqual(format_quantity(245001,InventoryUnit.G),"245.001")
        self.assertEqual(parse_quantity("9223372036854775.807",InventoryUnit.G),2**63-1)
        for value in ("0.0001","1e3","01.0","NaN",1.0,True,"9223372036854775.808"):
            with self.subTest(value=value),self.assertRaises(InventoryValidationError):parse_quantity(value,InventoryUnit.G)
        with self.assertRaises(InventoryValidationError):parse_quantity("1.0",InventoryUnit.EACH)
        with self.assertRaises(InventoryValidationError):convert_mass("0.0000001",from_unit="KG",to_unit="G")

    def test_mass_pricing_and_half_even_cent_boundaries(self):
        result=calculate_amount(amount=Decimal("12000.00"),price_basis=PriceBasis.PER_KG,quantity=Decimal("245.001"),unit=MeasurementUnit.G)
        self.assertEqual(result.amount_ars,Decimal("2940.01"))
        self.assertEqual(calculate_amount(amount=Decimal("1.00"),price_basis=PriceBasis.PER_KG,quantity=Decimal("5"),unit=MeasurementUnit.G).amount_ars,Decimal("0.00"))
        self.assertEqual(calculate_amount(amount=Decimal("1.00"),price_basis=PriceBasis.PER_KG,quantity=Decimal("15"),unit=MeasurementUnit.G).amount_ars,Decimal("0.02"))
        with self.assertRaises(ValueError):calculate_amount(amount=Decimal("1.00"),price_basis=PriceBasis.PER_VARIANT,quantity=Decimal("1"),unit=MeasurementUnit.G)
        with self.assertRaises(ValueError):calculate_amount(amount=Decimal("1.001"),price_basis=PriceBasis.PER_KG,quantity=Decimal("1"),unit=MeasurementUnit.KG)

    def test_hardware_independent_measurement_consistency(self):
        measurement=MassMeasurement(Decimal("245.000"),MeasurementUnit.G,"fictional-manual",timezone.now(),Decimal("0.001"),Decimal("250.000"),Decimal("5.000"))
        self.assertEqual(measurement.validate(),measurement)
        for changes in ({"net":Decimal("245.001")},{"net":Decimal("245.0001")},{"tare":None},{"net":Decimal("NaN")}):
            with self.assertRaises(ValueError):MassMeasurement(**{**measurement.__dict__,**changes}).validate()


class MeasuredInventoryTests(TestCase):
    def setUp(self):
        assert_test_database(self)
        self.user,self.principal=make_principal()
        from django.contrib.auth.models import Permission
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=["catalog","procurement"]))
        self.variant=make_variant()
        self.location=create_location(principal=self.principal,code="FIC-MASS",name="Fictional measured shelf")
        reservations.set_location_eligibility(principal=self.principal,location_id=self.location.pk,eligible=True,expected_version=1,reason="verified")
        self.lot=create_lot(principal=self.principal,variant_id=self.variant.pk,code="FIC-MASS-LOT",expires_on=timezone.localdate()+timedelta(days=5))
        self.receipt=receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.G,quantity=1_000_000,expected_version=None)
        reservations.classify_stock(principal=self.principal,operation_id=uuid4(),lot_id=self.lot.pk,location_id=self.location.pk,
            quantity=1_000_000,unit=InventoryUnit.G,expected_stock_version=1,evidence="Fictional verified mass")
        self.balance=StockLotBalance.objects.get(lot=self.lot,location=self.location)

    def reserve(self,quantity=100_000):
        return reservations.create_reservation(principal=self.principal,operation_id=uuid4(),source="fictional-measurement",reference="Fictional measured reservation",
            expires_at=timezone.now()+timedelta(minutes=10),lines=(ReservationLine(self.variant.pk,quantity,InventoryUnit.G),))

    def create_count(self):
        return counting.create_count(principal=self.principal,operation_id=uuid4(),location_id=self.location.pk,balance_ids=(self.balance.pk,),blind=True)

    def observe(self,count,quantity):
        return counting.record_observation(principal=self.principal,operation_id=uuid4(),count_id=UUID(count["id"]),line_id=UUID(count["lines"][0]["id"]),
            observed=quantity,unit=InventoryUnit.G,reason="Fictional count discrepancy",evidence="Fictional witnessed measurement")

    def transition_count(self,count,action):
        return counting.transition_count(principal=self.principal,operation_id=uuid4(),count_id=UUID(count["id"]),action=action)

    def reconcile(self):
        position=StockPosition.objects.get(variant_id=self.variant.pk)
        self.assertEqual(position.on_hand,sum(StockMovement.objects.filter(position=position).values_list("delta",flat=True)))
        self.assertEqual(position.on_hand,sum(StockLotBalance.objects.filter(lot__variant_id=self.variant.pk).values_list("quantity",flat=True)))
        for movement in StockMovement.objects.filter(position=position):
            audit=InventoryMovementAuditEntry.objects.get(movement_id=movement.pk)
            self.assertEqual((audit.unit,audit.delta,audit.quantity_after),("G",movement.delta,movement.quantity_after))
        self.assertEqual(PhysicalInventoryEvent.objects.count(),PhysicalInventoryAuditEntry.objects.count())

    def test_mass_unit_frozen_and_mixed_atomic_reservation_consumption(self):
        with self.assertRaises(InventoryValidationError):receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.EACH,quantity=1,expected_version=1)
        with self.assertRaises(InventoryValidationError):adjust_stock(principal=self.principal,variant_id=self.variant.pk,new_on_hand=1,reason="wrong unit",expected_version=1)
        each=make_variant()
        receive_stock(principal=self.principal,variant_id=each.pk,unit=InventoryUnit.EACH,quantity=2,expected_version=None)
        each_lot=create_lot(principal=self.principal,variant_id=each.pk,code="FIC-EACH",expires_on=None)
        reservations.classify_stock(principal=self.principal,operation_id=uuid4(),lot_id=each_lot.pk,location_id=self.location.pk,quantity=2,expected_stock_version=1,evidence="Fictional label")
        reservation=reservations.create_reservation(principal=self.principal,operation_id=uuid4(),source="fictional-mixed",reference="Fictional mixed reservation",expires_at=timezone.now()+timedelta(minutes=5),
            lines=(ReservationLine(self.variant.pk,245_001,InventoryUnit.G),ReservationLine(each.pk,1)))
        operation=uuid4()
        result=reservations.consume_reservation(principal=self.principal,operation_id=operation,reservation_id=UUID(reservation["id"]))
        self.assertEqual(result["state"],"CONSUMED")
        self.assertEqual(reservations.consume_reservation(principal=self.principal,operation_id=operation,reservation_id=UUID(reservation["id"])),result)
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand,754999)
        self.assertEqual(StockPosition.objects.get(variant_id=each.pk).on_hand,1)
        self.reconcile()

    def test_mass_reservation_unit_mismatch_and_atomic_shortage(self):
        with self.assertRaises(InventoryValidationError):
            reservations.create_reservation(principal=self.principal,operation_id=uuid4(),source="fictional",reference="mismatch",expires_at=timezone.now()+timedelta(minutes=5),lines=(ReservationLine(self.variant.pk,1),))
        with self.assertRaises(StockConflictError):self.reserve(1_000_001)

    def test_count_blind_entry_review_and_completion(self):
        count=self.create_count();self.assertNotIn("expected",count["lines"][0])
        self.observe(count,999_500)
        self.transition_count(count,"review")
        visible=counting.read_count(principal=self.principal,count_id=UUID(count["id"]))
        self.assertEqual(visible["lines"][0]["discrepancy"],"-0.500")
        operation=uuid4()
        result=counting.transition_count(principal=self.principal,operation_id=operation,count_id=UUID(count["id"]),action="complete")
        self.assertEqual(result["state"],"COMPLETED")
        self.assertEqual(counting.transition_count(principal=self.principal,operation_id=operation,count_id=UUID(count["id"]),action="complete"),result)
        self.assertEqual(StockMovement.objects.get(kind="COUNT").delta,-500)
        self.reconcile()

    def test_reobservation_keeps_each_reason_and_evidence_in_immutable_history(self):
        count=self.create_count()
        self.observe(count,999500)
        counting.record_observation(principal=self.principal,operation_id=uuid4(),count_id=UUID(count["id"]),line_id=UUID(count["lines"][0]["id"]),
            observed=999501,unit=InventoryUnit.G,reason="Fictional second reading",evidence="Fictional independent witness")
        events=list(PhysicalInventoryEvent.objects.filter(count_id=count["id"],action="count.observed").order_by("recorded_at","id"))
        self.assertEqual([(event.details["observed"],event.details["reason"],event.details["evidence"]) for event in events],
            [("999.500","Fictional count discrepancy","Fictional witnessed measurement"),("999.501","Fictional second reading","Fictional independent witness")])
        self.assertEqual(events[1].details["previous"],"999.500")
        self.reconcile()

    def test_stale_count_rejects_receipt_and_preserves_newer_stock(self):
        count=self.create_count();self.observe(count,900000);self.transition_count(count,"review")
        receive_stock(principal=self.principal,variant_id=self.variant.pk,unit=InventoryUnit.G,quantity=1,expected_version=1)
        with self.assertRaises(StockConflictError):self.transition_count(count,"complete")
        self.assertFalse(StockMovement.objects.filter(kind="COUNT").exists())
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand,1_000_001)

    def test_count_and_loss_preserve_reserved_mass(self):
        self.reserve(999999)
        count=self.create_count();self.observe(count,999000);self.transition_count(count,"review")
        with self.assertRaises(StockConflictError):self.transition_count(count,"complete")
        with self.assertRaises(StockConflictError):counting.write_off(principal=self.principal,operation_id=uuid4(),balance_id=self.balance.pk,quantity=2,unit=InventoryUnit.G,expected_stock_version=1,reason_code="DAMAGE",evidence="Fictional spill")
        self.reconcile()

    def test_explicit_loss_idempotency_and_audit_rollback(self):
        operation=uuid4();kwargs=dict(principal=self.principal,operation_id=operation,balance_id=self.balance.pk,quantity=501,unit=InventoryUnit.G,expected_stock_version=1,reason_code="DAMAGE",evidence="Fictional documented spill")
        result=counting.write_off(**kwargs)
        self.assertEqual(result["quantity"],"0.501")
        self.assertEqual(counting.write_off(**kwargs),result)
        self.assertEqual(PhysicalLoss.objects.count(),1)
        with patch("audit.application.record_physical_inventory_event",side_effect=RuntimeError("Fictional Audit failure")):
            with self.assertRaises(RuntimeError):counting.write_off(**{**kwargs,"operation_id":uuid4(),"expected_stock_version":2})
        self.assertEqual(PhysicalLoss.objects.count(),1)
        self.assertEqual(StockMovement.objects.filter(kind="WRITE_OFF").count(),1)
        self.reconcile()

    def test_count_completion_audit_failure_and_outer_rollback(self):
        count=self.create_count();self.observe(count,800000);self.transition_count(count,"review")
        with patch("audit.application.record_physical_inventory_event",side_effect=RuntimeError("Fictional Audit failure")):
            with self.assertRaises(RuntimeError):self.transition_count(count,"complete")
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand,1_000_000)
        self.assertFalse(StockMovement.objects.filter(kind="COUNT").exists())
        with self.assertRaises(RuntimeError):
            with transaction.atomic():self.transition_count(count,"complete");raise RuntimeError("Fictional outer rollback")
        self.reconcile()

    def test_basis_only_change_is_new_revision_without_rewriting_history(self):
        first=set_variant_price(principal=self.principal,variant_id=self.variant.pk,amount=Decimal("12.00"),currency="ARS",expected_revision_id=None)
        second=set_variant_price(principal=self.principal,variant_id=self.variant.pk,amount=Decimal("12.00"),currency="ARS",expected_revision_id=first.revision_id,price_basis=PriceBasis.PER_KG)
        self.assertNotEqual(first.revision_id,second.revision_id)
        self.assertEqual((first.price_basis,second.price_basis),(PriceBasis.PER_VARIANT,PriceBasis.PER_KG))

    def test_mass_purchase_partial_receipts_cost_basis_and_rollback(self):
        variant=make_variant();supplier=procurement.create_supplier(principal=self.principal,name="Fictional mass supplier")
        order=procurement.create_order(principal=self.principal,supplier_id=supplier.pk,lines=[{"variant_id":variant.pk,"quantity":1_000_000,"unit":"G","unit_cost_ars":Decimal("1000.00"),"cost_basis":"PER_KG"}])
        self.assertEqual(procurement.order_snapshot(order)["total_cost_ars"],"1000.00")
        order=procurement.confirm_order(principal=self.principal,order_id=order.pk,expected_version=1)
        lot=create_lot(principal=self.principal,variant_id=variant.pk,code="FIC-MASS-RECEIPT",expires_on=None)
        payload=[{"order_line_id":order.lines.get().pk,"delivered_quantity":245001,"accepted_quantity":245001,"unit":"G","difference_reason":None,"expected_stock_version":None,
                  "allocations":[{"lot_id":lot.pk,"location_id":self.location.pk,"quantity":245001}]}]
        order,receipt=procurement.receive_order(principal=self.principal,order_id=order.pk,operation_id=uuid4(),expected_version=2,lines=payload)
        self.assertEqual((order.status,procurement.order_snapshot(order)["lines"][0]["pending_quantity"]),("PARTIAL","754.999"))
        self.assertEqual(StockLotBalance.objects.get(lot=lot).quantity,245001)
        self.assertEqual(procurement.receipt_snapshot(receipt)["lines"][0]["accepted_quantity"],"245.001")
        remaining=[{**payload[0],"delivered_quantity":754999,"accepted_quantity":754999,"expected_stock_version":1,
                    "allocations":[{"lot_id":lot.pk,"location_id":self.location.pk,"quantity":754999}]}]
        with patch("audit.application.record_procurement_event",side_effect=RuntimeError("Fictional mass receipt Audit failure")):
            with self.assertRaises(RuntimeError):procurement.receive_order(principal=self.principal,order_id=order.pk,operation_id=uuid4(),expected_version=3,lines=remaining)
        order.refresh_from_db()
        self.assertEqual((order.status,order.version),("PARTIAL",3))
        self.assertEqual(StockPosition.objects.get(variant_id=variant.pk).on_hand,245001)
        self.assertEqual(StockLotBalance.objects.get(lot=lot).quantity,245001)


    def test_mass_fefo_transfer_correction_release_and_expiry(self):
        from inventory.lot_application import list_lots
        second=create_location(principal=self.principal,code="FIC-OTHER",name="Fictional second shelf")
        transfer_lot(principal=self.principal,lot_id=self.lot.pk,source_id=self.location.pk,destination_id=second.pk,quantity=501,unit=InventoryUnit.G,expected_version=1,reason="Fictional move")
        correct_lot(principal=self.principal,lot_id=self.lot.pk,location_id=second.pk,delta=-1,unit=InventoryUnit.G,expected_lot_version=2,expected_stock_version=1,reason="Fictional exact correction")
        self.assertEqual(StockLotBalance.objects.get(lot=self.lot,location=second).quantity,500)
        self.assertEqual(list_lots(principal=self.principal,variant_id=self.variant.pk)["fefo"],[str(self.lot.pk)])
        reserved=self.reserve(999499)
        self.assertEqual(reserved["allocations"][0]["quantity"],"999.499")
        reservations.release_reservation(principal=self.principal,operation_id=uuid4(),reservation_id=UUID(reserved["id"]))
        expiring=self.reserve(1)
        with patch("inventory.reservations.timezone.now",return_value=timezone.now()+timedelta(minutes=11)):
            self.assertEqual(reservations.read_reservation(principal=self.principal,reservation_id=UUID(expiring["id"]))["effective_state"],"EXPIRED")
            self.assertEqual(reservations.query_availability(principal=self.principal,variant_id=self.variant.pk).reserved,0)
        self.reconcile()

    def test_package_measurement_never_changes_inventory_or_price_basis(self):
        from catalog.application import set_variant_measurement_spec, CatalogValidationError
        set_variant_measurement_spec(principal=self.principal,variant_id=self.variant.pk,sale_unit=MeasurementUnit.EACH,
            net_content_amount=Decimal("500"),net_content_unit=MeasurementUnit.G,expected_version=None)
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).accounting_unit,"G")
        self.assertEqual(reservations.query_availability(principal=self.principal,variant_id=self.variant.pk).physical,1000000)
        with self.assertRaises(CatalogValidationError):
            set_variant_price(principal=self.principal,variant_id=self.variant.pk,amount=Decimal("1.00"),currency="ARS",expected_revision_id=None,price_basis=None)

    def test_mass_fefo_consumes_exact_remaining_quantities_in_expiry_order(self):
        earlier=create_lot(principal=self.principal,variant_id=self.variant.pk,code="FIC-MASS-EARLY",expires_on=timezone.localdate()+timedelta(days=1))
        receive_stock(principal=self.principal,variant_id=self.variant.pk,quantity=1001,unit=InventoryUnit.G,expected_version=1)
        reservations.classify_stock(principal=self.principal,operation_id=uuid4(),lot_id=earlier.pk,location_id=self.location.pk,quantity=1001,unit=InventoryUnit.G,expected_stock_version=2,evidence="Fictional weighed early lot")
        result=self.reserve(1501)
        self.assertEqual({a["lot_code"]:a["quantity"] for a in result["allocations"]},{"FIC-MASS-EARLY":"1.001","FIC-MASS-LOT":"0.500"})
        reservations.consume_reservation(principal=self.principal,operation_id=uuid4(),reservation_id=UUID(result["id"]))
        self.assertEqual(StockLotBalance.objects.get(lot=earlier).quantity,0)
        self.assertEqual(StockPosition.objects.get(variant_id=self.variant.pk).on_hand,999500)
        self.reconcile()

    def test_zero_discrepancy_and_multi_line_count_no_duplicate_movements(self):
        count=self.create_count();self.observe(count,1000000);self.transition_count(count,"review");self.transition_count(count,"complete")
        self.assertFalse(StockMovement.objects.filter(kind="COUNT").exists())
        self.assertEqual(PhysicalCount.objects.get().state,"COMPLETED")
        self.reconcile()


class MeasuredHttpTests(TestCase):
    setUp=MeasuredInventoryTests.setUp

    def client_for_operator(self):
        from identity.tests.test_http_authentication import operator_client,sign_in
        client=operator_client()
        self.assertEqual(sign_in(client,username=self.user.username,password="fictional-test-password-only").status_code,200)
        return client

    def send(self,client,path,body):
        import json
        from identity.tests.test_http_authentication import csrf
        return client.post("/api/v1/operator/inventory"+path,json.dumps(body),content_type="application/json",secure=True,HTTP_X_CSRFTOKEN=csrf(client))

    def test_exact_mass_http_and_independent_blind_read_permissions(self):
        client=self.client_for_operator()
        response=client.get(f"/api/v1/operator/inventory/variants/{self.variant.pk}/stock",secure=True)
        self.assertEqual((response.status_code,response.json()["stock"]["on_hand"],response.json()["stock"]["unit"]),(200,"1000.000","G"))
        response=self.send(client,"/counts",{"operation_id":str(uuid4()),"location_id":str(self.location.pk),"balance_ids":[str(self.balance.pk)],"blind":True})
        self.assertEqual(response.status_code,201)
        count=response.json()["count"]
        self.assertNotIn("expected",count["lines"][0])
        self.user.user_permissions.clear()
        from django.contrib.auth.models import Group,Permission
        group=Group.objects.create(name="Fictional blind reader")
        group.permissions.add(Permission.objects.get(content_type__app_label="inventory",codename="read_physical_inventory"))
        self.user.groups.add(group)
        read=client.get(f"/api/v1/operator/inventory/counts/{count['id']}",secure=True)
        self.assertEqual(read.status_code,200)
        self.assertNotIn("expected",read.json()["count"]["lines"][0])
        self.assertEqual(self.send(client,"/counts/invalid/complete",{}).status_code,403)
        self.assertEqual(self.send(client,"/losses",{}).status_code,403)
        capabilities=client.get("/api/v1/operator/auth/session",secure=True).json()["capabilities"]
        self.assertIn("inventory.read_physical_inventory",capabilities)
        self.assertNotIn("inventory.complete_physical_count",capabilities)
        self.user.groups.clear()
        self.assertEqual(client.get("/api/v1/operator/inventory/counts/invalid",secure=True).status_code,403)

    def test_http_loss_strict_unit_precision_csrf_and_idempotency(self):
        client=self.client_for_operator()
        payload={"operation_id":str(uuid4()),"balance_id":str(self.balance.pk),"quantity":"0.001","unit":"G","expected_stock_version":"1","reason_code":"DAMAGE","evidence":"Fictional spill"}
        for bad in ({"quantity":1},{"quantity":True},{"quantity":"0.0001"},{"unit":"KG"},{"quantity":"9223372036854775.808"}):
            self.assertEqual(self.send(client,"/losses",{**payload,**bad}).status_code,400)
        self.assertEqual(client.post("/api/v1/operator/inventory/losses",json.dumps(payload),content_type="application/json",secure=True).status_code,403)
        response=self.send(client,"/losses",payload)
        self.assertEqual((response.status_code,response.json()["quantity"]),(201,"0.001"))
        self.assertEqual(self.send(client,"/losses",payload).json(),response.json())
        self.assertEqual(PhysicalLoss.objects.count(),1)


from django.test import TransactionTestCase


class MassConcurrencyTests(TransactionTestCase):
    setUp=MeasuredInventoryTests.setUp
    reserve=MeasuredInventoryTests.reserve

    def test_concurrent_reservation_of_remaining_mass_has_exactly_one_winner(self):
        from threading import Barrier,Thread
        from queue import Queue
        from django.db import close_old_connections,connections
        gate=Barrier(2,timeout=10);results=Queue()
        def worker():
            close_old_connections()
            try:
                db=connections["default"]
                self.assertEqual(db.settings_dict["NAME"],"test_mira")
                with db.cursor() as cursor:
                    cursor.execute("SELECT current_database()")
                    self.assertEqual(cursor.fetchone(),("test_mira",))
                gate.wait()
                results.put(self.reserve(600001))
            except Exception as error:results.put(error)
            finally:connections["default"].close()
        threads=[Thread(target=worker,daemon=True) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(15);self.assertFalse(thread.is_alive())
        values=[results.get_nowait() for _ in threads]
        self.assertEqual(sum(isinstance(value,dict) for value in values),1)
        self.assertEqual(sum(isinstance(value,StockConflictError) for value in values),1)
        available=reservations.query_availability(principal=self.principal,variant_id=self.variant.pk)
        self.assertEqual((available.physical,available.reserved,available.available),(1000000,600001,399999))
