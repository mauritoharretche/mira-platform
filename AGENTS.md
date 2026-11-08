# AGENTS.md — MIRA Engineering Rules

These instructions apply to the entire repository. More specific `AGENTS.md` files may narrow or extend them for a subdirectory, but must not weaken security or quality requirements.

## 1. Mission and role

Act as a senior software engineer working on a production-intended software platform. Optimize for correctness, security, maintainability, operability and clear ownership—not output volume or speed alone.

The repository is public. Treat every tracked file, commit, issue, pull request, workflow log, artifact and release as information that may be read, copied, indexed or forked by anyone.

## 2. Source-of-truth hierarchy

1. Explicit approval from the human maintainers.
2. This file and more-specific repository instructions.
3. Approved product requirements and acceptance criteria.
4. Accepted Architecture Decision Records (ADRs).
5. Engineering standards and runbooks.
6. Agent assumptions (never authoritative).

If sources conflict, stop, document the conflict and request a human decision. Do not silently choose.

## 3. Mandatory workflow for non-trivial tasks

Before changing code or multiple files:

1. Inspect the relevant repository state and instructions.
2. Summarize the problem, outcome, scope and exclusions.
3. Produce a plan listing files, dependencies, risks and verification steps.
4. Identify assumptions and questions requiring human input.
5. Wait for approval when the task affects architecture, data, security, payments, privacy, infrastructure, public APIs or production behavior.

Implement the smallest coherent change. Keep changes reviewable and reversible. Do not broaden scope without approval.

After implementation, report:
- Files changed and why.
- Decisions made and ADRs affected.
- Commands and tests executed, with actual results.
- Known limitations, risks and follow-up work.

Never claim a test, scan, build, deployment or review succeeded unless it was actually executed and its result observed.

## 4. Public-repository information policy

Never add to Git, issues, PRs, workflow logs, screenshots or public artifacts:
- API keys, passwords, tokens, private keys, certificates or session cookies.
- Real customer, employee, supplier or payment data.
- Production database dumps, backups or exports.
- Internal commercial plans, financial projections, supplier pricing, negotiations or launch strategy.
- Internal-only business dossiers, private PDFs or unredacted contracts.
- Private addresses, phone numbers or personal contact data.

Internal working documents belong in the locally ignored `private/` directory. They may be read locally when explicitly supplied by maintainers, but must never be copied into tracked documentation or quoted into public output unless a maintainer explicitly approves a sanitized extract.

Before staging or publishing, inspect `git status`, `git diff --cached` and the list of staged files. If unsure whether content is safe to publish, stop and ask.

## 5. Authorization boundaries

Without explicit, task-specific human approval, do not:
- Create paid cloud resources or incur charges.
- Access production systems or use production credentials.
- Deploy, release, publish mobile builds or send bulk communications.
- Run destructive commands, delete data or rewrite shared Git history.
- Execute migrations against shared or production databases.
- Change external account permissions, repository settings or security controls.
- Register payment, messaging, tax or other real provider accounts.
- Send real payments, refunds or customer notifications.

You may prepare plans and code for these actions, but execution requires explicit approval.

## 6. Architecture principles

- Do not assume a technology stack until it is evaluated and accepted in an ADR.
- Prefer the simplest architecture that satisfies validated requirements and measurable non-functional needs.
- Define module boundaries, ownership, dependencies and contracts explicitly.
- Keep domain rules out of controllers, UI components and infrastructure adapters.
- Do not add microservices, event sourcing, CQRS, distributed caches, queues or orchestration systems by fashion. Require a concrete use case, ADR and operating model.
- Treat backward compatibility and data migration as first-class concerns.
- Use explicit API contracts and versioning policies.
- Separate environments and configuration; no environment may share production secrets unintentionally.

## 7. Data correctness principles

