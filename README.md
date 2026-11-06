# MIRA Platform

MIRA Platform is a greenfield software project exploring an integrated digital platform for specialty retail and related customer experiences.

> **Status:** Discovery and engineering foundation. Not production-ready. APIs, architecture, features and deployment model are subject to review and change.

## Repository goals

- Maintain a high-quality, reviewable engineering codebase.
- Make architecture and operational decisions explicit.
- Build incrementally with security, data integrity, accessibility and reliability in mind.
- Keep internal business information and real customer data out of this public repository.

## Important: public repository, license status

This repository is public, but **no open-source license has been selected yet**. Public visibility does not automatically grant permission to reuse, redistribute or create derivative works. See [`LICENSE-STATUS.md`](LICENSE-STATUS.md). Do not add a license until the maintainers decide what rights they intend to grant.

## Local setup

1. Clone the repository.
2. Open the repository root in VS Code.
3. Read `AGENTS.md`, `CONTRIBUTING.md` and the engineering standards under `docs/engineering/`.
4. Internal, non-public reference material may be placed locally under `private/`. This directory is git-ignored and must never be force-added.
5. No application stack is approved yet. Do not create application code until Phase 0 discovery and architecture decisions are reviewed.

## Repository map

- `AGENTS.md` — working rules for humans and coding agents.
- `PLANS.md` — standard for planning non-trivial changes.
- `CONTRIBUTING.md` — contribution and pull-request process.
- `SECURITY.md` — vulnerability reporting policy.
- `docs/architecture/` — architecture principles and ADR templates.
- `docs/engineering/` — quality, review and delivery standards.
- `docs/security/` — public-repository security checklist.
- `private/` — local-only internal references; never committed.

## Development status

The repository currently contains governance and documentation only. Technology selection, architecture, deployment, integrations and production readiness require explicit discovery and human approval.

## Maintainers

Repository owner and collaborators are listed in GitHub repository settings. Do not publish personal contact information in this README.
