# Reviewed delivery automation v1

Gate A supplies local tooling and tests. ADR-018 is Accepted. This document does
not activate GitHub protections, approve any content, or authorize publication.

## Bootstrap first

Publish the first engineering automation PR through the existing manually
approved process: complete patch, explicit staging/commit/push/PR authorization,
four CI successes, separate SHA-specific merge approval, squash merge and fresh
post-merge CI. Preserve its branch. Do not use the new publication command to
bootstrap itself.

`publish --execute` always reads configured and effective GitHub protections
before staging. The active protect-main ruleset must match the reviewed policy,
including required PRs, four source-bound strict checks and no bypass actors.
The running tool must also match the copy already committed in the approved
baseline. There is no bootstrap override. Local JSON is not evidence of remote
enforcement.

Gate B is separate: authorize the exact settings delta, read back actual rules,
verify all four contexts and GitHub Actions source, and observe a separately
approved harmless PR while pending and passing. Its publication and merge still
need their own approvals. Keep auto-merge and automatic branch deletion disabled.

## Runtime and commands

Use the existing locked Python toolchain, Git and authenticated GitHub CLI. No
additional packages or credentials are needed. Never execute a modified tool to
approve its own changes. For routine delivery use a reviewed copy whose SHA-256
matches the tool committed in the approved main baseline; it may reside outside
the working tree when the proposed PR modifies the tool itself.

Invoke with `mise exec -- python -I -B scripts/engineering/delivery.py`, or the
equivalent trusted external copy. Six operations are supported:

| Operation | Effect |
| --- | --- |
| prepare | Reads Git/content; creates review artifacts outside the repository |
| publish | Dry-run by default; execution requires all independent gates |
| rules | Read-only configured/effective enforcement validation |
| ci | Read-only exact-PR CI verification, optionally bounded observation |
| merge-preview | Validates and prints a command; cannot execute a merge |
| post-merge | Read-only squash parent/tree and fresh main-CI verification |

The tool never fetches, switches branches, resets data, updates settings, deploys,
runs migrations or deletes branches. Resolve stale local main/origin refs through
a separately approved synchronization procedure before preparing a revision.

## Prepare a review

Start on the approved feature branch with HEAD equal to clean synchronized main
and origin/main. The index must be empty. Expected implementation edits are
allowed; every changed or non-ignored untracked path must be in the allowlist.

Provide an external JSON array of exact paths, commit-message text, sanitized PR
body and actual validation evidence. Choose a new external output directory:

```bash
mise exec -- python -I -B scripts/engineering/delivery.py prepare \
  --branch "$FEATURE_BRANCH" --base "$APPROVED_BASE_SHA" \
  --allowlist "$EXTERNAL_ALLOWLIST" --commit-message "$EXTERNAL_MESSAGE" \
  --pr-title "$REVIEWED_TITLE" --pr-body "$EXTERNAL_PR_BODY" \
  --evidence "$EXTERNAL_EVIDENCE" --out-dir "$NEW_EXTERNAL_REVIEW_DIRECTORY"
```

Artifacts are owner-only. Output includes manifest.json, review.patch and
review-summary.json. The complete patch includes new files and explicit deletions.
Review public commit identity, PR text and evidence as well as code. Artifact
directories and files are created exclusively; existing material is not replaced.

## Canonical identity and approval

Canonical JSON uses sorted keys, UTF-8, compact separators, no non-finite values,
no duplicate keys and exactly one terminal newline. The manifest's SHA-256 is
the content-approval identity; it is not a signature or credential.

The manifest binds repository ID/name, source branch, exact base, all changed
paths/operations, before/after hashes and modes, complete patch digest and full
resulting-tree digest. The latter hashes the canonical map of every tracked path
to its mode and content SHA-256, including unchanged tracked files. It also binds
running-tool/policy digests, author/committer identity, commit message, PR text and
actual evidence. There is no timestamp or random value in this canonical payload.

