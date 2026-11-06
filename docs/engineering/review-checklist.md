# Pull Request Review Checklist

## Correctness
- [ ] Does the change solve the stated problem?
- [ ] Are boundary cases and failure paths handled?
- [ ] Are state transitions and concurrency behavior correct?

## Design
- [ ] Are responsibilities and module boundaries clear?
- [ ] Is complexity justified?
- [ ] Are public contracts backward-compatible or versioned?

## Data and security
- [ ] Are authorization checks server-side?
- [ ] Is sensitive data minimized and protected?
- [ ] Are secrets absent from the diff and logs?
- [ ] Are migrations safe and reversible where possible?

## Quality and operations
- [ ] Are tests meaningful and deterministic?
- [ ] Are logs, metrics and traces useful without leaking data?
- [ ] Are documentation and runbooks updated?
- [ ] Is rollout/rollback understood?
