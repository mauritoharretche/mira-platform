# Security Policy

## Scope

This policy covers the code and public artifacts in this repository. It does not authorize testing against production systems or third-party services.

## Reporting a vulnerability

**Do not report exploitable vulnerabilities, credentials or sensitive information in public GitHub Issues or Discussions.**

Preferred route: use GitHub's **Private vulnerability reporting** feature if it is enabled for this repository (repository page → Security → Advisories → Report a vulnerability, where available).

The maintainers must enable and test this feature in repository settings before inviting external security reports. Until it is enabled, contact a repository maintainer privately through GitHub; do not post technical exploit details publicly.

## Supported versions

No production release exists yet. Supported versions will be published when versioned releases begin.

## Response principles

1. Acknowledge and triage privately.
2. Assess severity, affected versions, exploitability and impact.
3. Reproduce safely without using real customer or production data.
4. Prepare a fix and regression test.
5. Coordinate disclosure and release timing.
6. Rotate exposed credentials and investigate logs if secrets or systems were affected.
7. Publish a sanitized advisory when appropriate.
