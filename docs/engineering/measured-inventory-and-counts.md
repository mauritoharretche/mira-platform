# INV-04 measured inventory and physical counts

## Scope and workflow

The maintainer's INV-04 mandate authorizes the local implementation plan in ADR-029. Inventory retains its single ledger and pilot transaction lock; Procurement owns receipt commands, Catalog owns immutable price revisions, Identity authorizes each operation and Audit attributes committed changes. ADR-028 is accepted against its merged INV-03 behavior; ADR-029 remains Proposed. Publication, deployment, non-test migrations and future scale/metrology approval remain separate gates.

In a product's **Existencia física**, explicitly choose EACH or G for the first receipt. That position's accounting unit is frozen. Existing EACH stock, revisions, receipts, allocations and operation fingerprints retain their original meaning. Package net content, sale-unit metadata, SKU and price never select or convert an Inventory unit. Quantity input must use a decimal point, without exponent or grouping separators.

EACH is an integer. G is an integer count of milligrams internally, with **0.001 G** resolution. HTTP G quantities are strings with at most three fractional digits, returned with exactly three. Conversion to KG is exact to six fractional digits: `245.001 G = 0.245001 KG`. The maximum base-unit integer is `9223372036854775807`. Excess precision and overflow fail; no quantity is silently rounded. The UI uses BigInt for validation/conversion, never binary floating point for quantity authority.

Procurement requires explicit units and cost bases: EACH / PER_VARIANT or G / PER_KG. Ordered, delivered, accepted, pending and lot-allocation quantities share the line's unit. Rejected quantities do not enter stock. Partial receipts retain operation identities and version checks. Existing callers omitting the newly optional receipt unit continue to mean EACH; a mass line requires G explicitly. Allocation is optional; stock without verified lot/location remains unallocated.

In **Lotes**, balances, availability and reservation lines expose the position unit. One reservation can contain different variants in EACH and G. FEFO, reserved-stock guards, release, expiry and idempotent consumption retain the INV-03 protocol. Neither physical quantity nor reservable quantity asserts sellability.

## Commercial calculations

Price writes may explicitly set `price_basis: PER_KG` in the existing price endpoint; omitted basis preserves PER_VARIANT. ARS amounts remain exact two-decimal strings, and `expected_revision_id` remains required. A basis-only change is a new attributed revision; an amount-and-basis no-op creates none. Historical revisions are not reinterpreted. Reading a current price does not resolve a conflict or uncertain write.

`catalog.calculations.calculate_price` accepts an immutable price snapshot, exact Decimal transaction quantity and explicit MeasurementUnit. `calculate_amount` is also reused for purchase costs. PER_VARIANT requires whole EACH; PER_KG requires G or KG at milligram precision. Decimal precision is 60: convert mass exactly, multiply once, round each line to ARS cents with ROUND_HALF_EVEN, then sum rounded line totals. For example, ARS 1.00/KG × 5 G is ARS 0.00; × 15 G is ARS 0.02. Inputs themselves are never rounded. No valuation, completed sale, payment or tax calculation is implemented.

`MassMeasurement` is a hardware-independent typed contract for net, optional paired gross/tare, G/KG, printable source identity, aware measurement timestamp and explicit resolution. It validates the milligram floor, bounded exact multiples and `gross - tare = net`. Drivers, scale protocols, label parsing and regulatory validation require a future real POC.

## Physical counts and losses

Under **Conteos físicos y mermas**, select one active location and 1–100 explicit lot-balance identities. A session records its responsible creator and initial quantities plus stock/lot versions. Scope may include several variants at that location. Blind entry never preloads expected quantities; users with independent review permission can explicitly consult expected values and discrepancies. Read-lot/read-stock permissions independently permit physical balance visibility; blind entry is not a mechanism to revoke those capabilities.

Record each observed quantity in its saved unit with reason and evidence, review all observations, then explicitly complete. At most 197 observation events plus creation/review/completion fit a session's bounded 200-event history. Completion validates every snapshot before applying any correction. A changed stock/lot version produces 409 rather than overwriting newer stock; open a new explicit scope when necessary. A zero discrepancy produces no StockMovement. Each effective discrepancy produces a COUNT movement and corresponding Inventory Audit, plus attributable workflow events. Effective reservations cannot be silently removed.

A write-off requires a known balance, explicit unit/quantity, current stock version, evidence and one reason code: EXPIRATION, DAMAGE, QUALITY_REJECTION, MEASUREMENT_DISCREPANCY, INTERNAL_CONSUMPTION or OTHER. It produces one negative WRITE_OFF movement, persistent loss identity and Audit. A blocked/expired/quarantined lot still physically exists until such an authorized exit. No monetary value is inferred.

After a rejected write, consult the snapshot and explicitly decide before resubmission. After an uncertain write, retain the operation UUID and consult its result: absence is not proof of failure, and a read never unlocks an uncertain POST automatically. Do not navigate away before retaining the displayed identity for investigation.

## Protected HTTP surface

All paths below are relative to `/api/v1/operator/inventory`; every adapter authorizes before payload validation or existence lookup. No permissions are granted automatically. IDs are UUIDs, versions are positive integer strings and each command requires an explicit operation UUID. All established session, CSRF, loopback and destination guards remain active.

| Method / path | Independent capability | Payload |
| --- | --- | --- |
| GET counts, counts/{id}, physical-operations/{id} | inventory.read_physical_inventory | Read only; list limited to 50 |
| POST counts | inventory.create_physical_count | operation_id, location_id, balance_ids, blind (strict boolean) |
| POST counts/{id}/lines/{line_id} | inventory.record_physical_count | operation_id, observed, unit, reason, evidence |
| POST counts/{id}/review | inventory.review_physical_count | operation_id |
| POST counts/{id}/complete | inventory.complete_physical_count | operation_id |
| POST losses | inventory.write_off_stock | operation_id, balance_id, quantity, unit, expected_stock_version, reason_code, evidence |

Count states are OPEN → REVIEWED → COMPLETED. Completion and write-off share the original Inventory transaction gate with all other writers; domain updates, operation result and Audit commit or roll back together. Operation UUID reuse with different actor, content or action conflicts. The original reservation-operation lookup does not disclose physical-count results. Workflow history has explicit bounds and no automatic POST retry.

## Compatibility, verification and rollback

New migrations add count/loss tables and the purchase cost/previous price basis columns, broaden G and movement constraints, and preserve existing EACH values. They do not edit earlier migrations, fabricate origins or convert historical quantities. Legacy price Audit may retain a null previous basis; new application writes supply it explicitly. Deployment of these migrations is not authorized here. Before any future non-test rollout, separately approve backup, schema compatibility, client recognition of COUNT/WRITE_OFF and PER_KG, observation and rollback procedures. Recovery uses forward audited corrections; never reverse/drop populated tables or reset balances.

Verification uses only the native Django test runner and test_mira, including SQL current_database verification, transactional failure injection, concurrency, exact quantity boundaries, protected HTTP and migration compatibility. Native browser projects run once per batch, workers=1 and retries=0, with the actual global login-window barrier. Exact observed results belong in delivery evidence. No hardware, production readiness, WCAG certification or real Chrome zoom coverage is claimed.
