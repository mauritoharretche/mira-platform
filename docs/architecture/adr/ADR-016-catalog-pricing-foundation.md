# ADR-016: Catalog pricing foundation

- Status: Accepted
- Date: 2026-11-07
- Human owner: Repository maintainer
- Technical owner: Repository maintainer, explicitly designated
- Related issues: None assigned

## Context

Catalog owns Product, Variant, Category and administrative activation. Identity
authorizes internal operations and Audit records successful changes in the same
transaction. The next bounded increment provides administrative price identity,
current retrieval and historical revisions without exposing a commercial adapter.

The repository maintainer and designated technical owner reviewed the complete
implementation and explicitly accepted this ADR. Publication of the reviewed
increment is separately authorized. Acceptance and publication do not authorize
merge, non-test migrations, deployment or production use.

## Decision drivers

- Keep one price authority and preserve module ownership.
- Use exact monetary values with explicit currency, precision and bounds.
- Preserve history and accurate attribution through failures and concurrency.
- Reject stale edits, including retries of changes that already committed.
- Preserve all existing Catalog, Identity and Audit behavior.
- Keep migrations additive and execution against non-test databases separate.

## Considered options

| Option | Benefits | Trade-offs | Disposition |
|---|---|---|---|
| Catalog owns pricing | Existing Variant ownership and locking; fewer interfaces | Later separation requires a reviewed transition | Selected implementation basis |
| Separate Pricing app | Independent ownership for richer price policies | More module interfaces and migration boundaries | Deferred |
| Product-level price | Fewer records | Requires inheritance and presentation fallback rules | Not selected |
| Price lists and UOM pricing now | Broader commercial behavior | Adds unapproved policies and scope | Deferred |

Separate Pricing ownership can be reconsidered when actual independent workflows,
ownership or release constraints justify it. Table growth alone is insufficient.

## Decision

### Ownership and monetary policy

Catalog owns VariantPriceRevision and all application writes. Audit owns its own
records; Identity owns authorization. These are application boundaries using the
existing default connection, not isolation through new database users or grants.

Each revision prices one Variant/presentation. Product and sibling prices are
never fallbacks. Currency is explicitly ARS. The price basis is the closed value
PER_VARIANT; names, labels and SKUs do not imply kilograms or any conversion.

Amounts are finite Decimal values stored as NUMERIC(14,2), strictly greater than
zero and no greater than 999999999999.99. The maximum is a technical bound, not a
commercial limit. The internal writer rejects floats, booleans, integers, strings,
NaN, infinities and values requiring rounding. Exact removal of trailing zeroes
is permitted. Canonicalization uses an explicit local Decimal context rather than
ambient precision, rounding or traps. No tax, net/gross or collection-rounding
semantics are inferred.

PostgreSQL's scaled NUMERIC conversion can round a direct SQL input before a CHECK
examines it. Exact input-scale rejection belongs to the application contract.
Tests must characterize that database behavior instead of claiming that the type
rejects all overprecision. Bounds and code constraints still apply to stored data.

### Revision identity and current retrieval

VariantPriceRevision has UUID id, a protected Variant FK, positive bigint sequence,
amount, currency (3), price_basis (16) and timezone-aware recorded_at. Code columns
use collation C. PostgreSQL enforces unique (variant, sequence), sequence greater
than zero, required references/fields, amount bounds and permitted codes.

The first sequence is 1. Each actual change appends the next sequence; the highest
visible committed sequence is current. No mutable current-price column duplicates
that authority. Recording timestamps are not commit timestamps or validity
intervals. Sequence orders history even when recording times repeat or move back.
There are no caller-supplied dates, scheduling, backdating or timestamp-as-of reads.

