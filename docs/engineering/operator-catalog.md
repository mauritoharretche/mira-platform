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

GHSA-g3hv-75wg-m249 / CVE-2026-118764 was a preexisting moderate development-only
transitive minimatch advisory. Its temporary acceptance covered isolated fictional
demonstration only. SEC-01 updates the affected tooling to resolve minimatch 11,
while preserving minimatch 3 for legacy ESLint plugins. The full audit remains
FAIL because of a separate picomatch advisory; the production audit reports zero
findings. Keep both audit commands and existing peer warnings visible.
MFA remains a prerequisite before real administrative exposure.

Publication requires review of the entire allowlisted patch and explicit external
approval naming its canonical manifest digest. ADR acceptance and SHA-bound merge
have separate human gates. Do not deploy or migrate non-test databases after merge.
Recovery preserves data/history and disables the surface or uses a reviewed fix.

## UX-01 Retail Calm: catalog workspace

Catalog is the principal destination after login and from the MIRA brand. New
product creation keeps /operations; details keep their existing UUID routes.
There are still only two primary navigation destinations. Current navigation is
exposed with aria-current; the keyboard skip link targets the workspace.

Product results precede secondary category administration in both DOM and mobile
reading order. The single semantic list presents aligned product/category/state
rows on desktop and compact cards below 768 pixels. Desktop column headings are
visual scanning aids, not a replacement for the accessible list and explicit
product links. Cards retain full product names, category fallback and textual
administrative state; meaning never depends on color. No responsive duplicate
list, per-product detail fetch, fake search, SKU, price or stock total is added.

A labeled filter remains next to results. A local Gestionar categorías anchor
reaches the secondary category panel without a new route or hidden menu. Loaded
category buttons reflect selection with aria-pressed. Category paging, opaque
product cursors, stale-read invalidation and filter resets remain unchanged.
Creation is displayed only with its existing capability. Missing category-read
permission retains the all/uncategorized filter and explicit permission feedback;
missing product-read permission exposes no result list. UI hints do not authorize
writes. Administrative activation is explicitly distinct from publication,
stock availability and sellability.

### Review acceptance

- At 390×844 the first loaded product is fully visible before category creation.
- At 1280×800 at least ten short-name product rows are fully visible.
- No page horizontal overflow at 320 pixels; wrap long names rather than truncate
  content. Verify the six requested viewport widths and 320-pixel reflow.
- Native labels, keyboard focus, 44-pixel operational targets and list/card
  semantics remain usable. Text contrast is at least 4.5:1 and essential control
  boundaries/focus at least 3:1; decorative dividers are not control boundaries.
- Retain every previous authentication, CSRF, pagination, permission, activation,
  Inventory and uncertain-POST scenario. No write retries are introduced.

Store actual screenshots, measured colors/layout, test output and the complete
nine-file canonical patch/manifest externally for independent review. Scripted
read-response presentation tests must be labeled separately from real persisted
browser workflows. Preserve the historical minimatch exception, current full-audit
FAIL, production audit result and historical ADR status references above as explicit
review limitations.
Publication, merge, real provisioning and non-test schema use retain separate gates.

## UX-03 Retail Calm: contextual catalog navigation

The product filter and product-page cursor are represented by the browser URL.
`category` is `uncategorized` or a category UUID; omission means all products.
`after` is the product UUID cursor returned by the preceding page. It is not the
separate category-list cursor. Changing category starts at the first product page.
Unknown, repeated, malformed or oversized URL parameters are normalized to a
safe catalog location. A valid cursor paired manually with an incompatible filter
may produce an empty page; use **Volver al inicio / actualizar** to clear it.
The catalog does not invent page totals, a previous-page cursor or a stable
cross-request snapshot.

Product links carry the current validated filter and cursor. **Volver al
catálogo** restores that view; a product opened directly returns to the initial
catalog. Browser Back, Forward and reload also use the URL. At the first page,
**Volver al inicio / actualizar** repeats the read without adding history. A
selected category that has not appeared in the loaded category pages keeps a
provisional label rather than changing the product filter. The UI preserves the
existing skip link, keyboard focus cues, loading/error announcements and all
server-side permissions. Navigation never retries a write.

## BC-01A: additional operator variants (Proposed ADR-023)

