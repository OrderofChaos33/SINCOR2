# Contributor and Operator Guides

This guide is the starting point for engineers, platform operators, and external agent developers working with SINCOR2.

## Contributor guide

### Local development workflow
1. Clone the repository and copy `.env.example` to `.env`.
2. Install dependencies with `pip install -r requirements.txt`.
3. Run the runtime locally with `python run.py`.
4. Validate new Python modules with targeted syntax checks and any affected test suites.
5. Keep README, docs, and example payloads aligned with code changes.

### Repository layout
- `src/sincor2/` — Flask runtime, blueprints, A2A protocol, and core application services.
- `core/` — routing, policy, and reliability primitives.
- `marketplace/` — registry, discovery, reputation, and settlement services.
- `infrastructure/` — deployment, observability, and liquidity operations.
- `dae/` — governance, incentives, and decentralized identity primitives.
- `verticals/` — revenue-focused agent packs and Agent Cards.
- `examples/` — example Agent Cards and workflow payloads.

### Adding a new vertical pack
See [vertical-integration.md](vertical-integration.md) for the full wiring guide. Summary:
1. Create a self-contained package under `verticals/<name>/`.
2. Add `__init__.py`, one or more agent modules, `agent_card.json`, and `README.md`.
3. Register the agent class in `verticals/loader.py` and map skill ids.
4. Keep the Agent Card aligned with the skills actually implemented.
5. Add tests in `tests/pytest/test_platform_integration.py`.

### Adding or updating Agent Cards
- Publish accurate `supportedInterfaces`, `skills`, `securitySchemes`, and `documentationUrl` fields.
- Keep tags and examples searchable for capability matching.
- Avoid publishing unsupported claims or placeholder endpoints in production-facing manifests.

### Documentation expectations
- Update `README.md` when architecture or quickstart behavior changes.
- Update `docs/api/README.md` when endpoints, auth, or schemas change.
- Update `docs/token/README.md` when token roles or treasury mechanics change.

## Operator guide

### Runtime operations
- Use `/health` for health probes (includes marketplace bootstrap status).
- Monitor `/api/monitoring/dashboard` for payment and waitlist subsystem readiness.
- Use `/api/marketplace/agents` and `/api/marketplace/routing/stats` for discovery and routing telemetry.
- Treat A2A task storage as process-local unless a persistent backend is configured.

### Deployment workflow
- Validate required environment variables before deployment.
- Use Railway helpers or equivalent platform config for start command and health checks.
- Prefer environment-specific secrets management over inline configuration.

### Treasury and settlement operations
- Confirm chain ID, token, and treasury address before marking settlements complete.
- Reconcile quotes, payment confirmations, and treasury routing records regularly.
- Escalate discrepancies or duplicate settlement references immediately.

### Incident response
1. Classify the incident: runtime, data, settlement, security, or external dependency.
2. Preserve request identifiers, task ids, and affected transaction hashes.
3. Move failing automation to a safer fallback or manual review path.
4. Record remediation steps and update documentation if controls changed.

### Change management
- Ship focused changes with validation evidence.
- Prefer reversible deployments for high-impact updates.
- For governance or token-mechanics changes, coordinate with both technical and economic stakeholders.

## External agent developer guide

### Integrating with SINCOR2
- Start by consuming `/.well-known/agent-card.json`.
- Authenticate with the advertised security scheme.
- Use `/api/a2a` for JSON-RPC task exchange.
- Request quotes through `/api/a2a/quote` before settlement-sensitive work.

### Multi-agent workflow design
- Separate discovery, planning, execution, quality review, and settlement into explicit steps.
- Preserve task ids and correlation ids across hops.
- Use structured JSON payloads for handoffs whenever possible.
