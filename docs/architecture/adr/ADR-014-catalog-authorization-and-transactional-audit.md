# ADR-014: Catalog authorization and transactional audit

- Status: Accepted
- Date: 2026-11-07
- Owners: Repository maintainer
- Related issues: None assigned

## Context

Catalog has internal product and variant identity use cases. Identity provides an
authorization contract, but catalog mutations need both mandatory authorization
and persistent attribution before a future administrative adapter can use them.
This increment adds those controls without exposing business HTTP endpoints or UI.
The repository maintainer has explicitly accepted this ADR and its implementation.

## Decision drivers

- Deny access before business validation, existence checks, or writes.
- Reuse the accepted user model, capabilities, and permission semantics.
- Commit business changes and required audit entries in one PostgreSQL transaction.
- Keep table ownership and module dependencies explicit.
- Preserve development data and minimize sensitive information in audit records.

## Considered options

### Increment scope

1. Internal authorized operations and minimal persistent audit: selected scope.
2. An administrative API with session authentication and audit: deferred because
   it adds HTTP authentication, CSRF, session, validation, and abuse controls.
3. A full administrative panel: deferred because it also requires frontend and
   additional product and operational decisions.

### Audit ownership

1. Catalog-owned history is smaller locally, but spreads audit ownership across
   domains and would need a later consolidation decision.
2. A minimal Audit app gives explicit ownership and an internal append interface:
   the selected approach, bounded to two catalog mutations.
3. ORM signals or ordinary logs alone do not reliably express the authenticated
   actor, authorized business operation, and required transactional attribution.

## Decision

### Catalog and Identity contracts

Keep the three existing Catalog application functions and their return types.
Require a keyword-only `principal` with no default. Each function chooses a fixed
capability from ADR-013; callers cannot substitute an actor or weaker capability.

| Catalog operation | Capability |
|---|---|
| `create_product_with_initial_variant` | `CREATE_PRODUCT_WITH_INITIAL_VARIANT` |
| `add_variant_to_product` | `ADD_VARIANT_TO_PRODUCT` |
| `get_product` | `READ_PRODUCT` |

Call `identity.application.require_capability()` before business validation or
business queries. Preserve normalizing rules, database constraints, protected
relations, and business errors. Reads require authorization but create no mutation
audit entry. No equivalent unguarded public entry point is retained.

The principal must come from Django `authenticate()` or a future adapter that
ensures authentication. A User instance and `is_authenticated` do not independently
prove a login. IDs, dictionaries, arbitrary objects, and ActorContext are not
credentials. The trusted caller remains responsible for principal provenance.
Identity reloads users to observe revocation, deactivation, and deletion between
calls. This does not guarantee atomic revocation of an operation already running.

Retain native direct/group permissions, deny-by-default behavior, no automatic
business privileges from `is_staff`, and the accepted active-superuser semantics
for recognized capabilities. Identity and its permission mappings are unchanged.

### Audit model and contract

Audit owns `CatalogAuditEntry`: UUID `id`, textual `actor_id` (128), `action` (64),
UUID `product_id`, UUID `variant_id`, timezone-aware `occurred_at`, and `result`
(16). The lengths are initial technical limits. Database constraints allow only:

- `catalog.product_created_with_initial_variant`
- `catalog.variant_added_to_product`

The only result is `success`. There are no cross-module foreign keys, so account
deletion does not cascade into historical entries. No backfill attributes old
operations to fictional actors.

The internal contract is:

```python
record_catalog_success(
    *, actor: ActorContext, action: CatalogAuditAction,
    product_id: UUID, variant_id: UUID,
) -> UUID
```

The action is a closed enum. The contract requires an active `default` transaction,
inserts through Audit's own model, and returns the entry ID. It neither opens a
separate transaction nor commits independently. It validates input shape but does
not authenticate or authorize the actor: ActorContext is attribution, not proof
of authority. There are no public audit update, deletion, or query operations.

### Transaction boundary and outcomes

Each Catalog mutation owns one atomic block on `default`: authorization, business
validation, product/variant writes, and Audit insertion. Both use the same database
and transaction. Audit does not read or write Catalog's tables, and Catalog does
not access Audit's model directly.

If Catalog fails, no false success entry survives. If Audit fails before or after
inserting, all changes from the operation roll back. An outer caller transaction
can still roll back both effects after the function returns; return does not
promise final commit. Exceptions propagate rather than become successful results.
Required audit insertion must not use `on_commit()`, signals, or background work.