The internal contracts are set_variant_price, get_current_variant_price and
list_variant_price_history. Each requires a keyword-only trusted principal. Write
arguments are Variant UUID, Decimal amount, explicit currency and mandatory
expected_revision_id (UUID or None for initial assignment). Missing variants raise
VariantNotFoundError, invalid input raises CatalogValidationError, and stale
expectations raise PriceConflictError. Existing unpriced variants return None for
current and an empty history. Historical results are evaluated in descending
sequence order. Frozen PriceSnapshot contains revision ID, Variant ID, sequence,
amount, currency, PriceBasis and recording time, without writable ORM state.

### Authorization and state separation

| Capability | Required permissions |
|---|---|
| catalog.set_variant_price | catalog.set_variant_price |
| catalog.read_variant_price | catalog.view_variantpricerevision |
| catalog.read_variant_price_history | catalog.view_variantpricerevision and catalog.view_variant_price_history |

Declare the custom write/history permissions on the revision model; assign none.
Existing Catalog permissions and native revision add/change permissions do not
substitute for the write capability. Preserve ADR-013's fresh-user checks, direct
and group grants, deny-by-default behavior, inactive-user denial, staff without
automatic privilege and active-superuser access only to known capabilities.

Authorization precedes business validation and existence queries. A User instance
or is_authenticated alone does not prove login; the trusted caller must obtain
the principal from authentication. ActorContext is attribution, not a credential.

Pricing inactive or uncategorized products is allowed for administrative setup.
Setting a price never activates a Product. Activation retains its category rule
without a price prerequisite. Absence is not zero. Administrative activation,
priced status, publication, stock availability and sellability are separate; this
increment computes no sellability decision. The existing seven operations remain
unchanged and new variants receive no fabricated price.

### Transactions, stale retries and no-ops

The writer authorizes and validates inside transaction.atomic(using="default"),
locks the existing Variant with select_for_update, reads its latest revision and
compares the mandatory expectation. Locking Variant also serializes concurrent
first assignments. A stale expectation fails before an equal-amount no-op check.
With a matching expectation, equal canonical amounts return the original snapshot
without changing rows, timestamps or audit. Otherwise append a revision and Audit
in the same transaction. Enclosing transactions can still roll back returned data.

A retry of an already committed change with its old expectation raises
PriceConflictError even when its amount matches current. Retrying first assignment
with None after a price exists also conflicts. There is no request-level
idempotency key, stored-result replay, blind retry or last-writer-wins fallback.
Request-level idempotency requires a future separately approved design.

### Compatible typed audit

Keep CatalogAuditEntry, CatalogChangeAuditEntry, their enums and existing append
contracts intact. Add CatalogPriceAuditEntry with UUID id, actor_id (128), fixed
action catalog.variant_price_set (64), Variant UUID, nullable previous revision
UUID/amount, required new revision UUID/amount, currency, basis, occurred_at and
result success (16). Do not add cross-module FKs or arbitrary payloads.

The new record_variant_price_set contract requires an active default transaction,
validates ActorContext and field shape, appends through Audit's own model and
returns its UUID. It does not authenticate the actor or commit independently.
Database checks restrict codes, result and amount bounds. The previous reference
and amount must both be NULL for initial assignment or both non-NULL for a change;
replacement IDs and amounts must differ. Use explicit NULL guards, inspect the
generated SQL and test direct ORM and parameterized SQL insertions.

A unique new_revision_id guarantees at most one such audit row. Correct
correspondence and exactly one row per controlled changing operation are enforced
by the atomic application path and integration tests, not cross-table CHECKs.
Audit failure before or after insertion rolls back the revision. Reads, denials,
stale conflicts and no-ops do not create success audit. Required attribution uses
neither on_commit, signals nor background jobs. Append-only and sequential history
are application policies, not tamper-proof guarantees against privileged SQL.

### Deferred extensions and snapshots

Future withdrawal requires explicit approval of an extension to the revision and
current-price model, retrieval and Audit. Never reinterpret zero as withdrawal,
delete history to create absence or silently change current resolution. There is
no withdrawal placeholder or operation in this increment.

