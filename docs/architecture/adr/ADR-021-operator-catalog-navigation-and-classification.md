# ADR-021: Operator catalog navigation and classification

- Status: Accepted
- Date: 2026-11-09
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

The isolated operator demonstration can create a Product with its first Variant
and receive initial EACH stock, but previously required a UUID to reopen a product.
Catalog already owns categories, administrative activation, pricing and commercial
measurement. Identity and transactional Audit already protect their contracts.

Expose navigation, classification and activation in one focused increment.
Exposing pricing and measurement editors simultaneously would add independent
Decimal, revision and version workflows. Those editors are deferred, not removed.
A read-only display alone would still require internal tools to classify products.

## Proposed decision

Catalog adds only two internal reads: list_products and list_category_page.
Both require keyword-only principal, authorize before validating filters/cursors,
and return frozen DTOs containing evaluated tuples. Preserve the twelve existing
Catalog operations and all seven Audit append contracts unchanged.

Product pages reuse READ_PRODUCT (view_product AND view_variant). Category pages
reuse LIST_CATEGORIES (view_category). SQL selects at most 51 rows to return fixed
pages of 50. Products use UUID ascending keyset ordering; categories use the
existing C-collated name_key and UUID ordering. No ORM models, lazy QuerySets,
price/measurement/stock aggregates or unrestricted full lists are exposed by the
new queries. The original internal list_categories operation remains intact.

HTTP uses explicit same-origin routes under /api/v1/operator, without trailing
slashes. GET products accepts category=all|uncategorized|UUID and optional after
UUID. GET categories accepts only an optional bounded canonical base64url cursor
containing UUID:name_key. Cursor values are validated parameters, not credentials;
there is no client-selected sort, page size or arbitrary filter. Unsupported,
repeated, malformed and incompatible query parameters are rejected. Navigation is
over currently committed data, not a snapshot across pages. Reset product cursor
when changing filter; reuse across filters has no special authority or snapshot.

POST categories calls create_category. POST products/{id}/category and /active
call set_product_category and set_product_active respectively, with their existing
granular capabilities. Payloads are strict and UUID/boolean types are validated.
Authorization precedes business validation and existence queries at both adapter
and application boundaries. Successful write responses contain only the affected
identity/state; they do not demand an additional read capability after committing.
The Product identity response gains nullable category_id additively.

Creation normalization and PostgreSQL uniqueness remain owned by Catalog.
Only the known category-key unique constraint becomes category_conflict (409).
Other database failures remain generic unavailable responses. Each effective
mutation and its existing Audit entry share the existing transaction. Reads,
no-ops and rejected operations create no successful-mutation attribution.

Product category and activation writers retain their shared row lock. They do
not gain optimistic version tokens: concurrent commands serialize, observe the
actual prior state and can replace an intervening value. No retry is automatic.
Uncertain HTTP write outcomes remain uncertain after a read; no read establishes
which actor performed a change or replays a lost success response.

## Browser and security boundaries

Add a catalog browser, category creation/filtering and a classification panel.
A Product workspace coordinates the authorized identity and existing StockPanel.
Keep the previous creation/receipt journey and its persistent Audit assertions.
Support partial permissions, keyboard interaction, loading/error/empty states,
mobile layouts and stale-response cancellation/invalidation. Session capability
hints never replace server authorization. No new frontend dependency is required.

Keep ADR-020 sessions, CSRF, expiry, login abuse controls, safe errors, no-store
responses, loopback-only demonstration and explicit rewrites. New routes inherit
the existing pre-session database fence: configured/effective PostgreSQL test_mira,
no mirror/destination overrides, cached connection identity checked without SQL.
The approved native runner still verifies current_database() directly.

Active requires category and means only administratively active. It does not mean
published, priced, measured, available or sellable. Pricing retains PER_VARIANT;
measurement G/KG does not reinterpret it. No sellability predicate, pricing editor,
measurement editor, additional Variant editor or new Inventory write is added.

## Data preservation, exceptions and approval

No models, migrations, backfills, permission mappings, dependencies, locks,
workflow, infrastructure or PostgreSQL configuration change. Only the native test
runner may create/migrate test_mira and perform its normal isolation/cleanup.
Never access/migrate development mira or modify volumes. Starting an independent
process with test settings does not select the isolated destination and remains
blocked. Unexpected database state requiring manual repair is a stop condition.

Carry forward ADR-020's temporary moderate minimatch advisory disposition:
isolated fictional demonstration only. Full dependency audit remains failing;
last reviewed production audit was clean and must be rechecked. Do not suppress
advisories or alter versions in this increment. The original operator runbook still
contains its outdated Proposed reference to ADR-020; the Accepted ADR and Decision
Log are authoritative. The companion catalog runbook explicitly records this.
MFA, real accounts, deployment and real administrative exposure remain deferred.

Technical acceptance, digest-specific publication, SHA-specific merge and fresh
post-merge verification retain independent human gates. This ADR remains Proposed
until explicitly accepted. Code integration never authorizes non-test migrations
or operational use. Recovery disables consumers or uses a reviewed forward fix;
never delete or rewrite business/Audit history to undo a UI change.

## Verification

Preserve baseline domain, delivery, frontend and browser scenarios. Add bounded
SQL/keyset tests, strict inputs, permission denial/revocation before business
queries, CSRF/session/default-disabled behavior, Audit failure rollback/no-ops,
and all-route zero-query unsafe-destination checks. Preserve PostgreSQL constraints
and existing concurrent writer tests. Exercise desktop/mobile category creation,
filter changes, classification, activation, stale reads and lost-write responses.
Report actual locked-install, lint/typecheck, tests, build and both audit results;
this ADR itself does not claim any check passed.

## References

- ADR-012 through ADR-017: ownership, identity, Audit and Catalog foundations.
- ADR-019: independent Inventory accounting and movements.
- ADR-020: native sessions and isolated same-origin operator workflow.
