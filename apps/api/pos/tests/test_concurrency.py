from queue import Queue
from threading import Barrier, Thread
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase
from catalog.contracts import MeasurementUnit
from inventory.contracts import StockConflictError
from inventory.models import StockMovement, StockReservation
from pos.contracts import PosConflictError
from pos.models import Sale
from .test_application import PosFixture


class PosConcurrencyTests(PosFixture,TransactionTestCase):
    def race(self,left,right):
        gate=Barrier(2,timeout=10);results=Queue()
        def work(call):
            close_old_connections()
            try:gate.wait();results.put(('ok',call()))
            except (PosConflictError,StockConflictError):results.put(('conflict',None))
            except BaseException as e:results.put(('error',type(e).__name__))
            finally:close_old_connections()
        threads=[Thread(target=work,args=(f,)) for f in (left,right)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=20);self.assertFalse(t.is_alive(),'Sale command did not complete')
        rows=[results.get_nowait() for _ in threads]
        self.assertFalse(any(r[0]=='error' for r in rows),rows)
        return rows

    def test_concurrent_last_units_one_sale(self):
        a=self.quote((self.line(quantity='10'),));b=self.quote((self.line(quantity='10'),))
        result=self.race(lambda:self.confirm(a),lambda:self.confirm(b))
        self.assertCountEqual([r[0] for r in result],['ok','conflict']);self.assertEqual(Sale.objects.count(),1);self.reconcile()

    def test_concurrent_measured_stock_one_sale(self):
        a=self.quote((self.line(self.mass,'1000',MeasurementUnit.G),));b=self.quote((self.line(self.mass,'1',MeasurementUnit.KG),))
        result=self.race(lambda:self.confirm(a),lambda:self.confirm(b))
        self.assertCountEqual([r[0] for r in result],['ok','conflict']);self.assertEqual(Sale.objects.count(),1);self.reconcile()

    def test_concurrent_duplicate_confirmation_returns_same_result(self):
        q=self.quote();op=uuid4()
        result=self.race(lambda:self.confirm(q,op),lambda:self.confirm(q,op))
        self.assertEqual([r[0] for r in result],['ok','ok']);self.assertEqual(result[0][1],result[1][1])
        self.assertEqual(Sale.objects.count(),1);self.assertEqual(StockReservation.objects.count(),1)
        self.assertEqual(StockMovement.objects.filter(kind='CONSUMPTION').count(),1);self.reconcile()

    def test_distinct_operations_cannot_confirm_same_quote_twice(self):
        q=self.quote()
        result=self.race(lambda:self.confirm(q),lambda:self.confirm(q))
        self.assertCountEqual([r[0] for r in result],['ok','conflict']);self.assertEqual(Sale.objects.count(),1);self.reconcile()

    def test_inventory_bridge_refuses_standalone_commit_without_owner_transaction(self):
        from django.db.transaction import TransactionManagementError
        from inventory.sales import consume_for_sale
        from inventory.contracts import ReservationLine
        with self.assertRaises(TransactionManagementError):
            consume_for_sale(principal=self.principal,operation_id=uuid4(),sale_id=uuid4(),lines=(ReservationLine(self.each.pk,1),))
        self.assertFalse(Sale.objects.exists());self.assertFalse(StockReservation.objects.exists());self.reconcile()
