# Contributing

Thank you for helping improve MIRA Platform. The project is in its discovery and engineering-foundation stage. Contributions that change product scope or architecture require maintainer approval before implementation.

## Before starting

1. Read `AGENTS.md` and the relevant documentation.
2. Open or discuss an issue describing the problem and expected outcome.
3. Wait for maintainer alignment before starting substantial work.
4. Never put private business information, customer data, credentials or internal PDFs in public issues or pull requests.

## Branches

During the initial bootstrap, discovery and engineering-foundation phases, maintainers may commit atomically to `main` after explicit approval. Short-lived branches remain available when useful:

- `docs/<topic>`
- `chore/<topic>`
- `feat/<topic>`
- `fix/<topic>`
- `test/<topic>`

Pull requests are optional in the current workflow. Do not force-push or rewrite shared history.

## Pull requests

When a pull request is used, it should include:

- Problem and motivation.
- Scope and explicit non-goals.
- Summary of implementation or documentation changes.
- Tests/checks executed and actual results.
- Security, privacy, data and compatibility impact.
- Migration, rollout and rollback considerations where applicable.
- Screenshots or recordings for user-facing changes, with no personal or sensitive data.

Keep changes focused and reasonably small. Human approval remains required for architecture, security, data, payment, privacy and production-sensitive changes.

## Commit messages

Prefer clear, imperative messages. Conventional Commit prefixes are encouraged (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `build:`, `ci:`).

## Tests and quality

Add or update tests for behavior changes. Run relevant formatting, linting, type checking, tests and build commands. If the project does not yet define a command, state that clearly instead of inventing a passing result.

## Security reports

Never disclose vulnerabilities through public issues. Follow `SECURITY.md`.
