# ADR-012: Modular monolith and API boundaries

- Status: Accepted
- Date: 2026-11-06

## Decision

Use a modular monolith with versioned HTTP APIs. The backend owns transactional state and modules communicate through explicit application interfaces rather than direct writes to another module's tables.

## Consequences

The initial foundation exposes only technical health endpoints. Product modules, external integrations, and asynchronous processing are introduced only when their requirements and approvals are available.

## Validation

Database readiness is tested separately from liveness. Health behavior is covered in backend integration tests and consumed through a server-side frontend proxy.
