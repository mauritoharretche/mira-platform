from django.contrib.auth import get_user_model

from .contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability

_REQUIRED_PERMISSIONS = {
    Capability.SET_VARIANT_ACTIVE: ("catalog.set_variant_active",),
    Capability.POS_DISCOVER: ("pos.discover_products",),
    Capability.POS_QUOTE: ("pos.quote_sale",),
    Capability.POS_CONFIRM: ("pos.confirm_sale",),
    Capability.POS_READ: ("pos.read_sale",),
    Capability.POS_HISTORY: ("pos.list_sales",),
    Capability.READ_PRODUCT: ("catalog.view_product", "catalog.view_variant"),
    Capability.CREATE_PRODUCT_WITH_INITIAL_VARIANT: (
        "catalog.add_product",
        "catalog.add_variant",
    ),
    Capability.ADD_VARIANT_TO_PRODUCT: ("catalog.add_variant",),
    Capability.CREATE_CATEGORY: ("catalog.add_category",),
    Capability.LIST_CATEGORIES: ("catalog.view_category",),
    Capability.SET_PRODUCT_CATEGORY: ("catalog.set_product_category",),
    Capability.SET_PRODUCT_ACTIVE: ("catalog.set_product_active",),
    Capability.SET_VARIANT_PRICE: ("catalog.set_variant_price",),
    Capability.READ_VARIANT_PRICE: ("catalog.view_variantpricerevision",),
    Capability.READ_VARIANT_PRICE_HISTORY: (
        "catalog.view_variantpricerevision", "catalog.view_variant_price_history",
    ),
    Capability.SET_VARIANT_MEASUREMENT_SPEC: ("catalog.set_variant_measurement_spec",),
    Capability.READ_VARIANT_MEASUREMENT_SPEC: ("catalog.view_variantmeasurementspec",),
    Capability.RECEIVE_STOCK: ("inventory.receive_stock",),
    Capability.ADJUST_STOCK: ("inventory.adjust_stock",),
    Capability.READ_STOCK: ("inventory.view_stockposition",),
    Capability.READ_STOCK_MOVEMENTS: ("inventory.view_stockmovement",),
    Capability.READ_STOCK_LOTS: ("inventory.view_stocklot", "inventory.view_stocklocation"),
    Capability.MANAGE_STOCK_LOCATIONS: ("inventory.manage_stock_locations",),
    Capability.MANAGE_STOCK_LOTS: ("inventory.manage_stock_lots",),
    Capability.ALLOCATE_STOCK_LOTS: ("inventory.allocate_stock_lots",),
    Capability.TRANSFER_STOCK_LOTS: ("inventory.transfer_stock_lots",),
    Capability.CORRECT_STOCK_LOTS: ("inventory.correct_stock_lots", "inventory.adjust_stock"),
    Capability.READ_PHYSICAL_INVENTORY: ("inventory.read_physical_inventory",),
    Capability.CREATE_PHYSICAL_COUNT: ("inventory.create_physical_count",),
    Capability.RECORD_PHYSICAL_COUNT: ("inventory.record_physical_count",),
    Capability.REVIEW_PHYSICAL_COUNT: ("inventory.review_physical_count",),
    Capability.COMPLETE_PHYSICAL_COUNT: ("inventory.complete_physical_count",),
    Capability.WRITE_OFF_STOCK: ("inventory.write_off_stock",),
    Capability.READ_AVAILABILITY: ("inventory.read_availability",),
    Capability.READ_RESERVATION: ("inventory.view_stockreservation",),
    Capability.RESERVE_STOCK: ("inventory.reserve_stock",),
    Capability.RELEASE_RESERVATION: ("inventory.release_reservation",),
    Capability.EXPIRE_RESERVATION: ("inventory.expire_reservation",),
    Capability.CONSUME_RESERVATION: ("inventory.consume_reservation",),
    Capability.CLASSIFY_STOCK: ("inventory.classify_stock",),
    Capability.SET_LOCATION_ELIGIBILITY: ("inventory.set_location_eligibility",),
    Capability.READ_SUPPLIER: ("procurement.view_supplier",),
    Capability.MANAGE_SUPPLIER: ("procurement.manage_supplier",),
    Capability.READ_PURCHASE_ORDER: ("procurement.view_purchaseorder",),
    Capability.CREATE_PURCHASE_ORDER: ("procurement.create_purchase_order",),
    Capability.EDIT_PURCHASE_ORDER: ("procurement.edit_purchase_order",),
    Capability.CONFIRM_PURCHASE_ORDER: ("procurement.confirm_purchase_order",),
    Capability.RECEIVE_PURCHASE_ORDER: ("procurement.receive_purchase_order", "inventory.receive_stock"),
}


def require_capability(*, principal: object, capability: Capability) -> ActorContext:
    """Authorize a principal supplied by a trusted authentication boundary.

    The caller must obtain the principal from Django authenticate(), or from a
    future HTTP adapter that verifies authentication before calling this function.
    A User instance (including its is_authenticated property) is not independent
    proof of authentication. Type and persistence checks cannot establish the
    provenance of a User constructed inside the process. Never construct a
    principal from client-supplied identifiers or dictionaries.

    Reloading the user observes state and permission changes between calls, not
    atomic revocation of an operation already in progress. ActorContext must not
    be reused as a credential or as a replacement for a new authorization check.
    """
    user_model = get_user_model()
    if (
        not isinstance(principal, user_model)
        or principal.pk is None
        or principal._state.adding
    ):
        raise AuthenticationRequired("Authentication required")

    try:
        user = user_model._default_manager.get(pk=principal.pk)
    except user_model.DoesNotExist:
        raise AuthenticationRequired("Authentication required") from None

    if not user.is_active:
        raise AccessDenied("Access denied")
    if not isinstance(capability, Capability):
        raise AccessDenied("Access denied")

    if not all(user.has_perm(permission) for permission in _REQUIRED_PERMISSIONS[capability]):
        raise AccessDenied("Access denied")

    return ActorContext(actor_id=str(user.pk))
