# ADR-013: Minimal internal identity and access

- Status: Accepted
- Date: 2026-11-06
- Owners: Repository maintainer
- Related issues: None assigned

## Context

Internal catalog use cases exist, but administrative adapters need a shared
authorization contract before they can expose business operations. This increment
introduces that contract without exposing login, user management, or business
HTTP endpoints. The repository maintainer has explicitly accepted this decision.

## Decision drivers

- Deny access by default and keep authentication separate from authorization.
- Reuse the approved Django stack, existing user schema, and native permissions.
- Preserve module ownership and minimize personal data and public attack surface.
- Observe account and permission changes between authorization calls.

## Considered options

1. Identity alone: too little to verify authorization for the next increment.
2. Identity plus an internal authorization contract: the proposed scope.
3. Identity, roles, persistent audit, and administrative endpoints together:
   exceeds the bounded increment.

For the user model, both native `auth.User` and a minimal `AbstractUser` subclass
support Django authentication, permissions, and a future Admin integration. A custom
model allows direct customization but introduces schema and migration ownership.
Changing `AUTH_USER_MODEL` after tables exist can require manual schema, data, and
dependency changes. A swappable user must be created in its owning app's first
migration. Existing databases must not be presumed empty or reset for convenience.
No current requirement justifies that transition. Either model would still need
an explicit design for future customer profiles and staff/customer access.

## Decision

Retain `auth.User`, accessed through `get_user_model()` inside Identity. Use the
existing `ModelBackend`, password hashing, Permissions, and Groups. Do not create
a user model, migrations, custom backend, roles, groups, or permission assignments.
The Identity app owns the application interface, not replacement Django tables or
separate PostgreSQL roles; the existing shared connection remains in use.

An operator is an internally provisioned account, not a new employee model.
This increment provisions only fictional accounts inside tests. `username` is the
native login identifier; `is_active` controls account availability. `is_staff`
does not grant business capabilities.

### Authorization contract and trust boundary

`require_capability(*, principal: object, capability: Capability) -> ActorContext`
is internal authorization, not independent authentication. Its caller must supply
a principal returned by Django `authenticate()`, or by a future HTTP adapter that
verifies authentication using Django's middleware-provided `request.user`.

`User.is_authenticated` is true for a User instance and does not prove a prior
login. Type and persistence checks cannot cryptographically establish provenance
or distinguish a valid User fabricated inside trusted application code. Callers
must never construct principals from client-supplied IDs or dictionaries. A future
HTTP adapter is responsible for enforcing authentication before invoking Identity.

Reject missing, anonymous, incompatible, and unsaved principals. Reload a persisted
user by PK, reject nonexistent or inactive users, require a `Capability` enum member,
and check every mapped permission on the fresh instance. Strings are not implicitly
converted to capabilities. Unknown capabilities are denied even for superusers.

| Capability | Required Django permissions |
|---|---|
| `catalog.read_product` | `catalog.view_product`, `catalog.view_variant` |
| `catalog.create_product_with_initial_variant` | `catalog.add_product`, `catalog.add_variant` |
| `catalog.add_variant_to_product` | `catalog.add_variant` |

Ordinary active users require every mapped permission, granted directly or through
Groups. Active superusers retain Django's native permission semantics for known
capabilities. Inactive users, including superusers, are denied. Authentication
alone and `is_staff` alone grant no capability. Database failures propagate rather
than authorize access. Authentication and access errors use generic messages.

### Actor, module boundaries, and deferred audit

Return only immutable `ActorContext(actor_id: str)`. It contains no username,
email, password, or permission snapshot. It is neither a credential nor a persistent
audit record. Consumers use the Identity interface instead of accessing its tables.

Catalog remains unchanged. Its internal use cases are not retroactively protected
by this increment. A later approved adapter must authorize before invoking them.
Administrative writes additionally require approved persistent actor attribution
and auditing at the appropriate transaction boundary.

### Database safety and HTTP prerequisites

There are no new models, migrations, or data backfills. Development data remains
protected. Tests use PostgreSQL and the explicitly isolated test database. Django
`test --keepdb` preserves that database, may create it if absent, and may apply
migrations. Normal TestCase rollback and TransactionTestCase cleanup are confined
to the test database; they do not authorize manual resets or recreation. Unexpected
state requiring manual intervention must be reported for human approval.

Before any real HTTP authentication surface, separately approve password policy
and explicit validation, CSRF, secure cookies, session lifetime and invalidation,
HTTPS, abuse controls, safe errors, and audit. Django sessions are the candidate
for a future internal panel; no new sessions, tokens, or HTTP adapters are added now.

## Consequences

### Positive

- Small internal contract with native authentication and permission behavior.
- No schema transition, extra dependency, or administrative HTTP surface.
- Stable actor identity without disclosing unnecessary personal information.

### Negative / trade-offs

- No object-level permissions, customer access, user management, or persistent audit.
- Retaining the default user model can increase the cost of later customization.
- Superusers retain broad authority; real provisioning needs separate approval.

### Risks and mitigations

- Principal spoofing: enforce type/persistence checks and document the mandatory
  trusted caller boundary; do not claim a User instance proves authentication.
- Stale permissions: reload for every call. This detects changes between calls,
  not atomic revocation of an operation already in progress.
- Accidental exposure: retain only the existing health routes and test that surface.
- Unsafe recovery: stop on unexpected test database state; do not reset data.
- Rollback: revert the code increment through human review, without schema rollback
  or deleting accounts, permissions, or development data.

## Validation

Test native authentication, invalid and spoofed principals, direct and group grants,
all required permissions, capability separation, staff and superuser behavior,
revocation/deactivation/deletion, fresh permission evaluation, immutable actor
identity, generic errors, and fail-closed database failures. Verify the actual
PostgreSQL test database, absence of Identity models/migrations, and unchanged HTTP
surface. Retain the existing catalog and health tests and engineering quality gates.

The repository maintainer has reviewed and approved this ADR and its implementation.

## References

- [Django authentication](https://docs.djangoproject.com/en/6.1/topics/auth/default/)
- [User model substitution and migrations](https://docs.djangoproject.com/en/6.1/topics/auth/customizing/#changing-to-a-custom-user-model-mid-project)
- [Test database lifecycle](https://docs.djangoproject.com/en/6.1/topics/testing/overview/#the-test-database)
- [ADR-011: Technology stack](ADR-011-technology-stack-selection.md)
- [ADR-012: Module and API boundaries](ADR-012-modular-monolith-boundaries.md)
