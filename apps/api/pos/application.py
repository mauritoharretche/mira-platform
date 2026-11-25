"""POS application boundary. Inventory owns every physical mutation."""
import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from audit import application as audit_application
from catalog import sales as catalog_sales
from catalog.calculations import calculate_price, MassMeasurement
from catalog.contracts import MeasurementUnit, PriceBasis
from identity.application import require_capability
from identity.contracts import Capability
from inventory import sales as inventory_sales
from inventory.contracts import InventoryUnit, ReservationLine
from inventory.pos_safety import require_test_sale_context, isolated_sale
from inventory.quantities import parse_quantity, format_quantity, convert_mass

from .contracts import CartLine, MAX_LINES, MAX_MONEY, QUOTE_SECONDS, PosConflictError, PosNotFoundError, PosValidationError
from .models import Sale, SaleLine, SaleQuote


def _uuid(value):
    if not isinstance(value, UUID): raise PosValidationError("UUID required")
    return value


def _fingerprint(actor, quote_id):
    return hashlib.sha256(json.dumps({"actor":actor.actor_id,"quote_id":str(quote_id),"action":"pos.confirm"},sort_keys=True,separators=(",", ":")).encode()).hexdigest()


def _commercial(v):
    p = v.price
    return {"variant_id":str(v.variant_id),"product_id":str(v.product_id),"product_name":v.product_name,
        "variant_label":v.label,"sku":v.sku,"barcode":v.barcode,"category_id":str(v.category_id) if v.category_id else None,
        "product_active":v.product_active,"variant_active":v.variant_active,"sale_unit":v.sale_unit,
        "measurement_version":str(v.measurement_version) if v.measurement_version else None,
        "price_revision_id":str(p.revision_id) if p else None,"price_amount":format(p.amount,".2f") if p else None,
        "price_basis":p.price_basis.value if p else None,"currency":p.currency if p else None}


def _reasons(v, stock):
    reasons=[]
    if not v.product_active: reasons.append("product_inactive")
    if not v.variant_active: reasons.append("variant_inactive")
    if v.category_id is None: reasons.append("category_missing")
    if v.price is None: reasons.append("price_missing")
    if v.sale_unit is None: reasons.append("measurement_unknown")
    if stock.unit is None: reasons.append("stock_uninitialized")
    if v.price and v.sale_unit and stock.unit:
        each = v.price.price_basis is PriceBasis.PER_VARIANT and v.sale_unit=="EACH" and stock.unit is InventoryUnit.EACH
        mass = v.price.price_basis is PriceBasis.PER_KG and v.sale_unit in {"G","KG"} and stock.unit is InventoryUnit.G
        if not (each or mass): reasons.append("commercial_unit_mismatch")
    if stock.available<=0: reasons.append("no_eligible_stock")
    return reasons


def discover(*, principal: object, search: str = "", after: UUID | None = None, category_id: UUID | None = None) -> dict:
    require_capability(principal=principal,capability=Capability.POS_DISCOVER)
    variants=catalog_sales.discover_variants(principal=principal,query=search,after=after,category_id=category_id)
    stocks=inventory_sales.discover_stock_page(principal=principal,variant_ids=tuple(v.variant_id for v in variants[:50]))
    items=[]
    for v in variants[:50]:
        stock=stocks[v.variant_id]
        reasons=_reasons(v,stock)
        items.append({**_commercial(v),"accounting_unit":stock.unit.value if stock.unit else None,
            "available_quantity":format_quantity(stock.available,stock.unit) if stock.unit else "0",
            "eligible":not reasons,"unavailable_reasons":reasons})
    return {"items":items,"next_cursor":str(variants[49].variant_id) if len(variants)>50 else None}


def _line(actor, line, v, stock):
    if not isinstance(line, CartLine) or not isinstance(line.unit,MeasurementUnit): raise PosValidationError("Typed cart line required")
    if _reasons(v,stock): raise PosConflictError("Commercial eligibility changed")
    if line.unit is MeasurementUnit.EACH:
        base=parse_quantity(line.quantity,"EACH",positive=True)
        if stock.unit is not InventoryUnit.EACH or line.measurement is not None: raise PosValidationError("EACH cannot carry mass measurement")
        quantity=str(base); measurement=None
    else:
        if stock.unit is not InventoryUnit.G: raise PosValidationError("Mass accounting mismatch")
        normalized=convert_mass(line.quantity,from_unit=line.unit.value,to_unit="G")
        base=parse_quantity(normalized,"G",positive=True)
        quantity=convert_mass(normalized,from_unit="G",to_unit=line.unit.value)
        m=line.measurement
        if not isinstance(m, MassMeasurement) or m.source!="manual" or m.unit is not line.unit or m.net!=Decimal(quantity): raise PosValidationError("Explicit matching manual measurement required")
        m.validate()
        if m.measured_at>timezone.now()+timedelta(minutes=1): raise PosValidationError("Measurement timestamp is in the future")
        measurement={"net":quantity,"unit":m.unit.value,"source":"manual","resolution":str(m.resolution),
            "measured_at":m.measured_at.isoformat(),"actor_id":actor.actor_id,
            "gross":str(m.gross) if m.gross is not None else None,"tare":str(m.tare) if m.tare is not None else None}
    if base>stock.available: raise PosConflictError("Insufficient eligible unreserved stock")
    amount=calculate_price(price=v.price,quantity=Decimal(quantity),unit=line.unit).amount_ars
    if amount>Decimal(MAX_MONEY): raise PosValidationError("Line monetary overflow")
    return {**_commercial(v),"quantity":quantity,"unit":line.unit.value,"accounting_unit":stock.unit.value,
        "base_quantity":str(base),"measurement":measurement,"amount_ars":format(amount,".2f"),"rounding":"HALF_EVEN_CENTS_PER_LINE"}


