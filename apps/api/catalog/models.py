import uuid

from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


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
