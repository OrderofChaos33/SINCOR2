# Contributing to SINCOR2

Thanks for contributing to SINCOR2.

## Contribution priorities

We prioritize contributions that improve:
- A2A interoperability and agent marketplace quality.
- Reliability and scalability of multi-agent orchestration.
- DAE readiness (identity, incentives, governance).
- Developer and operator clarity through documentation.

## Workflow

1. Fork and create a focused branch.
2. Keep scope narrow and avoid unrelated refactors.
3. Update docs when behavior, architecture, or interfaces change.
4. Run checks locally before opening a PR.
5. Open a PR with a clear summary, risks, and validation notes.

## Local validation

```bash
ruff check src/sincor2/app.py src/sincor2/mvp_app.py src/sincor2/settings.py src/sincor2/startup.py src/sincor2/error_handling.py src/sincor2/blueprints tests/pytest
pytest
```

## Pull request checklist

- [ ] Changes are scoped and non-breaking.
- [ ] README/docs updated when needed.
- [ ] Lint/tests executed (or clearly justified if not).
- [ ] Security and configuration implications considered.

## Code of collaboration

- Be respectful and precise.
- Provide actionable review feedback.
- Prefer explicit architecture decisions over implicit coupling.
