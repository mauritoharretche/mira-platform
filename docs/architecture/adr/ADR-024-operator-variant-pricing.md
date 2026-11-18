# ADR-024: Operator variant pricing

- Status: Accepted
- Date: 2026-11-17
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

ADR-016 gives Catalog immutable, exact ARS price revisions per Variant, with
optimistic concurrency and transactional Audit. The operator browser can manage
Variants but has no price workflow. Exposing price on the general Product response
would couple unrelated permissions and inflate catalog reads. A new pricing module
or new monetary policy would duplicate the accepted Catalog authority.

## Decision

Expose three same-origin operator routes: current price read, exact revision write
and bounded revision history. Identity advertises three independent capability
hints in the session response; every route reauthorizes before validation and
Variant lookup. POST accepts a canonical decimal string, ARS and an explicit
`expected_revision_id`, including null for the first revision. The existing
Catalog command keeps the row lock, no-op semantics, stale 409 response and
same-transaction Audit. The Product response gains no price field. History is
ordered by sequence, limited to 50 records per request and paged by exclusive
`before_sequence`.

The Product workspace hosts a price panel keyed by Variant. It uses string
decimals, never binary floating point, and shows price as ARS per Variant rather
than a mass price or a promise of sellability. Writes use the shared operation
permit. Old reads cannot overwrite a confirmed write. A 409 retains the entered
draft and blocks resubmission until the operator reads the current revision and
explicitly chooses to edit against it. A lost or malformed write response is
uncertain; its Variant remains blocked from resubmission in the mounted workspace,
including after a read or Variant switch. A read never confirms the attribution
or outcome of an uncertain POST. No write is retried automatically.

## Consequences and limits

This is an administrative price, not a tax, checkout, mass conversion or
commercial sellability rule. A write does not require read permission at the
backend; the browser editor requires both to present a safe revision. History
permission remains separate. No migration or new dependency is required.
ADR-016, ADR-017 and the CI-01B workspace coordination rules remain in force.

Only the isolated fictional `test_mira` runner may exercise the browser flow.
The operator surface stays disabled by default. Non-test database migrations,
real accounts, external exposure and deployment need separate approval. The
known complete development audit failure from minimatch remains visible and is
not waived. Maintainer Mauro Harretche explicitly accepted ADR-024 on
2026-11-17; the restrictions above remain in force.
