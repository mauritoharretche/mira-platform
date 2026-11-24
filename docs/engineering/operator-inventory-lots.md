# Operator lot inventory

The fictional, loopback-only operator surface has a **Lotes** page. It requires `inventory.read_stock_lots` to show physical lots, their status, expiry and quantities by location. It always shows the aggregate EACH `StockPosition` and an explicit `sin asignación` remainder. The latter includes historical stock and purchase receipts for which no physical lot was declared. It is not silently assigned to a lot.

Location creation requires `inventory.manage_stock_locations`. Lot creation and status changes require `inventory.manage_stock_lots`. Transfers require `inventory.transfer_stock_lots`. Physical corrections require `inventory.correct_stock_lots` plus `inventory.adjust_stock`; they create a normal StockMovement and Inventory Audit attribution. All mutating operations use expected versions where they affect existing quantities or status.

During a Procurement receipt, an operator with `inventory.read_stock_lots` and `inventory.allocate_stock_lots` may select a previously created lot and location for an accepted line. The HTTP contract also accepts up to ten explicit allocations for one line, with quantities summing exactly to the accepted quantity. Omission preserves the old unallocated behavior. A persisted operation UUID can be queried after an uncertain response; do not blindly resend the POST. Physical allocation and the purchase receipt are one transaction.

FEFO lists eligible positive lots by expiry date, then code and ID. Undated lots follow dated lots. Expired, blocked and quarantined lots stay visible but do not appear in that suggestion. It does not promise sellability or perform a reservation. The user must inspect physical stock and applicable business rules before acting.

No new API endpoint is enabled outside the existing operator boundary. The native test runner alone may exercise the additive migrations on `test_mira`; neither this page nor its server startup performs migrations.