Weight units/conversions, other currencies, lists, channels, intervals, promotions,
discounts, taxes and collection rules remain deferred. Historical PER_VARIANT
revisions must not be silently reinterpreted as prices per kilogram.

A future Orders contract must copy the applied revision ID, amount, currency and
basis rather than refer only to current price. Quantity/UOM, tax and discount
snapshots, quote expiry and confirmation timing require later approval. This
increment adds no orders, storefront, HTTP endpoints, UI, Admin access, checkout,
payments, POS, stock, delivery or customer data. It does not complete P-01.

### Migration, delivery and approval gates

Catalog 0003 adds only its revision table and permissions. Audit 0003 adds only its
price audit table. Each depends on its own app's 0002; prior migrations and records
remain intact. There is no backfill or invented history. Old models and contracts
must continue working on the expanded schema.

Only the authorized native runner may apply migrations in test_mira using
config.settings.test and --keepdb. Confirm PostgreSQL, destination separation and
no mirror before execution; assert current_database within tests. Keepdb may
create the test database if absent and apply migrations; ordinary test isolation
does not authorize manual resets, recreation, flush, history changes or volume
deletion. Unexpected state requiring manual intervention is a stop gate.

Development mira remains protected. Design approval, formal ADR acceptance and
merge do not authorize its migration. Real use requires a separately approved
preservation, schema verification and recovery procedure covering all pending
migrations. Missing schema must fail the operation, never skip audit.

Implement on the approved short-lived feature branch. Present the complete patch
and local validation for human review before explicit staging/commit/push/PR
authorization. ADR acceptance is separate and must be explicit. Require fresh PR
CI for backend, frontend and both Ubuntu x64/macOS ARM64 toolchains, then explicit
human approval for squash merge if supported. Verify main and post-merge CI.
Do not force-push, reset destructively, rebase automatically or merge automatically.
Non-test migrations, deployment and production use each retain separate gates.

## Consequences

### Positive

One internal authority provides exact amounts, immutable return snapshots,
historical revisions, explicit conflicts and atomic attribution without new
dependencies or commercial surface area.

### Negative / trade-offs

Current resolution and full history reads use revision queries. History is
internal only; pagination is required before external exposure or larger-volume
use. SQL can bypass application chronology, immutability and audit pairing.
Timestamp-as-of queries and scheduled/withdrawn prices are deliberately absent.

### Risks and mitigations

Test application scale rejection separately from SQL coercion. Use locks plus
expected revisions against stale edits, failure injection for attribution and
historical comparisons against destructive changes. Do not confuse priced with
sellable or presentation with weight units. Prefer stopping new consumers and a
reviewed forward fix; a correction appends another authorized revision. Code
rollback preserves data and prior authorization; schema reversal or history
deletion requires independent approval. Withdrawal is not a rollback shortcut.

## Validation

Retain all 125 existing tests. Add monetary boundaries/types, ambient Decimal
context, absence, immutable snapshots, history/order, no-op, committed retry,
permissions, SQL constraints, before/after attribution, rollback and concurrent
first/replacement writes. Verify old models on the expanded schema without
rewinding it; this is not a real-data migration rehearsal. Inspect generated and
installed SQL through the test connection. Run locked tools and dependencies,
Django checks, migration drift checks, native tests and unchanged frontend lint,
typecheck, Vitest, build and production dependency audit. Report observed evidence
separately; this ADR itself does not assert validation success.

## References

- ADR-011: Technology stack selection.
- ADR-012: Modular monolith and API boundaries.
- ADR-013: Minimal internal identity and access.
- ADR-014: Catalog authorization and transactional audit.
- ADR-015: Catalog categories and administrative activation.
- [PostgreSQL numeric types](https://www.postgresql.org/docs/18/datatype-numeric.html)
- [Python Decimal](https://docs.python.org/3.14/library/decimal.html)
- [Django row locking](https://docs.djangoproject.com/en/6.1/ref/models/querysets/#select-for-update)
