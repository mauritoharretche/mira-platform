# Runner-controlled interactive demo

Status: technically reviewed; ADR-022 Accepted. Human/technical owner: Mauro
Harretche. This runbook describes an opt-in fictional demonstration, not deployment
or permission to launch a human session before its separate approval.

## 1. Problem and context

Automated E2E owns short-lived servers and internal credentials; it cannot keep a
manual Chrome session available. Next dev alone is not a verified backend demo.

## 2. Observable outcome

A supervised native test runner can keep the existing operator interface available
on a reported loopback URL for a bounded manual session, then verify its closure.
No READY message is acceptance evidence after the corresponding processes exit.

## 3. Scope

Reuse login/logout, catalog pagination, product/initial Variant creation,
category creation/assignment/removal, administrative activation, initial EACH
receipt, stock and movements. No pricing/measurement editor, sales, new business
operation, HTTP control endpoint, auth architecture or production exposure.

## 4. Requirements and invariants

Use the approved locked runtimes and existing PostgreSQL at loopback port 55432.
Only config.settings.test and explicit TEST.NAME=test_mira without mirror/routing
override are allowed. The launcher refuses an already-selected test destination;
the native runner must select it. A connection guard blocks default connections
outside test_mira even during setup. SQL/current active identity are verified
before listener/fixture creation. Development mira and volumes are never touched.

The new TestCase is skipped in ordinary tests; enabling an environment variable
alone is insufficient. CI is rejected. The supervisor uses a cooperative lease;
confirm exclusive use and do not run any other suite/client on test_mira. This lock
cannot prevent unrelated programs from ignoring the rule.

## 5. Design and session behavior

The supervisor owns one native-runner child. The native LiveServerTestCase owns
Django's backend, fixtures and teardown; Next is a loopback child process, not a
standalone operational backend. Builds and servers have owned process groups and
recorded PID/UID/group/start identities. Next receives a filtered environment with
only its generated API origin, not backend/database/demo credentials. No settings
files or environment files are edited or copied.

Default duration is 30 minutes, configurable 1..60. A monotonic readiness deadline
is independent of wall-clock changes. Ctrl+C, SIGTERM, private STOP marker, timeout,
supervisor loss and failures request shutdown. Keep the controlling terminal open.
There is no automatic POST retry or bypass of existing authorization/CSRF.

## 6. Files and components

The launcher and two dedicated test modules implement control/verification.
The original automated E2E harness, Playwright settings and application code stay
unchanged. ADR-022 and its Decision Log entry are Accepted.

## 7. Data and credentials

One fictional active non-staff/non-superuser account receives exactly:

- catalog.add_product, catalog.add_variant, catalog.view_product, catalog.view_variant;
- catalog.add_category, catalog.view_category, catalog.set_product_category, catalog.set_product_active;
- inventory.receive_stock, inventory.view_stockposition, inventory.view_stockmovement.

No category/product/history is fabricated for the human demo. Create fictional
data through the UI. The strong random validated password exists only in backend
memory/hash and the private access file; never paste it into the conversation,
logs, screenshots, terminal transcripts or Git. The session directory is 0700;
access.json is 0600. The launcher prints only its location. Open/read that file
privately yourself. Do not save the password in Chrome or export session state.
Normal native teardown removes the test fixtures; there is no persistence promise.

## 8. Security and privacy

Use a dedicated Chrome incognito window, not real users/data. Preserve native
sessions, CSRF, permission checks, absolute expiry, login throttling, safe errors
and no-store. Only the existing loopback HTTP test cookie exception is used.
The twelve operator paths/rewrites remain unchanged. Active does not mean sellable.

## 9. Verification commands

After verifying the approved toolchain, run only the native isolated suites and
short verification, never concurrently:

```bash
# From apps/api; runtime loading must not print or copy the root .env.
mise exec -- uv run --env-file ../../.env python manage.py test --keepdb --noinput --settings=config.settings.test
mise exec -- uv run --env-file ../../.env python ../../scripts/engineering/run_interactive_demo.py verify --exclusive-test-database
MIRA_RUN_BROWSER_TESTS=1 mise exec -- uv run --env-file ../../.env python manage.py test system.tests.test_browser_workflow --keepdb --noinput --settings=config.settings.test
```

The existing guarded external runner may wrap the full native suite. verify uses a
short ten-second session, exercises native login/logout through Next and closes;
it does not leave a manual demo open. Repeat only after reviewing any failure.
Retain locked installs, delivery tests, lint, typecheck, Vitest, build, both audits
and migration-drift checks. Verify is not a production health/readiness assertion.

## 10. Manual launch and acceptance

Only after explicit human session-launch approval, from apps/api:

```bash
mise exec -- uv run --env-file ../../.env python ../../scripts/engineering/run_interactive_demo.py start --minutes 30 --exclusive-test-database
```

The console reports the actual URL, access-file location and session directory
only after successful readiness. Open that URL in installed Chrome and privately
read access.json for login. Explore product creation, uncategorized filtering,
category assignment, activation/deactivation, initial receipt and movement reads.
Use fictional inputs. Evaluate desktop and Chrome responsive mobile emulation;
call the latter an emulated viewport, not a real phone. Screenshots must be genuine
browser captures outside the repo, after hiding credentials and sensitive panels.
No real browser screenshots are implied by automated Chromium results.

## 11. Risks and shutdown

Use Ctrl+C, or from a separate terminal at repository root:

```bash
mise exec -- python -I -B scripts/engineering/run_interactive_demo.py stop --session /PRIVATE/TEMP/SESSION_PATH
```

Use the actual reported session path; the placeholder is not executable guidance.
The stop command verifies ownership and PID identities, never kills Chrome globally,
and never accesses a database. Close only your dedicated browser window afterwards.
Startup/build/server failures stop the session. SIGKILL may prevent native cleanup;
ports/process termination is not proof of fixture/session cleanup. Do not claim
success unless native cleanup and both port-release checks completed.

## 12. Recovery and rollout

Read supervisor-result.json, closed.json and private logs. A missing native cleanup
record means incomplete teardown. Recovery can stop recorded groups and remove the
credential file, but cannot certify or repair the database. A stale incomplete
lease blocks another start; request human review instead of deleting the lock,
resetting data, dropping/recreating a database or editing migration history.
Do not automatically restore any frontend drift: preserve its diff for review.
Credentials and runtime records are not public artifacts; retain only sanitized
lifecycle evidence and delete no unrelated files/processes.

## 13. Dependencies and limitations

No new package, runtime, cloud resource or provider. Use existing installed Chrome
for manual review; do not install it automatically. The preexisting moderate
minimatch advisory remains an explicitly failing full development audit, accepted
only for isolated fictional demonstration; production audit was previously clean.
Do not suppress warnings or change versions. Old ADR-020/ADR-021 runbook status
references remain historical inconsistencies; accepted ADRs are authoritative.
MFA and real administrative exposure remain separately gated.

## 14. Evidence and approval

Report actual test counts, failures, secret/mode/environment checks and process/
port/fixture cleanup separately. The private runtime logs must not contain the
password. A successful verification/closure produces DEMO_VERIFIED_CLOSED. An operation
that fails but has positively verified native cleanup reports DEMO_FAILED_CLEANED
and exits nonzero; it does not poison a clean lease or claim the operation passed.
Unverified teardown produces DEMO_TEARDOWN_INCOMPLETE and blocks reuse for review.
ADR-022 and this six-file implementation have received human technical acceptance.
Manual launch, publication, merge, deployment and non-test migrations still require
separate explicit authorization.
