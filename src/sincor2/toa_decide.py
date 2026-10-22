"""TOA uncertainty-resolver interface for the 24/7 loops.

Founder directive (2026-10-21): "TOA is the tool-orchestration layer agents
use to forecast — when in doubt, use TOA."

This module wires the REAL TOA machinery (:class:`agents.toa.TOAOrchestrator`
— Nadaraya-Watson kernel forecaster + Monte Carlo simulator + WFC collapser)
into a small decision interface for loop workers:

    result = toa_decide(
        decision_id="buildout-dispatch-2026-10-21",
        options=[
            {"id": "wave-a", "label": "Dispatch buildout wave A",
             "signals": [0.62, 0.65, 0.61, 0.70]},
            {"id": "hold", "label": "Hold this cycle",
             "signals": [0.50, 0.51, 0.49, 0.50]},
        ],
        context={"horizon": 6},
    )
    # -> {"action": "recommend", "recommended": "wave-a",
    #     "confidence": 0.83, ...}

Design notes
------------
* Each option's ``signals`` series is run through a real TOA pipeline;
  the option score is the probability-weighted expected utility
  of its collapsed action plan.  This is a forecast, not a heuristic.
* Confidence is a fixed-temperature (0.05) softmax over option scores, on
  the simulator's absolute [0, 1] expected-utility scale: a ~1pt edge is a
  coin flip, ~2pt leans recommend, ~5pt is decisive.  Identical scores ->
  1/n.  Single-option decisions are go/no-go against a null baseline
  (doing nothing = 0 utility).  A self-scaling temperature was tried and
  rejected: it amplifies noise-level spreads into false-high confidence.
* Below ``TOA_MIN_CONFIDENCE`` (default 0.55) the interface returns
  ``action: "escalate"`` — low-confidence forecasts never trigger
  autonomous action.
* Hard-gate categories are REFUSED outright (never forecast, never
  recommended): treasury/fund movement, merges, deploys, private keys,
  production/mainnet actions, broadcasts, withdrawals.  TOA advises; it
  never authorizes.
* Every call appends to an append-only JSONL decision log; outcomes are
  recorded later via :func:`toa_record_outcome` and fed back into TOA's
  own feedback loop (forecast -> act -> observe -> re-forecast).
* Deterministic per decision_id: the Monte Carlo stream is seeded from
  sha256(decision_id), so the same decision replays identically.  Different
  decisions get different streams.

Safety
------
This module never touches the money path, auth, ledgers, contracts, keys,
or production.  It produces advice + an audit trail, nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.toa_decide")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MIN_CONFIDENCE = 0.55
DEFAULT_OBJECTIVES: Dict[str, float] = {
    "revenue": 0.35,
    "risk": 0.30,
    "timeline": 0.20,
    "compliance": 0.15,
}
# Collapse threshold low enough that a real action plan survives: with the
# default 50 forecast paths, probabilities normalise to ~0.02 each, so the
# stock 0.05 threshold yields an empty plan by construction.
COLLAPSE_THRESHOLD = 0.01


def _repo_root() -> Path:
    # src/sincor2/toa_decide.py -> up 3 = repo root
    return Path(__file__).resolve().parents[2]


def _decision_log_path() -> Path:
    override = os.environ.get("TOA_DECISION_LOG")
    if override:
        return Path(override)
    return _repo_root() / "data" / "toa_decisions.jsonl"


def _min_confidence(explicit: Optional[float] = None) -> float:
    if explicit is not None:
        return float(explicit)
    return float(os.environ.get("TOA_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE))


# ---------------------------------------------------------------------------
# Hard-gate refusal
# ---------------------------------------------------------------------------
#
# TOA advises; it never authorizes.  Anything in these categories is refused
# outright — the worker must take it to the founder instead.

import re as _re

_HARD_GATE_PATTERNS = [
    _re.compile(r"treasury(?!_inflow)", _re.IGNORECASE),  # treasury_inflow is a legit objective name
    _re.compile(r"\bmerg(e|ing|ed)\b", _re.IGNORECASE),
    _re.compile(r"deploy", _re.IGNORECASE),  # covers deploy/redeploy/deployment
    _re.compile(r"private[_\s]key", _re.IGNORECASE),
    _re.compile(r"move\s+funds?", _re.IGNORECASE),
    _re.compile(r"fund\s+movement", _re.IGNORECASE),
    _re.compile(r"\bproduction\b", _re.IGNORECASE),
    _re.compile(r"\bmainnet\b", _re.IGNORECASE),
    _re.compile(r"broadcast", _re.IGNORECASE),
    _re.compile(r"\bwithdraw\w*\b", _re.IGNORECASE),
]

_HARD_GATE_LABELS = {
    0: "treasury",       # r"treasury(?!_inflow)"
    1: "merge",          # r"\bmerg(e|ing|ed)\b"
    2: "deploy",         # r"deploy"
    3: "private_key",    # r"private[_\s]key"
    4: "fund_movement",  # r"move\s+funds?"
    5: "fund_movement",  # r"fund\s+movement"
    6: "production",     # r"\bproduction\b"
    7: "mainnet",        # r"\bmainnet\b"
    8: "broadcast",      # r"broadcast"
    9: "withdrawal",     # r"\bwithdraw\w*\b"
}


def _hard_gate_hit(text: str) -> Optional[str]:
    for i, pat in enumerate(_HARD_GATE_PATTERNS):
        if pat.search(text):
            return _HARD_GATE_LABELS[i]
    return None


# ---------------------------------------------------------------------------
# TOA machinery (lazy import — agents/ lives at repo root)
# ---------------------------------------------------------------------------

_orchestrator = None


def _get_orchestrator():
    """Module-singleton TOAOrchestrator so feedback accumulates in-process."""
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator
    try:
        from agents.toa import TOAOrchestrator
        from agents.toa.config import TOAConfig
    except ImportError as exc:  # pragma: no cover — repo always ships agents/toa
        raise RuntimeError(
            "TOA machinery not importable (agents.toa missing); "
            "toa_decide refuses to decide without the real forecaster"
        ) from exc
    cfg = TOAConfig(collapse_threshold=COLLAPSE_THRESHOLD)
    _orchestrator = TOAOrchestrator(config=cfg)
    return _orchestrator


def _seed_from_decision(decision_id: str) -> int:
    """Seed all Monte Carlo randomness deterministically from decision_id.

    Covers the global ``random`` module, the global numpy RNG, AND the
    forecaster's private ``random.Random`` instance (which ignores global
    seeding) — the caller re-installs a seeded forecaster on the
    orchestrator.  Returns the seed int.
    """
    digest = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()
    seed = int(digest, 16) % (2**32)
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    return seed


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
#
# Option score = expected utility = Σ_path P(path) × utility(path), taken
# over ALL evaluated forecast paths (not just the collapsed top-k).  This is
# the honest forecast expectation of the option's future performance, on the
# simulator's [0, 1] utility scale.
#
# Confidence is a softmax over option scores with a FIXED temperature of
# 0.05 expected-utility points (documented, absolute scale): a ~1pt edge is
# a coin flip (escalate), ~2pt edge is lean-recommend, ~5pt edge is decisive.
# A self-scaling temperature was tried and rejected: it amplifies
# noise-level spreads into false-high confidence.


def _expected_utility(evaluated_paths: List[Dict[str, Any]]) -> float:
    total = 0.0
    for p in evaluated_paths:
        total += float(p.get("probability", 0.0) or 0.0) * float(
            p.get("utility_score", 0.0) or 0.0
        )
    return total


_CONFIDENCE_TEMPERATURE = 0.05


def _confidence_from_scores(scores: List[float]) -> List[float]:
    """Fixed-temperature softmax confidence per option (temperature = 0.05)."""
    n = len(scores)
    if n == 0:
        return []
    if max(scores) - min(scores) < 1e-12:
        return [1.0 / n] * n
    top = max(scores)
    weights = [math.exp((s - top) / _CONFIDENCE_TEMPERATURE) for s in scores]
    total = sum(weights)
    return [w / total for w in weights]


# ---------------------------------------------------------------------------
# Decision log (append-only JSONL)
# ---------------------------------------------------------------------------


def _append_log(record: Dict[str, Any]) -> None:
    path = _decision_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def toa_decide(
    decision_id: str,
    options: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    objectives: Optional[Dict[str, float]] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Forecast-and-recommend under uncertainty via the real TOA pipeline.

    Parameters
    ----------
    decision_id:
        Stable identifier for this decision (drives deterministic seeding
        and the decision log).
    options:
        Non-empty list of ``{"id": str, "label": str, "signals": [float, ...]}``.
        ``signals`` is the observed historical performance series for that
        option; each option is forecast independently.
    context:
        Optional extra context; ``horizon`` (int) sets the forecast horizon.
    objectives:
        Optional TOA objective-weight override (names must be registered:
        revenue, treasury_inflow, risk, timeline, compliance, governance).
    min_confidence:
        Override for the escalate threshold (env ``TOA_MIN_CONFIDENCE``,
        default 0.55).

    Returns
    -------
    dict with ``action`` one of ``recommend`` | ``escalate`` | ``refused``.
    """
    context = dict(context or {})
    threshold = _min_confidence(min_confidence)
    ts = _utcnow_iso()

    # ---- hard-gate refusal (before any forecasting) ----
    haystack = " ".join(
        [str(decision_id)]
        + [str(o.get("id", "")) + " " + str(o.get("label", "")) for o in (options or [])]
        + [str(context.get("note", ""))]
    )
    hit = _hard_gate_hit(haystack)
    if hit:
        result: Dict[str, Any] = {
            "decision_id": decision_id,
            "action": "refused",
            "recommended": None,
            "confidence": 0.0,
            "threshold": threshold,
            "rationale": (
                f"Hard-gate category '{hit}' never goes through TOA — "
                "treasury/funds, merges, deploys, legal, private keys and "
                "production actions stay founder-gated. TOA advises; it never authorizes."
            ),
            "scores": {},
            "forecast_detail": {},
            "ts": ts,
        }
        _append_log({"type": "decision", "outcome": None, **result,
                     "options": [o.get("id") for o in (options or [])]})
        return result

    # ---- input validation (fail closed) ----
    if not options:
        raise ValueError("toa_decide requires a non-empty options list")
    for o in options:
        if not o.get("id") or not isinstance(o.get("signals"), list) or len(o["signals"]) < 2:
            raise ValueError(
                f"option {o.get('id')!r} needs an id and a signals series of length >= 2"
            )
        o["signals"] = [float(v) for v in o["signals"]]

    horizon = int(context.get("horizon", 6))
    objectives = objectives or dict(DEFAULT_OBJECTIVES)

    # ---- real TOA pipeline per option ----
    # Uses the orchestrator's own wired components (forecaster -> simulator ->
    # collapser) directly, because TOAOrchestrator.run() returns only counts,
    # not the evaluated path list the expectation needs.  Same machinery,
    # same feedback merge as run().
    seed = _seed_from_decision(decision_id)
    orch = _get_orchestrator()
    # The forecaster owns a private random.Random(seed) that ignores global
    # seeding: reinstall a decision-seeded instance so replays are identical.
    # The orchestrator (and its feedback agent) stays the module singleton,
    # so the observe -> re-forecast link is preserved.
    from agents.toa.forecaster import KernelForecaster

    orch.forecaster = KernelForecaster(config=orch.config, seed=seed)
    feedback_summary = orch.feedback_agent.get_feedback_summary()
    scores: Dict[str, float] = {}
    detail: Dict[str, Any] = {}
    for o in options:
        opt_ctx = {k: v for k, v in context.items() if k != "horizon"}
        opt_ctx["values"] = list(o["signals"])
        opt_ctx["horizon"] = horizon
        enriched = {**opt_ctx, **feedback_summary}
        forecast_paths = orch.forecaster.forecast(context=enriched, horizon=horizon)
        evaluated = (
            orch.simulator.evaluate(paths=forecast_paths, objectives=objectives)
            if forecast_paths
            else []
        )
        plan = orch.collapser.collapse(evaluated_paths=evaluated)
        score = _expected_utility(evaluated)
        scores[o["id"]] = score
        top = plan[0] if plan else {}
        detail[o["id"]] = {
            "forecast_paths": len(forecast_paths),
            "evaluated_paths": len(evaluated),
            "plan_size": len(plan),
            "top_composite": float(top.get("composite_score", 0.0) or 0.0),
            "top_probability": float(top.get("probability", 0.0) or 0.0),
            "expected_utility": score,
        }

    # ---- confidence + gating ----
    ordered_ids = [o["id"] for o in options]
    ordered_scores = [scores[i] for i in ordered_ids]
    # Single-option decisions are go/no-go against a null baseline
    # (doing nothing has zero expected utility by definition).
    conf_scores = ordered_scores + [0.0] if len(ordered_ids) == 1 else ordered_scores
    confidences = _confidence_from_scores(conf_scores)
    best_idx = max(range(len(ordered_ids)), key=lambda i: (confidences[i], ordered_scores[i]))
    best_id = ordered_ids[best_idx]
    best_conf = confidences[best_idx]

    if best_conf >= threshold:
        action = "recommend"
        rationale = (
            f"TOA forecast selects '{best_id}' with confidence {best_conf:.2f} "
            f"(threshold {threshold:.2f}); expected utility "
            f"{ordered_scores[best_idx]:.4f} vs runner-up "
            f"{sorted(ordered_scores, reverse=True)[1] if len(ordered_scores) > 1 else 0.0:.4f}."
        )
    else:
        action = "escalate"
        rationale = (
            f"Top option '{best_id}' confidence {best_conf:.2f} below threshold "
            f"{threshold:.2f} — forecast too close to call; escalating to "
            f"founder/operator instead of acting autonomously."
        )
        result2 = {
            "decision_id": decision_id,
            "action": action,
            "recommended": None,
            "confidence": best_conf,
            "threshold": threshold,
            "rationale": rationale,
            "scores": scores,
            "forecast_detail": detail,
            "ts": ts,
        }
        _append_log({"type": "decision", "outcome": None, **result2,
                     "options": ordered_ids})
        return result2

    result = {
        "decision_id": decision_id,
        "action": action,
        "recommended": best_id,
        "confidence": best_conf,
        "threshold": threshold,
        "rationale": rationale,
        "scores": scores,
        "forecast_detail": detail,
        "ts": ts,
    }
    _append_log({"type": "decision", "outcome": None, **result, "options": ordered_ids})
    return result


def toa_record_outcome(decision_id: str, outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Record the observed outcome of a past decision (forecast -> act -> observe).

    Appends an outcome line to the decision log and ingests it into TOA's
    own feedback loop so the next forecast learns from this result
    (the re-forecast half of the self-healing loop).
    """
    ts = _utcnow_iso()
    record = {"type": "outcome", "decision_id": decision_id, "outcome": outcome, "ts": ts}
    _append_log(record)
    try:
        orch = _get_orchestrator()
        orch.ingest_feedback({
            "source": "toa_decide",
            "decision_id": decision_id,
            "timestamp": ts,
            "payload": {
                "success": bool(outcome.get("success", False)),
                "quality_rating": float(outcome.get("quality", 3.0)),
                **{k: v for k, v in outcome.items() if k not in ("success", "quality")},
            },
        })
        feedback_ingested = True
    except Exception as exc:  # feedback must never break outcome recording
        logger.warning("toa_record_outcome: feedback ingest failed: %s", exc)
        feedback_ingested = False
    record["feedback_ingested"] = feedback_ingested
    return record
