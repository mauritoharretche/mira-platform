# ADR-017: Catalog commercial units and measurement specification

- Status: Accepted
- Date: 2026-11-07
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context

Catalog owns Product, Variant, categories and administrative activation. ADR-016
prices an individual Variant using `PER_VARIANT` and immutable price revisions.
Identity authorizes trusted principals; Audit owns persistent attribution.

Variant identity, fixed-package net content, commercial sale unit, measured
transaction quantity, price basis and future inventory accounting unit are
different concepts. None may be inferred from labels, SKUs or another concept.
An existing price must never silently become a price per gram or kilogram.

This increment prepares internal measurement declarations without implementing
a retail engine. P-01 remains incomplete.

## Alternatives

| Alternative | Trade-off | Proposed disposition |
| --- | --- | --- |
| Fields directly on Variant | Simple joins, but changes the core identity schema and mixes unknown declarations into every record | Not selected |
| Optional dedicated Variant specification | Explicit absence, independent metadata and versioning; one additional join | Selected |
| Configurable unit registry and conversion graph | Supports broader policies, but introduces unapproved units, conversion and lifecycle rules | Deferred |
| Defer all persistence | Avoids schema change, but cannot validate authorized, attributable declaration changes | Not selected |

## Proposed decision

### Ownership and vocabulary

Catalog exclusively owns application-code writes to `VariantMeasurementSpec`.
It has one protected Variant reference, UUID identity, positive integer version,
creation/update timestamps and nullable measurement declarations. The shared
database connection remains unchanged; this is not PostgreSQL role isolation.

`MeasurementUnit` is a closed enumeration: `EACH`, `G`, `KG`. A nullable
`sale_unit` declares a commercial unit independently from net content. The
optional net-content pair contains a positive Decimal amount and a mass unit,
`G` or `KG`. Both members must be present or both absent. No `EACH` default,
legacy inference, mandatory specification or fabricated backfill is introduced.

These fictional examples describe data shapes rather than sales policies:

- A fixed package may declare `EACH` and net content of 250 `G`.
- A bulk presentation may declare `G` or `KG` with unknown net content.
- A unit presentation may declare `EACH` with unknown net content.

Partial knowledge is valid. Sale unit may remain unknown when net content is
known. Changing 250 `G` to 0.250 `KG` is an explicit declaration change, never
an automatic conversion or a reason to rewrite a historical price.

### Precision and PostgreSQL integrity

Net content uses `NUMERIC(18,6)` with a technical maximum of
`999999999999.999999` and a strictly positive minimum representable value of
`0.000001`. These are representational limits, not regulatory or commercial
policies. Application contracts accept finite `Decimal` values only, reject
significant rounding, and normalize insignificant trailing zeroes under an
explicit local Decimal context independent of ambient precision and traps.

PostgreSQL enforces the Variant FK and uniqueness, positive version, closed
case-sensitive unit codes using column collation `C`, valid amount range and
explicit NULL-safe pairing. An existing row with all declarations NULL is valid.
Scaled NUMERIC may round bypassed overprecision during conversion: rejection of
such input before conversion belongs to the application contract. Neither the
column nor its CHECK should be described as independently rejecting all rounding.

### Internal contracts and version semantics

`set_variant_measurement_spec` requires a trusted principal, Variant UUID, all
three replacement fields and an explicit expected version. `None` clears a
field; it never means "leave unchanged". Units use the closed enumeration.
`get_variant_measurement_spec` returns the current immutable snapshot or `None`.
The snapshot includes specification/Variant IDs, version, all declarations and
both technical timestamps. No writable model or lazy queryset is exposed.

The setter authorizes, validates, locks the Variant with `select_for_update`,
then compares the persisted version to the expectation before no-op detection:

- No row: expectation must be `None`. An all-unknown request returns `None`
  without creating a row or successful-mutation audit.
- First actual declaration: create version 1 and its attribution atomically.
- Existing row: a matching expectation and identical normalized fields return
  the existing snapshot without writes, timestamp changes or audit.
- Actual replacement: retain identity/creation time and advance by one.
- Explicit full clearing: retain the row and advance its version; retrieval
  returns that cleared snapshot, distinct from a missing specification.
- Any stale expectation conflicts, even if it submits the current state or
  repeats a previously committed change. There is no request-level idempotency.

Variant locking serializes competing initial assignments and updates, and
composes with the existing Pricing writer's lock without modifying its contract.
Readers see committed declarations. No timestamp-as-of or measurement-history
retrieval contract is introduced. Version order, rather than clock order, governs
optimistic concurrency. An enclosing transaction can roll back a returned result.

### Authorization

ADR-013 continues to define the trusted-principal boundary. A User object alone
does not prove completed authentication. Fresh persisted-user evaluation remains
deny by default, with no privileges implied by `is_staff` or earlier capabilities.
Active superusers retain only recognized capabilities; inactive users are denied.

| Capability | Permission |
| --- | --- |
| `catalog.set_variant_measurement_spec` | Custom `catalog.set_variant_measurement_spec` |
| `catalog.read_variant_measurement_spec` | `catalog.view_variantmeasurementspec` |

