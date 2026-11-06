# Engineering Plan Standard

Every non-trivial task must have a written plan before implementation.

## Plan template

- Status: Proposed | Approved | In Progress | Blocked | Done
- Human owner:
- Technical owner:
- Issue / ADR:
- Date:

### 1. Problem and context
What problem is being solved, for whom, and why now?

### 2. Observable outcome
Describe behavior and measurable results. Avoid vague goals.

### 3. Scope
- Included:
- Excluded:

### 4. Requirements and invariants
Identify actors, permissions, states, domain rules and source of truth.

### 5. Design
Modules, contracts, data model, flow, alternatives and trade-offs.

### 6. Files and components
List expected changes and rationale.

### 7. Data and integrations
Schema changes, migrations, providers, webhooks, compatibility and rollout.

### 8. Security and privacy
Threats, controls, sensitive data and access requirements.

### 9. Verification
Unit, integration, contract, E2E, performance, security and manual tests as applicable.

### 10. Acceptance criteria
Verifiable success and negative/boundary scenarios.

### 11. Risks and mitigations
Probability, impact, detection, mitigation and contingency.

### 12. Rollout and rollback
How to release, observe, disable and recover safely.

### 13. Dependencies and open questions
Do not invent answers.

### 14. Evidence and approval
Commands executed, results, changed files, documentation and human approval.

## Rules

- A Proposed plan is not authorization to execute.
- Approval must identify the exact scope.
- Material scope changes require a new approval.
- A blocked task must explain the blocker; do not compensate by expanding scope.
