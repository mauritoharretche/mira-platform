# ADR-018: Reviewed delivery automation and SHA-bound integration

- Status: Accepted
- Date: 2026-11-08
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context

Reviewed engineering increments use feature branches, complete patch review,
explicit publication approval, four CI gates and separate merge authorization.
Mechanical repetition should be automated without transferring human authority
to a file, command argument, mutable branch name or delayed merge request.

This decision concerns delivery, not application behavior or deployment. Accepted
ADRs 011–017 and their security, module ownership and migration gates remain intact.
Gate A implementation approval is not formal acceptance of this ADR.

## Decision drivers

- Bind publication to the exact reviewed content, including new files and modes.
- Preserve explicit human publication and integration authority.
- Reuse the locked Python runtime, standard library, Git and existing GitHub CLI.
- Fail closed on content, identity, CI, enforcement or remote-state uncertainty.
- Keep settings activation, non-test migration and deployment independent.

## Considered options

| Option | Consequences | Proposed disposition |
| --- | --- | --- |
| Manual runbook only | Minimal machinery, repeated checks remain manual | Bootstrap and recovery fallback |
| Standard-library local tool | Small operating footprint, deterministic fixtures, existing authentication | Selected |
| Native deferred auto-merge | Convenient but does not establish enduring approval of future head updates for a solo maintainer | Disabled in v1 |
| Independent bot or approval service | Could establish stronger separate authority, with new credentials and operating responsibilities | Deferred |

## Proposed decision

### Canonical content and external authority

A canonical UTF-8 JSON manifest identifies the repository, baseline, branch,
exact operations/paths, before/after SHA-256 and modes, complete patch and full
resulting-tree digest. It binds tooling/policy identity, public commit identities,
commit/PR text and actual validation evidence. Deterministic serialization rejects
duplicate keys, unsupported fields and non-finite values. Review artifacts remain
outside the repository and are not replaced silently.

An external, explicit maintainer decision must name the exact canonical manifest
SHA-256. Supplying that digest to a command is not proof of authorization. A
trusted operator verifies the external decision and supplies an owner-only record
of its action, approver, digest, reference and explicit statement. The tool checks
correspondence; it does not authenticate a human, validate a chat reference,
manufacture approval, or make a forged local file authoritative. No approve or
bootstrap-bypass operation exists. Changed content invalidates prior approval.

The eventual commit SHA is recorded after commit. Merge requires a separate
explicit human decision naming that exact published SHA, PR and current main.

### Local publication and trust boundary

Support prepare, publish, rules, ci, merge-preview and post-merge only. Publication
is dry-run by default. Preparation reads candidate content and emits complete
review artifacts without writing the Git index or objects.

Real execution requires exact branch/base/local and remote state, an empty index,
the exact reviewed changed/untracked set, scans, external approval correspondence,
trusted baseline tooling and live protection verification. Stage explicit literal
paths, inspect full staged blobs/modes/tree, preserve hooks, create one commit,
then verify its sole parent, tree, identities, message and clean working state.
Push a fixed verified SHA to one explicit feature ref without force, verify it,
and create the sanitized PR. Store external receipts around write boundaries.

No broad staging, automatic repair, rebase, destructive reset, amendment, write
retry, branch deletion, settings mutation or merge execution is implemented.
Unsupported files and transformations fail instead of invoking a fallback.

The workstation, reviewed tooling, Git configuration/hooks and operator are
trusted. This is not a sandbox against privileged local code or an administrator.
Targeted content scanning supplements human review; it is not a guarantee of
secret absence or independent authentication.

### Gate A bootstrap and Gate B enforcement

The first automation PR must be published and squash-merged through the existing
manually approved workflow. It cannot use its own publication command. Real
publication remains blocked until Gate B protections are active and effectively
verified, with no override. The running tool must match code already present in
the approved baseline; use an external trusted copy when changing the tool itself.

