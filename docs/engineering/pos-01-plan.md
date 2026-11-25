# POS-01 implementation plan

- Status: In Progress (grouped local implementation approved)
- Human owner: Mauro Harretche
- Technical owner: MIRA engineering
- Date: 2026-11-25
- Baseline: e43d0c6365c7d1313eaeb38a10e4b5e40cc178ae

## Outcome and scope

A fictional operator discovers variants, edits an EACH/mass cart, reviews a server quotation and confirms one atomic, attributable inventory consumption. The result is explicitly unsettled, non-fiscal and test-only. No payment, cash session, tax, hardware driver, customer data, production use or non-test migration.

## Implementation

1. Preserve the reusable development workspace; add the approved workspace policy. Review ADR-029 against its integrated exact arithmetic and unit contracts, accept it with an explicit later-scope note in older EACH-only decisions. Record POS decisions in ADR-030.
2. Add POS-owned immutable quotation/sale snapshots, operation identities and additive migrations. Catalog supplies typed commercial snapshots/discovery; Inventory owns reservation/FEFO/consumption through an explicitly authorized POS contract; Audit appends transactional attribution. Never write another module's ORM tables.
3. Enforce independent deny-by-default capabilities, authorize before parameters/existence, validate strict versioned HTTP inputs, exact quantities and bounded money. Persist actor-bound quote identities (five minutes), cancellation and canonical fingerprints. Confirm under Inventory's shared pilot transaction gate, then POS quote and ordered Catalog locks. Revalidate snapshots; changed quotations require explicit operator review. Replay only identical committed requests; uncertain outcomes use GET lookup.
4. Build a keyboard-first Libro operativo cashier page, exact text quantities, mixed carts, quotation review and immutable history. Keep pending/uncertain writes blocked until identity lookup establishes the result; no automatic write retries.
5. Verify focused PostgreSQL rollback/concurrency/security tests, backend regressions, engineering, lint/typecheck/Vitest/build, both dependency audits and native Django-owned desktop/mobile E2E. Preserve real login budgets, SQL identity guards, fictional fixtures and cleanup. Record actual Chrome zoom separately from viewport checks.

## Files and dependencies

POS app/contracts/application/models/HTTP/migrations/tests; Catalog commercial reference/discovery and explicit variant activation; Inventory POS adapter and shared reservation implementation; Identity capability/session mapping; Audit append/model/migration; settings/routes; web POS API/workspace/page/navigation/styles/proxy; native harness/E2E and route/module boundary tests; ADR-030, ADR-029 status and historical compatibility notes; AGENTS workspace policy and this plan. Existing lockfiles/toolchains only, no new service or dependency.

## Risks and verification

Primary risks: overselling, reverse locks, stale prices, duplicate confirmation, precision overflow and false settlement. Cover last-unit/mass races, multi-line atomic failure, Audit/outer rollback, changed eligibility, canonical identity reuse, revocation and non-test rejection. A new migration is applied only by Django's isolated test runner. No direct development/production connection, destructive rollback or publication without exact manifest approval. Private source documents remain outside Git; the separately named technical-system reference is unavailable unless found locally. Existing Blueprint and master dossier are context, not authorization to expand this mandate.

## Release boundary

Prepare one complete canonical candidate after verification. Publication needs separate exact SHA-256 approval; merge, deployment and non-test rollout remain separate. Retain all historical branches and retained worktrees.
