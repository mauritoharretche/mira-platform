# ADR-020: Same-origin operator workflow and native sessions

- Status: Accepted
- Date: 2026-11-08
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context

Catalog, Inventory, Identity and Audit have accepted internal contracts. The first
browser slice exposes only product/initial-Variant creation, retrieval by UUID,
initial EACH receipt, stock and movement reads, and session lifecycle. This is a
fictional local demonstration, not authorization for operational exposure.

## Alternatives and decision

Use native Django database sessions, ModelBackend, AuthenticationForm and an
adapted LoginView with CSRF. Explicit Next.js rewrites provide a same-origin
browser surface. DRF SessionAuthentication and IsAuthenticated guard the business
adapters. A separate BFF session, JWT, OAuth or authentication service would add
unnecessary authority and lifecycle boundaries. A wildcard proxy would expose
future routes accidentally; only nine operator paths are rewritten.

The backend obtains its principal exclusively from middleware-authenticated
request.user. Clients cannot supply actor IDs, credentials through arbitrary
identity headers, or permissions. Every adapter authorizes before business
validation; existing application services perform their own fresh-user checks.
Capabilities and all established domain signatures remain unchanged. A User
object alone still does not prove authentication. Staff is not a business grant.

Catalog HTTP uses only Catalog's existing operations and explicitly serializes
the returned identity. Inventory HTTP uses its four existing contracts, exposing
only initial receipt and reads. No adapter writes a business model directly.
Shared transport safety lives in system.http without domain imports; only HTTP
adapters may depend on it. There are no HTTP write routes for any deferred domain.

## Session, CSRF and trust

Login establishes a fixed timezone-aware deadline eight hours after successful
authentication. Both native expiry and a server-side deadline enforce it. Later
session changes are clamped to the original deadline; activity does not renew it.
Fresh successful credential authentication can establish a new deadline. A native
session without the operator deadline cannot become an operator principal.
Logout uses POST and native invalidation. Password changes retain Django's native
session invalidation. Expired native rows may remain pending normal maintenance;
they are not authenticated or extended.

Cookies are host-only, HttpOnly, Secure, SameSite=Lax and scoped to
/api/v1/operator/. Only the loopback HTTP development/test settings relax Secure.
The frontend obtains a masked CSRF token through a no-store endpoint, keeps it in
memory, and refreshes it from successful login after native secret rotation.
Anonymous login and all unsafe session requests enforce CSRF. Native login accepts
only username/password form fields and returns JSON, never a client redirect.
Known frontend origins are explicit. CORS, forwarded identity and trusted proxy
headers are not enabled. Host is validated on safe and unsafe operator requests.

The operator surface is disabled by default. Runtime checks require loopback and
a loopback public origin. Both servers must bind to loopback; these application
checks are not a substitute for network isolation or deployment review. Next
receives no backend environment or database secrets. The configured API origin is
server-only and restricted to loopback. Browser/API responses do not cache private
data. Error responses do not expose exception messages, SQL or credentials.

Before SessionMiddleware, the outer operator middleware also requires the
configured and effective default database to be PostgreSQL test_mira, with
TEST.NAME=test_mira, no mirror and no database/service destination override.
Any existing connection must report the same database in its cached connection
metadata. The guard opens no connection and executes no SQL. Disabled requests
remain 404; an enabled surface with an unsafe destination returns generic 503
before sessions, authentication, CSRF dispatch or business queries. Merely
selecting config.settings.test does not satisfy this guard: the native runner
must first select the isolated destination. Its direct current_database()
verification remains mandatory and is not replaced by configuration checks.

## Login abuse budget

Native Django does not throttle authentication. Identity gains one owned model,
LoginAttemptWindow, and an additive first migration. It stores only a HMAC key,
bounded counter and expiry, with no model permissions or user replacement.
This explicitly extends ADR-013's original no-owned-model increment while
preserving auth.User and every authorization contract.

Reserve five attempts per NFKC-normalized identifier in fifteen minutes and
thirty globally per minute, before checking credentials. Native form whitespace
normalization is applied first. Existing and absent usernames consume identical
budgets. The global row serializes reservations across processes, including
initial creation races; rows are locked in a stable global-first order. Limits
return generic 429 with Retry-After. A store failure denies login. Successful
authentication does not reset budgets. Only a bounded batch of long-expired
counter rows is cleaned; this is not deletion of business or security history.

