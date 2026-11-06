# Branching and Release Policy

## Branches

- `main`: protected, releasable baseline.
- Short-lived branches: `feat/`, `fix/`, `docs/`, `chore/`, `test/`, `refactor/`.

## Pull requests

All changes after bootstrap enter `main` through pull requests. Require at least one independent review where the team has more than one eligible reviewer. Dismiss stale approvals after new commits for sensitive changes.

## Releases

No production release process is approved yet. Before releases, define versioning, release notes, provenance, artifact retention, environment promotion, rollback and incident ownership.

Do not require CI status checks in branch rules until the corresponding workflows exist and have been observed passing.
