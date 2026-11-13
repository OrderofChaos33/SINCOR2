"""Tests for the OBS/AUD SKU registry, lifecycle gates, and the standing rule.

Standing rule (founder, 2026-09-29): working products only. These tests are
the mechanical enforcement:
- the registry is complete and honest (6 SKUs, exact pricing/stages),
- every spec file and draft page exists,
- the publish gate refuses all 6 SKUs today (nothing verified end-to-end),
- the publish gate CAN pass when evidence exists (mechanism proof).

Founder directive (2026-10-01) updates the public-surface posture: each SKU
gets its own live page, uniform with the 26 DeFi product pages, carrying
honest build-stage framing (private beta / service engagement) instead of
'live' claims. The lifecycle/publish gates are NOT weakened — they still
refuse on missing acceptance/walkthrough evidence, and the tests assert the
live pages carry no banned claims.
"""

import os

import pytest

from sincor2.obs_skus.gates import ObsProofLedger as ProofLedger
from sincor2.obs_skus import (
    SKU_BY_ID,
    SKUS,
    SIBLING_MODULE_PATHS,
    assert_registry_complete,
    sibling_module_path,
)
from sincor2.obs_skus import gates
from sincor2.obs_skus.gates import (
    BANNED_DRAFT_PHRASES,
    ENGAGEMENT_STAGES,
    SOFTWARE_STAGES,
    evaluate,
    evaluate_publish,
    find_public_wiring,
    next_stage,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def sku(sku_id):
    return SKU_BY_ID[sku_id]


# -- registry completeness -------------------------------------------------
def test_registry_complete():
    assert_registry_complete()
    assert len(SKUS) == 6
    assert sorted(SKU_BY_ID) == ["AUD-01", "AUD-02", "OBS-01", "OBS-02", "OBS-03", "OBS-ENT"]


EXPECTED = {
    "OBS-01": dict(name="Agent Vitals", price_usd=49.0, price_label="$49/mo per agent",
                   unit="per agent", billing="subscription", setup_fee_usd=0.0,
                   stage_label="BETA", lifecycle="software", acceptance_count=8),
    "OBS-02": dict(name="Agent Audit Trail", price_usd=199.0, price_label="$199/mo per deployment",
                   unit="per deployment", billing="subscription", setup_fee_usd=0.0,
                   stage_label="SELLABLE", lifecycle="software", acceptance_count=8),
    "OBS-03": dict(name="Drift & Quality Watch", price_usd=399.0, price_label="$399/mo",
                   unit="flat", billing="subscription", setup_fee_usd=0.0,
                   stage_label="SELLABLE", lifecycle="software", acceptance_count=8),
    "AUD-01": dict(name="Agent Forensic Audit", price_usd=0.0, price_label="$499 one-time flat",
                   unit="flat", billing="one_time", setup_fee_usd=0.0,
                   stage_label="SERVICE", lifecycle="engagement", acceptance_count=7),
    "AUD-02": dict(name="Compliance Pack", price_usd=999.0, price_label="$999/mo + $2,500 setup",
                   unit="per deployment", billing="subscription", setup_fee_usd=2500.0,
                   stage_label="SERVICE", lifecycle="engagement", acceptance_count=7),
    "OBS-ENT": dict(name="Enterprise Mesh", price_usd=2500.0, price_label="$2,500/mo",
                    unit="flat", billing="subscription", setup_fee_usd=0.0,
                    stage_label="SERVICE", lifecycle="engagement", acceptance_count=7),
}


@pytest.mark.parametrize("sku_id,exp", list(EXPECTED.items()))
def test_sku_pricing_and_stages(sku_id, exp):
    s = sku(sku_id)
    for k, v in exp.items():
        assert getattr(s, k) == v, f"{sku_id}.{k}: {getattr(s, k)!r} != {v!r}"
    # honest baseline: nothing has verified lifecycle progress yet
    assert s.lifecycle_stage == "spec"
    assert s.description and len(s.description) > 40


# Live pages, published by explicit founder directive (2026-10-01): each SKU
# gets its own page, uniform with the 26 DeFi product pages. AUD-02's scope
# comes straight from the repo (Compliance Pack — see
# src/sincor2/obs_skus/compliance_pack.py and the draft page); no assumption
# was needed.
LIVE_PAGES = {
    "OBS-01": "sku-obs-01-agent-vitals.html",
    "OBS-02": "sku-obs-02-agent-audit-trail.html",
    "OBS-03": "sku-obs-03-drift-quality-watch.html",
    "AUD-01": "sku-aud-01-agent-forensic-audit.html",
    "AUD-02": "sku-aud-02-compliance-pack.html",
    "OBS-ENT": "sku-obs-ent-enterprise-mesh.html",
}


def _live_slug(sku_id):
    return LIVE_PAGES[sku_id][:-len(".html")]


def test_sibling_module_paths_declared():
    assert sorted(SIBLING_MODULE_PATHS) == sorted(SKU_BY_ID)
    assert sibling_module_path("OBS-01") == "src/sincor2/obs_skus/vitals.py"
    assert sibling_module_path("OBS-02") == "src/sincor2/obs_skus/audit_trail.py"
    assert sibling_module_path("OBS-03") == "src/sincor2/obs_skus/drift_quality.py"
    assert sibling_module_path("AUD-01") == "src/sincor2/obs_skus/forensic_audit.py"
    assert sibling_module_path("AUD-02") == "src/sincor2/obs_skus/compliance_pack.py"
    assert sibling_module_path("OBS-ENT") == "src/sincor2/obs_skus/enterprise_mesh.py"
    # post-integration: every declared sibling module exists on disk
    for rel in SIBLING_MODULE_PATHS.values():
        assert os.path.exists(os.path.join(ROOT, rel)), f"missing: {rel}"


# -- spec files ------------------------------------------------------------
@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_spec_file_exists_and_has_numbered_criteria(sku_id):
    s = sku(sku_id)
    path = os.path.join(ROOT, s.spec_relpath)
    assert os.path.exists(path), f"missing spec: {s.spec_relpath}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "## 4. Acceptance criteria" in text
    assert "## 7. Publish gate" in text
    assert "walkthrough" in text.lower()
    # numbered criteria count matches the registry
    import re
    m = re.search(r"## 4\. Acceptance criteria\n(.*?)\n---", text, re.S)
    assert m, f"{sku_id}: criteria section not parseable"
    numbered = re.findall(r"^(\d+)\.\s", m.group(1), re.M)
    assert [int(n) for n in numbered] == list(range(1, s.acceptance_count + 1)), (
        f"{sku_id}: expected criteria 1..{s.acceptance_count}, found {numbered}"
    )


# -- draft pages: present, unrouted, clean ---------------------------------
@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_draft_page_exists_and_marked_draft(sku_id):
    s = sku(sku_id)
    assert s.draft_page_relpath.startswith("templates/products/drafts/")
    path = os.path.join(ROOT, s.draft_page_relpath)
    assert os.path.exists(path), f"missing draft: {s.draft_page_relpath}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "DRAFT — NOT ROUTED" in text
    assert "DRAFT — NOT PUBLISHED" in text
    assert '{% include "_pro_nav.html" %}' in text
    assert '{% include "_pro_footer.html" %}' in text


@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_draft_copy_has_no_banned_claims(sku_id):
    s = sku(sku_id)
    assert BANNED_DRAFT_PHRASES, "banned-phrase list must not be empty"
    with open(os.path.join(ROOT, s.draft_page_relpath), encoding="utf-8") as fh:
        text = fh.read().lower()
    for phrase in BANNED_DRAFT_PHRASES:
        assert phrase not in text, f"{sku_id} draft contains banned phrase {phrase!r}"


def test_no_public_top_level_pages():
    """Live SKU pages are published by explicit founder directive
    (2026-10-01: each SKU gets its own live page, uniform with the DeFi
    pages). The standing-rule machinery in gates.py still refuses the
    publish/lifecycle gates on missing evidence — the pages themselves
    carry honest build-stage framing instead of 'live' claims."""
    top = os.path.join(ROOT, "templates", "products")
    names = os.listdir(top)
    for sku_id, live_name in LIVE_PAGES.items():
        assert live_name in names, f"{sku_id}: live page {live_name} missing"
        path = os.path.join(top, live_name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert "DRAFT — NOT ROUTED" not in text
        assert "DRAFT — NOT PUBLISHED" not in text
        assert '{% include "_pro_nav.html" %}' in text
        assert '{% include "_pro_footer.html" %}' in text


@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_live_sku_copy_has_no_banned_claims(sku_id):
    """Public pages must stay honest: no banned 'live production' claims."""
    assert BANNED_DRAFT_PHRASES, "banned-phrase list must not be empty"
    with open(os.path.join(ROOT, "templates", "products", LIVE_PAGES[sku_id]),
              encoding="utf-8") as fh:
        text = fh.read().lower()
    for phrase in BANNED_DRAFT_PHRASES:
        assert phrase not in text, f"{sku_id} live page contains banned phrase {phrase!r}"


@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_no_public_wiring(sku_id):
    """Live pages ARE publicly wired (founder directive 2026-10-01):
    routed in pages.py and discoverable from the products catalog."""
    hits = find_public_wiring(sku(sku_id), ROOT)
    assert hits != [], f"{sku_id}: expected public wiring for the live page, found none"
    pages_py = os.path.join(ROOT, "src", "sincor2", "mvp_blueprints", "pages.py")
    with open(pages_py, encoding="utf-8") as fh:
        text = fh.read()
    slug = _live_slug(sku_id)
    assert slug in text, f"pages.py missing route slug {slug}"


def test_product_slugs_allowlist_has_no_obs_skus():
    """The obs/audit SKU slugs live in their own _SKU_SLUGS allowlist in
    pages.py (merged into _PRODUCT_SLUGS), keeping them distinct from the
    26 DeFi product slugs."""
    pages_py = os.path.join(ROOT, "src", "sincor2", "mvp_blueprints", "pages.py")
    with open(pages_py, encoding="utf-8") as fh:
        text = fh.read()
    assert "_SKU_SLUGS" in text
    for sku_id in EXPECTED:
        slug = _live_slug(sku_id)
        assert f'"{slug}"' in text, f"pages.py missing SKU slug {slug}"


# -- lifecycle gates -------------------------------------------------------
def test_software_stage_order():
    s = sku("OBS-01")
    assert SOFTWARE_STAGES == ("spec", "build", "test", "audit", "product", "catalog")
    assert next_stage(s, "spec") == "build"
    assert next_stage(s, "product") == "catalog"
    assert next_stage(s, "catalog") is None
    assert next_stage(s, "playbook") is None  # wrong lifecycle


def test_engagement_stage_order():
    s = sku("AUD-01")
    assert ENGAGEMENT_STAGES == ("spec", "playbook", "rehearsal", "delivery", "report")
    assert next_stage(s, "spec") == "playbook"
    assert next_stage(s, "delivery") == "report"
    assert next_stage(s, "report") is None
    assert next_stage(s, "build") is None  # wrong lifecycle


def test_evaluate_refuses_stage_skip(tmp_path):
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    res = evaluate(sku("OBS-01"), "spec", "test", ledger, ROOT)
    assert not res.ok
    assert any(r.check == "no_skip" for r in res.reasons)


def test_evaluate_refuses_unknown_stage(tmp_path):
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    res = evaluate(sku("AUD-01"), "spec", "deploy", ledger, ROOT)
    assert not res.ok
    assert any(r.check == "valid_stage" for r in res.reasons)


def test_spec_to_build_refused_on_public_wiring(tmp_path):
    """The lifecycle gate still enforces the standing rule mechanically:
    with live pages publicly wired and no acceptance/walkthrough evidence,
    spec->build is refused and the public-wiring check is named."""
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    for sku_id in ("OBS-01", "AUD-01"):
        s = sku(sku_id)
        nxt = next_stage(s, "spec")
        res = evaluate(s, "spec", nxt, ledger, ROOT)
        assert not res.ok, f"{sku_id}: expected refusal while publicly wired"
        by_check = {r.check: r for r in res.reasons}
        assert not by_check["not_publicly_wired"].ok, (
            f"{sku_id}: the not_publicly_wired check should name the public wiring"
        )


def test_build_to_test_refused_until_evidence(tmp_path):
    """Post-integration the sibling implementation modules exist, so the
    build->test gate no longer fails on implementation_present — it refuses
    on missing test evidence, which is the honest remaining gap."""
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    res = evaluate(sku("OBS-02"), "build", "test", ledger, ROOT)
    assert not res.ok
    by_check = {r.check: r for r in res.reasons}
    assert by_check["implementation_present"].ok
    assert not by_check["unit_tests_passing"].ok


# -- publish gate: red today, green when evidence exists -------------------
@pytest.mark.parametrize("sku_id", list(EXPECTED))
def test_publish_gate_red_today(sku_id, tmp_path):
    """Nothing has verified end-to-end evidence: the gate must refuse."""
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    res = evaluate_publish(sku(sku_id), ledger, ROOT)
    assert not res.ok
    failed = {r.check for r in res.reasons if not r.ok}
    assert "lifecycle_complete" in failed
    assert "acceptance_green" in failed
    assert "walkthrough_complete" in failed


def _fill_evidence(s, ledger):
    for n in range(1, s.acceptance_count + 1):
        ledger.append(s.sku_id, gates.KIND_ACCEPTANCE_RUN,
                      {"criterion": n, "passed": True, "failed": 0})
    ledger.append(s.sku_id, gates.KIND_WALKTHROUGH,
                  {"all_boxes_ticked": True, "conducted_by": "test-founder",
                   "date": "2026-09-29"})


@pytest.mark.parametrize("sku_id,final", [("OBS-01", "catalog"), ("OBS-02", "catalog"),
                                          ("OBS-03", "catalog"), ("AUD-01", "report"),
                                          ("AUD-02", "report"), ("OBS-ENT", "report")])
def test_publish_gate_passes_when_evidence_exists(sku_id, final, tmp_path):
    """Mechanism proof: with full evidence the gate CAN go green.

    Uses a throwaway ledger + lifecycle override. This proves the gate
    logic works; it makes no claim about current SKU status.
    """
    s = sku(sku_id)
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    _fill_evidence(s, ledger)
    res = evaluate_publish(s, ledger, ROOT, lifecycle_stage=final)
    assert res.ok, "; ".join(r.reason for r in res.reasons if not r.ok)


def test_publish_gate_partial_evidence_still_red(tmp_path):
    """Acceptance green but no walkthrough: still refused."""
    s = sku("OBS-02")
    ledger = ProofLedger(path=str(tmp_path / "ledger.json"))
    for n in range(1, s.acceptance_count + 1):
        ledger.append(s.sku_id, gates.KIND_ACCEPTANCE_RUN,
                      {"criterion": n, "passed": True, "failed": 0})
    res = evaluate_publish(s, ledger, ROOT, lifecycle_stage="catalog")
    assert not res.ok
    assert any(r.check == "walkthrough_complete" and not r.ok for r in res.reasons)