Assign neither automatically. Native model add/change permissions, Catalog and
Pricing permissions cannot substitute for the new write capability. Authorize
before business validation, existence checks and authorized no-ops.

### Transactional Audit

Audit owns a new `CatalogMeasurementAuditEntry` and a single internal contract,
`record_variant_measurement_spec_changed`. Existing Audit models, actions and
contracts remain unchanged. The new action is closed:
`catalog.variant_measurement_spec_changed`, with result restricted to `success`.

The entry stores UUID identity, actor ID, action, Variant/specification UUIDs,
previous/new versions, previous/new sale unit, previous/new net-content amount
and unit, occurrence time and result. It contains no cross-module ForeignKeys,
arbitrary payloads or imports from Catalog.

The contract requires an active `default` transaction, validates typed before/
after data and inserts using the same connection. It does not authenticate actors,
commit independently, schedule callbacks, or expose updates/deletion/history.

PostgreSQL checks explicitly distinguish NULL from known values:

- Initial version: previous version and all previous declarations are NULL,
  and new version is 1.
- Replacement: previous version is non-null and positive, and new version is
  previous + 1.
- Each before/after net pair is both absent or both valid and present.
- At least one declaration changes, using NULL-safe disjunctions guarded by
  `IS NULL` / `IS NOT NULL`. Version changes alone do not qualify.
- `(spec_id, new_version)` is unique.

The controlled path guarantees exactly one matching Audit entry per mutation;
the unique constraint alone guarantees at most one. Cross-table truth, version
continuity and append-only Audit are not tamper-proof against privileged SQL.
Failure before or after attribution insertion, or outer rollback, must revert
both modules. Reads, rejections, stale requests and no-ops create no success row.

### Compatibility and boundaries

Do not modify Product/Variant identities, SKUs, barcodes, Category, activation,
Pricing contracts, immutable price revisions or earlier Audit records.
Administrative activation, priced status, publication, stock availability and
sellability remain independent. A measured or priced Variant is not thereby
sellable; a Product need not have measurement metadata to be active or priced.

`PER_VARIANT` retains its original meaning. Historical prices and future order
snapshots must not acquire new interpretations from current measurement metadata.
Weight pricing and its snapshots require a separately approved Pricing extension.

Exclude conversions, transaction quantities/increments/tolerances, inventory
units or movements, scale integration, retail calculations, prices per mass,
taxes, promotions, Orders, POS, checkout, payments, delivery, UI, HTTP endpoints,
Admin exposure, customer data and production deployment.

### Migration, verification and approval gates

Catalog and Audit receive additive `0004` migrations after their respective
`0003` migrations. Existing tables, historical migrations and records remain
unchanged; existing Variants have no measurement row. No schema rewinds or
fabricated measurement history are permitted.

Only the approved native Django runner may apply these migrations to isolated
`test_mira` after settings/destination checks. Test isolation and cleanup are
normal runner behavior, not permission for manual resets. Generation, merge or
ADR acceptance does not authorize migration of development database `mira`.
Non-test migration requires a separate preservation and recovery gate, and any
actual use must verify that the required schema is installed.

Verify PostgreSQL constraints directly, malformed NULL combinations, permission
denial, stale requests, explicit clearing, rollback, concurrent writes, coexistence
with Pricing, old-model compatibility and all baseline tests. Inspect generated
and installed SQL, and preserve the locked toolchain and established quality gates.

Local implementation approval does not accept this ADR. Human technical review,
ADR acceptance, staging/commit/push/PR publication, merge, non-test migration,
deployment and operational use retain their independent approval gates. Follow a
short-lived feature branch, reviewed diff, approved publication, four successful
PR CI jobs, explicit merge approval and post-merge verification.

Recovery favors stopping new-contract consumption and a reviewed forward fix.
Correct declarations through a new authorized version and Audit, not by rewriting
attribution. Schema reversal or record deletion requires separate authorization.

## Consequences and deferred policies

The optional specification keeps identity stable and supports unknown metadata
without inferring trade rules. Explicit versioning and Variant locking cost one
row lock and may serialize otherwise independent Pricing changes; no performance
claim is made without measurement. Typed Audit duplicates some integrity checks
across modules deliberately, without coupling their model ownership.

Additional dimensions/units, conversion rules, weighing precision, transaction
increments, inventory accounting, price basis, fiscal/labeling interpretation and
historical order snapshots remain future human decisions. This ADR provides no
regulatory determination or authorization for real commercial use.

## References

- ADR-012: Modular monolith boundaries.
- ADR-013: Internal Identity and Access contract.
- ADR-014: Authorized Catalog operations and transactional Audit.
- ADR-015: Catalog categories and administrative activation.
- ADR-016: Catalog pricing foundation.
- [PostgreSQL 18 numeric types](https://www.postgresql.org/docs/18/datatype-numeric.html).
- [PostgreSQL 18 constraints](https://www.postgresql.org/docs/18/ddl-constraints.html).
- [Python Decimal contexts](https://docs.python.org/3.14/library/decimal.html).
