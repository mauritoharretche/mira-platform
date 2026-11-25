from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.models import Permission, Group
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from audit.models import PosAuditEntry, InventoryMovementAuditEntry
from catalog import application as catalog
from catalog.calculations import MassMeasurement
from catalog.contracts import MeasurementUnit as Unit, PriceBasis
from catalog.sales import set_variant_active
from identity.application import require_capability
from identity.contracts import AccessDenied, Capability
from inventory.contracts import StockConflictError, InventoryValidationError
from inventory.lot_application import set_lot_status
from inventory.models import StockPosition, StockMovement, StockLotBalance, StockReservation, ReservationAllocation
from inventory.tests.test_application import assert_test_database
from pos import application as app
from pos.contracts import CartLine, PosConflictError, PosValidationError
from pos.models import Sale, SaleLine, SaleQuote
from .fixtures import cashier, prepare_catalog_stock


class PosFixture:
    def setUp(self):
        super().setUp()
        assert_test_database(self)
        self.user,self.principal=cashier()
        self.preparer,self.category,self.location,self.variants=prepare_catalog_stock()
        self.each,self.mass=self.variants

    def line(self, variant=None,quantity="1",unit=Unit.EACH):
        v=variant or self.each
        m=None if unit is Unit.EACH else MassMeasurement(Decimal(quantity),unit,"manual",timezone.now(),Decimal("0.001") if unit is Unit.G else Decimal("0.000001"))
        return CartLine(v.pk,quantity,unit,m)

    def quote(self,lines=None):return app.quote_sale(principal=self.principal,lines=lines or (self.line(),))
    def confirm(self,quote,op=None):return app.confirm_sale(principal=self.principal,operation_id=op or uuid4(),quote_id=UUID(quote['id']))
    def reconcile(self):
        for position in StockPosition.objects.all():
            movements=list(StockMovement.objects.filter(position=position).order_by('sequence'))
            self.assertEqual(position.on_hand,sum(m.delta for m in movements))
            self.assertEqual(position.on_hand,sum(StockLotBalance.objects.filter(lot__variant_id=position.variant_id).values_list('quantity',flat=True)))
            for m in movements:
                a=InventoryMovementAuditEntry.objects.get(movement_id=m.pk)
                self.assertEqual((m.delta,m.quantity_after,m.unit),(a.delta,a.quantity_after,a.unit))
        for sale in Sale.objects.all():
            audit=PosAuditEntry.objects.get(sale_id=sale.pk,action="pos.committed")
            self.assertEqual(audit.actor_id,sale.actor_id)
            reservation=StockReservation.objects.get(pk=sale.reservation_id)
            self.assertEqual(reservation.state,'CONSUMED')
            self.assertTrue(all(a.stock_movement_id for a in ReservationAllocation.objects.filter(reservation=reservation)))
            self.assertEqual(sale.total_ars,sum(Decimal(l.snapshot['amount_ars']) for l in sale.lines.all()))


