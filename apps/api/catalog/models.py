import uuid

from django.db import models


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
