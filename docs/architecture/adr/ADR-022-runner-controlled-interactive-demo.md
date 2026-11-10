# ADR-022: Native-runner-controlled interactive demonstration

- Status: Accepted
- Date: 2026-11-09
- Human maintainer and technical owner: Mauro Harretche
- Implementing agent: Codex

## Context and alternatives

The accepted operator interface supports the Phase 1.14 browser journey, but the
automated E2E harness is bounded to test execution, uses internal credentials and
stops its servers afterwards. It is not an interactive demonstration session.
Starting Next alone does not establish backend authentication or database safety.

Choose a dedicated supervisor and one native Django LiveServerTestCase. An
independent Django runserver, unbounded pause in E2E, test-settings-only routing,
new database, custom authentication or protection bypass is not selected. Keep the
existing automated browser harness and all business interfaces unchanged.

## Accepted decision

A local-only standard-library launcher acquires a cooperative owner-only lease,
requires explicit exclusive-use acknowledgement and rejects CI, wrong settings,
frontend drift and durations outside 1..60 minutes. Default interactive duration
is 30 minutes. A separate verify command creates only a short verification session,
not a long-running human demonstration. It does not report manual readiness.

The supervisor starts a native-runner child in its own process group. An inherited
private pipe and lease FD establish launcher-controlled entry; a bare environment
flag cannot activate the interactive TestCase during normal discovery. These are
local process controls, not a new HTTP authentication system or a sandbox against
a malicious workstation owner. The child retains the lease if the supervisor dies.

Install a default-connection guard before Django setup, refusing every default
destination outside PostgreSQL test_mira, and allow only the native runner's
loopback postgres maintenance connection. Validate exact test settings, explicit
TEST.NAME, no mirrors or destination overrides, approved loopback host/port and
initial destination separation. Do not rewrite NAME to select the test database.
The native runner alone selects/creates/migrates test_mira using keepdb/noinput.
Before starting the native listener or creating fixtures, validate configured,
effective and active identity and directly assert current_database()=test_mira.
Preserve the existing pre-session HTTP database fence without changes.

The native TestCase creates exactly one fictional, non-staff, non-superuser User
with the eleven permissions needed for existing creation/read, category,
activation and initial receipt/read interfaces. No pricing, measurement, stock
adjustment, user administration or unrelated permissions. Passwords use secrets
and Django's validators/hashers. Store access data only in a newly created 0600
file inside the 0700 external session directory; never print its contents or pass
them to Next. The console may identify the access-file location, not credentials.
Backend output is redacted and private. No credential, cookie, HAR or trace export.

Build/start Next using the existing toolchain, filtered environment and loopback
origin. Both build and frontend use supervised owned process groups. Detect tracked
frontend drift; preserve/report it without automatic restoration. Only report
INTERACTIVE_DEMO_READY after same-origin readiness and test-database verification.
An independent monotonic deadline starts at readiness and is bounded to 60 minutes.
Existing Django session, CSRF, expiry, abuse controls and permissions remain intact;
only the already-approved loopback HTTP test cookie exception is scoped locally.

## Shutdown and abnormal recovery

Accept Ctrl+C/SIGTERM, a private STOP marker, duration expiry and failures. The
child also detects supervisor loss. Close Next/build groups created for this run
using recorded UID/PID/group/start identity, never killall or unrelated browsers.
Stop the native listener before native fixture teardown. Verify both ports are
released; verify account/session disappearance after native fixture cleanup while
the connection is still test_mira. Delete the credential file and write separate
server, fixture and supervisor evidence. No manual flush/drop/reset/recreation.

Execution success and verified cleanup are separate: a failed operation exits
nonzero even if native fixture cleanup, process/port closure and credential removal
are verified. Only unverified cleanup leaves the lease incomplete.

The recovery command reads only owner-only session records, verifies PID identities,
and stops only recorded groups. It performs no database operations. A dead runner
or SIGKILL without verified native teardown must never be described as cleaned:
record incomplete fixture/session cleanup, even if ports and credential file were
removed. Incomplete lease state prevents a fresh start pending human review; there
is no bypass or automatic database repair. Do not claim guaranteed cleanup if all
supervising processes are forcibly killed or external files are lost.

The lock coordinates these launchers, not unrelated test processes/SQL clients.
Do not run other suites against test_mira while a demonstration is active. Data is
fictional and ephemeral; normal native cleanup does not promise persistence.

## Consequences and boundaries

Manual Chrome can use the real operator interface without new HTTP routes,
business services, models, migrations, settings, dependencies or CI changes.
No production exposure, deployment, real account or development mira operation.
The supervisor adds local lifecycle complexity and temporary plaintext credentials
protected by OS ownership; same-user compromise is outside its trust guarantee.
Keep the existing moderate minimatch full-audit failure and isolated-demo-only
risk disposition. Production audit was previously clean and must be rechecked.
Existing stale runbook ADR references are unchanged; accepted ADRs are authoritative.

## Validation and approval gates

Preserve all existing domain, delivery, frontend and automated Chromium scenarios.
Test opt-in/CI rejection, wrong routing before connection, exact grants/denials,
file permissions, secret redaction/environment filtering, monotonic timeout, STOP,
signals, startup failure, owned PID/port cleanup and abnormal recovery without DB
cleanup claims. Use a short native verification and the unchanged E2E separately,
never concurrently on test_mira. Inspect frontend bytes before/after execution.

Implementation approval does not accept this ADR or authorize a long-running human
session. Present the entire six-file patch, canonical manifest and actual evidence.
Human technical acceptance, permission to launch the manual demo and any later
publication/merge remain separate gates. No Phase 1.15 work is implied.

## References

- ADR-013: native Identity authorization and trusted principals.
- ADR-018: external review and SHA-bound delivery approval.
- ADR-020: same-origin sessions and isolated operator workflow.
- ADR-021: operational catalog navigation and classification.
