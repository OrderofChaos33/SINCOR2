# Clinton Auto Detailing — Monetization Engine (Pro)

**North Star:** Fill the calendar via self‑booking → https://www.clintondetailing.com/booking/

This repository is a **production‑quality, Notion‑style knowledge base** plus automation directives for agents. It’s structured like a software project with CI, schemas, and docs.

## Quickstart
```bash
make install   # no-op placeholder
make check     # run static checks (Markdown/YAML/jsonschema hints)
```
To publish the docs site:
```bash
make docs-serve   # local mkdocs server
```

## Repo Layout
- `docs/` – published docs site powered by MkDocs (material theme config included)
- `notion/` – source of truth pages in Markdown with frontmatter
- `agents/` – YAML directives for SINCOR agents
- `automations/` – posting schedules and channels
- `content/templates/` – post/SMS templates
- `data/` – CSV datasets + JSON Schemas
- `.github/` – CI workflows, issue templates
- `scripts/` – utility scripts (incl. `push_to_github.sh`)

## Status
- Ready to commit & push.
- Designed for developer‑grade maintainability (lintable, navigable, versionable).
