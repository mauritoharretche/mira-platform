# Isolated operator catalog

## Boundary and prerequisites

Phase 1.14 extends the fictional browser demonstration with catalog navigation,
classification and administrative activation. ADR-021 is Proposed. No real
operators/data, deployment, non-test migrations or external exposure are authorized.
Keep the existing loopback session/CSRF controls and pre-session database guard.
Use only the approved native runner's verified PostgreSQL test_mira environment;
config.settings.test by itself does not select that database. No startup migrate,
manual reset, database recreation or access to development mira is permitted.

The [original workflow](operator-workflow.md) describes the Phase 1.13 journey and
still contains an outdated statement that ADR-020 is Proposed. ADR-020 and the
Architecture Decision Log say Accepted and are authoritative. That known reference
is preserved, not silently corrected here. Its old enumeration of exposed features
is historical; this supplement defines the additional Phase 1.14 scope.

## Browser workflow

1. Sign in through the existing native session flow.
2. Open Catalog in the navigation. Browse products, including inactive products.
3. Choose all, uncategorized, or a loaded category; changing filter resets paging.
4. Load more categories/products as needed. There is no free-text search.
5. Create a category if authorized; normalization and duplicate detection remain
   in Catalog. Categories have no rename, delete or hierarchy interface.
6. Open a product and assign, replace or remove its category if authorized.
7. Activate only after assigning a category. Deactivate before removing it.
8. Use the unchanged fictional initial receipt and movement workflow when allowed.

Capabilities can be partial: category creation does not grant list permission;
activation does not grant reads. The category chooser needs category-list access;
HTTP commands still retain their existing independent write permissions. Product
reads need both view_product and view_variant. The session capability list is a UI
hint only; the backend reauthorizes every operation and internal contract.

The workspace coordinates the same Product identity with StockPanel. Never label
administrative activation as sellability. No pricing or measurement editor appears
in this increment; this says nothing about whether those records exist. Prices
remain per Variant, and commercial measurement never implies an inventory unit or
price per kilogram. Product activation requirements have not changed.

## Additional HTTP contracts

Prefix /api/v1/operator; no trailing slash. Preserve existing routes and methods.

| Method / suffix | Input | Capability |
| --- | --- | --- |
| GET /catalog/products | category=all\|uncategorized\|UUID; optional after UUID | catalog.read_product |
| GET /catalog/categories | optional after category cursor | catalog.list_categories |
| POST /catalog/categories | name | catalog.create_category |
| POST /catalog/products/{id}/category | category_id: UUID or null | catalog.set_product_category |
| POST /catalog/products/{id}/active | is_active: JSON boolean | catalog.set_product_active |

List responses use products/categories arrays and nullable next_cursor. Product
summaries contain id, name, category_id, is_active. Detail/creation gain nullable
category_id without removing identity fields. Mutation responses are minimal:
category creation returns id/name; assignment returns id/category_id; activation
returns id/is_active. No additional read is required to commit those writes.

Pages are fixed at 50; SQL retrieves at most 51. Products order by UUID ascending.
Categories order by C-collated name_key then UUID. The category cursor is canonical
unpadded base64url of UTF-8 UUID:name_key, maximum 2048 characters; its key must be
canonical and within the existing 255-character technical limit. It is not an
access token. Reject unknown/repeated query parameters and malformed cursors.
Pagination is not a cross-request snapshot. Product cursors do not bind a filter:
the browser must reset them on filter changes. A nonexistent category filter
returns an empty product page; missing assignment targets return 404 after auth.

Known duplicate category constraint: 409 category_conflict. Bad input/invariants:
400 validation_error. No access: 403. Internal failures: generic 503. Native CSRF,
JSON-only writes, host checks, no-store and response sanitization still apply.
New rewrites enumerate three additional paths, never a wildcard operator proxy.

## Concurrency and uncertain outcomes

Category and activation use existing Product row locks, not optimistic versions.
Another authorized command may replace a value; each Audit records its actual
before/after. Matching no-ops change neither timestamps nor attribution. Audit
failure rolls back the business write. Do not add HTTP-layer duplicate auditing.

POST is never retried automatically. A lost response blocks that form's further
writes and keeps the uncertain warning. Authorized read-only refresh may show
current state but never reclassifies a lost result as confirmed success. Explicit
fresh observation/review is required before any new command. No request-level
idempotency, overwrite protection or actor attribution is inferred from a GET.

## Verification and known risks

Use the locked toolchain and native test runner with config.settings.test and
--keepdb after verifying destination separation, no mirror and no unsafe connection.
The runner may create/migrate test_mira; normal TestCase/TransactionTestCase cleanup
is confined to it. Keep the independent-process zero-connection regression test.
Schema drift review runs on the verified test connection; no new migration exists.
The existing isolated browser harness owns its fictional users, permissions,
servers, temporary artifacts and cleanup. It exercises old and new desktop/mobile
flows and separately verifies their persisted domain/Audit results. No real account
provisioning or standalone demonstration server is authorized by these instructions.

Retain all backend/delivery/frontend/browser gates, frozen installs, lint,
typecheck, production build, production audit and full audit. Required CI remains
backend, frontend, Toolchain (ubuntu-latest) and Toolchain (macos-15).

GHSA-g3hv-75wg-m249 / CVE-2026-118764 is a preexisting moderate development-only
transitive minimatch advisory. Temporary acceptance covers isolated fictional
demonstration only. Full audit remains failing and must be reported as FAIL;
production audit was previously clean and must be rechecked. No suppression,
dependency update or weakened audit command is authorized here. Remediation needs
a separate reviewed compatibility update. Preserve existing peer warnings.
MFA remains a prerequisite before real administrative exposure.

Publication requires review of the entire allowlisted patch and explicit external
approval naming its canonical manifest digest. ADR acceptance and SHA-bound merge
have separate human gates. Do not deploy or migrate non-test databases after merge.
Recovery preserves data/history and disables the surface or uses a reviewed fix.
