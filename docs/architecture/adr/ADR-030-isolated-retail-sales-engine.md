# ADR-030: Isolated retail sales engine

- Status: Proposed
- Date: 2026-11-25
- Human owner: Mauro Harretche
- Technical owner: MIRA engineering
- Local architecture/implementation: explicitly approved by the POS-01 mandate

## Decision and alternatives

POS owns quotations, cancellation before confirmation, committed-unsettled sales, immutable commercial line snapshots, operation identities and history. Catalog owns identities/activation, measurement declarations and price revisions. Inventory alone reserves, allocates FEFO and consumes; Audit owns transactional attribution. This remains the existing modular monolith/default PostgreSQL connection. No second ledger, stock ORM writes in POS, external service, payment, cash session, fiscal document, customer, tax or promotion is introduced.

A browser-calculated total and independent per-line stock mutations were rejected: neither preserves authoritative pricing and all-or-nothing multi-line consumption. Persistent abandoned-cart reservations were also rejected. An editable browser cart consumes nothing. A server quotation lasts five minutes, belongs to its authenticated actor, and stores the ordered exact normalized cart, commercial snapshot, rounded line amounts and ARS total. Cancellation is attributable/idempotent and consumes nothing. Changed or expired quotes require a new quote and an explicit review, never a silent price replacement.

## Exact contracts and eligibility

Amounts are exact Decimal ARS, bounded at 999999999999.99 for each line and total. Round each line once with Catalog's HALF_EVEN calculation; sum those rounded cents at precision 60. Maximum 30 distinct variants per cart. EACH is a strictly positive whole integer; mass uses explicit G/KG strings converted to Inventory's integer milligrams. Reject overprecision, mismatches, exponent notation, floats and overflow before persistence. Fixed-package content never multiplies accounting quantity. Manual mass requires net, explicit unit, resolution, aware timestamp, source `manual` and attributed operator; optional gross/tare are paired and consistent. No fabricated hardware provenance.

Product active/category, Variant active, price revision/basis, declared sale unit, initialized accounting unit and eligible unreserved FEFO stock are distinct checks. No one flag implies sale eligibility. Catalog adds independent Variant activation (default true preserves the historical registered-variant contract); existing inactive Products stay inactive, and no price/stock or permissions are fabricated. Changing that flag requires its own Catalog capability, expected boolean state and transactional Audit. Barcode lookup is exact; SKU and names use bounded prefix discovery with keyset pages of 50 and explicit query indexes. Empty/unknown commercial metadata blocks quoting.

## Atomic confirmation and lock order

Confirmation is server-restricted to native `config.settings.test`, default-only PostgreSQL `test_mira`, no connection routing/mirror override, enabled isolated operator surface and verified SQL identity. Metadata is rejected before opening a connection, even through direct application calls. This is a test-only rollout guard, not an approval to trade. Production enabling requires a later reviewed design; no environment variable alone authorizes it.

One transaction takes Inventory's shared pilot advisory gate, the POS quote lock, then Catalog Product rows ordered by UUID and Variant rows ordered by UUID. It reauthorizes the principal and rechecks the full commercial snapshot (including revisions, measurement version and names), authoritative totals and eligibility. Existing Catalog writers acquire Product or Variant locks but never enter Inventory; Inventory never calls POS or Procurement. Procurement's existing order-before-Inventory edge remains one-way. No network call occurs within the transaction.

Inventory's narrow POS command independently authorizes POS confirmation; it does not grant or impersonate broad inventory administration. The same reviewed reservation/transition implementation is reused behind explicit owner APIs. Stable UUIDv5 child operation IDs derive from the POS operation UUID. Allocate and consume eligible FEFO stock within the outer transaction, require terminal CONSUMED, retain allocations/movement references, then write Sale/lines, quote transition and POS Audit. Every effect commits or rolls back together. No committed historical sale has an update/delete/reversal API. Privileged SQL is outside these application immutability guarantees.

## Identity, recovery and surface

Five independent capabilities: discover, quote/cancel/read own quote, confirm/lookup own operation, read sale, and list sale history. No automatic assignment or Inventory privilege inheritance. Authorize before parsing identifiers or revealing record existence. HTTP adapters preserve native session, CSRF, loopback, host, deadline and fail-closed database boundaries. Next proxies only enumerated routes.

The confirmation payload is exactly quote UUID plus operation UUID. Its canonical fingerprint binds the authenticated actor, action and quote identity; the persisted immutable quote binds the normalized payload. An identical committed replay returns its saved result, including after quote expiry. Changed actor/quote under the same UUID conflicts. Different UUIDs cannot confirm the same quote twice. Failed transactions leave no consumed stock or partial sale. Read-only operation lookup is actor-bound; absence does not prove failure.

The browser synchronously blocks duplicate submits and persists the pending UUID/quote pair locally before sending confirmation. Uncertain network/malformed success responses retain that block, including navigation/reload. GET lookup may reveal the previously committed matching result, but cannot attribute a different operation or infer failure from absence. Conflicts preserve the cart, invalidate review and require another server quotation plus explicit operator review. There are no automatic write retries.

## UI, evidence and rollout

Use Libro operativo tokens/native accessible controls: keyboard barcode/prefix discovery, distinct draft cart and authoritative quotation, explicit mass entry, prominent exact total, persistent uncertain recovery and immutable history. Output always says COMMITTED_UNSETTLED / UNSETTLED, non-fiscal and not proof of payment. History is bounded/keyset-paginated and filterable by identity, UTC date and operator; display timestamps in the browser's local timezone. No cash/payment receipt is issued.

Additive POS models, Audit attribution and Catalog fields/indexes are verified only by the native isolated test runner. No backfill of price, stock, device readings or permissions. Non-test migration, backup/rollout, actual cash operation and deployment need separate authorization. Recovery preserves identities/history and uses reviewed forward changes; no destructive resets, rewrites or automatic reversals.

Focused PostgreSQL tests cover mixed quantities, rounding/overflow, stale snapshots, lifecycle, FEFO eligibility, final-unit and measured races, duplicate confirmation, rollback/Audit failure, isolated destination denial and granular permissions. Full relevant backend/frontend/engineering and native desktop/mobile gates remain required. Viewport emulation is not evidence of real Chrome zoom; unperformed manual checks remain explicit gaps. Exact canonical digest approval, publication, CI and merge are separate ADR-018 gates.

The settings source is checked through `DJANGO_SETTINGS_MODULE`, matching the native supervisor: Django 6.1 `UserSettingsHolder` reports `settings.SETTINGS_MODULE=None` inside legitimate overrides. This is not accepted as evidence of a changed source. Configured/effective destination checks and `current_database()` remain independent and mandatory.
