# ADR-028: Commercial EACH inventory and transactional reservations

- Status: Accepted
- Date: 2026-11-24
- Human owner: Mauro Harretche
- Technical owner: MIRA engineering

## Context and authorization

The maintainer authorized grouped local INV-03 implementation against the merged INV-02 baseline. Inventory needs to prevent concurrent claims on physical stock before future Orders or POS integration. This decision does not implement a sale, payment, checkout, multistore operation or non-test rollout. ADR-019 and ADR-026 remain authoritative for ledger and receipt ownership; ADR-027 describes physical lot attribution.

## Decision

Inventory owns reservations, allocation details, operation identities and immutable reservation events. A reservation has ACTIVE, RELEASED, CONSUMED or EXPIRED state. It contains explicit source/reference identities, actor, creation and timezone-aware expiration timestamps. A canonical SHA-256 request fingerprint binds the action, actor and normalized payload to a unique operation UUID. The same identity returns its saved result; different content/action/actor conflicts. Operation lookup is read-only. No automatic business retry occurs.

Availability distinguishes physical on-hand, physical lot allocation, unallocated physical stock, eligible physical stock, effective reservations and commercial availability. Safety stock is explicitly zero; no threshold is invented. Eligibility requires positive physical quantity, ACTIVE lot, an unexpired lot and an active location explicitly enabled for commercial allocation. New and historical locations default to commercial eligibility disabled. Product activation, price and measurement do not imply sellability. The snapshot describes reservable inventory only.

An expiry date remains valid throughout its UTC calendar date, preserving the baseline timezone and boundary policy. Timestamp reservation expiration applies at `expires_at <= now`. Effective reservations exclude expired reservations even if cleanup never runs. Reads report stored and effective states without writing Audit. The explicit bounded `expire_reservations` application command persists due transitions idempotently and can be called by a future authorized worker; no new service is introduced.

All relevant balances participate in allocation; the UI's limited lot list is never its source. FEFO orders dated eligible lots first by expiry, code and lot UUID, then location code/UUID; undated lots come last. Multi-variant reservations allocate every requested line or roll back the entire command. Counts remain bounded integer EACH, with strings over HTTP. Future decimal inventory needs a new explicit unit/scale contract; no conversions are inferred.

Unavailable stock is the union of unallocated stock, ineligible physical stock and effective reservations. Overlapping reasons are not subtracted twice. Whole-reservation invalidation releases every allocation when a safety change makes a lot or location ineligible, retaining an attributed invalidation event. Blocking and quarantine remain possible while stock is reserved. Transfers and corrections may affect only units left after effective reservations. Aggregate adjustment may not reduce below all lot-assigned physical quantity.

## Lock hierarchy and transaction boundary

Every Inventory writer first acquires the same PostgreSQL transaction advisory gate for the single pilot operation. The gate serializes receipts, physical allocation, classification, lot/location mutation, reservation, release, expiration and consumption. A caller-owned transaction retains it through commit/rollback; nested commands reacquire it safely. Within the gate, position/lot/balance/reservation row locks are taken only by that transaction, with variants sorted for multi-variant commands. Reads needing a multi-query coherent snapshot take the same gate. Procurement can hold its order lock before entering Inventory; Inventory never calls Procurement, so the dependency does not form a reverse lock edge. No network calls occur while Inventory locks are held.

This intentionally trades write concurrency for a small, reviewable correctness boundary in one operation. A future partitioned/per-variant locking design must migrate all writers together and receive a separate review; mixing lock protocols is forbidden. Privileged SQL outside these contracts can break application invariants; no cross-table SQL CHECK or cryptographic ledger guarantee is claimed.

Consumption rechecks expiration and physical eligibility, then reduces the exact allocation balances and aggregate StockPosition. One explicit CONSUMPTION movement per variant and corresponding Inventory Audit explains the exit; allocation rows link those movement IDs. It is never represented as an adjustment or completed sale. Consumed reservations are terminal. Release/expiry do not alter physical quantity. All domain and Audit writes participate in the same default transaction, including a future outer POS/Orders transaction.

Historical classification requires a verified quantity, explicit lot/location, expected aggregate version and evidence. It moves only unallocated stock into physical classification; on-hand and StockMovement history do not increase or change. It records current verification, not a fabricated supplier or historical receipt.

## Authorization and compatibility

Independent capabilities authorize availability read, reservation read, create, release, expire, consume, classification and location eligibility before identifiers or payload validation. No permission is assigned automatically. Existing session/CSRF/host/destination guards remain intact. Existing receipt and adjustment payloads remain valid. Movement clients must recognize the additive CONSUMPTION kind; the operator history renders it explicitly. Old clients must not treat an unknown movement kind as an adjustment.

## Alternatives, migration and recovery

Per-row locks without a common protocol would leave gaps across initially absent stock, locations and reservations. An external cache/queue would add a second authority. The shared PostgreSQL gate is selected for the pilot scale. Existing rows receive no provenance backfill; only location eligibility defaults and new empty reservation structures are added. Existing movement/Audit checks are extended to permit negative CONSUMPTION, preserving all prior valid rows. No historical movement or Audit is rewritten.

Only the isolated native runner may apply these migrations to test_mira during local verification. Deployment and non-test migrations need a separate preservation/backup/rollout decision. Recovery uses operation lookup, explicit terminal transitions or forward fixes; never reset physical balances, delete events or blindly resubmit uncertain writes. Publication remains governed by ADR-018 and approval of the exact canonical manifest.

## Subsequent compatibility clarification — 2026-11-25

The EACH-only wording above records this ADR’s original increment. ADR-029 subsequently extends the same ledger and typed contracts to explicit G accounting in integer milligrams, measured reservations/receipts and PER_KG commercial calculation. Existing EACH records and meanings remain unchanged; no historical quantities or provenance are inferred or converted. ADR-030 describes the separately authorized test-only POS consumer. This clarification does not authorize non-test migrations, payment, settlement or real trade.