Denied attempts and failed operations create no persistent entries in this scope.
Access-attempt logging is a separate future capability. No event sourcing, outbox,
message broker, or generic audit framework is introduced.

### Security and actual limits

Store only actor/action/entity references, result, and insertion time. Do not store
names, SKUs, barcodes, emails, usernames, credentials, payloads, or permission
snapshots. Actor IDs can still be linked to individuals; real use requires separate
decisions on retention and access to audit history. Before/after snapshots and
auditing future edits require a later design.

Append-only is an application-interface policy. The shared PostgreSQL connection
and privileged SQL can still modify the table; there is no separate database role,
tamper-proof storage, or cryptographic integrity guarantee. Static import tests
detect common explicit ownership violations, not malicious Python, dynamic access,
or direct SQL. Returned ORM objects do not authorize out-of-contract writes.

### Migration and operational gates

The initial Audit migration is additive, without changes to Catalog or Identity.
Only the authorized Django test runner may apply it to the isolated test database.
Verify test settings, PostgreSQL, destination separation, and no mirror before
testing; assert the effective database within the suite. Keepdb may create and
migrate that database, and normal test isolation may clean test data. None of this
authorizes manual resets, recreation, migration-history edits, or volume deletion.

Merging code does not authorize migrating the development database. That database
may remain without Audit after merge. Any deployment or real use of these operations
must first verify the schema is applied. Missing schema must fail the operation,
not enable a path without audit. Migrating any non-test database requires a separate
authorization with preservation, validation, and recovery strategy.

### Delivery and recovery

Develop on the short-lived `feat/phase-1.8-catalog-admin-audit` branch from the
approved, synchronized main commit. Present the full diff for human review before
staging, committing, publishing, or opening a PR. Those steps need separate
authorization. Validate the four existing CI jobs on the PR, obtain explicit human
merge approval, and use squash merge if supported. Verify the new main SHA and
post-merge CI separately. Do not force-push or resolve divergence automatically.

General contribution-policy updates belong in a separate documentation PR. Branch
protection and rulesets require separate authorization; this increment changes none.

Recovery favors disabling consumption or a reviewed forward-fix while preserving
audit records. A code rollback must not reopen unguarded administrative writes.
Schema reversal or dropping audit history requires separate approval and data
preservation; merge itself is not authorization for schema changes.

## Consequences

### Positive

- The established Catalog entry points now require authorization.
- Successful mutations have attribution committed with their business changes.
- One small Audit app owns its table and append contract without extra dependencies.

### Negative / trade-offs

- Internal callers must supply a trusted principal; ORM access is not sandboxed.
- Audit failures intentionally prevent Catalog writes.
- There is no persistent audit of reads, denied attempts, or failed operations.
- This is not a complete commercial catalog or an Internet-ready admin surface.

### Risks and mitigations

- Bypass and fabricated principals: mandatory public entry-point checks, documented
  trust boundary, and static dependency tests, with their limitations explicit.
- Partial writes: shared atomic transaction and real PostgreSQL rollback tests.
- Privileged audit alteration: limited append interface without exaggerated claims.
- Unapplied schema: separate migration gate; no unaudited fallback or auto-migration.
- Divergent branches or unavailable squash: stop for review, without configuration
  changes, destructive recovery, or automatic integration.

## Validation

Preserve all existing test scenarios. Test direct/group access, incomplete grants,
invalid/inactive/deleted principals, staff and superuser semantics, revocation,
mandatory principal arguments, and denial before business information is queried.
Verify exact audit attribution, no sensitive fields, historical actor preservation,
and PostgreSQL action/result constraints. Exercise committed success, Audit failure
before/after insertion, Catalog failures, duplicate identifiers, and outer rollback.
Retain health-only HTTP exposure and the established locked-install and quality gates.

The repository maintainer has completed technical review and accepted this decision.
Merge, deployment, and migration of non-test databases require separate approvals.

## References

- [ADR-011: Stack](ADR-011-technology-stack-selection.md)
- [ADR-012: Module boundaries](ADR-012-modular-monolith-boundaries.md)
- [ADR-013: Identity and access](ADR-013-minimal-identity-and-access.md)
- [Django transactions](https://docs.djangoproject.com/en/6.1/topics/db/transactions/)
- [Django test database](https://docs.djangoproject.com/en/6.1/topics/testing/overview/#the-test-database)
