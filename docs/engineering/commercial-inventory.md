# INV-03 commercial inventory

## Approved local plan and scope

Status: In Progress. Owner: Mauro Harretche. Technical owner: MIRA engineering. Date: 2026-11-24. ADR-028 records the design. The maintainer's INV-03 mandate approves local architecture, additive test migrations, API, UI and verification; publication requires a separate manifest approval.

Implement Inventory reservation models/contracts and Audit, extend all Inventory writers with a common transaction gate, add granular Identity capabilities and explicit operator routes, extend the existing Inventory workspace, and verify PostgreSQL transactions/concurrency plus desktop/mobile native journeys. Preserve Procurement ownership, old receipt clients, ledger history and unallocated stock. Risks are double allocation, stale eligibility, expiry races, partial multi-variant writes and uncertain POSTs; the tests must exercise each. Rollout is local/test only. Reverse migrations and production rollout are not authorized.

## Operator flow

In Lotes, **Disponibilidad y reservas** shows physical, lot-assigned, unallocated, eligible, reserved, unavailable and reservable quantities. A snapshot has an explicit query timestamp and zero safety stock. It does not declare a product sellable. Locations start commercially ineligible. An authorized operator verifies a location and records its eligibility with a reason and expected version.

Add one or more variants to a draft reservation, specify a fictional source/reference and explicit expiration. The server allocates FEFO atomically. Reservation details expose allocation and state history; release, expiry and consumption have independent permissions. Consumption records a physical CONSUMPTION movement, without creating a sale. If a write result is uncertain, retain its operation UUID and query it. Absence of an operation does not prove a write failed. No UI POST is automatically retried.

Classify existing stock only after verifying current count, lot label and physical location. The command preserves on-hand and old ledger history. Without verifiable evidence, retain unallocated stock, which is excluded from reservations.

## HTTP surface

All routes below are under `/api/v1/operator/inventory`. EACH quantities and versions are canonical integer strings; INV-04 adds explicit G quantities under [the measured contract](measured-inventory-and-counts.md); IDs are UUIDs; timestamps require an explicit UTC offset. Every endpoint authorizes before parameter/existence checks.

| Method/path | Capability |
| --- | --- |
| GET variants/{id}/availability | inventory.read_availability |
| GET reservations, reservations/{id}, operations/{id} | inventory.read_reservation |
| POST reservations | inventory.reserve_stock |
| POST reservations/{id}/release | inventory.release_reservation |
| POST reservations/{id}/expire | inventory.expire_reservation |
| POST reservations/{id}/consume | inventory.consume_reservation |
| POST classifications | inventory.classify_stock |
| POST locations/{id}/eligibility | inventory.set_location_eligibility |

Create requires `operation_id`, `source`, `reference`, `expires_at` and 1–30 unique variant lines `{variant_id, quantity}`. Lifecycle commands require a fresh operation identity or the identical identity for a previously recorded result. Different request content on the same identity returns `lot_conflict` 409. Insufficient eligibility or an invalid transition also conflicts. Read results include effective expiry without requiring a scheduled job. Listing is bounded and marked truncated; direct UUID lookup remains available.

Classification requires `operation_id`, `lot_id`, `location_id`, `quantity`, `expected_stock_version` and `evidence`. Eligibility changes require strict boolean `eligible`, `expected_version` and `reason`. Safety disabling releases entire impacted reservations atomically. Expiration cleanup is the typed `inventory.reservations.expire_reservations(principal=..., limit=...)` application command; correctness is independent of its scheduling. No background infrastructure is added.

## Verification and limits

Tests use real PostgreSQL exclusively through the native isolated test_mira runner, with current_database verification. Cover all 24 INV-03 scenarios, old Procurement and stock regressions, frontend/engineering gates, dependency audits and native desktop/mobile journeys. Exact outcomes are recorded in canonical delivery evidence, not inferred from this document. No production readiness, accessibility certification, POS behavior or non-test migration is claimed.

Two private source documents were available for local domain review. The separate development-system master document was unavailable. Private text and business information are not copied into this public documentation. The explicit INV-03 mandate supplies the implementation boundary.
