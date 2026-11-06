# Quality Strategy

Testing strategy will be tailored after stack selection. The target model is a risk-based testing pyramid:

1. Unit tests for domain rules and pure logic.
2. Integration tests for persistence, transactions and adapters.
3. Contract tests for APIs and external provider boundaries.
4. End-to-end tests for critical user journeys.
5. Performance, resilience and security tests before production milestones.

Tests must be deterministic, isolated, explainable and runnable in CI. Avoid brittle tests that merely mirror implementation details.

No coverage percentage is set until modules and risk profiles are known. Critical financial, identity, inventory and data-integrity behavior will require stronger evidence than low-risk presentation code.
