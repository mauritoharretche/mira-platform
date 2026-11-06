# ADR-011: Technology stack selection

- Status: Accepted
- Date: 2026-11-06

## Decision

Use Python 3.14.10, Django 6.1.3, Django REST Framework 3.18.3, Psycopg 3.3.8, PostgreSQL 18.6, Node.js 26.13.0, Next.js 16.4.5, React 19.3.0, TypeScript 6.0.3, pnpm 10.19.0, and uv 0.13.7 for the engineering foundation.

## Consequences

Tool versions are declared through mise and resolved by committed lockfiles. Django 6.1.3 is not LTS; compatibility with Django 6.2 LTS and DRF must be reviewed when that release is stable. TypeScript 7 is not adopted until the Next.js TypeScript plugin is reviewed.

## Validation

CI installs the locked toolchain on macOS ARM64 and Linux x64, then runs backend checks/tests and frontend lint, typecheck, tests, and build.
