# Local operator workflow

## Boundary

Phase 1.13 is a fictional, loopback-only browser demonstration. ADR-020 is Proposed.
It does not authorize production, real operators/data, exposure outside loopback,
or any migration of development database mira. MFA remains a prerequisite for
future real administrative use. Keep both server processes bound to loopback.

The runtime surface is off unless MIRA_OPERATOR_ENABLED=1. Do not enable it against
an existing database without a separate schema/account/preservation gate. The
approved browser harness enables it only while the native test runner owns
test_mira. Missing schema fails closed; no server runs migrate on startup.

Before session middleware, operator requests are also fenced by database routing
metadata. Both the configured and effective default destination must be PostgreSQL
test_mira, TEST.NAME must be test_mira, and mirrors/database/service overrides are
rejected. An already-open connection must have matching cached database identity.
The guard opens no connection to decide safety. An enabled surface pointed at
mira returns generic 503 before sessions, authentication or business queries;
disabled defaults remain 404. Native-runner current_database() verification is
still required. Starting an independent process with config.settings.test leaves
its default NAME unchanged and therefore cannot bypass the fence.

## UX-01 Retail Calm: current workspace entry

The current fictional operator login lands at /operations/catalog. The MIRA
brand returns there. Compact navigation exposes only Catálogo and Nuevo producto;
aria-current identifies the current page, or the Catalog area while reading a
product. /operations remains the creation route; product creation still opens
its existing detail route. Its fields, permissions, CSRF, result uncertainty and
independent stock-receipt transaction are unchanged.

The shared light workspace uses the existing MIRA green, system fonts, native
controls and visible keyboard focus. There are no added dependencies, fonts,
search, aggregate metrics, commercial fields or unavailable destinations. The
UUID lookup remains secondary on the creation page. Logout remains the original
POST flow, including uncertain-logout feedback; demo/fictional-data indicators
remain visible. Client capabilities are presentation hints, never server grants.

The following Phase 1.13 journey/API description is historical to that increment;
current catalog navigation, categories and administrative activation are described
in operator-catalog.md. Known documentation debt is retained explicitly: the
opening ADR-020 Proposed reference above is outdated; Accepted in the Architecture
Decision Log is authoritative. UX-01 does not silently rewrite that historical
reference or authorize real operational exposure.

## Browser journey

Use /login, /operations and /operations/products/{UUID}. Create the product and
initial Variant, retain the returned UUID, explicitly select EACH, then record
the first positive receipt. Product creation and receipt commit independently.
If receipt fails, return to the same UUID; do not create a replacement product.

There is no product search/list, extra-variant editor, later receipt, adjustment,
price/category/activation editor, Admin, sale, reservation or customer flow.
Physical on-hand, activation, publication and sellability are distinct.

## API contract

All operator routes begin /api/v1/operator and have no trailing slash. Next
rewrites only the nine declared paths to the configured loopback backend origin.
Login is POST form-urlencoded username/password. Business writes are JSON.

| Method and suffix | Required capability |
| --- | --- |
| GET /auth/csrf | Public masked CSRF bootstrap |
| POST /auth/login | Native credentials and CSRF, throttled |
| GET /auth/session | Native session status; capabilities are UI hints |
| POST /auth/logout | CSRF, native invalidation |
| POST /catalog/products | catalog.create_product_with_initial_variant |
| GET /catalog/products/{UUID} | catalog.read_product |
| POST /inventory/variants/{UUID}/initial-receipt | inventory.receive_stock |
| GET /inventory/variants/{UUID}/stock | inventory.read_stock |
| GET /inventory/variants/{UUID}/movements | inventory.read_stock_movements |

Product input has product_name (255), variant_label (255), sku (128) and optional
barcode (255). Response includes product ID/name/administrative state and Variant
ID/label/SKU/barcode. The creation permission authorizes that result; no extra read
permission is silently required to commit creation. A later detail read requires
both catalog.view_product and catalog.view_variant.

Initial receipt has quantity as a canonical positive decimal string, unit exactly
EACH and mandatory expected_version null. Counts and versions range up to
9223372036854775807 without JavaScript Number coercion. Stock response is
{"stock": null} for an existing uninitialized Variant; missing Variant is 404.
Movements use descending sequences, fifty entries and next_cursor. Pass that
cursor as before_sequence to retrieve older entries. The internal full-history
evaluation remains a documented volume limit.

No client actor, principal, permission or arbitrary identity header is accepted.
Each domain contract enforces its own fresh-user authorization. Staff is not a
business grant. The full journey needs catalog.add_product, catalog.add_variant,
catalog.view_product, catalog.view_variant, inventory.receive_stock,
inventory.view_stockposition and inventory.view_stockmovement. None are assigned
automatically. Test accounts are synthetic; real provisioning is separate.

## Session and security

Eight hours is an absolute deadline after successful credential login, including
session mutations. Activity does not renew it. Cookies are HttpOnly, host-only,
SameSite=Lax, scoped to /api/v1/operator/ and Secure except loopback HTTP testing
and development. The browser keeps only the masked CSRF token in memory; login
refreshes it after secret rotation. Credentials/session state are not persisted
in browser storage. Logout is POST with CSRF, never a link performing a GET.

