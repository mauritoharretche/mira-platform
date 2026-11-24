# MIRA Platform

MIRA Platform is a modular retail management platform under active development.

> **Status:** Local operational increments for Catalog, Inventory and Procurement are implemented. Production operation, external integrations, deployment and non-test migrations require separate approval.

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
5. Install the locked local toolchain with `mise install --locked python node pnpm uv`, then follow [`docs/engineering/local-development.md`](docs/engineering/local-development.md). Do not use production credentials or data.

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

The Django/Next.js operator workspace includes products, variants, pricing, measurement declarations, stock adjustments, purchase receipts, lots and physical locations. INV-03 adds commercial availability and transactional reservations for future Orders/POS integration; it does not implement sales. See [commercial inventory](docs/engineering/commercial-inventory.md) and the [decision log](docs/architecture/decision-log.md). Database-backed local verification remains isolated to `test_mira` with fictional data.

## Maintainers

Repository owner and collaborators are listed in GitHub repository settings. Do not publish personal contact information in this README.