class PosApplicationTests(PosFixture,TestCase):
    def test_discovery_barcode_sku_names_category_and_cursor(self):
        for term in (self.each.barcode,self.each.sku,'Fictional FIC-POS EACH','Fictional EACH'):
            self.assertEqual(app.discover(principal=self.principal,search=term)['items'][0]['variant_id'],str(self.each.pk))
        self.assertEqual(len(app.discover(principal=self.principal,category_id=self.category.pk)['items']),2)
        self.assertEqual(app.discover(principal=self.principal,search='unmatched')['items'],[])

    def test_discovery_explains_independent_unavailable_states(self):
        from catalog.models import Product,Variant,VariantPriceRevision,VariantMeasurementSpec
        Product.objects.filter(pk=self.each.product_id).update(is_active=False)
        set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=False,expected_active=True)
        VariantPriceRevision.objects.filter(variant_id=self.each.pk).delete()
        VariantMeasurementSpec.objects.filter(variant_id=self.each.pk).delete()
        reasons=app.discover(principal=self.principal,search=self.each.sku)['items'][0]['unavailable_reasons']
        self.assertTrue({'product_inactive','variant_inactive','price_missing','measurement_unknown'}.issubset(reasons))
        with self.assertRaises(PosConflictError):self.quote()

    def test_mixed_unit_atomic_sale_and_reconciliation(self):
        q=self.quote((self.line(quantity='2'),self.line(self.mass,'0.245001',Unit.KG)))
        self.assertEqual(q['total_ars'],'224.50')
        self.assertEqual(StockMovement.objects.count(),2)
        self.assertEqual(StockReservation.objects.count(),0)
        sale=self.confirm(q)
        self.assertEqual(sale['state'],'COMMITTED_UNSETTLED');self.assertEqual(sale['settlement'],'UNSETTLED')
        self.assertEqual(StockPosition.objects.get(variant_id=self.mass.pk).on_hand,754999)
        self.assertEqual(StockPosition.objects.get(variant_id=self.each.pk).on_hand,8)
        self.assertEqual(sale['lines'][1]['measurement']['source'],'manual')
        self.assertEqual(sale['lines'][1]['measurement']['actor_id'],str(self.user.pk))
        self.reconcile()

    def test_line_rounding_once_then_total(self):
        from catalog.models import VariantPriceRevision
        old=VariantPriceRevision.objects.filter(variant_id=self.mass.pk).first()
        catalog.set_variant_price(principal=self.preparer,variant_id=self.mass.pk,amount=Decimal('1.00'),currency='ARS',expected_revision_id=old.pk,price_basis=PriceBasis.PER_KG)
        for quantity,expected in [('5','0.00'),('15','0.02'),('25','0.02')]:
            self.assertEqual(self.quote((self.line(self.mass,quantity,Unit.G),))['total_ars'],expected)

    def test_reject_invalid_quantities_and_measurements(self):
        for quantity in ['0','-1','1.1','01','1e2','NaN','9223372036854775808',True,1.5]:
            with self.subTest(quantity=quantity),self.assertRaises((ValueError,InventoryValidationError,PosValidationError)):
                self.quote((self.line(quantity=quantity),))
        for quantity,unit in [('0.0001',Unit.G),('0.0000001',Unit.KG)]:
            with self.assertRaises((ValueError,InventoryValidationError)):self.quote((self.line(self.mass,quantity,unit),))
        line=self.line(self.mass,'10',Unit.G)
        for measurement in [None,replace(line.measurement,source='fictional-device'),replace(line.measurement,measured_at=timezone.now().replace(tzinfo=None)),replace(line.measurement,gross=Decimal('12'),tare=Decimal('1'))]:
            with self.assertRaises((PosValidationError,ValueError)):self.quote((replace(line,measurement=measurement),))

    def test_line_and_total_overflow_roll_back_quote_audit(self):
        from catalog.models import VariantPriceRevision
        from inventory.application import receive_stock
        from inventory.contracts import InventoryUnit
        from inventory.reservations import classify_stock
        from inventory.models import StockLot
        old=VariantPriceRevision.objects.filter(variant_id=self.each.pk).first()
        catalog.set_variant_price(principal=self.preparer,variant_id=self.each.pk,amount=Decimal('999999999999.99'),currency='ARS',expected_revision_id=old.pk)
        with self.assertRaises(PosValidationError):self.quote((self.line(quantity='2'),))
        # Each line fits; their rounded sum does not.
        old=VariantPriceRevision.objects.filter(variant_id=self.mass.pk).first()
        catalog.set_variant_price(principal=self.preparer,variant_id=self.mass.pk,amount=Decimal('999999999999.99'),currency='ARS',expected_revision_id=old.pk,price_basis=PriceBasis.PER_KG)
        with self.assertRaises(PosValidationError):self.quote((self.line(),self.line(self.mass,'1',Unit.KG)))
        self.assertEqual(SaleQuote.objects.count(),0);self.assertFalse(PosAuditEntry.objects.exists())

    def test_price_and_product_eligibility_changes_require_new_review(self):
        from catalog.models import VariantPriceRevision
        q=self.quote();old=VariantPriceRevision.objects.filter(variant_id=self.each.pk).first()
        catalog.set_variant_price(principal=self.preparer,variant_id=self.each.pk,amount=Decimal('101.00'),currency='ARS',expected_revision_id=old.pk)
        with self.assertRaises(PosConflictError):self.confirm(q)
        q=self.quote();catalog.set_product_active(principal=self.preparer,product_id=self.each.product_id,is_active=False)
        with self.assertRaises(PosConflictError):self.confirm(q)
        self.assertFalse(Sale.objects.exists());self.assertEqual(StockMovement.objects.count(),2)

    def test_measurement_version_and_variant_activation_changes_invalidate_quote(self):
        q=self.quote();set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=False,expected_active=True)
        with self.assertRaises(PosConflictError):self.confirm(q)
        set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=True,expected_active=False)
        q=self.quote()
        catalog.set_variant_measurement_spec(principal=self.preparer,variant_id=self.each.pk,sale_unit=Unit.EACH,net_content_amount=Decimal('200'),net_content_unit=Unit.G,expected_version=1)
        with self.assertRaises(PosConflictError):self.confirm(q)

    def test_quote_expiry_cancel_and_committed_cannot_be_cancelled(self):
        q=self.quote()
        with patch('pos.application.timezone.now',return_value=timezone.now()+timedelta(minutes=6)):
            with self.assertRaises(PosConflictError):self.confirm(q)
        cancelled=app.cancel_quote(principal=self.principal,quote_id=UUID(q['id']))
        self.assertEqual(cancelled['state'],'CANCELLED')
        app.cancel_quote(principal=self.principal,quote_id=UUID(q['id']))
        self.assertEqual(PosAuditEntry.objects.filter(action='pos.cancelled').count(),1)
        with self.assertRaises(PosConflictError):self.confirm(q)
        q=self.quote();self.confirm(q)
        with self.assertRaises(PosConflictError):app.cancel_quote(principal=self.principal,quote_id=UUID(q['id']))

    def test_idempotent_replay_actor_binding_conflicting_reuse_and_lookup(self):
        q=self.quote();op=uuid4();sale=self.confirm(q,op)
        self.assertEqual(self.confirm(q,op),sale)
        self.assertEqual(app.lookup_operation(principal=self.principal,operation_id=op),sale)
        with self.assertRaises(PosConflictError):self.confirm(self.quote(),op)
        other,principal=cashier('fictional-other-cashier')
        with self.assertRaises(PosConflictError):app.confirm_sale(principal=principal,operation_id=op,quote_id=UUID(q['id']))
        from pos.contracts import PosNotFoundError
        with self.assertRaises(PosNotFoundError):app.lookup_operation(principal=principal,operation_id=op)
        with self.assertRaises(PosNotFoundError):app.read_quote(principal=principal,quote_id=UUID(q['id']))
        self.assertEqual(Sale.objects.count(),1);self.assertEqual(SaleLine.objects.count(),1)
        self.reconcile()

    def test_insufficient_stock_and_blocked_lot_roll_back_all_lines(self):
        q=self.quote((self.line(quantity='10'),self.line(self.mass,'1',Unit.KG)))
        from inventory.models import StockLot
        lot=StockLot.objects.get(variant_id=self.mass.pk)
        set_lot_status(principal=self.preparer,lot_id=lot.pk,status='QUARANTINE',expected_version=lot.version,reason='Fictional quarantine')
        with self.assertRaises(StockConflictError):self.confirm(q)
        self.assertFalse(Sale.objects.exists());self.assertFalse(StockReservation.objects.exists());self.assertEqual(StockMovement.objects.count(),2)
        self.reconcile()

    def test_audit_failure_and_outer_rollback_undo_entire_sale(self):
        q=self.quote()
        with patch('pos.application.audit_application.record_pos_event',side_effect=RuntimeError('fictional audit rejection')):
            with self.assertRaises(RuntimeError):self.confirm(q)
        self.assertFalse(Sale.objects.exists());self.assertFalse(StockReservation.objects.exists())
        try:
            with transaction.atomic():self.confirm(q);raise RuntimeError('fictional outer rollback')
        except RuntimeError:pass
        self.assertFalse(Sale.objects.exists());self.assertEqual(StockMovement.objects.count(),2)
        self.assertEqual(SaleQuote.objects.get(pk=q['id']).state,'QUOTED');self.reconcile()

    def test_history_is_immutable_when_catalog_changes(self):
        q=self.quote();sale=self.confirm(q)
        from catalog.models import Product,VariantPriceRevision
        Product.objects.filter(pk=self.each.product_id).update(name='Fictional renamed product')
        old=VariantPriceRevision.objects.filter(variant_id=self.each.pk).first()
        catalog.set_variant_price(principal=self.preparer,variant_id=self.each.pk,amount=Decimal('102.00'),currency='ARS',expected_revision_id=old.pk)
        self.assertEqual(app.read_sale(principal=self.principal,sale_id=UUID(sale['id'])),sale)
        result=app.history(principal=self.principal,actor_id=str(self.user.pk),on_date=timezone.now().date().isoformat(),sale_id=UUID(sale['id']))
        self.assertEqual(result['items'][0]['total_ars'],'100.00')

    def test_direct_group_partial_revoked_permissions_and_no_broad_inventory_grants(self):
        self.assertFalse(self.user.has_perm('inventory.reserve_stock'));self.assertFalse(self.user.has_perm('inventory.consume_reservation'))
        self.user.user_permissions.clear()
        with self.assertRaises(AccessDenied):self.quote()
        group=Group.objects.create(name='Fictional quoting only');group.permissions.add(Permission.objects.get(content_type__app_label='pos',codename='quote_sale'))
        self.user.groups.add(group);q=self.quote()
        with self.assertRaises(AccessDenied):self.confirm(q)
        group.permissions.clear()
        with self.assertRaises(AccessDenied):self.quote()

    def test_real_operation_metadata_rejected_before_any_connection(self):
        from django.db.backends.base.base import BaseDatabaseWrapper
        with patch.dict(connection.settings_dict,NAME='mira'),patch.object(BaseDatabaseWrapper,'ensure_connection',side_effect=AssertionError('Must not connect')) as connect:
            with self.assertRaises(AccessDenied):self.confirm({'id':str(uuid4())})
            connect.assert_not_called()

    def test_fefo_uses_dated_eligible_lot_before_undated_stock(self):
        from inventory.application import receive_stock
        from inventory.contracts import InventoryUnit
        from inventory.lot_application import create_lot
        from inventory.reservations import classify_stock
        lot=create_lot(principal=self.preparer,variant_id=self.each.pk,code='FIC-EARLY',expires_on=timezone.now().date()+timedelta(days=1))
        receive_stock(principal=self.preparer,variant_id=self.each.pk,quantity=1,unit=InventoryUnit.EACH,expected_version=1)
        classify_stock(principal=self.preparer,operation_id=uuid4(),lot_id=lot.pk,location_id=self.location.pk,quantity=1,expected_stock_version=2,evidence='Fictional verified earlier lot')
        sale=self.confirm(self.quote())
        self.assertEqual(sale['inventory_trace']['allocations'][0]['lot_id'],str(lot.pk));self.reconcile()

    def test_audit_failure_after_append_rolls_back_sale_inventory_and_attribution(self):
        from audit.application import record_pos_event
        q=self.quote()
        def after_append(**kwargs):
            record_pos_event(**kwargs)
            raise RuntimeError('Fictional failure after persisted attribution')
        with patch('pos.application.audit_application.record_pos_event',side_effect=after_append):
            with self.assertRaises(RuntimeError):self.confirm(q)
        self.assertFalse(Sale.objects.exists());self.assertFalse(StockReservation.objects.exists())
        self.assertFalse(PosAuditEntry.objects.filter(action='pos.committed').exists());self.reconcile()

    def test_bounded_discovery_and_sale_history_keyset_pages(self):
        from catalog.models import Product,Variant
        for i in range(52):
            product=Product.objects.create(name=f'Fictional bounded search {i}')
            Variant.objects.create(product=product,label='Fictional bounded pack',sku=f'FIC-PAGE-SEARCH-{i}')
        first=app.discover(principal=self.principal,search='FIC-PAGE-SEARCH-')
        second=app.discover(principal=self.principal,search='FIC-PAGE-SEARCH-',after=UUID(first['next_cursor']))
        self.assertEqual((len(first['items']),len(second['items'])),(50,2))
        self.assertFalse({i['variant_id'] for i in first['items']} & {i['variant_id'] for i in second['items']})
        _,_,_,variants=prepare_catalog_stock(prefix='FIC-HISTORY',each=60)
        self.each=variants[0]
        for _ in range(51):self.confirm(self.quote())
        first=app.history(principal=self.principal);second=app.history(principal=self.principal,after=UUID(first['next_cursor']))
        self.assertEqual((len(first['items']),len(second['items'])),(50,1))
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertGreaterEqual(first['items'][0]['created_at'],first['items'][-1]['created_at']);self.reconcile()

    def test_exact_barcode_takes_precedence_over_another_variant_sku(self):
        from catalog.models import Variant
        Variant.objects.create(product_id=self.each.product_id,label="Fictional ambiguous code",sku=self.each.barcode)
        result=app.discover(principal=self.principal,search=self.each.barcode)
        self.assertEqual([r['variant_id'] for r in result['items']],[str(self.each.pk)])

    def test_variant_activation_noop_conflict_and_audit_rollback(self):
        from audit.models import CatalogChangeAuditEntry
        from catalog.application import PriceConflictError
        self.assertTrue(set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=True,expected_active=True))
        self.assertFalse(CatalogChangeAuditEntry.objects.filter(target_id=self.each.pk).exists())
        with self.assertRaises(PriceConflictError):set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=False,expected_active=False)
        with patch('catalog.sales.audit_application.record_variant_active_changed',side_effect=RuntimeError('Fictional audit failure')):
            with self.assertRaises(RuntimeError):set_variant_active(principal=self.preparer,variant_id=self.each.pk,is_active=False,expected_active=True)
        self.each.refresh_from_db();self.assertTrue(self.each.is_active)
        with self.assertRaises(AccessDenied):set_variant_active(principal=self.principal,variant_id=self.each.pk,is_active=False,expected_active=True)

    def test_quote_expiring_while_acquiring_catalog_locks_is_rejected(self):
        from catalog.sales import confirmation_variants
        q=self.quote();clock=[timezone.now()]
        def after_lock(**kwargs):
            result=confirmation_variants(**kwargs)
            clock[0]+=timedelta(minutes=6)
            return result
        with patch('pos.application.timezone.now',side_effect=lambda:clock[0]),patch('pos.application.catalog_sales.confirmation_variants',side_effect=after_lock):
            with self.assertRaises(PosConflictError):self.confirm(q)
        self.assertFalse(Sale.objects.exists());self.assertFalse(StockReservation.objects.exists());self.reconcile()

    def test_nonloopback_mirrors_and_router_metadata_rejected_before_connection(self):
        from django.db.backends.base.base import BaseDatabaseWrapper
        for override in ({"HOST":"unsafe.invalid"},{"PORT":"9999"},{"TEST":{"NAME":"test_mira","MIRROR":"other"}},{"OPTIONS":{"service":"unsafe"}}):
            with self.subTest(keys=list(override)),patch.dict(connection.settings_dict,override),patch.object(BaseDatabaseWrapper,'ensure_connection',side_effect=AssertionError('No connection allowed')) as connect:
                with self.assertRaises(AccessDenied):self.confirm({'id':str(uuid4())})
                connect.assert_not_called()

    def test_native_settings_holder_does_not_false_reject_exact_test_source(self):
        from django.test import override_settings
        with override_settings(OPERATOR_HTTP_ENABLED=True):
            self.assertIsNone(settings.SETTINGS_MODULE)
            self.confirm(self.quote())
        self.reconcile()

    def test_non_test_settings_source_is_rejected_before_connecting(self):
        import os
        from django.db.backends.base.base import BaseDatabaseWrapper
        with patch.dict(os.environ,DJANGO_SETTINGS_MODULE="config.settings.development"),patch.object(BaseDatabaseWrapper,'ensure_connection',side_effect=AssertionError('No connection allowed')) as connect:
            with self.assertRaises(AccessDenied):self.confirm({'id':str(uuid4())})
            connect.assert_not_called()
