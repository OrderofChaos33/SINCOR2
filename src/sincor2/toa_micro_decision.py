"""TOA Micro-Decision — single-read x402 paid resource (skills gap G7).

The bundled ``toa-decision`` skill on the agent card (6 AXM, 45s,
report-style) unbundled to ONE ranked-choice call: a buyer submits a
decision as structured options + observed signal series and gets back
the real TOA forecast — action, recommendation, confidence — priced as
the ``toa-micro-decision`` resource in ``config/x402_pricing.yaml``
($0.05 USDC on Base; price labelled [inference] in
skills-gap-map-2026-10-24 §4 item 4 — no direct TOA comparator exists;
anchored above a $0.005 probability read because TOA runs a Monte
Carlo per call, far below the bundled 6 AXM skill).

The decision itself is made by ``sincor2.toa_decide.toa_decide``,
REUSED UNCHANGED (Nadaraya-Watson forecaster + Monte Carlo simulator
+ WFC collapser, PR #503): fixed-temperature softmax confidence,
``TOA_MIN_CONFIDENCE`` (default 0.55) escalate threshold, and the
hard-gate regex blocklist that refuses treasury / merge / deploy /
private-key / production-type questions by design.

Honesty contract:

- The response IS TOA's output (``action``, ``recommended``,
  ``confidence``, ``threshold``, ``rationale``, ``scores``,
  ``forecast_detail``, ``ts``, ``decision_id``) — this wrapper adds
  NO interpretation, no re-ranking, no confidence massage. Confidence
  is forecast spread, not ground-truth accuracy; the ``escalate``
  state is returned as-is, never collapsed into a forced pick.
- ``escalate`` (top confidence below threshold) and ``refused``
  (hard-gated wording) are LEGITIMATE paid results — the product
  working as designed, not errors. They are served with HTTP 200
  after payment, exactly like a ``recommend``.
- Malformed payloads are rejected with 400 AFTER payment, per the
  platform's token-consumption pattern (the single-use access token
  is consumed by ``access_granted`` in the billing blueprint before
  this handler runs — same as the sibling paid resources).

Determinism: TOA seeds its Monte Carlo stream from ``decision_id``.
A buyer may supply their own ``decision_id``; when they don't, one is
derived deterministically from the canonical payload (sha256 of the
sorted-JSON options + context), so the same input replays to the same
decision_id — and therefore the same ranking, confidence and scores.

Limitation (deliberate, out of scope): this module NEVER calls
``toa_record_outcome``. Outcomes belong to the buyer, who observes
what actually happened after acting; a paid path that recorded its
own outcomes would corrupt TOA's feedback loop with self-graded
data. A buyer feedback endpoint is future work — the loop closes
today only when the buyer reports an outcome through the existing
``toa_record_outcome`` interface on their own side.

Payment is NOT handled here: the read sits behind the standard x402
flow (bound challenge -> verify with require_bound_challenge=True ->
single-use access token consumed by ``access_granted`` in the
billing blueprint).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Optional, Tuple

RESOURCE_ID = "toa-micro-decision"

_ALLOWED_TOP_KEYS = {"options", "decision_id", "context"}
_ALLOWED_CONTEXT_KEYS = {"horizon", "note"}
_MIN_OPTIONS = 2
_MAX_OPTIONS = 5
_MIN_SIGNALS = 2
_MAX_SIGNALS = 100
_MAX_ID_LEN = 64
_MAX_LABEL_LEN = 200
_MAX_DECISION_ID_LEN = 128
_MAX_NOTE_LEN = 200
_MIN_HORIZON = 1
_MAX_HORIZON = 24


# ---------------------------------------------------------------------------
# Payload validation (strict, fail closed — runs AFTER payment)
# ---------------------------------------------------------------------------

def _err(code: str) -> Tuple[int, Dict[str, Any]]:
    return 400, {"ok": False, "error": code, "resource": RESOURCE_ID}


def _is_finite_number(v: Any) -> bool:
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(float(v))
    )


def validate_payload(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    """Validate a buyer payload into toa_decide arguments.

    Returns ``(parsed, None)`` on success — parsed has ``decision_id``,
    ``options`` (signals coerced to float) and ``context`` — or
    ``(None, (status, body))`` with a 400 body on any malformation.
    """
    if not isinstance(payload, dict):
        return None, _err("payload_must_be_object")
    unknown = set(payload) - _ALLOWED_TOP_KEYS
    if unknown:
        return None, _err("unknown_payload_fields")

    options_in = payload.get("options")
    if not isinstance(options_in, list):
        return None, _err("options_required")
    if not (_MIN_OPTIONS <= len(options_in) <= _MAX_OPTIONS):
        return None, _err("options_count_out_of_range")

    options = []
    seen_ids = set()
    for raw in options_in:
        if not isinstance(raw, dict):
            return None, _err("option_must_be_object")
        if set(raw) - {"id", "label", "signals"}:
            return None, _err("unknown_option_fields")
        oid = raw.get("id")
        if not isinstance(oid, str) or not oid.strip():
            return None, _err("option_id_required")
        oid = oid.strip()
        if len(oid) > _MAX_ID_LEN:
            return None, _err("option_id_too_long")
        if oid in seen_ids:
            return None, _err("duplicate_option_id")
        seen_ids.add(oid)
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            return None, _err("option_label_required")
        label = label.strip()
        if len(label) > _MAX_LABEL_LEN:
            return None, _err("option_label_too_long")
        signals = raw.get("signals")
        if not isinstance(signals, list):
            return None, _err("option_signals_required")
        if not (_MIN_SIGNALS <= len(signals) <= _MAX_SIGNALS):
            return None, _err("option_signals_length_out_of_range")
        if not all(_is_finite_number(v) for v in signals):
            return None, _err("option_signals_must_be_finite_numbers")
        options.append({
            "id": oid,
            "label": label,
            "signals": [float(v) for v in signals],
        })

    context: Dict[str, Any] = {}
    context_in = payload.get("context")
    if context_in is not None:
        if not isinstance(context_in, dict):
            return None, _err("context_must_be_object")
        if set(context_in) - _ALLOWED_CONTEXT_KEYS:
            return None, _err("unknown_context_fields")
        horizon = context_in.get("horizon")
        if horizon is not None:
            if (
                not isinstance(horizon, int)
                or isinstance(horizon, bool)
                or not (_MIN_HORIZON <= horizon <= _MAX_HORIZON)
            ):
                return None, _err("context_horizon_out_of_range")
            context["horizon"] = horizon
        note = context_in.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note) > _MAX_NOTE_LEN:
                return None, _err("context_note_invalid")
            context["note"] = note

    decision_id = payload.get("decision_id")
    if decision_id is not None:
        if (
            not isinstance(decision_id, str)
            or not decision_id.strip()
            or len(decision_id) > _MAX_DECISION_ID_LEN
        ):
            return None, _err("decision_id_invalid")
        decision_id = decision_id.strip()
    else:
        decision_id = derive_decision_id(options, context)

    return {
        "decision_id": decision_id,
        "options": options,
        "context": context,
    }, None


def derive_decision_id(options: Any, context: Any) -> str:
    """Deterministic decision_id for a payload that didn't supply one:
    same options + context -> same id -> TOA replays identically."""
    canonical = json.dumps(
        {"options": options, "context": context},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"toa-micro-{digest[:24]}"


# ---------------------------------------------------------------------------
# Serve path (called by the billing blueprint ONLY after the x402
# access token has been consumed)
# ---------------------------------------------------------------------------

def serve_toa_micro_decision(
    payload: Optional[Dict[str, Any]],
) -> Tuple[int, Dict[str, Any]]:
    """Run one paid TOA micro-decision.

    Returns TOA's actual output verbatim (plus the ok/resource
    envelope the sibling paid resources use). ``recommend``,
    ``escalate`` and ``refused`` are all HTTP 200 paid results;
    malformed payloads are 400; a TOA machinery failure is 500.
    Never calls toa_record_outcome (see module docstring).
    """
    parsed, error = validate_payload(payload)
    if error is not None:
        return error
    assert parsed is not None
    from sincor2.toa_decide import toa_decide

    try:
        result = toa_decide(
            parsed["decision_id"],
            parsed["options"],
            context=parsed["context"],
        )
    except Exception:
        return 500, {
            "ok": False,
            "error": "toa_unavailable",
            "resource": RESOURCE_ID,
        }
    return 200, {"ok": True, "resource": RESOURCE_ID, **result}