Global limits avoid fabricating an IP identity behind the same-origin proxy but
can temporarily deny legitimate operators. They are local-demo defaults and do
not provide volumetric denial-of-service protection. Security logs contain only
generic outcome codes, without attempted usernames, passwords, IPs or cookies.
Business attribution continues through the existing transactional Audit contracts.

## Transport and failure semantics

All bigint quantities, versions, sequences and deltas use canonical decimal
strings on the wire. Narrow serializers parse them strictly to bounded Python
integers, requiring explicit EACH and null expected_version for initial receipt.
Identifiers and labels retain their established normalization and uniqueness.
Unknown fields are rejected; UUID and business validation follow authorization.

Creation and receipt are separate transactions. Failed receipt leaves an existing
uninitialized product. Audit failure rolls back its corresponding business write.
The UI never automatically retries a POST. A lost creation response without an ID
remains uncertain and blocks resubmission. Receipt conflicts/lost responses can
perform authorized read reconciliation, preserving the conflict/uncertain message
instead of fabricating a successful replay.

Movement responses have a fixed fifty-entry cursor page. The internal contract
still evaluates the full history; pagination bounds the response, not query cost.
Separate position/history reads may observe concurrent versions; one bounded
read-only reconciliation precedes an explicit changed-during-read warning.
This slice computes no reserved quantity, availability or sellability.

## Schema, tests and approval boundaries

Only the established native runner creates/migrates test_mira. Before testing,
verify settings, PostgreSQL, destination separation and no mirror; assert the
effective database in tests. LiveServerTestCase owns browser fixtures and backend
lifecycle. A filtered Next process and Chromium consume that isolated server.
Test-only process IPC can revoke fictional permissions or expire fictional
sessions; it creates no HTTP control endpoint. New browser tests use exactly
@playwright/test 1.65.0 as a development dependency and run in the existing backend
CI job. All four required check contexts, action SHAs and tool versions remain.

Schema checks and generated SQL are reviewed only on the test connection. No
startup migration or schema fallback exists. Merge does not migrate development
mira. Real accounts, permissions, non-test migrations, retention, recovery and
operational use retain separate approval gates. MFA remains required before real
administrative exposure; its deferral applies only to this fictional local demo.
Native password validators must be explicitly used when provisioning accounts;
login does not retroactively enforce password complexity on stored credentials.

Implementation approval does not accept this ADR. Technical review, ADR acceptance,
digest-specific automated publication, four successful checks, SHA-specific merge
approval and post-merge verification remain independent. Recovery disables the
surface and favors a reviewed forward fix, preserving domain and Audit history.
No schema reversal, balance rewrite or account deletion is implicitly authorized.

## Validation

Preserve the baseline domain and delivery suites. Test anonymous CSRF, rotation,
cookie scope, absolute expiry under session mutation, logout, generic failures,
fresh permission denial/revocation, independent capabilities, concurrent throttle,
exact bigint transport, duplicate receipts, Audit rollback and actual browser
lost-response outcomes. Verify desktop/mobile keyboard and responsive behavior,
disabled defaults, safe errors, migration drift/SQL, locked installs and dependency
audits. Verify that a separate process using test settings and NAME=mira rejects
all operator routes without a connection or session/authentication/business calls.
Observed results belong in the review evidence, not inferred from this ADR.

## Temporary dependency-risk disposition

The maintainer explicitly accepts GHSA-g3hv-75wg-m249 / CVE-2026-118764 temporarily
only for the isolated local demonstration. It is a moderate, preexisting,
development-only transitive minimatch dependency of ESLint. The production
dependency audit reports zero findings; the full dependency audit remains failing
and must not be described as passing. No advisory suppression, audit weakening or
dependency version change is authorized in this functional increment. This risk
disposition does not permit production deployment or real operator exposure.
Remediation requires a separately reviewed, compatibility-tested dependency update.

## References

- ADR-012 through ADR-019.
- [Django authentication](https://docs.djangoproject.com/en/6.1/topics/auth/default/).
- [Django CSRF](https://docs.djangoproject.com/en/6.1/ref/csrf/).
- [DRF session authentication](https://www.django-rest-framework.org/api-guide/authentication/#sessionauthentication).
- [Next.js rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites).
