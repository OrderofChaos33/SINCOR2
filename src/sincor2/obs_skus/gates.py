"""Stage gates for the observability / audit SKU pipeline.

Two lifecycles, one discipline:

- Software SKUs (OBS-01, OBS-02, OBS-03):
    spec -> build -> test -> audit -> product -> catalog
  (mirrors ``defi/gates.py`` exactly)

- Engagement SKUs (AUD-01, AUD-02, OBS-ENT) — service engagements, so
  software deploy gates would be fiction. Instead:
    spec -> playbook -> rehearsal -> delivery -> report

Stages are strictly ordered; no stage may be skipped. Evidence is verified
from repo files and the proof ledger where possible. Anything that cannot
be verified from repo evidence is an explicit manual-checklist item in the
refusal reasons.

PUBLISH GATE (standing rule, founder 2026-09-29 — working products only):
a SKU's draft page (templates/products/drafts/) may be promoted to the
public surface (route, nav, pricing, sitemap) ONLY after
``evaluate_publish`` returns ok. The checks are:

1. lifecycle_complete — the SKU reached the final lifecycle stage
   ("catalog" for software, "report" for engagement).
2. acceptance_green — the proof ledger holds a passing acceptance_run
   entry for EVERY numbered acceptance criterion in the SKU's spec.
3. walkthrough_complete — the proof ledger holds a walkthrough entry with
   all checklist boxes ticked (founder-conducted, end-to-end).
4. draft_claims_clean — static scan of the draft page: no banned phrases
   (live claims, guarantees, certifications, treasury details).

``find_public_wiring`` is the tripwire: it lists every public-surface
location that references a SKU. While the publish gate is red, that list
must be empty.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .registry import SKU, SKU_BY_ID, sibling_module_path

# Reuses the DeFi arm's append-only proof ledger (same repo, always present).
from sincor2.defi.proof_ledger import (
    KIND_AUDIT_REPORT,
    KIND_TEST_RUN,
    ProofLedger,
)


SOFTWARE_STAGES: Tuple[str, ...] = ("spec", "build", "test", "audit", "product", "catalog")
ENGAGEMENT_STAGES: Tuple[str, ...] = ("spec", "playbook", "rehearsal", "delivery", "report")

FINAL_STAGE = {"software": "catalog", "engagement": "report"}

# OBS-arm proof-ledger evidence kinds (test_run / audit_report reused from defi).
KIND_ACCEPTANCE_RUN = "acceptance_run"
KIND_WALKTHROUGH = "walkthrough"
KIND_REHEARSAL = "rehearsal"
KIND_DELIVERY_RECEIPT = "delivery_receipt"
KIND_MARKETING_APPROVED = "marketing_approved"
KIND_PRICING_LIVE = "pricing_live"

OBS_KINDS: Tuple[str, ...] = (
    KIND_ACCEPTANCE_RUN,
    KIND_WALKTHROUGH,
    KIND_REHEARSAL,
    KIND_DELIVERY_RECEIPT,
    KIND_MARKETING_APPROVED,
    KIND_PRICING_LIVE,
)


class ObsProofLedger(ProofLedger):  # type: ignore[misc]
    """ProofLedger extended with the OBS-arm evidence kinds.

    Reuses the defi ledger's append-only semantics (no update/delete,
    atomic tmp+replace writes) without modifying the DeFi arm's module.
    """

    def append(self, sku: str, kind: str, details: Dict[str, Any],
               commit: Optional[str] = None,
               recorded_by: str = "operator") -> Dict[str, Any]:
        if kind in OBS_KINDS:
            import time as _time
            import uuid as _uuid
            entry = {
                "entry_id": "ev_" + _uuid.uuid4().hex[:12],
                "sku": str(sku),
                "kind": kind,
                "timestamp": _time.time(),
                "commit": commit,
                "recorded_by": recorded_by,
                "details": dict(details or {}),
            }
            self._entries.append(entry)
            self._save()
            return dict(entry)
        return super().append(sku, kind, details, commit=commit,
                             recorded_by=recorded_by)

# Phrases that must never appear in a customer-facing draft page.
# ("real-time" alone is allowed only when the walkthrough proved it; the
# blanket ban here covers claims we cannot verify statically.)
BANNED_DRAFT_PHRASES: Tuple[str, ...] = (
    "live now",
    "guaranteed",
    "certified",
    "soc 2",
    "soc2",
    "certik",
    "audited by",
    "risk-free",
    "treasury",
    "0x09e289",
)

# Public-surface files that must not reference an unpublished SKU.
# (slug, id, and draft filename stems are all scanned.)
PUBLIC_SURFACE_FILES: Tuple[str, ...] = (
    os.path.join("src", "sincor2", "mvp_blueprints", "pages.py"),
    os.path.join("templates", "_pro_nav.html"),
    os.path.join("templates", "pricing.html"),
    os.path.join("templates", "sitemap.html"),
    os.path.join("templates", "products.html"),
)


def _slug(sku: SKU) -> str:
    stem = sku.draft_page_relpath.rsplit("/", 1)[-1]
    return stem[:-len(".html")] if stem.endswith(".html") else stem


def find_public_wiring(sku: SKU, root: str) -> List[str]:
    """Return public-surface locations that reference this SKU.

    Empty list = correctly unpublished. Non-empty = standing-rule violation
    (or a completed, publish-gated promotion — check evaluate_publish).
    """
    needles = {_slug(sku).lower(), sku.sku_id.lower(), sku.name.lower()}
    hits: List[str] = []
    for rel in PUBLIC_SURFACE_FILES:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read().lower()
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                hits.append(f"{rel}: contains {needle!r}")
                break
    return hits


@dataclass
class CheckResult:
    check: str
    ok: bool
    reason: str
    evidence: str = "unverified"


@dataclass
class GateResult:
    ok: bool
    reasons: List[CheckResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": ok,
            "reasons": [r.__dict__ for r in self.reasons],
        }


def _stages_for(sku: SKU) -> Tuple[str, ...]:
    return SOFTWARE_STAGES if sku.lifecycle == "software" else ENGAGEMENT_STAGES


def next_stage(sku: SKU, stage: str) -> Optional[str]:
    stages = _stages_for(sku)
    try:
        idx = stages.index(stage)
    except ValueError:
        return None
    return stages[idx + 1] if idx + 1 < len(stages) else None


def _join(root: str, rel: str) -> str:
    return os.path.join(root, rel)


# -- shared evidence helpers ----------------------------------------------
def _passing_entries(ledger: Any, sku: str, kind: str) -> List[Dict[str, Any]]:
    out = []
    for e in ledger.read(sku=sku, kind=kind):
        d = e.get("details", {})
        if d.get("failed", 0) == 0 and d.get("passed", 0) > 0:
            out.append(e)
    return out


def _check_spec_exists(sku: SKU, root: str, ledger: Any) -> CheckResult:
    path = _join(root, sku.spec_relpath)
    ok = os.path.exists(path)
    return CheckResult("spec_exists", ok,
                       "spec on file" if ok else f"missing spec: {sku.spec_relpath}",
                       path if ok else "unverified")


def _check_draft_page_present(sku: SKU, root: str, ledger: Any) -> CheckResult:
    path = _join(root, sku.draft_page_relpath)
    ok = os.path.exists(path)
    return CheckResult("draft_page_present", ok,
                       "draft page on file (unrouted)" if ok
                       else f"missing draft: {sku.draft_page_relpath}",
                       path if ok else "unverified")


def _check_not_publicly_wired(sku: SKU, root: str, ledger: Any) -> CheckResult:
    hits = find_public_wiring(sku, root)
    return CheckResult("not_publicly_wired", not hits,
                       "no public-surface references" if not hits
                       else "PUBLIC WIRING FOUND: " + "; ".join(hits),
                       "public-surface scan" if not hits else "; ".join(hits))


def _check_implementation_present(sku: SKU, root: str, ledger: Any) -> CheckResult:
    rel = sibling_module_path(sku.sku_id)
    path = _join(root, rel)
    ok = os.path.exists(path)
    return CheckResult("implementation_present", ok,
                       "implementation present" if ok else f"not yet built: {rel}",
                       path if ok else "unverified")


def _check_unit_tests_passing(sku: SKU, root: str, ledger: Any) -> CheckResult:
    runs = _passing_entries(ledger, sku.sku_id, KIND_TEST_RUN)
    if runs:
        return CheckResult("unit_tests_passing", True,
                           f"{len(runs)} passing test-run(s) in proof ledger",
                           runs[-1]["entry_id"])
    return CheckResult("unit_tests_passing", False,
                       "no passing unit-test evidence in proof ledger "
                       "(manual: run the SKU's test suite and record a test_run entry)",
                       "unverified")


def _check_audit_report(sku: SKU, root: str, ledger: Any) -> CheckResult:
    reports = ledger.read(sku=sku.sku_id, kind=KIND_AUDIT_REPORT)
    if not reports:
        return CheckResult("audit_report", False,
                           "no audit report in proof ledger", "unverified")
    latest = reports[-1]
    open_critical = int(latest.get("details", {}).get("open_critical", 0))
    if open_critical > 0:
        return CheckResult("audit_report", False,
                           f"audit report has {open_critical} open critical finding(s)",
                           latest["entry_id"])
    return CheckResult("audit_report", True, "audit report clean (0 open criticals)",
                       latest["entry_id"])


def _check_playbook_present(sku: SKU, root: str, ledger: Any) -> CheckResult:
    rel = sku.spec_relpath.replace("-spec.md", "-playbook.md")
    path = _join(root, rel)
    ok = os.path.exists(path)
    return CheckResult("playbook_present", ok,
                       "engagement playbook on file" if ok else f"missing playbook: {rel}",
                       path if ok else "unverified")


def _check_rehearsal_logged(sku: SKU, root: str, ledger: Any) -> CheckResult:
    runs = _passing_entries(ledger, sku.sku_id, KIND_REHEARSAL)
    if runs:
        return CheckResult("rehearsal_logged", True,
                           f"{len(runs)} passing rehearsal(s) in proof ledger",
                           runs[-1]["entry_id"])
    return CheckResult("rehearsal_logged", False,
                       "no passing engagement rehearsal in proof ledger", "unverified")


def _check_delivery_receipt(sku: SKU, root: str, ledger: Any) -> CheckResult:
    receipts = ledger.read(sku=sku.sku_id, kind=KIND_DELIVERY_RECEIPT)
    if receipts and receipts[-1].get("details", {}).get("delivered"):
        return CheckResult("delivery_receipt", True, "delivery receipt recorded",
                           receipts[-1]["entry_id"])
    return CheckResult("delivery_receipt", False,
                       "no delivery receipt in proof ledger", "unverified")


SOFTWARE_TRANSITION_CHECKS = {
    ("spec", "build"): [_check_spec_exists, _check_draft_page_present, _check_not_publicly_wired],
    ("build", "test"): [_check_implementation_present, _check_unit_tests_passing],
    ("test", "audit"): [_check_unit_tests_passing, _check_audit_report],
    ("audit", "product"): [_check_audit_report, _check_not_publicly_wired],
    ("product", "catalog"): [_check_not_publicly_wired],
}

ENGAGEMENT_TRANSITION_CHECKS = {
    ("spec", "playbook"): [_check_spec_exists, _check_draft_page_present, _check_not_publicly_wired],
    ("playbook", "rehearsal"): [_check_playbook_present],
    ("rehearsal", "delivery"): [_check_rehearsal_logged],
    ("delivery", "report"): [_check_delivery_receipt],
}


def evaluate(sku: SKU, from_stage: str, to_stage: str, ledger: Any,
             root: str) -> GateResult:
    """Evaluate the lifecycle gate for sku: from_stage -> to_stage."""
    stages = _stages_for(sku)
    reasons: List[CheckResult] = []
    if from_stage not in stages or to_stage not in stages:
        reasons.append(CheckResult("valid_stage", False,
                                   f"unknown stage for {sku.lifecycle} lifecycle: "
                                   f"{from_stage!r} -> {to_stage!r}", "unverified"))
        return GateResult(False, reasons)
    if to_stage != next_stage(sku, from_stage):
        reasons.append(CheckResult(
            "no_skip", False,
            f"stages are strictly ordered: from '{from_stage}' the only allowed "
            f"next stage is '{next_stage(sku, from_stage)}', not '{to_stage}'",
            "gates.py"))
        return GateResult(False, reasons)
    table = SOFTWARE_TRANSITION_CHECKS if sku.lifecycle == "software" else ENGAGEMENT_TRANSITION_CHECKS
    for fn in table.get((from_stage, to_stage), []):
        reasons.append(fn(sku, root, ledger))
    return GateResult(ok=all(r.ok for r in reasons), reasons=reasons)


# -- publish gate ----------------------------------------------------------
def _check_lifecycle_complete(sku: SKU, stage: str, root: str, ledger: Any) -> CheckResult:
    want = FINAL_STAGE[sku.lifecycle]
    ok = stage == want
    return CheckResult("lifecycle_complete", ok,
                       f"lifecycle at '{stage}'" if ok
                       else f"lifecycle at '{stage}', need '{want}' before publishing",
                       "registry lifecycle_stage")


def _check_acceptance_green(sku: SKU, stage: str, root: str, ledger: Any) -> CheckResult:
    want = set(range(1, sku.acceptance_count + 1))
    have = set()
    for e in ledger.read(sku=sku.sku_id, kind=KIND_ACCEPTANCE_RUN):
        d = e.get("details", {})
        if d.get("passed") is True and isinstance(d.get("criterion"), int):
            have.add(d["criterion"])
    missing = sorted(want - have)
    if not missing:
        return CheckResult("acceptance_green", True,
                           f"all {sku.acceptance_count} acceptance criteria verified",
                           f"{len(ledger.read(sku=sku.sku_id, kind=KIND_ACCEPTANCE_RUN))} entries")
    return CheckResult("acceptance_green", False,
                       f"acceptance criteria unverified: {missing}",
                       "unverified")


def _check_walkthrough_complete(sku: SKU, stage: str, root: str, ledger: Any) -> CheckResult:
    entries = ledger.read(sku=sku.sku_id, kind=KIND_WALKTHROUGH)
    if entries:
        d = entries[-1].get("details", {})
        if d.get("all_boxes_ticked") is True and d.get("conducted_by"):
            return CheckResult("walkthrough_complete", True,
                               f"live walkthrough completed by {d['conducted_by']}",
                               entries[-1]["entry_id"])
    return CheckResult("walkthrough_complete", False,
                       "no completed live-walkthrough checklist in proof ledger "
                       "(manual: run the spec's walkthrough, tick every box, record it)",
                       "unverified")


def _check_draft_claims_clean(sku: SKU, stage: str, root: str, ledger: Any) -> CheckResult:
    path = _join(root, sku.draft_page_relpath)
    if not os.path.exists(path):
        return CheckResult("draft_claims_clean", False,
                           f"draft page missing: {sku.draft_page_relpath}", "unverified")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().lower()
    except OSError:
        return CheckResult("draft_claims_clean", False, "draft page unreadable",
                           "unverified")
    found = [p for p in BANNED_DRAFT_PHRASES if p in text]
    if found:
        return CheckResult("draft_claims_clean", False,
                           f"banned claim(s) in draft: {found}", path)
    return CheckResult("draft_claims_clean", True,
                       "draft copy clean (no banned claims)", path)


PUBLISH_CHECKS = (
    _check_lifecycle_complete,
    _check_acceptance_green,
    _check_walkthrough_complete,
    _check_draft_claims_clean,
)


def evaluate_publish(sku: SKU, ledger: Any, root: str,
                     lifecycle_stage: Optional[str] = None) -> GateResult:
    """The standing-rule gate: may this SKU's draft page go public?

    Returns ok=True only when the lifecycle is complete, every numbered
    acceptance criterion is verified in the proof ledger, the live
    walkthrough checklist is fully ticked, and the draft copy is clean.
    ``lifecycle_stage`` overrides the registry's recorded stage (used by
    tests to prove the gate CAN pass when evidence exists).
    """
    stage = lifecycle_stage if lifecycle_stage is not None else sku.lifecycle_stage
    reasons = [fn(sku, stage, root, ledger) for fn in PUBLISH_CHECKS]
    return GateResult(ok=all(r.ok for r in reasons), reasons=reasons)
