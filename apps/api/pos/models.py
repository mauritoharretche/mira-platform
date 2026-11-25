import uuid
from django.db import models


class SaleQuote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField(max_length=128, db_collation="C")
    state = models.CharField(max_length=16, default="QUOTED", db_collation="C")
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        permissions = [("discover_products", "Can discover POS products"), ("quote_sale", "Can quote sales"),
                       ("confirm_sale", "Can confirm isolated sales"), ("read_sale", "Can retrieve sales"),
                       ("list_sales", "Can list sales history")]
        constraints = [models.CheckConstraint(condition=models.Q(state__in=["QUOTED", "CANCELLED", "COMMITTED"]), name="pos_quote_state_valid")]
        indexes = [models.Index(fields=["actor_id", "created_at"], name="pos_quote_actor_created")]


class Sale(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.OneToOneField(SaleQuote, on_delete=models.PROTECT)
    operation_id = models.UUIDField(unique=True)
    request_sha256 = models.CharField(max_length=64, db_collation="C")
    actor_id = models.CharField(max_length=128, db_collation="C")
    state = models.CharField(max_length=32, default="COMMITTED_UNSETTLED", db_collation="C")
    settlement = models.CharField(max_length=16, default="UNSETTLED", db_collation="C")
    total_ars = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="ARS", db_collation="C")
    reservation_id = models.UUIDField(unique=True)
    inventory_trace = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(state="COMMITTED_UNSETTLED", settlement="UNSETTLED", currency="ARS"), name="pos_sale_unsettled_only"),
            models.CheckConstraint(condition=models.Q(total_ars__gte=0, total_ars__lte="999999999999.99"), name="pos_sale_total_bounded"),
        ]
        indexes = [models.Index(fields=["created_at", "id"], name="pos_sale_created_id"), models.Index(fields=["actor_id", "created_at"], name="pos_sale_actor_created")]


class SaleLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="lines")
    ordinal = models.PositiveSmallIntegerField()
    variant_id = models.UUIDField()
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sale", "ordinal"], name="pos_line_ordinal_unique"),
                       models.UniqueConstraint(fields=["sale", "variant_id"], name="pos_line_variant_unique"),
                       models.CheckConstraint(condition=models.Q(ordinal__gte=1, ordinal__lte=30), name="pos_line_ordinal_bounded")]
