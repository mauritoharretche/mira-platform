# Contributing

Thank you for helping improve MIRA Platform. The project is in its discovery and engineering-foundation stage. Contributions that change product scope or architecture require maintainer approval before implementation.

## Before starting

1. Read `AGENTS.md` and the relevant documentation.
2. Open or discuss an issue describing the problem and expected outcome.
3. Wait for maintainer alignment before starting substantial work.
4. Never put private business information, customer data, credentials or internal PDFs in public issues or pull requests.

## Branches

Use short-lived branches such as:

- `docs/<topic>`
- `chore/<topic>`
- `feat/<topic>`
- `fix/<topic>`
- `test/<topic>`

Do not commit directly to `main` after bootstrap. Do not force-push shared branches.

## Pull requests

A pull request should include:

- Problem and motivation.
- Scope and explicit non-goals.
- Summary of implementation or documentation changes.
- Tests/checks executed and actual results.
- Security, privacy, data and compatibility impact.
- Migration, rollout and rollback considerations where applicable.
- Screenshots or recordings for user-facing changes, with no personal or sensitive data.

Keep PRs focused and reasonably small. Request review from a maintainer. Do not merge your own sensitive architectural or data changes without independent review.

## Commit messages

Prefer clear, imperative messages. Conventional Commit prefixes are encouraged (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `build:`, `ci:`).

## Tests and quality

Add or update tests for behavior changes. Run relevant formatting, linting, type checking, tests and build commands. If the project does not yet define a command, state that clearly instead of inventing a passing result.

## Security reports

Never disclose vulnerabilities through public issues. Follow `SECURITY.md`.
