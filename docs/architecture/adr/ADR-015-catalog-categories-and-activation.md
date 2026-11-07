# ADR-015: Catalog categories and administrative activation

- Status: Accepted
- Date: 2026-11-07
- Owners: Repository maintainer; technical owner to be designated
- Related issues: None assigned

## Context

Catalog owns Product and Variant identity and its three authorized operations.
ADR-014 provides transactional attribution for product and variant creation.
This increment adds flat categories and Product-level administrative activation.
It does not complete the catalog product scope or introduce pricing, sales,
publication, inventory, HTTP endpoints, UI or administrative user management.
The repository maintainer has explicitly accepted this ADR after technical review.

## Decision drivers

- Preserve existing identities, operations, permissions and audit history.
- Deny new operations unless their specific capabilities are granted.
- Require a category for activation without interpreting active as sellable.
- Record accurate before/after states under concurrent writes.
- Enforce change-record shapes in PostgreSQL even when application validation
  is bypassed, accounting explicitly for SQL NULL semantics.
- Keep schema changes additive and database execution separately authorized.

## Considered options

1. Categories and activation: a bounded administrative increment; selected as
   the accepted implementation basis.
2. Categories, activation and pricing together: deferred because currency,
   precision, fiscal treatment, validity and history require separate decisions.
3. Pricing first: deferred; it does not resolve classification or activation.

For auditing, extending the existing record would change an established
contract. A separate typed change record preserves that contract. Generic JSON
payloads and cross-module foreign keys are unnecessary for these three actions.

## Decision

### Catalog ownership and normalization

Catalog remains the only application-code owner and writer of its tables;
this is not isolation through additional database users or grants. Category has
a UUID identity, required display name, internal name_key and technical
timestamps. Both text limits are initially 255 characters, technical rather
than commercial or regulatory limits.

The creation contract strips exterior whitespace, normalizes the display name
to Unicode NFC, then calculates the key using casefold followed by NFC. It
rejects empty or overlong display names and keys without truncation. Interior
spaces and accents are preserved. PostgreSQL uses column collation C and a
unique constraint on name_key, plus nonempty checks on both fields.

The UNIQUE constraint protects the stored key only. It cannot ensure that
arbitrary SQL computed name_key correctly from name. That correspondence is
owned by the Catalog writing contract; no trigger or generated column is added.
There is no category hierarchy, rename, deletion, activation flag or slug.

Product gains a nullable protected category and non-null is_active with both
Python and database defaults of False. Existing and new products start inactive;
defaults do not reconstruct historical states or fabricate deactivation events.
A database CHECK also requires a category whenever is_active is True. Category
removal requires prior deactivation; an active product can be reassigned to a
different existing category. Variants are unchanged. Active does not imply
published, available, priced or sellable; administrative reads include inactive
products. The existing creation contract cannot assign a category or activate.

### Authorization and application contracts

The four new internal keyword-only contracts are create_category,
list_categories, set_product_category and set_product_active. All require a
principal from the trusted boundary described in ADR-013. Authorization occurs
before business validation or existence checks; is_staff confers no capability.
No permissions are automatically assigned.

| Capability | Django permission |
|---|---|
| catalog.create_category | catalog.add_category |
| catalog.list_categories | catalog.view_category |
| catalog.set_product_category | catalog.set_product_category |
| catalog.set_product_active | catalog.set_product_active |

The last two are custom Product permissions. Existing permissions do not grant
these operations. Category listing returns an evaluated list ordered by name_key
and ID; a future HTTP adapter must separately design pagination.

Missing categories raise CategoryNotFoundError, missing products retain
ProductNotFoundError, and invalid business data raises CatalogValidationError.
Activation accepts only actual booleans. A duplicate normalized category is
ultimately rejected by the PostgreSQL unique constraint as IntegrityError.
Authorized requests that do not change state preserve timestamps and create
no modification audit entry.

### Typed audit and transaction boundary

CatalogAuditEntry and record_catalog_success remain unchanged. Audit owns the
new CatalogChangeAuditEntry table and three append-only internal contracts:
record_category_created, record_product_category_changed and
record_product_active_changed. Each requires the caller's active transaction
on default and an ActorContext issued by authorization; attribution is not
independent authentication. Audit does not commit separately.