Git's eventual tree OID and commit SHA are recorded after commit. A content digest
is not a commit SHA: commit timestamps make the latter unknown before creation.
Subsequent merge approval names the actual published SHA. Any changed content,
mode, metadata, tool or policy invalidates the previous manifest approval.

The maintainer must explicitly approve the exact canonical digest **outside the
tool**. A trusted operator verifies that decision and supplies its reference in
an external owner-only JSON record with exactly these fields:

```json
{
  "action": "publish",
  "approver": "Mauro Harretche",
  "manifest_sha256": "REVIEWED_64_HEX_DIGEST",
  "reference": "reference to the actual external maintainer approval",
  "statement": "I authorize publication of canonical manifest SHA-256 REVIEWED_64_HEX_DIGEST."
}
```

Do not generate such a record unless the actual human decision exists. The tool
checks correspondence and required explicit wording. It cannot authenticate the
record's author, verify an external conversation, or prevent a trusted operator
from fabricating a statement. File ownership is not independent human approval.
There is deliberately no approve command, implicit approval or self-approval flag.

## Dry-run and publication

Dry-run checks the current candidate against the manifest and live remote refs;
it does not stage, commit, push, create a PR or write Git objects/index entries:

```bash
mise exec -- python -I -B scripts/engineering/delivery.py publish \
  --manifest "$EXTERNAL_MANIFEST" --approved-manifest-sha256 "$REVIEWED_DIGEST"
```

The digest argument identifies content, even in dry-run; it is never sufficient
authorization for execution. After Gate B and explicit external approval only:

```bash
mise exec -- python -I -B "$TRUSTED_DELIVERY_TOOL" publish \
  --manifest "$EXTERNAL_MANIFEST" --approved-manifest-sha256 "$REVIEWED_DIGEST" \
  --approval-record "$EXTERNAL_APPROVAL_RECORD" \
  --out-dir "$NEW_EXTERNAL_RECEIPT_DIRECTORY" --execute
```

Do not run this real operation during Gate A. A negative Gate A probe must fail
before any writer operation; tests exercise writers only in disposable fixtures.

Execution stages explicit literal paths only, checks every staged blob/mode and
the complete staged tree, saves staged.patch, checks whitespace and commits with
hooks enabled. It verifies parent, tree, message, identities and clean working
state after hooks. A fixed commit SHA is pushed to one explicit ref without
force, and read back before creating the reviewed PR. The final receipt names the
actual commit/tree and PR URL. No broad staging or automatic corrective writes.

## Rules, CI and merge

The inert specification is `.github/rulesets/protect-main.json`. Read-only check:

```bash
mise exec -- python -I -B "$TRUSTED_DELIVERY_TOOL" rules \
  --policy .github/rulesets/protect-main.json
```

Both the named active ruleset and effective rules applying to main must agree:
PR required, squash only, deletion/non-fast-forward protection, strict checks,
no bypass and exact GitHub Actions integration ID 15368 for:

- backend
- frontend
- Toolchain (ubuntu-latest)
- Toolchain (macos-15)