The stored protect-main JSON is inert. Separately authorized Gate B updates the
existing ruleset and verifies configured and effective enforcement: exact main
target, active status, no bypass actors, PRs, squash strategy, deletion and
non-fast-forward protection, and strict checks from GitHub Actions integration
15368 for backend, frontend and the Ubuntu/macOS toolchains. Unknown or additional
policy needs review. Verify actual pending/passing behavior on a separately
approved harmless PR before activating routine publication. Local configuration
or a push dry-run is not proof that the server enforces these conditions.

Zero native required approvals reflects the solo-maintainer constraint. It is
not server-side enforcement of the external human decision against the owner.
PR authors cannot approve their own PRs. Do not invent a second reviewer identity.

### CI and merge

Observe the exact workflow path, event, source/base, PR, run/suite, attempts, jobs,
GitHub Actions source and actual checkout. Jobs emit public revision metadata via
environment variables and structured output, without interpolating PR text into
shell commands. Distinguish source head from synthetic PR checkout.

Only all four completed/success conclusions pass. Missing, pending, skipped,
neutral, cancelled and failed checks do not. Observation has bounded commands,
pagination and a maximum 600-second deadline, and performs no workflow retries.
Carried-forward successes are correlated with their original execution timestamps,
steps and logs; reports distinguish record attempt from execution attempt.

Native auto-merge remains disabled. An expected-head condition when enabling a
deferred merge is not treated as durable approval of later writer pushes. The
tool only previews a human-controlled squash command with --match-head-commit;
it never executes it and includes no admin, auto-merge or branch deletion flags.
Any head/base change requires a new decision. Unexpected merge queues are blocked.

Post-merge verification checks the actual squash SHA, sole parent, approved tree,
current main and a new push workflow with all four jobs successful. Pending or
failed post-merge CI leaves verification incomplete, even after integration.

### Data, rollout and recovery

No application model, migration, dependency, stack, credential or deployment is
added. CI retains its four-job structure, immutable actions and read-only token.
Test automation uses disposable Git repositories and mocked GitHub data. Existing
backend/frontend gates remain required; test migrations retain their isolated
test-runner authorization only.

Merge, ADR acceptance and code publication never authorize non-test migrations,
development database/volume changes, deployment or operational use. GitHub settings
and branch cleanup require separate approval.

On partial failure, preserve index, commits, branches and remote evidence; stop.
Receipts identify the last attempted write and known SHA. Ambiguous push/PR
responses require read-back before any separately authorized continuation. There
is no automatic resume, rollback or retry; recovery itself requires review.
Prefer disabling tool consumption and a reviewed forward fix/revert PR. Never
weaken protections or delete history/data as automatic recovery.

## Consequences

The same reviewed content is mechanically checked through staging, commit and
publication without a new service or credential. Human decisions remain explicit.
Strict synchronization may require fresh review and CI when main advances. V1
does not handle binaries, submodules, symlinks or arbitrary Git transformations.
Solo-owner approval remains a trusted process rather than independent separation
of duties. More autonomous integration requires a separately approved design.

## Validation

Test deterministic manifests/full trees, tampering, unauthorized paths, staged
drift, hook interference, partial failures, network ambiguity, missing permissions,
configured/effective policy drift, job/source/checkout/attempt mismatches, deadlines,
stale PRs, conflicts and failed post-merge CI. Demonstrate real Gate A execution
refusal without modifying the repository index or publishing anything. Retain
the exact approved file allowlist and all existing locked quality gates.

This ADR does not assert tests passed. Record observed evidence in the review
report. Acceptance, publication, settings activation, merge and deployment remain
separate human approvals.

## References

- ADR-011 through ADR-017.
- [Ruleset APIs](https://docs.github.com/en/rest/repos/rules).
- [GitHub CLI merge options](https://cli.github.com/manual/gh_pr_merge).
- [Native auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request).
- [Pull request workflow identity](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).