@inventory_sales.sale_transaction
def quote_sale(*, principal: object, lines: tuple[CartLine, ...]) -> dict:
    actor=require_capability(principal=principal,capability=Capability.POS_QUOTE)
    if not isinstance(lines,tuple) or not 1<=len(lines)<=MAX_LINES or any(not isinstance(l,CartLine) for l in lines): raise PosValidationError("One to thirty typed lines required")
    ids=tuple(_uuid(l.variant_id) for l in lines)
    if len(set(ids))!=len(ids): raise PosValidationError("Duplicate variant; edit the existing line")
    variants={v.variant_id:v for v in catalog_sales.quote_variants(principal=principal,variant_ids=ids)}
    rows=[]
    with localcontext() as ctx:
        ctx.prec=60
        for line in lines:
            rows.append(_line(actor,line,variants[line.variant_id],inventory_sales.quote_stock(principal=principal,variant_id=line.variant_id)))
        total=sum((Decimal(l['amount_ars']) for l in rows),Decimal(0))
    if total>Decimal(MAX_MONEY): raise PosValidationError("Sale monetary overflow")
    quote=SaleQuote.objects.create(actor_id=actor.actor_id,snapshot={"lines":rows,"total_ars":format(total,".2f"),"currency":"ARS"},expires_at=timezone.now()+timedelta(seconds=QUOTE_SECONDS))
    audit_application.record_pos_event(actor=actor,event_id=uuid4(),quote_id=quote.pk,sale_id=None,action="pos.quoted")
    return _quote_snapshot(quote)


def _quote_snapshot(q):
    return {"id":str(q.pk),"state":"EXPIRED" if q.state=="QUOTED" and q.expires_at<=timezone.now() else q.state,
        "created_at":q.created_at.isoformat(),"expires_at":q.expires_at.isoformat(),**q.snapshot}


def _quote(actor,quote_id,*,lock=False):
    q=SaleQuote.objects
    if lock:q=q.select_for_update()
    found=q.filter(pk=_uuid(quote_id),actor_id=actor.actor_id).first()
    if found is None:raise PosNotFoundError("Quote not found")
    return found


def read_quote(*, principal: object, quote_id: UUID) -> dict:
    actor=require_capability(principal=principal,capability=Capability.POS_QUOTE)
    return _quote_snapshot(_quote(actor,quote_id))


@transaction.atomic
def cancel_quote(*, principal: object, quote_id: UUID) -> dict:
    actor=require_capability(principal=principal,capability=Capability.POS_QUOTE)
    q=_quote(actor,quote_id,lock=True)
    if q.state=="COMMITTED":raise PosConflictError("Committed sale cannot be cancelled")
    if q.state!="CANCELLED":
        q.state="CANCELLED";q.save(update_fields=["state"])
        audit_application.record_pos_event(actor=actor,event_id=uuid4(),quote_id=q.pk,sale_id=None,action="pos.cancelled")
    return _quote_snapshot(q)


def _sale_snapshot(s):
    return {"id":str(s.pk),"operation_id":str(s.operation_id),"quote_id":str(s.quote_id),"actor_id":s.actor_id,
        "state":s.state,"settlement":s.settlement,"currency":s.currency,"total_ars":format(s.total_ars,".2f"),
        "created_at":s.created_at.isoformat(),"reservation_id":str(s.reservation_id),"inventory_trace":s.inventory_trace,
        "lines":[l.snapshot for l in s.lines.order_by("ordinal")],"notice":"Non-fiscal internal record; not proof of payment. Test operation only."}


