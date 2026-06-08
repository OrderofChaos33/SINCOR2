# Changelog

All notable changes to SINCOR2 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

### Added
- Root `ARCHITECTURE.md` with high-level system overview and data flow
- Expanded directory structure: `verticals/`, `marketplace/`, `core/`, `dae/`, `docs/`
- Improved A2A compliance scaffolding and Agent Card support
- Security and CI workflow hardening (gitleaks, pip-audit, permissions)

### Changed
- Updated README.md with clearer architecture references
- Refactored Flask runtime paths and Railway deployment configuration

## [2026-06-07]
- Large-scale addition of missing production-oriented components via Copilot-assisted development
- Initial population of vertical packs scaffolding (healthcare, dental, compliance, trading, lead_gen)
