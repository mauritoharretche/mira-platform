# ADR-023: Operator variant management

- Status: Accepted
- Date: 2026-11-16
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

Catalog can already append a Variant with transactional attribution, but the
operator can create only an initial presentation. Expose this existing capability
as one vertical slice. A larger pricing/measurement/inventory editor would combine
independent concurrency policies; a second Product screen would duplicate context.

## Decision

Add one explicit same-origin POST products/{product_id}/variants route and rewrite.
Use Catalog's existing add_variant_to_product command and catalog.add_variant
permission. Authenticate through the existing session boundary and authorize before
validation/existence. Strict bounded JSON accepts label/sku/optional barcode and
returns only the confirmed Variant identity, without an extra read permission.
Catalog retains normalization, uniqueness and the atomic Variant/Audit write.
No schema or internal contract changes, direct HTTP ORM writes or extra Audit exist.

ProductWorkspace owns selection and merges confirmed additions with a functional
update, deduplicated and ordered by UUID. It invalidates old Product reads before
merging, preserving current category/activation. StockPanel remounts by Variant to
isolate reads; receipt outcomes survive selection in workspace state. Selection,
refresh and additions are blocked during pending writes. Unknown write outcomes
remain blocked in the creation panel even after refresh; GET cannot prove actor or
replay success. No automatic POST retry or new ReadGeneration behavior is introduced.

## Security, data and recovery

Preserve ADR-020/022 loopback/test_mira/session/CSRF fences, disabled defaults and
native SQL identity verification. No unit inference or implicit stock, price or
measurement is allowed. Full minimatch audit remains a known FAIL, not suppressed.
CI-01 remains open; this slice cannot be called fully verified while its gate fails.
Only isolated fictional testing is permitted. Real operation, MFA, non-test schema
readiness, deployment and account provisioning require separate review. Disable
consumption or use a reviewed forward fix; never remove Variant or Audit history
as a rollback shortcut. Acceptance, canonical publication and merge are separate.

## Verification

HTTP tests cover independent permissions, strict input, conflicts, missing Product,
CSRF, revocation and Audit rollback. Include the route in disabled/unsafe-destination
zero-query tests. Dedicated desktop/mobile browser cases cover confirmed additions,
stale reads, uncertainty, selection locking and receipt-state preservation; the
native harness checks persistent correspondence. Record actual results and blocked
checks separately. Acceptance of this ADR does not assert that those suites passed.
