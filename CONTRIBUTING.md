# Contributing

Thank you for helping improve MIRA Platform. The project is in its discovery and engineering-foundation stage. Contributions that change product scope or architecture require maintainer approval before implementation.

## Before starting

1. Read `AGENTS.md` and the relevant documentation.
2. Open or discuss an issue describing the problem and expected outcome.
3. Wait for maintainer alignment before starting substantial work.
4. Never put private business information, customer data, credentials or internal PDFs in public issues or pull requests.

## Branches

Use a short-lived branch from clean, synchronized `main`, after scope approval:

- `docs/<topic>`
- `chore/<topic>`
- `feat/<topic>`
- `fix/<topic>`
- `test/<topic>`

Pull requests targeting `main` are required by delivery policy. Do not commit directly to `main`, force-push or rewrite shared history. GitHub ruleset activation is a separate approval gate; do not assume this policy file activates enforcement.

## Pull requests

Every pull request should include:

- Problem and motivation.
- Scope and explicit non-goals.
- Summary of implementation or documentation changes.
- Tests/checks executed and actual results.
- Security, privacy, data and compatibility impact.
- Migration, rollout and rollback considerations where applicable.
- Screenshots or recordings for user-facing changes, with no personal or sensitive data.

Keep changes focused and reasonably small. Human approval remains required for architecture, security, data, payment, privacy and production-sensitive changes.

Review the complete patch, including new files, before publication. Record explicit external maintainer approval naming the canonical manifest SHA-256; a command-line digest alone is not approval. Changed content needs new approval. After publication, require all four established CI jobs and separate approval of the exact PR head SHA before squash merge. Auto-merge remains disabled; preserve branches and verify post-merge CI.

Engineering Automation itself must bootstrap through the existing manually approved workflow. Do not use its real publication path before Gate B protections are active and verified. See [Delivery automation](docs/engineering/delivery-automation.md) for manifests, trust boundaries and partial-failure handling.

## Commit messages

Prefer clear, imperative messages. Conventional Commit prefixes are encouraged (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `build:`, `ci:`).

## Tests and quality

Add or update tests for behavior changes. Run relevant formatting, linting, type checking, tests and build commands. If the project does not yet define a command, state that clearly instead of inventing a passing result.

## Security reports

Never disclose vulnerabilities through public issues. Follow `SECURITY.md`.
