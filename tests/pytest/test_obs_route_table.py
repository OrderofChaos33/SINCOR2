"""Route-table integrity for the production app (observability build #8).

Why this exists
---------------
Live probes on 2026-10-28 (see
``~/workspace/site-audit-2026-10-28/observability-features.md`` §3/§5.6)
disagreed across rounds on at least six endpoints —
``/v1/a2a/registration-velocity``, ``/api/x402/resources``,
``/v1/a2a/agents``, ``/api/monitoring/dashboard``,
``/api/sinc/burn-stats`` and ``/v1/kya/health`` flipped between
200 / 404 / differing payloads.  The routes all exist on main, so the
suspects were (a) conditional / exception-swallowed registration making
different builds register different route sets, and (b) mixed deployed
builds serving different code.

This test pins (a) shut: it builds the production app exactly the way
production does — ``railway_start`` loads the module-level
``sincor2.mvp_app:app`` (there is no factory function) — and asserts
that every documented public route is present in the URL map and
resolves through the test client (a route that exists but demands
auth/payment is a PASS: 401/402/405 are distinguished from the 404 of
a missing route).

Diagnosis recorded 2026-10-28 (clean build of main @ 7a3aeacb):
the full documented set registers deterministically — 303 URL rules,
identical rule-set hash under test env and production-like env
(``FLASK_ENV=production`` + ``RAILWAY_ENVIRONMENT`` set), and identical
again after an in-process re-import (see the idempotency test below).
No registration bug was found on a clean build, which points the live
intermittency at hypothesis (b), mixed deployed builds — the thing the
build-stamping branch makes diagnosable in-product.

Registration-risk notes (why the pin matters going forward):
``mvp_app`` and ``mvp_blueprints.register_mvp_blueprints`` mount
several surfaces inside try/except blocks that swallow failures with a
log/print line (A2A bootstrap + inbound, task-queue routes, rail
routes/delivery/pay pages, wardrobe, freshness, CHROMA, underwriting,
the hand-carried treasury route).  If any of those imports ever fails
on a given build, that build silently serves 404s for the whole
surface.  This test is the alarm: it fails the moment any documented
route drops out of the table, whatever the cause.

Intentionally NOT asserted (documented exclusions, not bugs):
- ``GET /.well-known/liveness-agents.json`` — ``attach_liveness()`` is
  called only in legacy ``sincor2/app.py``; production 404 is a known
  finding owned by the sibling liveness-mount branch (build #7).
- ``GET /api/monitoring/dashboard`` and the rest of the monitoring
  blueprint — deliberately not mounted on ``mvp_app``; only
  ``treasury_metrics`` is hand-carried in (audit §3).
- ``/api/command-center/*`` — deliberately NOT mounted: its killswitch
  POST is unauthenticated upstream (see the comment in ``mvp_app.py``).
  Only the ``/command-center`` page shell is production surface.
- ``/boot`` — served by the ``railway_start`` WSGI wrapper, not by the
  Flask app, so it cannot appear in this URL map.
- ``/sitemap.xml`` — no route exists anywhere in the codebase (the
  sitemap page links to it); owned by the sitemap truth-pass build.
- Stripe routes — mounted only when ``fiat_payments_enabled()``; the
  founder removed Stripe, so their absence in this build is intended.
- Parameterised detail routes whose handler legitimately 404s unknown
  ids (``/v1/a2a/tasks/<id>``, ``/v1/a2a/stake/<agent_id>``,
  ``/v1/a2a/escrow-exposure/<agent_id>``, ``/agents/<agent_id>``,
  ``/download/<intake_id>``, ``/rail/pay/<resource_id>``,
  ``/api/paid/<resource_id>``) are asserted in the URL map only —
  probing them with a fake id cannot distinguish a missing route from
  a missing record.
- ``/api/acceptance/status`` is URL-map asserted only: its handler
  performs a live network read, so its status code (200/500) is
  environment-dependent; what must never vary is that the route exists.
"""
from __future__ import annotations

import importlib

import pytest

