# ADR-026: Procurement purchase orders and goods receipts

- Status: Accepted
- Date: 2026-11-22
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context

Inventory owns stock positions and movements under ADR-019. Catalog owns Variant identity. The pilot operation needs fictional suppliers, purchase orders and traceable partial goods receipts without turning physical adjustments into purchase receipts or adding accounting valuation, payments or fiscal documents.

## Decision

Procurement owns Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceipt, ReceiptLine and its event history. It stores a Variant UUID and a bounded display snapshot but never writes Catalog tables. Its application contract asks Catalog for a read-only Variant reference and Inventory to apply an accepted EACH receipt. Inventory remains the sole writer of StockPosition and StockMovement and returns the movement identity. Audit stores independent attribution for Procurement events and Inventory movements.

Supplier names are normalized with NFKC and casefolded for uniqueness; inactive suppliers cannot be used for a new or revised order. An order has DRAFT, CONFIRMED, PARTIAL and COMPLETE states. Draft edits replace lines under an order row lock and expected version. Confirmation requires the expected draft version and an active supplier. A line has an explicit integer EACH quantity and exact ARS unit cost with two decimal places; it never derives cost from Catalog price or measurement. The order's displayed total is a sum of Decimal line costs, not an inventory valuation.

A receipt has a client-generated UUID operation identity persisted uniquely with a digest of the normalized request. Repeating the identical operation returns its recorded receipt; reusing the identity with different content is a conflict. The operator can query the identity after an uncertain response. No POST is retried automatically. Delivered and accepted quantities are separate. A short reason records a difference; only accepted quantity reduces the order's pending quantity and increases Inventory. Each accepted line records the resulting StockMovement UUID. A line cannot accept more than delivered, and delivered cannot exceed pending. A rejected quantity remains pending so another delivery can complete the order.

One default database transaction locks the order and relevant lines, applies Inventory receipts, writes Procurement receipt and event rows, and records required Audit attribution. Any failure rolls back all writes. Existing stock positions require an exact expected version; a missing position requires explicit null. No background worker, payment integration, second approver or supplier data import is introduced.

Versioned operator HTTP routes authorize each operation before parsing IDs or payloads. Session capability hints remain granular and do not grant permission. Receiving requires both Procurement's receive permission and Inventory's receive permission. The UI preserves drafts after conflicts and stops blind resubmission after uncertain responses; receipt identity lookup is read-only and cannot itself confirm an uncertain write if the operation is not found.

## Alternatives and consequences

Direct Procurement writes to Inventory tables would reduce calls but violate ADR-019 ownership. Reusing Inventory's physical adjustment would misstate receipt provenance. A separate service or queue would add consistency work without a pilot need. The selected application contract keeps one transactional boundary in the modular monolith.

The first release has one operation, one accounting location, EACH quantities and ARS costs. It does not implement lots, expiry, FEFO, multiple locations, advanced valuation, sellable availability, payments or tax. Those remain future work. Historical Variant labels and SKUs are snapshots for display, not a substitute for Catalog identity. Cross-table reconciliation is enforced by application tests rather than a cross-module foreign key.

The new migrations are additive. Only the native isolated Django test runner may apply them to `test_mira` during local verification. This ADR records the maintainer's explicit grouped architecture approval for the Procurement MVP on 2026-11-22. Publication, merge, deployment and any non-test migration remain separate decisions under ADR-018.
