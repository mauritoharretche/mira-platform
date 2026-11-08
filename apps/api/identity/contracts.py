from dataclasses import dataclass
from enum import Enum


class Capability(Enum):
    READ_PRODUCT = "catalog.read_product"
    CREATE_PRODUCT_WITH_INITIAL_VARIANT = "catalog.create_product_with_initial_variant"
    ADD_VARIANT_TO_PRODUCT = "catalog.add_variant_to_product"
    CREATE_CATEGORY = "catalog.create_category"
    LIST_CATEGORIES = "catalog.list_categories"
    SET_PRODUCT_CATEGORY = "catalog.set_product_category"
    SET_PRODUCT_ACTIVE = "catalog.set_product_active"
    SET_VARIANT_PRICE = "catalog.set_variant_price"
    READ_VARIANT_PRICE = "catalog.read_variant_price"
    READ_VARIANT_PRICE_HISTORY = "catalog.read_variant_price_history"
    SET_VARIANT_MEASUREMENT_SPEC = "catalog.set_variant_measurement_spec"
    READ_VARIANT_MEASUREMENT_SPEC = "catalog.read_variant_measurement_spec"
    RECEIVE_STOCK = "inventory.receive_stock"
    ADJUST_STOCK = "inventory.adjust_stock"
    READ_STOCK = "inventory.read_stock"
    READ_STOCK_MOVEMENTS = "inventory.read_stock_movements"


@dataclass(frozen=True)
class ActorContext:
    """Actor identity only; neither a credential nor a persistent audit record."""

    actor_id: str


class AuthenticationRequired(PermissionError):
    """A trusted, persisted user principal is required."""


class AccessDenied(PermissionError):
    """The requested capability is not authorized."""
