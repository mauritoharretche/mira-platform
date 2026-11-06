# Branching and Release Policy

## Branches

- `main`: current engineering-foundation baseline. Maintainers may commit atomic, approved changes directly during this phase.
- Short-lived branches: `feat/`, `fix/`, `docs/`, `chore/`, `test/`, `refactor/`, when useful.

## Pull requests

Pull requests are optional during the current phase. Require explicit human approval for architecture, security, data, payments, privacy, infrastructure and production-sensitive changes; reassess mandatory PR protections when the delivery process matures.

## Releases

No production release process is approved yet. Before releases, define versioning, release notes, provenance, artifact retention, environment promotion, rollback and incident ownership.

Do not require CI status checks in branch rules until the corresponding workflows exist and have been observed passing.
