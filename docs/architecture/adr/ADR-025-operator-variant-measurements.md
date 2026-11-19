# ADR-025: Operator variant measurement declarations

- Status: Accepted
- Date: 2026-11-18
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

ADR-017 already gives Catalog an optional, versioned `VariantMeasurementSpec`
with exact decimals, separate read/write capabilities and transactional Audit.
The operator cannot yet view or edit it. Adding declarations to the general
Product response would couple product reads to measurement permissions; a new
commercial measurement module would duplicate the accepted Catalog authority.

## Proposed decision

Expose GET and POST on the same-origin operator route
`/catalog/variants/{variant_id}/measurement-spec`. GET requires
`catalog.read_variant_measurement_spec`; POST independently requires
`catalog.set_variant_measurement_spec`. Each route authorizes before input
validation or Variant lookup, and the Catalog service reauthorizes. Session
capabilities are UI hints only. No permissions are assigned automatically.

GET returns `{"spec": null}` for an absent row. A present, fully unknown row
retains its ID, version and timestamps. POST requires all three replacement
fields and `expected_version`; null clears a declaration. Version is a canonical
positive 64-bit decimal string or null, and net amount is an exact plain decimal
string or null. Responses use strings for version and six-place net amount.
The existing service preserves no-op, stale 409 and same-transaction Audit
semantics. The Product response remains unchanged.

The product workspace presents measurement for the selected Variant. Its shared
operation permit prevents competing writes; read generations reject old
responses. Drafts, conflicts and uncertain outcomes persist by Variant while
the workspace is mounted. A conflict requires a fresh read and an explicit
operator decision before another POST. A lost or malformed write response is
uncertain and blocks resubmission for that Variant. Reading cannot establish
which actor made an uncertain write. The browser editor requires both read and
write capabilities to obtain a safe expected version; backend write permission
remains independent of read permission. No POST is retried automatically.

## Consequences and limits

This is administrative metadata, not unit conversion, price by mass, stock
accounting, tax, checkout or sellability. ARS prices remain per Variant; stock
remains EACH. ADR-017 and the existing Catalog, Identity, Audit and CI-01B
contracts remain authoritative. No model, migration, dependency or new service
is required.

Operator routes stay disabled by default. Browser verification is limited to
the native isolated `test_mira` runner and fictional accounts. Real operators,
non-test migrations, external exposure and deployment require separate review.
This ADR was accepted by the maintainer on 2026-11-18. Acceptance does not authorize publication, merge, deployment or non-test operational use.
