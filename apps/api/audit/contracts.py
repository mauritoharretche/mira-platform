from enum import Enum


class CatalogAuditAction(Enum):
    PRODUCT_CREATED_WITH_INITIAL_VARIANT = "catalog.product_created_with_initial_variant"
    VARIANT_ADDED_TO_PRODUCT = "catalog.variant_added_to_product"


class CatalogChangeAuditAction(Enum):
    CATEGORY_CREATED = "catalog.category_created"
    PRODUCT_CATEGORY_CHANGED = "catalog.product_category_changed"
    PRODUCT_ACTIVE_CHANGED = "catalog.product_active_changed"


class CatalogPriceAuditAction(Enum):
    VARIANT_PRICE_SET = "catalog.variant_price_set"


class CatalogMeasurementAuditAction(Enum):
    SPEC_CHANGED = "catalog.variant_measurement_spec_changed"


class InventoryMovementAuditAction(Enum):
    STOCK_RECEIVED = "inventory.stock_received"
    STOCK_ADJUSTED = "inventory.stock_adjusted"