@isolated_sale
@inventory_sales.sale_transaction
def confirm_sale(*, principal: object, operation_id: UUID, quote_id: UUID) -> dict:
    actor=require_capability(principal=principal,capability=Capability.POS_CONFIRM)
    require_test_sale_context()
    _uuid(operation_id);_uuid(quote_id)
    digest=_fingerprint(actor,quote_id)
    prior=Sale.objects.filter(operation_id=operation_id).first()
    if prior:
        if prior.actor_id!=actor.actor_id or prior.request_sha256!=digest:raise PosConflictError("Operation identity reuse")
        return _sale_snapshot(prior)
    q=_quote(actor,quote_id,lock=True)
    if q.state!="QUOTED" or q.expires_at<=timezone.now():raise PosConflictError("Quote is no longer confirmable")
    ids=tuple(UUID(l['variant_id']) for l in q.snapshot['lines'])
    current={v.variant_id:v for v in catalog_sales.confirmation_variants(principal=principal,variant_ids=ids)}
    lines=[]
    with localcontext() as ctx:
        ctx.prec=60;total=Decimal(0)
        for line in q.snapshot['lines']:
            v=current[UUID(line['variant_id'])]
            if _commercial(v)!={k:line[k] for k in _commercial(v)}:raise PosConflictError("Quote changed; review a new quotation explicitly")
            # Inventory owns authoritative eligibility/availability revalidation in its atomic FEFO command.
            amount=calculate_price(price=v.price,quantity=Decimal(line['quantity']),unit=MeasurementUnit(line['unit'])).amount_ars
            if format(amount,'.2f')!=line['amount_ars']:raise PosConflictError("Quote arithmetic changed")
            total+=amount
            lines.append(ReservationLine(v.variant_id,int(line['base_quantity']),InventoryUnit(line['accounting_unit'])))
    if total>Decimal(MAX_MONEY) or format(total,'.2f')!=q.snapshot['total_ars']:raise PosConflictError("Quote totals changed")
    if q.expires_at<=timezone.now():raise PosConflictError("Quote expired while acquiring commercial locks")
    sale_id=uuid4()
    consumed=inventory_sales.consume_for_sale(principal=principal,operation_id=operation_id,sale_id=sale_id,lines=tuple(lines))
    s=Sale.objects.create(id=sale_id,quote=q,operation_id=operation_id,request_sha256=digest,actor_id=actor.actor_id,
        total_ars=total,reservation_id=consumed.reservation_id,inventory_trace=consumed.trace)
    for i,line in enumerate(q.snapshot['lines'],1):SaleLine.objects.create(sale=s,ordinal=i,variant_id=UUID(line['variant_id']),snapshot=line)
    q.state="COMMITTED";q.save(update_fields=["state"])
    audit_application.record_pos_event(actor=actor,event_id=operation_id,quote_id=q.pk,sale_id=s.pk,action="pos.committed")
    return _sale_snapshot(s)


def read_sale(*, principal: object, sale_id: UUID) -> dict:
    require_capability(principal=principal,capability=Capability.POS_READ)
    found=Sale.objects.filter(pk=_uuid(sale_id)).first()
    if found is None:raise PosNotFoundError("Sale not found")
    return _sale_snapshot(found)


def lookup_operation(*, principal: object, operation_id: UUID) -> dict:
    actor=require_capability(principal=principal,capability=Capability.POS_CONFIRM)
    found=Sale.objects.filter(operation_id=_uuid(operation_id),actor_id=actor.actor_id).first()
    if found is None:raise PosNotFoundError("Not observed; absence does not prove failure")
    return _sale_snapshot(found)


def history(*, principal: object, after: UUID | None = None, actor_id: str = "", on_date: str = "", sale_id: UUID | None = None) -> dict:
    require_capability(principal=principal,capability=Capability.POS_HISTORY)
    if not isinstance(actor_id,str) or len(actor_id)>128:raise PosValidationError("Bounded actor filter required")
    q=Sale.objects.order_by("-created_at","-id")
    if after:
        from django.db.models import Q
        anchor=Sale.objects.filter(pk=_uuid(after)).first()
        if anchor is None:raise PosValidationError("Invalid history cursor")
        q=q.filter(Q(created_at__lt=anchor.created_at)|Q(created_at=anchor.created_at,id__lt=anchor.pk))
    if sale_id:q=q.filter(pk=_uuid(sale_id))
    if actor_id:q=q.filter(actor_id=actor_id)
    if on_date:
        from datetime import date
        try:d=date.fromisoformat(on_date)
        except (ValueError,TypeError):raise PosValidationError("ISO date required") from None
        q=q.filter(created_at__date=d)
    rows=list(q[:51])
    return {"items":[{"id":str(s.pk),"actor_id":s.actor_id,"created_at":s.created_at.isoformat(),"total_ars":format(s.total_ars,'.2f'),"state":s.state,"settlement":s.settlement} for s in rows[:50]],"next_cursor":str(rows[49].pk) if len(rows)>50 else None}
