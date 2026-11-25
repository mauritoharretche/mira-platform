"""Strict versioned POS adapters; authorize before parsing or revealing existence."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from rest_framework import serializers
from rest_framework.response import Response

from catalog.application import CatalogValidationError, VariantNotFoundError
from catalog.calculations import MassMeasurement
from catalog.contracts import MeasurementUnit
from identity.application import require_capability
from identity.contracts import Capability
from inventory.contracts import InventoryValidationError, StockConflictError
from system.http import OperatorAPIView, StrictSerializer, StrictTextField, error_response
from . import application
from .contracts import CartLine, PosConflictError, PosNotFoundError, PosValidationError


def _uuid(value):
    try:
        result=UUID(value)
        if str(result)!=value.lower():raise ValueError
        return result
    except (ValueError,TypeError,AttributeError):raise serializers.ValidationError("UUID required") from None


def _query(request, allowed):
    if set(request.query_params)-set(allowed) or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
        raise serializers.ValidationError("Unexpected or duplicate query parameter")
    return dict(request.query_params.items())


def _respond(call):
    try:return Response(call())
    except (PosConflictError,StockConflictError):return error_response("pos_conflict",409)
    except (PosNotFoundError,VariantNotFoundError):return error_response("not_found",404)
    except (PosValidationError,CatalogValidationError,InventoryValidationError,ValueError,InvalidOperation):return error_response("validation_error",400)


class CanonicalUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        return _uuid(data)


class MeasurementInput(StrictSerializer):
    net=StrictTextField(max_length=30)
    unit=serializers.ChoiceField(choices=["G","KG"])
    source=serializers.ChoiceField(choices=["manual"])
    measured_at=StrictTextField(max_length=40)
    resolution=StrictTextField(max_length=30)
    gross=StrictTextField(max_length=30,allow_null=True,required=False,default=None)
    tare=StrictTextField(max_length=30,allow_null=True,required=False,default=None)

    def validate(self, attrs):
        import re
        for k in ("net","resolution","gross","tare"):
            v=attrs[k]
            if v is not None and not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",v):raise serializers.ValidationError("Canonical exact decimal required")
        try:
            m=MassMeasurement(net=Decimal(attrs['net']),unit=MeasurementUnit(attrs['unit']),source=attrs['source'],
                measured_at=datetime.fromisoformat(attrs['measured_at']),resolution=Decimal(attrs['resolution']),
                gross=Decimal(attrs['gross']) if attrs['gross'] is not None else None,tare=Decimal(attrs['tare']) if attrs['tare'] is not None else None).validate()
        except (ValueError,InvalidOperation):raise serializers.ValidationError("Invalid explicit manual measurement") from None
        return m


class LineInput(StrictSerializer):
    variant_id=CanonicalUUIDField()
    quantity=StrictTextField(max_length=30,trim_whitespace=False)
    unit=serializers.ChoiceField(choices=["EACH","G","KG"])
    measurement=MeasurementInput(required=False,allow_null=True,default=None)


class QuoteInput(StrictSerializer):
    lines=LineInput(many=True,allow_empty=False,max_length=30)


class ConfirmInput(StrictSerializer):
    operation_id=CanonicalUUIDField()
    quote_id=CanonicalUUIDField()


class DiscoveryView(OperatorAPIView):
    def get(self,request):
        require_capability(principal=request.user,capability=Capability.POS_DISCOVER)
        q=_query(request,{"search","after","category_id"})
        return _respond(lambda:application.discover(principal=request.user,search=q.get('search',''),after=_uuid(q['after']) if q.get('after') else None,category_id=_uuid(q['category_id']) if q.get('category_id') else None))


class QuotesView(OperatorAPIView):
    def post(self,request):
        require_capability(principal=request.user,capability=Capability.POS_QUOTE)
        data=QuoteInput(data=request.data);data.is_valid(raise_exception=True)
        lines=tuple(CartLine(variant_id=l['variant_id'],quantity=l['quantity'],unit=MeasurementUnit(l['unit']),measurement=l['measurement']) for l in data.validated_data['lines'])
        return _respond(lambda:{"quote":application.quote_sale(principal=request.user,lines=lines)})


class QuoteView(OperatorAPIView):
    def get(self,request,quote_id):
        require_capability(principal=request.user,capability=Capability.POS_QUOTE)
        _query(request,set())
        return _respond(lambda:{"quote":application.read_quote(principal=request.user,quote_id=_uuid(quote_id))})

    def post(self,request,quote_id):
        require_capability(principal=request.user,capability=Capability.POS_QUOTE)
        if request.data!={"action":"cancel"}:raise serializers.ValidationError("Explicit cancel action required")
        return _respond(lambda:{"quote":application.cancel_quote(principal=request.user,quote_id=_uuid(quote_id))})


class SalesView(OperatorAPIView):
    def get(self,request):
        require_capability(principal=request.user,capability=Capability.POS_HISTORY)
        q=_query(request,{"after","actor_id","on_date","sale_id"})
        for k in ('after','sale_id'):
            if q.get(k):q[k]=_uuid(q[k])
        return _respond(lambda:application.history(principal=request.user,**q))

    def post(self,request):
        require_capability(principal=request.user,capability=Capability.POS_CONFIRM)
        data=ConfirmInput(data=request.data);data.is_valid(raise_exception=True)
        return _respond(lambda:{"sale":application.confirm_sale(principal=request.user,**data.validated_data)})


class SaleView(OperatorAPIView):
    def get(self,request,sale_id):
        require_capability(principal=request.user,capability=Capability.POS_READ)
        _query(request,set())
        return _respond(lambda:{"sale":application.read_sale(principal=request.user,sale_id=_uuid(sale_id))})


class OperationView(OperatorAPIView):
    def get(self,request,operation_id):
        require_capability(principal=request.user,capability=Capability.POS_CONFIRM)
        _query(request,set())
        return _respond(lambda:{"sale":application.lookup_operation(principal=request.user,operation_id=_uuid(operation_id))})