Login budgets are five attempts per normalized identifier in fifteen minutes and
thirty globally per minute, including absent users. They use PostgreSQL atomic
HMAC-keyed windows. A global exhausted budget may temporarily block legitimate
operators; these defaults are not approved production security policy. 429 includes
Retry-After. Unavailable throttle/session/Audit storage must not authorize writes.

Responses are no-store and errors contain safe codes, not internal exceptions.
DRF returns 403 authentication_required for missing/expired session; access_denied
means insufficient permission. Login failure is generic 401, invalid input 400,
known uniqueness/stock conflicts 409, database failures 503. The client never
automatically retries a POST. Lost product creation without its UUID remains
uncertain; there is no claim of SKU-based reconciliation. Lost stock receipt can
read its known Variant, but conflict/uncertainty remains explicit even when the
persisted balance is observed. There is no request-level idempotency replay.

## Isolated verification

Verify mise 2026.11.2, then install only the locked project tools and dependencies:

```bash
mise install --locked python node pnpm uv
uv sync --locked --directory apps/api
pnpm install --frozen-lockfile
pnpm --filter @mira/web exec playwright install chromium
```

Browser binaries go to the user test cache. On ephemeral Ubuntu CI only, the
existing backend job uses Playwright's --with-deps Chromium installation. No
global runtime, PostgreSQL service or new infrastructure is installed locally.

From apps/api, after verifying test settings, TEST.NAME=test_mira, separation and
no mirror, execute the authorized runner:

```bash
uv run --env-file ../../.env python manage.py check --settings=config.settings.test
uv run --env-file ../../.env python manage.py test --keepdb --settings=config.settings.test
MIRA_RUN_BROWSER_TESTS=1 uv run --env-file ../../.env python manage.py test system.tests.test_browser_workflow --keepdb --noinput --settings=config.settings.test
```

Do not inspect, print, copy or modify the local .env. Runtime loading passes DB
credentials only to Django; the harness filters the environment supplied to Next
and Playwright. The browser gate is skipped explicitly in the ordinary unit suite
and must run separately; a skipped browser gate is not acceptance evidence.

LiveServerTestCase starts the actual Django test server after native migration of
test_mira and validates current_database(). It creates fictional least-privilege
accounts, builds Next with that server's exact origin and starts an isolated Next
process. Two Chromium viewport projects run serially without write retries.
Test-process-only IPC revokes fixture permissions/expires fixture sessions, with
no test-control HTTP endpoint. Temporary logs/screenshots remain outside Git;
session/password storage and traces are disabled. The harness checks persisted
business/Audit correspondence and terminates its own process groups afterwards.

Keepdb may create/migrate test_mira; normal TestCase/TransactionTestCase cleanup
belongs only there. It does not authorize manual flush, reset, drop, recreation,
schema reversal or volume deletion. Stop for human review on unexpected state.
Selecting config.settings.test alone does not switch a standalone server to the
test database. Never start an independent server with it expecting isolation.

Retain lint, strict typecheck, Vitest, production build, production/full dependency
audit, delivery tests and diff/secret checks. Preserve the four required GitHub
checks; E2E executes within backend, not a new protection context.

## Temporary dependency-risk disposition

GHSA-g3hv-75wg-m249 / CVE-2026-118764 is a moderate, preexisting, development-only
minimatch dependency inherited through ESLint. The maintainer's explicit temporary
acceptance is restricted to this isolated local demonstration. The production
dependency audit reports zero findings; the full dependency audit still fails and
must be reported as failing. Keep both audit commands and all warnings visible.
Do not change versions or suppress the advisory in this functional increment.
Remediation requires a separately reviewed and compatibility-tested dependency
update. This temporary disposition does not authorize production deployment or
real operator exposure.

## Review and recovery

Present the full approved allowlist patch and observed evidence before ADR
acceptance or publication. Publication binds a canonical manifest to explicit
human approval; merge is separately SHA-authorized after four successful checks.
Integration does not authorize use of mira, deployment or real operator creation.
Disable the surface on failure and favor reviewed forward fixes. Preserve every
business movement and Audit record; no destructive recovery is implicit.

## UX-01 visual verification

Run the existing native browser harness serially on its verified test_mira target.
No manual interactive session or standalone next dev/server is part of UX-01.
The same real fictional fixtures are used for before/after screenshots. Login
screenshots must be taken only while both credential fields are empty; never
reproduce historical captures with filled credentials. Traces and videos remain
disabled. Artifacts and canonical review material stay outside the checkout.

Retain all prior browser security, receipt, revocation, expiry, cursor, lost-response
and Audit-persistence assertions. Frontend-only scripted GET responses additionally
exercise long names, fifty categories, partial capabilities, loading, empty and
error/recovery states; they are not evidence of new backend functionality or
permission grants. Measure page reflow at 320, 390, 768, 1024, 1280 and 1440 pixels,
keyboard focus and contrast from rendered colors. Neither automated coverage nor
screenshots constitute a full WCAG conformance audit or human usability approval.