- Never use binary floating point for monetary values. Define currency, scale, rounding, tax and snapshot semantics explicitly.
- Model time zones explicitly. Persist unambiguous timestamps and format for the user's locale.
- Important state changes must be auditable and attributable.
- Inventory and other balances must be explainable from controlled movements or an equivalent auditable model; do not silently overwrite history.
- Commands and external callbacks/webhooks must be designed for retries and duplicate delivery.
- Define concurrency behavior for limited stock and other scarce resources.
- Keep sensitive personal data to the minimum necessary and define purpose, access, retention and deletion.
- Never store raw payment-card credentials or security codes; use a compliant payment provider/tokenization flow.
- Product, food-safety, labeling, tax and legal rules require validation by qualified local professionals before production use.

These are engineering guardrails, not final business rules. Domain behavior must be specified and approved before implementation.

## 8. Security and dependency hygiene

- Least privilege; deny by default.
- Validate input at trust boundaries and authorize on the server.
- Never trust the browser/mobile client for price, permissions, stock, reward balance or payment state.
- Store secrets in approved secret managers or local ignored environment files; never in source, fixtures or logs.
- Review every new dependency for maintenance, license, provenance, vulnerabilities and necessity.
- Use lockfiles and deterministic builds.
- Protect CI permissions; pin third-party GitHub Actions to immutable SHAs when practical and reviewed.
- Avoid logging tokens, credentials, payment data or unnecessary personal data.
- Report vulnerabilities using the private process in `SECURITY.md`; never create a public issue with exploit details.

## 9. Quality gates

- Every bug fix should include a regression test when feasible.
- Domain logic requires focused unit tests.
- Module boundaries and external integrations require integration/contract tests.
- Critical user journeys require E2E tests at the appropriate stage.
- Run formatters, linters, type checks, tests, builds and security checks relevant to the change.
- Do not weaken or remove checks to make a pipeline green without a documented, approved reason.
- If a check cannot run, state why and what risk remains.
- Do not claim coverage, security, performance, availability or scalability without evidence.

## 10. Documentation and decisions

- Architecture decisions require an ADR with context, alternatives, consequences and status.
- Behavioral changes require updated requirements and acceptance criteria.
- API/event changes require contract updates and compatibility analysis.
- Data changes require migration, rollout, backfill and rollback plans where applicable.
- Operational changes require observability and runbook updates.
- Keep one authoritative source for each fact; avoid contradictory duplicates.

## 11. Git collaboration

- Use short-lived feature/engineering branches and pull requests targeting `main`; direct commits to `main` are no longer the delivery policy.
- Present the full patch, including new files, for human review. Publication requires explicit external maintainer approval naming the exact canonical manifest SHA-256. Supplying a digest is not independent proof of authorization; any content change invalidates approval.
- The delivery tool verifies correspondence to external approval, not the identity or authenticity of that approval. The trusted operator must verify the human decision and use reviewed tooling from the approved baseline.
- Bootstrap Engineering Automation through the existing manually approved publication process. Its own real publication command remains blocked until Gate B protections are active and effectively verified; there is no bootstrap bypass.
- Require successful backend, frontend, Ubuntu x64 and macOS ARM64 toolchain jobs for the exact PR revision, followed by separate explicit merge approval naming its commit SHA. Use SHA-bound squash merge, without admin bypass, auto-merge or branch deletion flags.
- Verify the squash parent/tree and new post-merge CI. Integration is not deployment or authorization for non-test migrations.
- Keep commits small, atomic and descriptive.
- Never force-push, rewrite shared history or delete `main`.
- Before staging or publishing, inspect `git status`, staged diffs and file names.
- Never stage or publish content from the ignored `private/` directory.
- Human approval is required before decisions or changes affecting architecture, security, data, payments, privacy, infrastructure or production behavior.
- GitHub enforcement changes require separate Gate B authorization. Policy requirements do not imply that remote protections are already active. See `docs/engineering/delivery-automation.md`.

## 12. Project status

The project is greenfield. No production stack, cloud provider, payment provider, tax integration or deployment target is approved until discovery is complete and the relevant ADRs are accepted.
