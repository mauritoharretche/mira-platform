from enum import Enum


class CatalogAuditAction(Enum):
    PRODUCT_CREATED_WITH_INITIAL_VARIANT = "catalog.product_created_with_initial_variant"
    VARIANT_ADDED_TO_PRODUCT = "catalog.variant_added_to_product"