# (rule, method) pairs for every documented public route on the
# production app.  Sources: the audit's §1 inventory tables, the agent
# card / discovery surfaces, and docs/distribution route lists,
# cross-checked against the decorators under src/sincor2/**.
EXPECTED_RULES: set[tuple[str, str]] = {
    # --- health / readiness ---
    ("/health", "GET"), ("/api/health", "GET"), ("/healthz", "GET"),
    ("/ready", "GET"),
    # --- discovery / agent presence ---
    ("/.well-known/agent-card.json", "GET"),
    ("/.well-known/agent.json", "GET"),
    ("/.well-known/sincor-marketplace.json", "GET"),
    ("/.well-known/security.txt", "GET"),
    ("/.well-known/sinc-token.json", "GET"),
    ("/.well-known/tokenlist.json", "GET"),
    ("/.well-known/trust-stack.json", "GET"),
    ("/llms.txt", "GET"), ("/ai.txt", "GET"), ("/robots.txt", "GET"),
    ("/docs/a2a", "GET"),
    # --- A2A inbound market fabric ---
    ("/v1/a2a/pool", "GET"), ("/v1/a2a/activity", "GET"),
    ("/v1/a2a/tasks", "GET"), ("/v1/a2a/tasks", "POST"),
    ("/v1/a2a/tasks/<task_id>", "GET"),
    ("/v1/a2a/agents", "GET"),
    ("/v1/a2a/registration-velocity", "GET"),
    ("/v1/a2a/auctions", "GET"), ("/v1/a2a/cards", "GET"),
    ("/v1/a2a/chain", "GET"), ("/v1/a2a/directory", "GET"),
    ("/v1/a2a/stream", "GET"),
    ("/v1/a2a/register", "POST"), ("/v1/a2a/heartbeat", "POST"),
    ("/v1/a2a/bids", "POST"), ("/v1/a2a/bids/commit", "POST"),
    ("/v1/a2a/bids/reveal", "POST"),
    ("/v1/a2a/stake/<agent_id>", "GET"),
    ("/v1/a2a/escrow-exposure/<agent_id>", "GET"),
    ("/v1/a2a/sybil/holds", "GET"),
    ("/api/marketplace/agents", "GET"),
    ("/api/marketplace/register", "POST"),
    # --- A2A discovery / skills ---
    ("/api/a2a/agents", "GET"), ("/api/a2a/leaderboard", "GET"),
    ("/api/a2a/pricing", "GET"), ("/api/a2a/quote", "GET"),
    ("/api/a2a/peek", "GET"), ("/api/a2a/peek/<skill_id>", "GET"),
    ("/api/a2a", "POST"),
    # --- KYA / underwriting ---
    ("/v1/kya/health", "GET"), ("/v1/kya/directory", "GET"),
    ("/v1/kya/tombstones", "GET"), ("/v1/kya/lookup", "GET"),
    ("/v1/kya/stack", "GET"), ("/v1/kya/bind", "POST"),
    ("/v1/kya/verify", "POST"),
    ("/v1/underwriting/health", "GET"),
    # --- x402 / billing / rail ---
    ("/api/x402/resources", "GET"),
    ("/api/x402/peek/<resource_id>", "GET"),
    ("/api/x402/verify", "POST"), ("/api/x402/challenge-bind", "POST"),
    ("/x402/<resource_id>", "GET"),
    ("/api/paid/<resource_id>", "GET"),
    ("/api/platform/plans", "GET"),
    ("/api/platform/checkout", "POST"),
    ("/api/platform/verify", "POST"),
    ("/rail/checkout", "POST"), ("/rail/settle", "POST"),
    ("/rail/pay/<resource_id>", "GET"), ("/rail/onramp", "GET"),
    ("/download/<intake_id>", "GET"),
    ("/api/orders", "GET"), ("/api/orders/<email>", "GET"),
    # --- metrics / ops / status data ---
    ("/api/metrics/treasury", "GET"),
    ("/api/metrics/freshness", "GET"),
    ("/api/price/official", "GET"), ("/api/hook/status", "GET"),
    ("/api/acceptance/status", "GET"),
    ("/api/sinc/curve", "GET"), ("/api/sinc/burn-stats", "GET"),
    ("/api/token/metadata", "GET"), ("/api/token/security", "GET"),
    ("/api/defi/catalog", "GET"),
    ("/api/ops/schedulers", "GET"),
    ("/api/polyclaw/status", "GET"),
    ("/v1/ops/rail-revenue", "GET"),
    ("/api/obs/vitals", "GET"),
    # --- task queue ---
    ("/api/tasks", "GET"), ("/api/tasks/<job_id>", "GET"),
    # --- public + console pages ---
    ("/", "GET"), ("/dashboard", "GET"), ("/agents", "GET"),
    ("/agents/<agent_id>", "GET"), ("/tasks", "GET"), ("/pool", "GET"),
    ("/catalog", "GET"), ("/pricing", "GET"), ("/products", "GET"),
    ("/agent-card", "GET"), ("/register-agent", "GET"), ("/toa", "GET"),
    ("/my-orders", "GET"), ("/security", "GET"), ("/terms", "GET"),
    ("/privacy", "GET"), ("/whitepaper", "GET"), ("/sitemap", "GET"),
    ("/docs", "GET"), ("/login", "GET"), ("/signup", "GET"),
    ("/webbuilder", "GET"), ("/obs/vitals", "GET"),
    ("/command-center", "GET"), ("/console", "GET"),
    ("/operator", "GET"), ("/admin", "GET"),
    ("/launch/review", "GET"), ("/launch/partners", "GET"),
    ("/chroma/health", "GET"),
}

