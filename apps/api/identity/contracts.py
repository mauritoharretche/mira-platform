from dataclasses import dataclass
from enum import Enum


class Capability(Enum):
    READ_PRODUCT = "catalog.read_product"
    CREATE_PRODUCT_WITH_INITIAL_VARIANT = "catalog.create_product_with_initial_variant"
    ADD_VARIANT_TO_PRODUCT = "catalog.add_variant_to_product"


@dataclass(frozen=True)
class ActorContext:
    """Actor identity only; neither a credential nor a persistent audit record."""

    actor_id: str


class AuthenticationRequired(PermissionError):
    """A trusted, persisted user principal is required."""


class AccessDenied(PermissionError):
    """The requested capability is not authorized."""
