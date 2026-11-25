# Isolated POS-01 operator workflow

POS-01 is the foundation for later cash/payment/fiscal work. It is **not authorized for real trade**. A confirmed record means Inventory consumption committed, with `COMMITTED_UNSETTLED` and `UNSETTLED`; it is non-fiscal and not proof of payment. There is no cash session, tender, payment provider, receipt issuance, return/reversal, offline completion or production enablement.

## Workspace and permissions

Use the reusable development worktree and the approved locked toolchain. Keep primary main and retained historical worktrees unchanged. The operator surface remains disabled by default; its existing loopback/session/CSRF and test database guards remain mandatory. No development or production migration is authorized.

| Capability | Permission | Surface |
| --- | --- | --- |
| `pos.discover_products` | `pos.discover_products` | GET products |
| `pos.quote_sale` | `pos.quote_sale` | POST quotation, GET own quotation, explicit cancellation |
| `pos.confirm_sale` | `pos.confirm_sale` | POST confirmation and GET own operation identity |
| `pos.read_sale` | `pos.read_sale` | GET immutable sale detail |
| `pos.list_sales` | `pos.list_sales` | GET bounded sale history |
| `catalog.set_variant_active` | `catalog.set_variant_active` | POST explicit Variant activation with expected state |

Permissions are independent and denied by default. No migration assigns them. A POS operator does not need broad Catalog maintenance or Inventory administration. Every adapter/application operation reauthorizes the trusted native principal. Own quotations and operation lookups are actor-bound; authorized sale read/history capabilities permit operational review across operators. Revocation is observed on subsequent calls.

## Operator procedure

1. Open **Caja** with the appropriate capabilities. Search an exact barcode, SKU or the beginning of a product/variant name; category filtering is available in the API. Pages contain at most 50 matches. Discovery reports Product activation, Variant activation, price basis/revision, sale-unit declaration, accounting unit and available eligible stock separately, including reasons for exclusion.
2. Select a variant and explicitly enter its quantity/unit. EACH is whole units. Mass accepts G or KG with exact milligram precision and a manual measurement timestamp/resolution; optional gross/tare must be paired. Fixed package content does not change accounting quantities. The editable cart neither reserves nor consumes stock.
3. Apply draft changes, request a server quotation and review its exact line amounts, price revisions and total. Quotes last five minutes. Check the explicit review control before confirming. The browser cannot supply prices or totals to confirmation.
4. Confirmation uses one persistent operation UUID and the quotation UUID. The server revalidates commercial snapshots and eligible FEFO stock, then commits the reservation, consumption, sale snapshots and Audit atomically. The result explicitly remains unsettled.
5. A 409 preserves the cart and invalidates quote review. Obtain and review a new quotation; no old price is silently substituted. An expired quotation is not confirmable. Cancellation before confirmation is recorded without consumption. Committed records cannot be edited, cancelled or deleted through POS.
6. On a lost/malformed response, keep the pending identity. Reload/navigation retain the confirmation block. **Consultar operación** performs only GET; a matching committed result reconciles that operation. A missing result is not proof of failure and does not unlock a blind resubmission. There are no automatic POST retries.
7. Authorized history supports sale UUID, operator ID, UTC date and cursor. Timestamps display in local browser time; the underlying UTC expiry policy is unchanged. Internal summaries never imply payment or fiscal issuance.

## HTTP contract

All paths are under `/api/v1/operator`, use native authenticated sessions/CSRF and return no-store JSON. Unknown fields and duplicate/unknown query parameters are rejected. JSON money and quantities are strings; identities are canonical UUIDs. Authorization precedes parameter validation and protected existence disclosure.

| Method | Path | Input / result |
| --- | --- | --- |
| GET | `/pos/products` | `search`, optional `category_id`, `after`; 50 items and next cursor |
| POST | `/pos/quotes` | 1–30 distinct lines: variant UUID, exact quantity, EACH/G/KG, explicit manual measurement for mass; authoritative quotation |
| GET | `/pos/quotes/{id}` | Own quotation, effective expiry/state and immutable snapshot |
| POST | `/pos/quotes/{id}` | Exactly `{"action":"cancel"}` |
| POST | `/pos/sales` | Exactly operation UUID and quote UUID; committed-unsettled result |
| GET | `/pos/operations/{id}` | Own committed operation; absence never proves failure |
| GET | `/pos/sales` | Optional `actor_id`, `on_date` (UTC), `sale_id`, `after`; at most 50 summaries |
| GET | `/pos/sales/{id}` | Immutable snapshots and Inventory allocation/movement trace |
| POST | `/catalog/variants/{id}/active` | Explicit boolean `is_active` and `expected_active`; owner-authorized, attributed activation |

Money is ARS, at most 999999999999.99 per line/total. Catalog rounds each line once with HALF_EVEN cents; POS sums rounded amounts at precision 60. EACH/mass quantities obey existing signed-bigint base-unit limits. Unsupported commercial combinations and unknown declarations block quotation. Barcode/SKU indexes and explicit case-normalized name-prefix indexes support discovery. History uses `(created_at,id)` keyset pagination. API errors distinguish validation (400), authorization (403), missing protected result (404), changed quote/stock/identity (409) and uncertain server failures (5xx).

## Isolation, migrations and recovery

Confirming stock consumption is additionally guarded by exact `config.settings.test`, a single PostgreSQL default alias, configured/effective `test_mira`, no routing/mirror override and SQL `current_database()`. Unsafe metadata is rejected before any connection. Only the authorized native Django runner may migrate its isolated test database. Existing destination guards, exclusive lease, fictional fixtures, native lifecycle, login budget of 30/60s, workers=1 and retries=0 remain intact. Native E2E now reserves 22 logins for each existing project and waits only for the real budget window when necessary.

The new POS migration creates quotations/sales/lines; Audit adds POS attribution and Variant activation support; Catalog adds the independent activation flag and discovery indexes. Historical EACH records, prices, quantities and movements are not rewritten. Existing Variants retain their previous registered status with activation true; this alone never implies commercial eligibility or production authorization. Non-test rollout requires separately approved backup, migration, operational enablement and reconciliation. A future rollback must preserve sales, quotes and ledger/Audit history; use a reviewed forward change instead of destructive database reversal.

Verification must reconcile Sale/lines, consumed reservation allocations, StockMovement/StockPosition/lot balances and matching Audit. Focused tests exercise mixed units, rounding/overflow, stale data, FEFO, final-stock races, duplicate operations, changed identities and transaction/Audit rollback. The full native suite verifies desktop/mobile, permissions, uncertain recovery and responsive/keyboard behavior. Viewport screenshots do not demonstrate real browser zoom; record unperformed zoom and assistive-technology checks explicitly. No partial accessibility test establishes WCAG conformance or production readiness.

The settings source is checked through `DJANGO_SETTINGS_MODULE`, matching the native supervisor: Django 6.1 `UserSettingsHolder` reports `settings.SETTINGS_MODULE=None` inside legitimate overrides. This is not accepted as evidence of a changed source. Configured/effective destination checks and `current_database()` remain independent and mandatory.