Each new record contains UUID id and target_id, actor_id (128), action (64),
nullable before/after category UUIDs and activation booleans, occurred_at and
result (16, restricted to success). There are no cross-module foreign keys or
arbitrary payloads. The target denotes Category for category creation and Product
for the other actions.

| Action | Category before/after | Active before/after |
|---|---|---|
| catalog.category_created | Both NULL | Both NULL |
| catalog.product_category_changed | Distinct states, allowing one NULL | Both NULL |
| catalog.product_active_changed | Both NULL | Non-NULL and unequal |

The category-change predicate explicitly permits NULL to UUID, UUID to NULL
and unequal non-NULL UUIDs, rejecting two NULLs and equal IDs. The activation
predicate explicitly requires both booleans non-NULL before comparison. Separate
action/result checks and NOT NULL fields apply as well. No isolated inequality
is trusted to reject a row when SQL could produce UNKNOWN. Generated migration
SQL and installed PostgreSQL constraints must be inspected and directly tested.

Every changing Catalog command authorizes, validates, writes and appends audit
in one transaction.atomic(using="default"). Both Product setters lock the same
row with select_for_update before evaluating current state. Audit failures
before or after insertion and enclosing rollbacks revert both effects.
Required attribution does not use on_commit. Reads, denials, failures and no-ops
do not generate successful modification records. Permission revocation is not
promised to be atomic with an operation already in progress. This application
policy does not make audit tables immutable to privileged SQL.

### Migration and delivery gates

Catalog 0002 creates Category and adds Product fields, permissions and checks.
Audit 0002 creates only the new change table and checks. Previous migrations and
the original audit table are retained. No fabricated classifications or history
are introduced. The database default supports old model inserts omitting the
activation column.

The authorized test runner may apply pending migrations in test_mira using
config.settings.test and --keepdb. This can create the test database if missing;
normal test isolation is not permission for manual resets or recreation.
Unexpected test-database state requiring manual intervention is a stop gate.
Development mira, its data, volume and service configuration remain protected.
Merging code does not authorize migrate against mira, including any earlier
pending Audit migration. Real use requires the complete applicable schema and a
separate migration approval with preservation and recovery planning.

Implementation occurs on a short-lived feature branch. Local validation and the
complete review patch precede human approval for staging, commit and a PR to
main. Publication requires a secret/privacy review. Merge requires explicit
human approval and all four CI jobs; prefer squash merge when available and
verify main and post-merge CI. No force push, destructive reset or automatic
rebase is authorized. Acceptance of this ADR does not authorize merge,
deployment or migration of non-test databases.

## Consequences

### Positive

The increment adds classification and administrative state without introducing
price decisions or changing established identity and attribution contracts.
Database checks and shared row locks protect invariants across concurrent writes.

### Negative / trade-offs

Name/key consistency depends on the writing contract. A listing without
pagination is internal only. Separate audit tables require future reporting to
understand both formats. New code and unapplied development schema can coexist,
but the new operations must not be used until the schema gate is satisfied.

### Risks and mitigations

NULL-safe checks require actual PostgreSQL validation, not only application
tests. Canonical-key expansion must be length-checked. Granular permission tests
guard against accidental privilege reuse. Row-lock integration tests guard
against stale before states. Prefer a reviewed forward fix or disabling new
operation consumers while preserving data; schema reversal and record deletion
require separate approval. Pricing remains deferred: money requires Decimal,
approved currency, scale, bounds, fiscal rules, validity, history and snapshots.

## Validation

Retain the 83 existing tests. Add normalization, permissions, defaults, no-op,
rollback and PostgreSQL concurrency tests. Direct ORM inserts without full_clean
and parameterized SQL must exercise the audit shape, valid transitions and
invalid NULL combinations, identifying the failing constraint. Inspect generated
SQL and installed constraints on test_mira. Historical model inserts on the
expanded schema verify older-code compatibility and defaults without rewinding
the schema; they are not a rehearsal of migrating a real dataset.

Use locked tool/dependency installs, Django checks, migration drift checks and
the native test suite, plus unchanged frontend lint, typecheck, tests, build and
production dependency audit. Record actual execution results separately; this
ADR does not itself assert that validation has passed.

## References

- ADR-011: Engineering stack selection.
- ADR-012: Modular monolith and API boundaries.
- ADR-013: Minimal internal identity and access.
- ADR-014: Catalog authorization and transactional audit.