GitHub returns two explicit pull-request parameters that the local policy also
declares: `required_reviewers: []` and
`require_extra_approval_for_unattributed_changes: true`. The empty reviewer list
adds no reviewer/file-pattern requirements. GitHub's [REST rules
documentation](https://docs.github.com/en/rest/repos/rules) specifies that the
extra-approval flag requires another review for unattributed Copilot pull requests
only when a nonzero approval count is required. It has no effect with this
policy's `required_approving_review_count: 0`.

Compare the complete configured and effective parameter maps exactly, including
these two values. Missing, changed or additional unexpected parameters fail
closed; do not filter unknown fields or normalize away server-added values.
Further API schema changes require human review.

Unknown/additional rules and API schema drift stop for review. Zero GitHub
approving reviews reflects the solo-maintainer constraint; it does not enforce
external human review against the owner. The owner must perform the explicit
SHA-bound merge decision. No admin bypass or native auto-merge is supported.

```bash
mise exec -- python -I -B "$TRUSTED_DELIVERY_TOOL" ci \
  --pr "$PR_NUMBER" --expected-head "$APPROVED_HEAD_SHA" \
  --expected-base "$APPROVED_MAIN_SHA" --watch --timeout 600
```

Observation is at most 600 seconds, with bounded read calls and pagination. Only
matching workflow path, event, source/base/PR, run, suite, attempt, job and checkout
evidence may pass. Each job emits public revision identity without shell expansion
of PR text. Synthetic PR checkout and source-head identities are kept distinct.
Missing, pending, skipped, neutral, cancelled or failed checks never pass. No
workflow reruns, automatic code fixes or write retries are issued.

GitHub may create new attempt records for successful jobs carried forward from
an earlier attempt. Identical original timestamps, runner, steps and success are
matched to the original execution record/log. Reports distinguish record attempt
from execution attempt; an older success never substitutes for a newer failure.

After all gates pass, merge-preview prints only:

```bash
gh pr merge "$PR_NUMBER" --repo mauritoharretche/mira-platform \
  --squash --match-head-commit "$APPROVED_HEAD_SHA"
```

Use merge-preview with `--pr`, `--expected-head` and `--manifest`. Obtain separate
explicit approval naming this PR, head and current main immediately before the
human-controlled command. No branch deletion flags, bypass or auto-merge. Any new
head/base invalidates the preview and approval; do not update/rebase automatically.

Use post-merge with the same identity arguments, optionally `--watch --timeout
600`, to verify actual squash SHA, sole parent, approved tree and a new successful
push workflow on main. A successful PR run is not post-merge verification. A
pending or failed push run leaves verification incomplete. Advanced main is
reported as a stop condition rather than reset locally or remotely.

## Partial failures and limits

Publication writes external phase receipts before staging, commit, push and PR
creation. A failure preserves the index, local commit, branch and any remote
effects. stopped.json and prior receipts identify the last attempted operation.

- Commit failure: inspect staged state; do not automatically unstage or reset.
- Commit exists but tree/hook verification fails: do not push or amend blindly.
- Push timeout/error: read the exact remote ref; a timeout does not prove rejection.
- PR creation ambiguity: inspect PRs for that exact source revision before any
  separately authorized continuation. Do not create a duplicate.
- API/permission failure: stop without changing credentials or escalating rights.

There is no automatic resume/retry command. Repeating publish after a partial
commit fails its original-base/empty-index gates. Human inspection and a reviewed
recovery procedure are required; receipts are evidence, not permission to repair.
Do not weaken protections, rewrite history or delete data to make a retry pass.

V1 is for a trusted local workstation and reviewed Git configuration/hooks. It
does not sandbox malicious hooks, prevent an administrator from ignoring policy,
or cryptographically authenticate human decisions. Content matching and staged/
committed checks detect drift; targeted scans do not guarantee secret absence.
Unsupported binary files, symlinks, submodules, Git transformations and sensitive
paths require a separately reviewed manual process. No skip-security override.

## Verification and data preservation

Run standard-library fixture tests with the locked Python interpreter:

```bash
mise exec -- python -I -B -m unittest discover -s tests/engineering -p 'test_*.py'
```

Tests use temporary repositories, local bare remotes and mocked GitHub responses.
Only those fixtures permit test staging/commit/push. The real repository and
GitHub remain unmodified during Gate A verification. Existing backend/frontend
quality gates remain required; no new dependency or fifth CI job is introduced.

Delivery does not migrate development databases, reset volumes, deploy or enable
operational use. Only the existing independently authorized test runner may apply
test migrations in isolated test_mira. Non-test schemas, deployments, settings and
recovery actions retain separate approvals. Stop for review on unexpected state.
