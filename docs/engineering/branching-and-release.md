# Branching and Release Policy

## Branches

- `main`: reviewed integration baseline. Changes reach it through pull requests.
- Use short-lived `feat/`, `fix/`, `docs/`, `chore/`, `test/` or `refactor/` branches from a verified clean, synchronized main commit.

## Pull requests

Review the entire patch, including new files, before explicit publication authorization. Engineering Automation binds that authorization to a canonical manifest SHA-256. Passing a digest does not authenticate an approver: an external, explicit maintainer decision naming it must exist and be verified by the trusted operator.

Publish only the approved paths/content, with an atomic commit and non-force push. Require backend, frontend, Toolchain (ubuntu-latest) and Toolchain (macos-15) to complete successfully for the exact revision. Missing, pending, skipped, neutral, cancelled and failed jobs are not sufficient.

Obtain separate merge approval for the exact PR head and current main SHA. Use squash only, with the expected head SHA. Do not use admin bypass, auto-merge, branch deletion flags, automatic branch updates, rebase or destructive resets. Preserve the feature branch. Verify the resulting commit's parent/tree and all four new post-merge CI results before declaring integration verified.

## Bootstrap and activation

Engineering Automation Gate A and its GitHub compatibility correction have been merged and verified through the manually approved workflow. Gate B branch protections are active; the real automated publication exercise remains the final validation step and requires separate explicit authorization naming the reviewed canonical manifest SHA-256. Do not use the tool to bootstrap its own publication. ADR-018 acceptance, publication and merge remain separate approvals.

The tool's real publication operation is blocked until Gate B protections are active and read back through both configured and effective-rule APIs. No bootstrap bypass exists. The stored ruleset JSON is inert. Gate B requires specific settings authorization, effective check/source/strictness verification and a separately authorized harmless PR demonstrating actual pending/passing behavior. Keep native auto-merge disabled for v1.

## Failures and data boundaries

Stop on divergence, changed content, stale heads, conflicts, missing permissions, API errors or uncertain write outcomes. Preserve local/staged/remote evidence. Never automatically retry writes, clean the index, amend commits, rewrite history, delete branches or weaken protections to recover.

Code integration does not apply schemas or authorize operational use. No delivery command runs non-test migrations, modifies development databases or volumes, deploys, or changes repository settings. See [Delivery automation](delivery-automation.md) for the operator procedure.

## Releases

No production release process is approved yet. Before releases, define versioning, release notes, provenance, artifact retention, environment promotion, rollback and incident ownership.

Do not require CI status checks in branch rules until the corresponding workflows exist and have been observed passing.
