import uuid
from decimal import Decimal

from django.db import models

from .contracts import MeasurementUnit, PriceBasis


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    name_key = models.CharField(max_length=255, db_collation="C", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_category_name_key_unique"),
            models.CheckConstraint(
                condition=~models.Q(name="") & ~models.Q(name_key=""),
                name="catalog_category_names_nonempty",
            ),
        ]


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.PROTECT, related_name="products"
    )
    is_active = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("set_product_category", "Can set product category"),
            ("set_product_active", "Can set product active state"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_active=False) | models.Q(category__isnull=False),
                name="catalog_active_product_has_category",
            ),
        ]


class Variant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    label = models.CharField(max_length=255)
    sku = models.CharField(max_length=128, db_collation="C")
    barcode = models.CharField(max_length=255, null=True, blank=True, db_collation="C")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sku"], name="catalog_variant_sku_unique"),
            models.UniqueConstraint(fields=["barcode"], name="catalog_variant_barcode_unique"),
        ]


class VariantPriceRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name="price_revisions")
    sequence = models.PositiveBigIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, db_collation="C")
    price_basis = models.CharField(
        max_length=16, db_collation="C",
        choices=[(basis.value, basis.name) for basis in PriceBasis],
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("set_variant_price", "Can set variant price"),
            ("view_variant_price_history", "Can view variant price history"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "sequence"], name="catalog_price_variant_sequence_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gt=0), name="catalog_price_sequence_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0, amount__lte=Decimal("999999999999.99")),
                name="catalog_price_amount_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(currency="ARS"), name="catalog_price_currency_ars",
            ),
            models.CheckConstraint(
                condition=models.Q(price_basis=PriceBasis.PER_VARIANT.value),
                name="catalog_price_basis_valid",
            ),
        ]


class VariantMeasurementSpec(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.OneToOneField(Variant, on_delete=models.PROTECT, related_name="measurement_spec")
    version = models.PositiveBigIntegerField()
    sale_unit = models.CharField(
        max_length=8, null=True, blank=True, db_collation="C",
        choices=[(unit.value, unit.name) for unit in MeasurementUnit],
    )
    net_content_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    net_content_unit = models.CharField(
        max_length=8, null=True, blank=True, db_collation="C",
        choices=[(unit.value, unit.name) for unit in (MeasurementUnit.G, MeasurementUnit.KG)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("set_variant_measurement_spec", "Can set variant measurement specification")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(version__gt=0), name="catalog_measurement_version_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_unit__isnull=True) | models.Q(
                    sale_unit__in=[unit.value for unit in MeasurementUnit]
                ),
                name="catalog_measurement_sale_unit_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(net_content_amount__isnull=True, net_content_unit__isnull=True)
                    | models.Q(
                        net_content_amount__isnull=False, net_content_unit__isnull=False,
                        net_content_amount__gt=0, net_content_amount__lte=Decimal("999999999999.999999"),
                        net_content_unit__in=["G", "KG"],
                    )
                ),
                name="catalog_measurement_net_valid",
            ),
        ]
