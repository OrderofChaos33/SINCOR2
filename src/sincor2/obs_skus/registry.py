"""Canonical registry of the six observability / audit SKUs.

Mirrors the DeFi arm's ``defi/catalog.py`` pattern (executable specs, not
markdown), but with two honest fields per SKU:

- ``stage_label`` — the *commercial* label (BETA / SELLABLE / SERVICE).
- ``lifecycle_stage`` — the *verified* lifecycle position today. All six
  start at ``spec`` (software SKUs) or ``spec`` (engagement SKUs): nothing
  here has passed an end-to-end verification yet.

Sibling builders (B2-B6) own the implementation modules. Their expected
paths are declared in ``SIBLING_MODULE_PATHS`` so this registry, the gates,
and the specs all agree on where each build will land. The modules do not
exist yet and are referenced by path only — never imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


# sku_id -> repo-relative path of the implementation module owned by the
# sibling builder. Declared here so registry, gates, specs, and tests share
# one source of truth. The files do not exist yet; references are lazy.
SIBLING_MODULE_PATHS: Dict[str, str] = {
    "OBS-01": "src/sincor2/obs_skus/vitals.py",          # B2: Agent Vitals
    "OBS-02": "src/sincor2/obs_skus/audit_trail.py",     # B3: Agent Audit Trail
    "OBS-03": "src/sincor2/obs_skus/drift_quality.py",   # B4: Drift & Quality Watch
    "AUD-01": "src/sincor2/obs_skus/forensic_audit.py",  # B5: Agent Forensic Audit
    "AUD-02": "src/sincor2/obs_skus/compliance_pack.py", # B5: Compliance Pack
    "OBS-ENT": "src/sincor2/obs_skus/enterprise_mesh.py",# B6: Enterprise Mesh
}


@dataclass(frozen=True)
class SKU:
    sku_id: str
    name: str
    tier: str               # observability | audit | enterprise
    price_usd: float        # recurring price per unit (0 when one-time only)
    price_label: str        # exact customer-facing price line
    unit: str               # per agent | per deployment | flat | + setup
    billing: str            # subscription | one_time
    setup_fee_usd: float    # one-time setup fee (0 when none)
    description: str
    stage_label: str        # commercial label: BETA | SELLABLE | SERVICE
    lifecycle: str          # software | engagement
    lifecycle_stage: str    # verified lifecycle position today
    gates: Tuple[str, ...]  # gate keywords enforced by gates.py
    acceptance_count: int  # number of numbered acceptance criteria in the spec
    spec_relpath: str       # repo-relative spec file
    draft_page_relpath: str # repo-relative draft template (NOT routed)


SKUS: List[SKU] = [
    SKU(
        sku_id="OBS-01",
        name="Agent Vitals",
        tier="observability",
        price_usd=49.0,
        price_label="$49/mo per agent",
        unit="per agent",
        billing="subscription",
        setup_fee_usd=0.0,
        description=(
            "Current health picture for every agent in your fleet: status, "
            "liveness, and activity signals on one dashboard, with clear "
            "unknown-instead-of-healthy reporting."
        ),
        stage_label="BETA",
        lifecycle="software",
        lifecycle_stage="spec",
        gates=("dashboard_customer_facing", "unknown_not_healthy", "per_agent_scoping"),
        acceptance_count=8,
        spec_relpath="docs/specs/obs-skus/OBS-01-spec.md",
        draft_page_relpath="templates/products/drafts/obs-01-agent-vitals.html",
    ),
    SKU(
        sku_id="OBS-02",
        name="Agent Audit Trail",
        tier="observability",
        price_usd=199.0,
        price_label="$199/mo per deployment",
        unit="per deployment",
        billing="subscription",
        setup_fee_usd=0.0,
        description=(
            "A tamper-evident, append-only record of what your agents did and "
            "when — searchable by agent, action, and time window, with "
            "exportable evidence for reviews and disputes."
        ),
        stage_label="SELLABLE",
        lifecycle="software",
        lifecycle_stage="spec",
        gates=("append_only", "per_deployment_scoping", "exportable_evidence"),
        acceptance_count=8,
        spec_relpath="docs/specs/obs-skus/OBS-02-spec.md",
        draft_page_relpath="templates/products/drafts/obs-02-agent-audit-trail.html",
    ),
    SKU(
        sku_id="OBS-03",
        name="Drift & Quality Watch",
        tier="observability",
        price_usd=399.0,
        price_label="$399/mo",
        unit="flat",
        billing="subscription",
        setup_fee_usd=0.0,
        description=(
            "Continuous watch over agent behavior: drift detection against "
            "established baselines and quality signals tied to real "
            "on-platform outcomes — ghosting, disputes, and reputation."
        ),
        stage_label="SELLABLE",
        lifecycle="software",
        lifecycle_stage="spec",
        gates=("baseline_drift", "outcome_tied_quality", "alert_routing"),
        acceptance_count=8,
        spec_relpath="docs/specs/obs-skus/OBS-03-spec.md",
        draft_page_relpath="templates/products/drafts/obs-03-drift-quality-watch.html",
    ),
    SKU(
        sku_id="AUD-01",
        name="Agent Forensic Audit",
        tier="audit",
        price_usd=0.0,
        price_label="$499 one-time flat",
        unit="flat",
        billing="one_time",
        setup_fee_usd=0.0,
        description=(
            "A one-time forensic review of an agent or deployment: what it "
            "did, what went wrong, and what to change. Delivered as a written "
            "findings report by a SINCOR reviewer."
        ),
        stage_label="SERVICE",
        lifecycle="engagement",
        lifecycle_stage="spec",
        gates=("engagement_playbook", "written_findings", "delivery_receipt"),
        acceptance_count=7,
        spec_relpath="docs/specs/obs-skus/AUD-01-spec.md",
        draft_page_relpath="templates/products/drafts/aud-01-agent-forensic-audit.html",
    ),
    SKU(
        sku_id="AUD-02",
        name="Compliance Pack",
        tier="audit",
        price_usd=999.0,
        price_label="$999/mo + $2,500 setup",
        unit="per deployment",
        billing="subscription",
        setup_fee_usd=2500.0,
        description=(
            "Ongoing compliance support for regulated deployments: evidence "
            "templates mapped to your controls, monthly review cadence, and a "
            "standing reviewer who knows your stack."
        ),
        stage_label="SERVICE",
        lifecycle="engagement",
        lifecycle_stage="spec",
        gates=("engagement_playbook", "evidence_templates", "monthly_cadence"),
        acceptance_count=7,
        spec_relpath="docs/specs/obs-skus/AUD-02-spec.md",
        draft_page_relpath="templates/products/drafts/aud-02-compliance-pack.html",
    ),
    SKU(
        sku_id="OBS-ENT",
        name="Enterprise Mesh",
        tier="enterprise",
        price_usd=2500.0,
        price_label="$2,500/mo",
        unit="flat",
        billing="subscription",
        setup_fee_usd=0.0,
        description=(
            "Fleet-grade operations for many deployments: unified onboarding "
            "runbook, cross-deployment visibility, and a named SINCOR "
            "operator with defined response times."
        ),
        stage_label="SERVICE",
        lifecycle="engagement",
        lifecycle_stage="spec",
        gates=("engagement_playbook", "onboarding_runbook", "response_sla"),
        acceptance_count=7,
        spec_relpath="docs/specs/obs-skus/OBS-ENT-spec.md",
        draft_page_relpath="templates/products/drafts/obs-ent-enterprise-mesh.html",
    ),
]


SKU_BY_ID: Dict[str, SKU] = {s.sku_id: s for s in SKUS}


def sibling_module_path(sku_id: str) -> str:
    """Repo-relative path of the sibling builder's implementation module."""
    return SIBLING_MODULE_PATHS[sku_id]


def assert_registry_complete() -> None:
    if len(SKUS) != 6:
        raise AssertionError(f"expected 6 SKUs, found {len(SKUS)}")
    ids = [s.sku_id for s in SKUS]
    if sorted(ids) != ["AUD-01", "AUD-02", "OBS-01", "OBS-02", "OBS-03", "OBS-ENT"]:
        raise AssertionError(f"unexpected SKU ids: {ids}")
    for s in SKUS:
        if s.sku_id not in SIBLING_MODULE_PATHS:
            raise AssertionError(f"{s.sku_id}: no sibling module path declared")
        if s.lifecycle not in ("software", "engagement"):
            raise AssertionError(f"{s.sku_id}: bad lifecycle {s.lifecycle!r}")
        if s.lifecycle_stage != "spec":
            raise AssertionError(
                f"{s.sku_id}: lifecycle_stage must be 'spec' until gates verify "
                f"otherwise (standing rule: working products only)"
            )
