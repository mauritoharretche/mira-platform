# ADR-029: Measured inventory, physical counts and losses

- Status: Proposed
- Date: 2026-11-24
- Human owner: Mauro Harretche
- Technical owner: MIRA engineering

## Approved local scope and plan

The INV-04 mandate authorizes local architecture and implementation following verified INV-03 post-merge CI. Extend the existing Inventory ledger, quantities, reservations, Procurement, Catalog prices and Audit; add bounded physical counts and operational losses, operator controls and isolated tests. Preserve EACH history and the single pilot lock hierarchy. No POS sale, payment, hardware driver, deployment or non-test migration is authorized. Publication requires ADR-018 approval of the exact canonical manifest.

## Exact quantities and compatibility

EACH remains an integer item count. G uses integer milligrams in the same signed-bigint bounded ledger and balance fields: one base unit is 0.001 G. HTTP G quantities are exact decimal strings of at most three fractional digits, formatted with three digits; KG conversion permits at most six fractional digits. One KG is exactly 1000 G. Input below milligram precision, floats, exponent notation, overflow and implicit rounding are rejected. Public typed application commands use integer base units and an explicit accounting unit; old EACH signatures and omitted HTTP units retain EACH only. A stock position fixes its accounting unit at its first explicit receipt. No inference from SKU, price or measurement metadata occurs; no existing quantity is converted.

Lot balances inherit their position unit. Receipts, adjustments, transfers, classification, corrections, availability, reservations and consumption validate that unit. Reservation lines may mix different variants' EACH and G units, never a different unit for the same position. The existing Inventory advisory transaction gate serializes every writer, including counts and losses. No second accounting ledger is introduced.

## Pricing and commercial arithmetic

Existing PER_VARIANT revisions retain their meaning. New PER_KG revisions explicitly price kilograms in ARS with two decimal places, independent of physical accounting, sale-unit declarations and package content. New revision attribution also records the previous basis, so a basis-only change is an effective audited revision. A reusable hardware-independent calculation uses Decimal at precision 60, converts declared G/KG transaction mass exactly, multiplies once, then rounds the line amount to ARS cents with ROUND_HALF_EVEN. Totals sum rounded line amounts, not intermediate rounded mass or prices. Input prices and quantities are never rounded. No valuation or completed sale is implied.

## Counts and losses

A count has a persistent operation/session identity, explicit location and up to 100 balance identities, responsible actor, initial balance/stock/lot version snapshots, observed counts and reasons/evidence. Blind entry hides expected quantities from entry-only readers; review permission exposes discrepancies. States are OPEN, REVIEWED, COMPLETED. Completion requires every observation, an authorized review and unchanged snapshots. It rejects concurrent physical changes or removal of reserved units. Each effective discrepancy creates a COUNT StockMovement and matching Audit; a zero discrepancy creates no movement. Workflow events remain attributable and immutable; sessions cap observation history at 200 events including creation and terminal transitions.

Losses use persistent operation identities and explicit EXPIRATION, DAMAGE, QUALITY_REJECTION, MEASUREMENT_DISCREPANCY, INTERNAL_CONSUMPTION or OTHER reason codes with evidence. WRITE_OFF is a distinct negative movement. Status blocking/quarantine does not remove physical stock; an authorized write-off does. All stock, balance, operation and Audit writes commit or roll back together; uncertain writes use identity lookup and never automatic retries.

## Measurement boundary and rollout

A typed measurement carries net mass, optional gross/tare, source, aware timestamp, unit and explicit resolution. Gross minus tare must equal net exactly when both are supplied; every amount must respect the declared resolution and milligram floor. This is a software contract, not scale protocol or regulatory approval. Hardware, PLU and metrology validation remain future POS work.

Migrations add structures/columns and broaden existing constraints for G and new movement/price bases without editing earlier migrations or rewriting history. Only the native test_mira runner may apply them locally. A non-test rollout requires a separate preservation, backup and compatibility gate. Recovery uses explicit forward corrections and event history, never destructive stock replacement.
