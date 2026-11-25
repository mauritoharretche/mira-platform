from rest_framework import serializers
from rest_framework.response import Response

from identity.application import require_capability
from identity.contracts import Capability
from system.http import OperatorAPIView, StrictSerializer, StrictTextField
from . import counting
from .contracts import InventoryValidationError
from .lot_http import _respond, _uuid, _positive
from .quantities import parse_quantity, unit as accounting_unit


class CountCreateInput(StrictSerializer):
    operation_id=serializers.UUIDField()
    location_id=serializers.UUIDField()
    balance_ids=serializers.ListField(child=serializers.UUIDField(),min_length=1,max_length=100)
    blind=serializers.BooleanField()

    def validate_blind(self,value):
        if type(self.initial_data.get("blind")) is not bool:raise serializers.ValidationError("Explicit boolean required")
        return value


class ObservationInput(StrictSerializer):
    operation_id=serializers.UUIDField()
    unit=StrictTextField()
    observed=StrictTextField(max_length=30)
    reason=StrictTextField(max_length=255)
    evidence=StrictTextField(max_length=255)

    def validate(self,attrs):
        try:
            attrs["unit"]=accounting_unit(attrs["unit"])
            attrs["observed"]=parse_quantity(attrs["observed"],attrs["unit"])
        except InventoryValidationError:raise serializers.ValidationError("Exact observation and explicit unit required") from None
        return attrs


class LossInput(StrictSerializer):
    operation_id=serializers.UUIDField()
    balance_id=serializers.UUIDField()
    unit=StrictTextField()
    quantity=StrictTextField(max_length=30)
    expected_stock_version=StrictTextField(max_length=19)
    reason_code=StrictTextField(max_length=32)
    evidence=StrictTextField(max_length=255)

    def validate_expected_stock_version(self,value):return _positive(value)

    def validate(self,attrs):
        try:
            attrs["unit"]=accounting_unit(attrs["unit"])
            attrs["quantity"]=parse_quantity(attrs["quantity"],attrs["unit"],positive=True)
        except InventoryValidationError:raise serializers.ValidationError("Exact loss quantity and explicit unit required") from None
        return attrs


class TransitionInput(StrictSerializer):
    operation_id=serializers.UUIDField()


class CountsView(OperatorAPIView):
    def get(self,request):
        require_capability(principal=request.user,capability=Capability.READ_PHYSICAL_INVENTORY)
        result=counting.list_counts(principal=request.user)
        return Response({"counts":result[:50],"truncated":len(result)>50})

    def post(self,request):
        require_capability(principal=request.user,capability=Capability.CREATE_PHYSICAL_COUNT)
        serializer=CountCreateInput(data=request.data);serializer.is_valid(raise_exception=True)
        data=dict(serializer.validated_data);data["balance_ids"]=tuple(data["balance_ids"])
        result=_respond(lambda:counting.create_count(principal=request.user,**data))
        return result if hasattr(result,"status_code") else Response({"count":result},status=201)


class CountView(OperatorAPIView):
    http_method_names=["get","options"]

    def get(self,request,count_id):
        require_capability(principal=request.user,capability=Capability.READ_PHYSICAL_INVENTORY)
        result=_respond(lambda:counting.read_count(principal=request.user,count_id=_uuid(count_id)))
        return result if hasattr(result,"status_code") else Response({"count":result})


class ObservationView(OperatorAPIView):
    http_method_names=["post","options"]

    def post(self,request,count_id,line_id):
        require_capability(principal=request.user,capability=Capability.RECORD_PHYSICAL_COUNT)
        serializer=ObservationInput(data=request.data);serializer.is_valid(raise_exception=True)
        result=_respond(lambda:counting.record_observation(principal=request.user,count_id=_uuid(count_id),line_id=_uuid(line_id),**serializer.validated_data))
        return result if hasattr(result,"status_code") else Response({"count":result})


class ReviewCountView(OperatorAPIView):
    http_method_names=["post","options"]
    capability=Capability.REVIEW_PHYSICAL_COUNT
    action="review"

    def post(self,request,count_id):
        require_capability(principal=request.user,capability=self.capability)
        serializer=TransitionInput(data=request.data);serializer.is_valid(raise_exception=True)
        result=_respond(lambda:counting.transition_count(principal=request.user,count_id=_uuid(count_id),action=self.action,**serializer.validated_data))
        return result if hasattr(result,"status_code") else Response({"count":result})


class CompleteCountView(ReviewCountView):
    capability=Capability.COMPLETE_PHYSICAL_COUNT
    action="complete"


class LossView(OperatorAPIView):
    http_method_names=["post","options"]

    def post(self,request):
        require_capability(principal=request.user,capability=Capability.WRITE_OFF_STOCK)
        serializer=LossInput(data=request.data);serializer.is_valid(raise_exception=True)
        result=_respond(lambda:counting.write_off(principal=request.user,**serializer.validated_data))
        return result if hasattr(result,"status_code") else Response(result,status=201)


class PhysicalOperationView(OperatorAPIView):
    http_method_names=["get","options"]

    def get(self,request,operation_id):
        require_capability(principal=request.user,capability=Capability.READ_PHYSICAL_INVENTORY)
        result=_respond(lambda:counting.query_physical_operation(principal=request.user,operation_id=_uuid(operation_id)))
        return result if hasattr(result,"status_code") else Response(result)
