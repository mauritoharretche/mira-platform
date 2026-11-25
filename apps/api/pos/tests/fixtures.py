"""Fictional test data created through owner application commands."""
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission

from catalog.application import set_variant_price, set_variant_measurement_spec
from catalog.contracts import MeasurementUnit, PriceBasis
from catalog.models import Category, Product, Variant
from inventory.application import receive_stock
from inventory.contracts import InventoryUnit
from inventory.lot_application import create_location, create_lot
from inventory.reservations import classify_stock, set_location_eligibility

PASSWORD="fictional-pos-only-2026!"


def cashier(username="fictional-pos-cashier"):
    user=get_user_model().objects.create_user(username=username,password=PASSWORD)
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="pos"))
    return user,authenticate(username=username,password=PASSWORD)


def prepare_catalog_stock(prefix="FIC-POS", each=10, mass=1_000_000):
    username=f"fictional-preparer-{uuid4().hex[:12]}"
    get_user_model().objects.create_superuser(username=username,password=PASSWORD)
    preparer=authenticate(username=username,password=PASSWORD)
    category=Category.objects.create(name=prefix,name_key=prefix.casefold())
    location=create_location(principal=preparer,code=prefix,name="Fictional POS shelf")
    set_location_eligibility(principal=preparer,location_id=location.pk,eligible=True,expected_version=location.version,reason="Fictional isolated test eligibility")
    variants=[]
    for suffix,unit,quantity,basis in (("EACH",InventoryUnit.EACH,each,PriceBasis.PER_VARIANT),("MASS",InventoryUnit.G,mass,PriceBasis.PER_KG)):
        product=Product.objects.create(name=f"Fictional {prefix} {suffix}",category=category,is_active=True)
        variant=Variant.objects.create(product=product,label=f"Fictional {suffix}",sku=f"{prefix}-{suffix}",barcode=f"{prefix}-BAR-{suffix}")
        set_variant_price(principal=preparer,variant_id=variant.pk,amount=Decimal("100.00"),currency="ARS",expected_revision_id=None,price_basis=basis)
        set_variant_measurement_spec(principal=preparer,variant_id=variant.pk,sale_unit=MeasurementUnit.EACH if unit is InventoryUnit.EACH else MeasurementUnit.G,
            net_content_amount=None,net_content_unit=None,expected_version=None)
        receive_stock(principal=preparer,variant_id=variant.pk,unit=unit,quantity=quantity,expected_version=None)
        lot=create_lot(principal=preparer,variant_id=variant.pk,code=f"{prefix}-{suffix}",expires_on=None)
        classify_stock(principal=preparer,operation_id=uuid4(),lot_id=lot.pk,location_id=location.pk,quantity=quantity,expected_stock_version=1,evidence="Fictional physical stock verification",unit=unit)
        variants.append(variant)
    return preparer,category,location,tuple(variants)
