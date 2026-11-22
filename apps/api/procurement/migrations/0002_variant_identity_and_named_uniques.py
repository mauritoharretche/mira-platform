from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("procurement", "0001_initial")]

    operations = [
        migrations.AlterField(model_name="supplier", name="name_key",
                              field=models.CharField(max_length=120, db_collation="C")),
        migrations.AddConstraint(model_name="supplier",
                                 constraint=models.UniqueConstraint(fields=["name_key"], name="proc_supplier_name_unique")),
        migrations.AlterField(model_name="goodsreceipt", name="operation_id", field=models.UUIDField()),
        migrations.AddConstraint(model_name="goodsreceipt",
                                 constraint=models.UniqueConstraint(fields=["operation_id"], name="proc_receipt_operation_unique")),
        migrations.AddField(model_name="purchaseorderline", name="variant_label",
                            field=models.CharField(max_length=255, default=""), preserve_default=False),
        migrations.AddField(model_name="purchaseorderline", name="variant_sku",
                            field=models.CharField(max_length=128, default=""), preserve_default=False),
        migrations.AddField(model_name="purchaseorderline", name="product_name",
                            field=models.CharField(max_length=255, default=""), preserve_default=False),
    ]
