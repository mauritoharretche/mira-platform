import uuid

from django.db import models

from .contracts import OrderStatus


class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    name_key = models.CharField(max_length=120, db_collation="C")
    active = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("manage_supplier", "Can manage suppliers")]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="proc_supplier_name_unique"),
            models.CheckConstraint(condition=models.Q(version__gt=0), name="proc_supplier_version_positive"),
        ]


class PurchaseOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=16, db_collation="C", default=OrderStatus.DRAFT.value)
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [
            ("create_purchase_order", "Can create purchase orders"),
            ("edit_purchase_order", "Can edit draft purchase orders"),
            ("confirm_purchase_order", "Can confirm purchase orders"),
            ("receive_purchase_order", "Can receive purchase orders"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gt=0), name="proc_order_version_positive"),
            models.CheckConstraint(condition=models.Q(status__in=[s.value for s in OrderStatus]), name="proc_order_status_valid"),
        ]


class PurchaseOrderLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    variant_id = models.UUIDField()
    variant_label = models.CharField(max_length=255)
    variant_sku = models.CharField(max_length=128)
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveBigIntegerField()
    received_quantity = models.PositiveBigIntegerField(default=0)
    unit = models.CharField(max_length=8, default="EACH", db_collation="C")
    unit_cost_ars = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "variant_id"], name="proc_order_variant_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="proc_order_quantity_positive"),
            models.CheckConstraint(condition=models.Q(received_quantity__lte=models.F("quantity")), name="proc_order_received_bounded"),
            models.CheckConstraint(condition=models.Q(unit="EACH"), name="proc_order_unit_each"),
            models.CheckConstraint(condition=models.Q(unit_cost_ars__gte=0), name="proc_order_cost_nonnegative"),
        ]


class GoodsReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    operation_id = models.UUIDField()
    request_sha256 = models.CharField(max_length=64, db_collation="C")
    actor_id = models.CharField(max_length=128)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["operation_id"], name="proc_receipt_operation_unique")]


class ReceiptLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, related_name="lines")
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipts")
    delivered_quantity = models.PositiveBigIntegerField()
    accepted_quantity = models.PositiveBigIntegerField()
    difference_reason = models.CharField(max_length=255, null=True, blank=True)
    stock_movement_id = models.UUIDField(unique=True, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["receipt", "order_line"], name="proc_receipt_line_unique"),
            models.CheckConstraint(condition=models.Q(delivered_quantity__gt=0), name="proc_delivered_positive"),
            models.CheckConstraint(condition=models.Q(accepted_quantity__lte=models.F("delivered_quantity")), name="proc_accepted_bounded"),
            models.CheckConstraint(condition=(
                models.Q(accepted_quantity=models.F("delivered_quantity"), difference_reason__isnull=True)
                | models.Q(accepted_quantity__lt=models.F("delivered_quantity"), difference_reason__isnull=False)
            ), name="proc_difference_shape"),
            models.CheckConstraint(condition=(
                models.Q(accepted_quantity=0, stock_movement_id__isnull=True)
                | models.Q(accepted_quantity__gt=0, stock_movement_id__isnull=False)
            ), name="proc_movement_shape"),
        ]


class ProcurementEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, related_name="events")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, null=True, related_name="events")
    receipt_id = models.UUIDField(null=True)
    action = models.CharField(max_length=40, db_collation="C")
    version = models.PositiveBigIntegerField()
    actor_id = models.CharField(max_length=128)
    snapshot = models.JSONField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gt=0), name="proc_event_version_positive"),
            models.CheckConstraint(condition=models.Q(action__in=[
                "supplier.created", "supplier.updated", "order.created", "order.updated",
                "order.confirmed", "order.received",
            ]), name="proc_event_action_valid"),
        ]
