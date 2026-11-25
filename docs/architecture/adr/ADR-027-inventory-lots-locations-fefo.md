# ADR-027: Physical lot attribution and FEFO in the pilot operation

- Status: Accepted
- Date: 2026-11-24
- Human owner: Mauro Harretche
- Technical owner: MIRA engineering

## Context

ADR-019 defines a single EACH StockPosition and immutable StockMovement ledger per Variant. ADR-026 makes accepted purchase receipts through Inventory's application contract. Neither historical stock nor earlier receipts contain reliable lot or location provenance. Physical operators now need explicit lot codes, expiry dates, locations and an ordering for inspecting eligible lots.

## Decision

Inventory owns StockLocation, StockLot, StockLotBalance and append-only StockLotEvent. There is one pilot operation; location codes are unique within it. A lot belongs to one Catalog Variant and can have quantities in multiple locations. Expiry is optional and a calendar date, with a lot expired only after that date. A lot may be ACTIVE, BLOCKED or QUARANTINE. These statuses describe physical handling and do not change aggregate stock.

The aggregate `StockPosition.on_hand` remains authoritative. `unallocated = on_hand - sum(StockLotBalance.quantity)` is explicit and nonnegative. Existing stock and receipts without physical data remain unallocated; no migration backfills or guesses their provenance. Physical allocation requires the full accepted receipt quantity, explicit lot and location UUIDs, and a fresh receipt movement. Procurement persists a ReceiptAllocation snapshot, but Inventory alone mutates physical balances. Procurement's existing receipt schema remains valid without allocations, preserving previous clients and operation identities.

FEFO is a read-only ordering of positive physical quantities in ACTIVE lots whose expiry has not passed. Earlier expiry precedes later expiry, undated lots come last, and lot code plus UUID break ties. BLOCKED, QUARANTINE and expired lots remain visible but are excluded. FEFO is a picking suggestion, not a reservation or assertion of available or sellable stock.

Status changes and transfers use an expected lot version. Corrections use both expected lot and aggregate stock versions, create an ordinary Inventory adjustment movement, and retain its reason. The existing aggregate adjustment may not reduce on-hand below explicitly allocated physical quantity. Transfers do not alter aggregate stock or invent a StockMovement. Every physical mutation records a StockLotEvent and a matching Audit row in the same transaction. Purchase receipt, Inventory movement, physical balances, Procurement receipt and both Audit streams commit or roll back together. No POST is retried automatically.

Capabilities are denied by default: reading lot/location, managing locations, managing lot status/identity, allocating a receipt, transferring and correcting quantities are independently checked by the server before payload or existence lookup. Correcting also requires the existing Inventory adjustment permission. Procurement receiving still requires its existing Procurement and Inventory permissions; supplying allocations additionally requires the allocation permission. Client session hints never grant rights.

## Alternatives and consequences

Backfilling legacy lot assignments would fabricate origin. Replacing the aggregate ledger with lot balances would break ADR-019 and old clients. A single implicit location would hide uncertainty. The chosen additive projection keeps both histories visible and reconciles them without rewriting earlier movements. Cross-table equality is enforced in application transactions and tests, not by a SQL CHECK across tables. Direct privileged SQL could violate it.

The first increment excludes serial numbers, reservations, POS, sellable availability, unit conversion, procurement valuation, tax, payment, multiple operations and automatic relocation. A future rollout to non-test data requires a separate preservation and migration gate. This ADR and local implementation do not authorize deployment or non-test migrations.

## Verification

Exercise current_database() = test_mira, authorization precedence, optimistic conflicts, duplicate receipt operation identity, total allocation equality, legacy unallocated stock, FEFO boundaries and ties, Audit failure rollback, aggregate/physical reconciliation, native desktop/mobile browser journeys and cleanup. New migrations are additive and run only through the authorized isolated test runner.
