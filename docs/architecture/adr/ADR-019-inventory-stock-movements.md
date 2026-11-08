# ADR-019: Inventory accounting and stock movements

- Status: Accepted
- Date: 2026-11-08
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

Catalog owns identity, commercial measurement and pricing. Inventory accounting
is independent of those declarations. Keeping stock in Catalog would reduce
module interfaces but mix operational responsibilities; a separate Inventory app
is the approved implementation basis. This does not complete a retail system.

The maintainer approved bounded local implementation. Formal ADR acceptance,
publication, merge, non-test migrations and operational use remain separate gates.

## Decision

Inventory alone writes StockPosition and StockMovement. A protected, unique
Variant reference is declared by string, without importing Catalog models or
dereferencing its ORM relations. Catalog provides only variant_exists through its
read-only references interface, after the consuming module has authorized. This
integration interface does not authenticate users or expose business HTTP routes.
Existing Catalog application signatures and behavior remain unchanged.

Inventory depends on Catalog.references, Identity and Audit's append interface.
Audit depends only on Identity attribution, never on Inventory models. These are
application boundaries on the shared default connection, not new database roles.

### Accounting and commands

The explicitly supplied InventoryUnit.EACH is the only unit. Never infer it from
measurement, price, names or SKU. One count denotes one declared stock item of the
Variant; it is not a conversion or a sales quantity policy. Unit never changes
through this increment. On-hand is neither reserved nor available nor sellable.

receive_stock requires a trusted principal, Variant UUID, explicit EACH, positive
integer quantity and mandatory expected_version. None expects no position; the
first receipt creates version 1 and a movement from zero. Later receipts require
the exact version. adjust_stock requires an initialized position, target integer
balance, trimmed nonblank reason (technical limit 255) and exact expected version.
Missing Variants raise InventoryVariantNotFoundError, absent stock during an
adjustment raises StockNotInitializedError, stale expectations raise
StockConflictError, and invalid inputs raise InventoryValidationError.

Counts and versions reject booleans, floats, Decimal and text. Technical integer
range is zero through 2^63-1; versions are positive. Reject quantity, delta or
version overflow before writing. Stale checks precede no-op detection. A matching
adjustment to the current balance returns the existing immutable snapshot without
writes, timestamp changes or attribution. There is no request-level replay or
automatic business retry, including after an already committed initial receipt.

get_stock returns a frozen snapshot or None for an existing uninitialized Variant.
list_stock_movements returns evaluated frozen snapshots in descending sequence
order, or an empty tuple for an existing uninitialized Variant. Missing Variant
is always an error. Zero after an adjustment retains position and history.

### Persistence, concurrency and attribution

StockPosition is a transactional balance projection; StockMovement is its
explanatory history. Each effective mutation advances position.version by one
and inserts the matching sequence. Controlled writes preserve on_hand equal to
the movement chain's final balance and sum of deltas. Sequence, not timestamps,
orders history. All business operations authorize before validation/existence.

One atomic default transaction owns balance, movement and required Audit. Existing
positions are locked with select_for_update before expectation comparison.
Concurrent first receipts race on the named immediate Variant uniqueness
constraint inside a savepoint: only that violation becomes StockConflictError.
There is no lookup-and-replay loop or retry of a business mutation. Other database
failures propagate. Outer rollback and Audit failure before or after insertion
revert all three records, including initial position creation.

Audit owns InventoryMovementAuditEntry and record_inventory_movement. It requires
an active default transaction and typed actor/action/references/counts; it neither
authenticates nor commits independently. Actions are inventory.stock_received and
inventory.stock_adjusted; result is success. The row stores actor ID, Variant,
position/movement UUIDs, sequence, unit and before/delta/after quantities. The
reason remains in the referenced movement, without duplicated free text.

PostgreSQL enforces required fields, protected references, one position per
Variant, positive versions/sequences, nonnegative quantities, nonzero bounded
deltas, EACH codes, before/delta/after arithmetic and first-receipt shape. Receipt
reason is NULL; adjustment reason is non-NULL and nonempty after SQL space trim.
Application strip additionally rejects other whitespace-only reasons. Checks use
explicit NULL guards and numeric-cast arithmetic to avoid signed bigint overflow.
Unique position/sequence and unique Audit movement ID prevent duplicate records.

Cross-row sequence continuity, unit stability, projection/history correspondence,
exactly-one matching attribution and append-only behavior are application
guarantees. Shared privileged SQL can bypass them; no trigger, cryptographic
immutability or cross-table CHECK is claimed. Native SQL casts do not replace
strict input-type validation. Missing schema fails; there is no unaudited fallback.

### Permissions and trust

| Capability | Permission |
| --- | --- |
| inventory.receive_stock | inventory.receive_stock |
| inventory.adjust_stock | inventory.adjust_stock |
| inventory.read_stock | inventory.view_stockposition |
| inventory.read_stock_movements | inventory.view_stockmovement |

Assign none automatically. Older permissions and native model add/change do not
replace the write capabilities. ADR-013 trusted-principal/fresh-user semantics
remain intact. User/is_authenticated alone does not prove login; IDs, arbitrary
objects and ActorContext are not credentials. No staff shortcut is introduced.
Active superusers receive recognized capabilities only; inactive users are denied.

## Scope, rollout and recovery

Preserve all identity, activation, price revisions, measurement and prior Audit
contracts. Stock can be prepared for inactive, uncategorized, unpriced Variants
without measurement declarations. That does not activate or publish anything.
Exclude reservations, purchasing, suppliers, lots, expiry, locations, weight,
transfers, valuation, sales, Orders, POS, payments, UI, HTTP, Admin and deployment.

Inventory 0001 and Audit 0005 are additive with no backfill or fabricated history.
Previous migrations and records remain untouched. Only the native isolated test
runner may create/migrate test_mira, after destination/separation/no-mirror checks.
Keepdb and normal runner cleanup do not authorize manual resets, flush, database
recreation, history changes or volume deletion. Stop on unexpected test state.
Development mira is untouched. Merge/acceptance does not authorize non-test schema
application; actual use needs an independently approved preservation/recovery gate.

Use the bounded feature branch and reviewed canonical manifest publication flow
from ADR-018. Human review, explicit ADR acceptance, digest-specific publication,
four PR CI jobs, SHA-specific squash approval and fresh main CI remain required.
No automatic publication, merge, deployment or infrastructure expansion.

Recover through a reviewed forward fix or a new authorized adjustment, preserving
history and attribution. Stop consumption on integrity drift. Do not silently
overwrite old movements, rebuild balances, reverse schema or delete records.

## Verification

Preserve baseline tests and exercise strict inputs/bounds, absence versus zero,
independent permissions, initial-race conflicts, row locking, stale no-ops,
overflow, immutable snapshots/history, direct PostgreSQL checks and NULL shapes,
Audit failure/outer rollback, balance/ledger/Audit reconciliation, historical-model
compatibility and unchanged routes. Review generated/installed SQL and migration
drift using the test connection only. This ADR does not claim tests have passed;
record actual results in the consolidated technical review.

## References

- ADR-012 through ADR-018.