The isolated operator surface adds POST /catalog/products/{product_id}/variants.
Strict JSON accepts label, sku and optional nullable barcode. It returns 201 with
id/label/sku/barcode. The existing Catalog command normalizes identity and writes
one Variant and its Audit atomically. The capability catalog.add_variant_to_product
requires catalog.add_variant; reads still require both Product and Variant view
permissions. Session hints grant nothing. Authorization precedes input validation.
Known SKU/barcode conflicts return identifier_conflict (409); missing Product is
404, invalid input 400 and database failure a sanitized 503. No replay is provided.

The Product workspace owns the selected Variant. A confirmed addition invalidates
older detail reads, merges only the Variant into the latest local Product, orders
variants by UUID and selects it. It never fabricates stock, price or measurement.
A lost/malformed write response blocks further additions in that mounted panel;
refresh is read-only and never converts uncertainty to success. Review independently
before leaving this screen and starting another operation. Selection and additional
creation are locked during a receipt or addition. Stock reads are scoped to a keyed
Variant panel; old responses cannot populate a different selection. Receipt outcomes
remain in the workspace by Variant, including warnings while viewing another one.
No pricing, measurement, sellability, later receipt or adjustment editor is added.

BC-01A browser cases are catalog-variants and catalog-variants-lost in both projects.
The native harness retains all previous cases and checks additional persisted
Variant/Audit correspondence, duplicate/lost-response behavior and explicit-only
stock creation. CI-01 remains an independent unresolved catalog-journey failure.
The historical minimatch exception, current full-audit FAIL and historical ADR
status references above remain documented. Only the verified native test_mira
runner may execute fixtures;
no development mira access, real accounts, external exposure or deployment is allowed.

## PC-01: Variant price in the operator workspace (Accepted ADR-024)

The price panel is scoped to the selected Variant. It displays the current ARS
amount per Variant, an exact-decimal editor and revision history. It does not
infer units from net content, SKU or Inventory, and administrative activation is
not a statement that a product is sellable. A missing price is distinct from
zero. The general Product response does not include price.

`GET /catalog/variants/{id}/price` requires `catalog.read_variant_price`.
`POST` on the same route requires `catalog.set_variant_price` independently and
accepts `amount` as a plain decimal string, `currency: "ARS"`, and mandatory
`expected_revision_id` (null for first price). A stale expectation is 409; a
matching amount and revision is a no-op. `GET .../price/revisions` requires
`catalog.read_variant_price_history` and returns at most 50 descending records
with an exclusive `before_sequence` cursor. The session capability list is only
a UI hint; each route performs fresh server-side authorization before input or
existence checks. No permissions are assigned automatically.

During a pending write, the workspace blocks selection and competing writes.
On 409, the entered amount remains visible. The operator must consult the current
price and explicitly choose **Editar sobre esta revisión** before saving. A
network loss or invalid write response leaves the outcome uncertain and blocks
another POST for that Variant in the mounted workspace, even after a read or
switch to a sibling Variant. Reading a current price cannot prove who wrote it.
The history is bounded and may be refreshed after a confirmed write; it is not
a write receipt. No automatic POST retry is performed.

The operator routes remain disabled by default. Browser fixtures are fictional
and run only through the native runner after SQL verification of `test_mira`.
The historical minimatch exception and current picomatch full dependency audit
failure remain documented; publication and non-test use require separate gates.

## MS-01: Variant measurement declarations (ADR-025 Accepted)

The selected Variant has an independent commercial measurement panel. It shows
the optional sale unit (`EACH`, `G`, `KG`) and optional positive net-content
amount with unit (`G`, `KG`). An absent specification differs from an existing
specification whose declarations are all unknown. No unit conversion, stock
change, price reinterpretation or sellability decision follows from this panel.

`GET /catalog/variants/{id}/measurement-spec` requires
`catalog.read_variant_measurement_spec`; `POST` on the same route independently
requires `catalog.set_variant_measurement_spec`. POST requires `sale_unit`,
`net_content_amount`, `net_content_unit` and `expected_version`, including
explicit nulls. Versions and exact decimals cross JSON as strings. A stale
version returns `409 measurement_conflict`, even for an otherwise identical
write. A matching no-op creates no new version or Audit entry. Every actual
change and its actor are recorded in the same transaction by Catalog and Audit.

The browser editor requires read and write capabilities, because it needs the
current version. Its draft, conflict and uncertain-result state survive Variant
switches in the mounted workspace. A conflict requires a fresh read and an
explicit **Editar sobre esta versión** decision. A read never confirms an
uncertain write, and no POST is retried automatically. The existing isolated
`test_mira` runner remains the only authorized browser execution path.