# Unauthenticated GETs that must return 200 on a healthy build.
# Includes all three endpoints observed flipping 200<->404 live
# (registration-velocity, /api/x402/resources, /v1/a2a/agents) plus
# /v1/kya/health, whose payload shape diverged across live instances.
PROBE_GET_200 = [
    "/health", "/api/health", "/healthz", "/ready",
    "/api/metrics/freshness",
    "/v1/a2a/pool", "/v1/a2a/activity", "/v1/a2a/tasks",
    "/v1/a2a/registration-velocity", "/v1/a2a/agents",
    "/v1/a2a/auctions", "/v1/a2a/cards", "/v1/a2a/chain",
    "/v1/a2a/directory",
    "/api/a2a/agents", "/api/a2a/pricing", "/api/a2a/leaderboard",
    "/api/marketplace/agents",
    "/.well-known/agent-card.json", "/.well-known/agent.json",
    "/.well-known/sincor-marketplace.json", "/.well-known/security.txt",
    "/.well-known/sinc-token.json", "/.well-known/tokenlist.json",
    "/.well-known/trust-stack.json",
    "/docs/a2a", "/llms.txt", "/ai.txt", "/robots.txt",
    "/v1/kya/health", "/v1/kya/directory", "/v1/kya/tombstones",
    "/v1/kya/stack", "/v1/underwriting/health",
    "/api/x402/resources", "/api/x402/peek/wallet-sanctions-screen",
    "/api/defi/catalog", "/api/price/official", "/api/ops/schedulers",
    "/api/polyclaw/status", "/api/platform/plans",
    "/api/token/metadata", "/api/token/security", "/chroma/health",
    "/", "/agents", "/tasks", "/pool", "/catalog", "/pricing",
    "/products", "/agent-card", "/register-agent", "/toa",
    "/my-orders", "/security", "/terms", "/privacy", "/whitepaper",
    "/sitemap", "/docs", "/login", "/signup", "/webbuilder",
    "/launch/review", "/launch/partners",
]

# Routes that exist but answer unauthenticated GETs with something
# other than 200.  The assertion is "not 404": the route resolved.
# Expected codes observed on a clean build are noted per entry.
PROBE_GET_NOT_404 = [
    "/api/metrics/treasury",      # 401 JWT-gated (audit §5.1)
    "/v1/a2a/sybil/holds",        # 401 admin-gated
    "/v1/a2a/stream",             # 401 auth-gated
    "/api/obs/vitals",            # 401 session-gated (beta)
    "/v1/ops/rail-revenue",       # 401 admin-gated
    "/api/orders",                # 401 auth-gated
    "/api/sinc/curve",            # 410 retired mechanics
    "/api/sinc/burn-stats",       # 410 burn retired (locked fee policy)
    "/api/a2a/quote",             # 400 missing skill parameter
    "/x402/wallet-sanctions-screen",  # 400/402 payment envelope
    "/api/hook/status",           # 200, tolerates RPC failure modes
    "/dashboard",                 # 302 login redirect
    "/command-center",            # 302 login redirect
    "/console",                   # 302 login redirect
    "/operator",                  # 302 login redirect
    "/admin",                     # 302 login redirect
    "/obs/vitals",                # 302 session redirect (beta)
]


def _rule_pairs(app) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for rule in app.url_map.iter_rules():
        for method in rule.methods or ():
            if method in ("HEAD", "OPTIONS"):
                continue
            pairs.add((rule.rule, method))
    return pairs


def _rule_signature(app) -> list[tuple[str, tuple[str, ...], str]]:
    return sorted(
        (
            rule.rule,
            tuple(sorted(m for m in (rule.methods or ()) if m not in ("HEAD", "OPTIONS"))),
            rule.endpoint,
        )
        for rule in app.url_map.iter_rules()
    )


@pytest.fixture(scope="module")
def prod_app():
    pytest.importorskip("flask")
    from sincor2.mvp_app import app

    return app


def test_documented_public_routes_are_registered(prod_app) -> None:
    missing = EXPECTED_RULES - _rule_pairs(prod_app)
    assert not missing, (
        "documented public routes missing from the production app's "
        f"URL map (registration regression?): {sorted(missing)}"
    )


def test_public_get_routes_resolve_200(prod_app) -> None:
    client = prod_app.test_client()
    failures = {}
    for path in PROBE_GET_200:
        resp = client.get(path)
        if resp.status_code != 200:
            failures[path] = resp.status_code
    assert not failures, f"expected 200, got: {failures}"


def test_gated_routes_resolve_not_404(prod_app) -> None:
    client = prod_app.test_client()
    failures = {}
    for path in PROBE_GET_NOT_404:
        resp = client.get(path)
        if resp.status_code == 404:
            failures[path] = resp.status_code
    assert not failures, f"routes resolving as 404 (missing route?): {failures}"


def test_app_reimport_registers_identical_route_table(prod_app) -> None:
    """Import idempotency: rebuilding the app module in-process must
    yield exactly the same route table — no duplicate registrations
    dropped by Flask's first-wins rule, none missing."""
    import sincor2.mvp_app as mvp_app_module

    before = _rule_signature(prod_app)
    reloaded = importlib.reload(mvp_app_module)
    after = _rule_signature(reloaded.app)
    assert after == before, (
        "route table changed across re-import: "
        f"only-before={sorted(set(before) - set(after))} "
        f"only-after={sorted(set(after) - set(before))}"
    )
    missing = EXPECTED_RULES - _rule_pairs(reloaded.app)
    assert not missing, f"routes missing after re-import: {sorted(missing)}"
