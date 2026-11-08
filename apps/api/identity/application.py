from django.contrib.auth import get_user_model

from .contracts import AccessDenied, ActorContext, AuthenticationRequired, Capability

_REQUIRED_PERMISSIONS = {
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
