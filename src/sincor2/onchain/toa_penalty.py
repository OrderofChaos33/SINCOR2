"""Dynamic slashing curve + deterministic TOA penalty encodings (CNP-3).

This module is PURE: no ledger imports, no I/O, no randomness, no
wall-clock.  Everything it computes is a deterministic function of its
inputs, in exact integer (basis-point) arithmetic.

Curve (offense-scaled escalation, hard-capped)
---------------------------------------------
The ratified anchors stay the defaults:

  * ghosting             -> 10_000 bps (100 % of locked stake)
  * quality failure      ->  5_000 bps ( 50 % of locked stake)

The dynamic curve is an EXPLICITLY GATED escalation ON TOP of those
anchors, never a replacement: when the gate is off, the anchors apply
exactly.  When the gate is on, an offense may escalate as

    slash_bps = min(HARD_CAP_BPS,
                    anchor_bps * severity_factor * repeat_factor)

with severity_factor = 1 + severity/100 (in [1, 2]) and repeat_factor =
1.25 ** min(prior_offenses, MAX_PRIOR_OFFENSES) (saturating).

The escalation is one-sided: severity 0 / first offense reproduces the
anchor exactly, and repeat-offender escalation SATURATES at the hard cap
(100 % of the locked stake — a slash can never take more than the lock,
whatever the inputs).

Why the constants are NOT env-tunable: the founder's standing policy
rejects adaptive/roving economic parameters (certainty deters; tuning is
attack surface).  Like COLLATERAL_ALPHA in stake_ledger.py, these are
launch-fixed module constants; changing them is a governance action.
The only runtime knob is the boolean gate SINCOR_DYNAMIC_SLASH_ENABLED,
default OFF (fail-closed).

TOA decision -> penalty-parameter encoding
------------------------------------------
A TOA (Temporal Optimization Agent) penalty decision is a dict
conforming to TOA_PENALTY_SCHEMA.  encode_toa_penalty() turns it into a
deterministic byte string:

  * keys sorted, fixed separators, UTF-8, NFC-normalized strings,
  * integers only for numeric fields (bools and floats REJECTED —
    float formatting is platform-locale sensitive and would break
    byte-equality across runtimes),
  * unknown fields REJECTED (one decision -> one encoding, no
    JSON-whitespace / field-smuggling malleability),
  * a version tag (b"TOAPEN\x01:") prevents cross-protocol confusion,
  * NO timestamps, run ids, or other non-deterministic fields enter the
    encoding.  When the decision is applied, the ledger records the
    wall-clock in its own audit event, next to the encoding digest.

Same decision dict (in ANY key order) -> identical bytes, byte-for-byte,
across runs and processes.  toa_penalty_digest() = sha256(enc) hex, used
to bind the decision to the ledger's audit entry.

ADVERSARIAL SELF-REVIEW (CNP-3 is a security/economics item)
------------------------------------------------------------
1. Offense splitting (sybil). The repeat multiplier is per-agent-id, so
   an attacker who rotates identities never accumulates it.  CLOSED
   partially: every new identity starts at R_i = 0 (5x collateral) and
   must post its own deposit, so farming costs fresh capital per
   identity.  RESIDUAL: without KYA-gated identity, a funded attacker
   can burn one identity per offense and dodge escalation forever.
   Long-term fix is identity binding (CNP-2 / CNP-6).

2. Severity-oracle gaming.  Severity is an adjudicator/TOA input; a
   bribed or buggy source could push any offense to the cap.  CLOSED
   partially: the cap is 100 % of the LOCKED stake (bounded blast
   radius), escalation only applies to offenses the protocol already
   slashes (severity never invents a slash), the adjudicator key stays
   the single trusted input (documented), and the TOA decision is bound
   into the audit entry by digest so post-hoc severity edits are
   detectable by re-encoding.  RESIDUAL: a compromised adjudicator can
   still maximize every slash; that trust assumption predates CNP-3.

3. Re-entry / counter inflation.  slash() pops the lock BEFORE
   accounting, so a second slash() on the same task finds no lock,
   slashes 0, and does NOT increment the offense counter — only
   slashed_wei > 0 counts as an offense.  TESTED.  The audit event
   records the no-op.

4. Gate toggling mid-flight.  The flag is read at slash time; an
   operator toggling it could escalate disputes already in flight.
   CLOSED partially: the flag state is written into the audit event
   (dynamic:true/false), so any escalation is attributable.  The flag
   itself is a deliberate operator action, not a market signal.

5. TOA decision malleability.  Canonical serialization (sorted keys,
   fixed separators, NFC unicode, int-only numbers, unknown-field
   rejection, version tag) gives exactly one encoding per decision:
   no whitespace games, no duplicate-field games, no float-format
   drift.  TESTED byte-for-byte.

6. Curve-parameter capture.  Constants are module-level, not env
   overrides: there is no knob an attacker (or an eager operator) can
   turn to silently reprice slashing.  The gate is boolean only.

7. Audit-log exhaustion.  The ledger's event ring is bounded at 5000;
   the offense COUNTER lives in the agent record, not the ring, so
   flooding the journal cannot reset or inflate escalation.  The ring
   is a journal, not the balance of record.

8. Precision.  All bps math is integer; there is no float anywhere on
   the money path (float64 loses hundreds of wei at 1e18 scale).

9. TOA as decision authority.  The TOA decision is ADVISORY: the
   adjudicator key still signs the ruling, and slash_dynamic() derives
   prior_offenses from the ledger's OWN history — never from
   caller-supplied counts.  A TOA (or a malicious caller) cannot talk
   itself into a lower multiplier by lying about its record.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from typing import Any, Dict, Mapping

# --- Curve constants (launch-fixed; see docstring) -----------------------------
HARD_CAP_BPS = 10_000          # slash can never exceed 100 % of the lock
REPEAT_MULTIPLIER_BPS = 12_500  # 1.25x per prior offense
MAX_PRIOR_OFFENSES = 8        # exponent saturates here; further repeats
                              # add nothing (escalation already at cap)
SEVERITY_MIN = 0
SEVERITY_MAX = 100
DYNAMIC_SLASH_ENV = "SINCOR_DYNAMIC_SLASH_ENABLED"

# --- Encoding constants -------------------------------------------------------
TOA_PENALTY_PROTOCOL = "toa_penalty"
TOA_PENALTY_VERSION = 1
_TOA_PENALTY_TAG = b"TOAPEN\x01:"

_OFFENSES = ("ghosting", "quality_failure")


class DynamicSlashDisabled(PermissionError):
    """Dynamic escalation was requested but the gate is off."""


class InvalidToaDecision(ValueError):
    """A TOA penalty decision failed schema validation."""


def dynamic_slash_enabled() -> bool:
    """Fail-closed gate for the dynamic curve.  Env '1'/'true'/'yes'
    enables; anything else (including unset) disables."""
    return os.environ.get(DYNAMIC_SLASH_ENV, "").strip().lower() in (
        "1", "true", "yes")


def compute_dynamic_slash_bps(anchor_bps: int, severity: int,
                              prior_offenses: int) -> int:
    """Escalated slash ratio for an offense, hard-capped.

    anchor_bps     one of the ratified anchors (e.g. 5000 quality,
                   10000 ghosting); must be in (0, 10000].
    severity       0..100; severity 0 leaves the anchor untouched,
                   severity 100 doubles it (pre-cap).
    prior_offenses count of the agent's prior capital-destroying
                   slashes (>= 0); each multiplies by 1.25, saturating
                   at MAX_PRIOR_OFFENSES (which saturates at the cap).

    Returns min(HARD_CAP_BPS, anchor * (1 + severity/100) *
    1.25**min(prior, 8)) in exact integer arithmetic.  severity 0 +
    prior 0 reproduces the anchor exactly.
    """
    if type(anchor_bps) is not int or not 0 < anchor_bps <= HARD_CAP_BPS:
        raise ValueError(f"anchor_bps must be an int in (0, {HARD_CAP_BPS}]")
    if type(severity) is not int or not SEVERITY_MIN <= severity <= SEVERITY_MAX:
        raise ValueError(
            f"severity must be an int in [{SEVERITY_MIN}, {SEVERITY_MAX}]")
    if type(prior_offenses) is not int or prior_offenses < 0:
        raise ValueError("prior_offenses must be a non-negative int")

    # severity factor in bps: 10000 (severity 0) .. 20000 (severity 100)
    sev_factor_bps = 10_000 + severity * 100
    # repeat factor in bps, stepwise with floor at each step: the cap on
    # the exponent means the loop is bounded and the result is exact.
    mult_bps = 10_000
    for _ in range(min(prior_offenses, MAX_PRIOR_OFFENSES)):
        mult_bps = mult_bps * REPEAT_MULTIPLIER_BPS // 10_000

    escalated = anchor_bps * sev_factor_bps // 10_000
    escalated = escalated * mult_bps // 10_000
    return min(escalated, HARD_CAP_BPS)


# --- TOA decision schema / canonical encoding ---------------------------------
# Every field required, no extras.  Integers only for numeric fields;
# bool is explicitly rejected (isinstance(True, int) is True in Python).
_SCHEMA: Dict[str, Any] = {
    "protocol": ("str", TOA_PENALTY_PROTOCOL),
    "version": ("int", TOA_PENALTY_VERSION),
    "agent_id": ("str", None),
    "task_id": ("str", None),
    "offense": ("offense", None),
    "severity": ("int", (SEVERITY_MIN, SEVERITY_MAX)),
    "prior_offenses": ("int", (0, None)),
    "anchor_bps": ("int", (1, HARD_CAP_BPS)),
    "recommended_slash_bps": ("int", (1, HARD_CAP_BPS)),
    "adjudicator_id": ("str", None),
    "nonce": ("int", (0, None)),
}


def _canonicalize(value: Any, field: str) -> Any:
    kind, bound = _SCHEMA[field]
    if kind == "str":
        if type(value) is not str or not value:
            raise InvalidToaDecision(f"{field} must be a non-empty string")
        value = unicodedata.normalize("NFC", value)
        if bound is not None and value != bound:
            raise InvalidToaDecision(
                f"{field} must be {bound!r}, got {value!r}")
        return value
    if kind == "int":
        if type(value) is not int:  # bool rejected on purpose
            raise InvalidToaDecision(f"{field} must be an int")
        if isinstance(bound, int):
            if value != bound:
                raise InvalidToaDecision(
                    f"{field} must be {bound}, got {value}")
        else:
            lo, hi = bound
            if value < lo or (hi is not None and value > hi):
                raise InvalidToaDecision(
                    f"{field} must be in [{lo}, {hi}], got {value}")
        return value
    if kind == "offense":
        if type(value) is not str or value not in _OFFENSES:
            raise InvalidToaDecision(
                f"{field} must be one of {list(_OFFENSES)}")
        return value
    raise InvalidToaDecision(f"unknown schema field {field}")  # pragma: no cover


def encode_toa_penalty(decision: Mapping[str, Any]) -> bytes:
    """Deterministically encode a TOA penalty decision to bytes.

    Canonical: keys sorted, fixed separators, UTF-8, NFC strings,
    int-only numerics, unknown fields rejected, version tag prefixed.
    No randomness, no wall-clock: the same decision dict (any key order)
    encodes to identical bytes, byte-for-byte, in every process."""
    if not isinstance(decision, Mapping):
        raise InvalidToaDecision("decision must be a mapping")
    keys = set(decision.keys())
    expected = set(_SCHEMA.keys())
    if keys != expected:
        missing = expected - keys
        extra = keys - expected
        raise InvalidToaDecision(
            f"decision fields must be exactly {sorted(expected)}; "
            f"missing={sorted(missing)} extra={sorted(extra)}")
    canonical = {k: _canonicalize(decision[k], k) for k in sorted(expected)}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return _TOA_PENALTY_TAG + payload


def decode_toa_penalty(blob: bytes) -> Dict[str, Any]:
    """Decode and re-validate a TOA penalty encoding."""
    if type(blob) is not bytes or not blob.startswith(_TOA_PENALTY_TAG):
        raise InvalidToaDecision("bad version tag")
    try:
        parsed = json.loads(blob[len(_TOA_PENALTY_TAG):].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidToaDecision(f"undecodable payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidToaDecision("payload must be an object")
    return {k: _canonicalize(parsed[k], k) for k in sorted(_SCHEMA.keys())}


def toa_penalty_digest(decision: Mapping[str, Any]) -> str:
    """sha256 hex of the canonical encoding: the audit-log binding."""
    return hashlib.sha256(encode_toa_penalty(decision)).hexdigest()


def make_toa_decision(*, agent_id: str, task_id: str, offense: str,
                      severity: int, prior_offenses: int, anchor_bps: int,
                      recommended_slash_bps: int, adjudicator_id: str,
                      nonce: int) -> Dict[str, Any]:
    """Build a schema-valid decision dict (validated on encode anyway)."""
    return {
        "protocol": TOA_PENALTY_PROTOCOL,
        "version": TOA_PENALTY_VERSION,
        "agent_id": agent_id,
        "task_id": task_id,
        "offense": offense,
        "severity": severity,
        "prior_offenses": prior_offenses,
        "anchor_bps": anchor_bps,
        "recommended_slash_bps": recommended_slash_bps,
        "adjudicator_id": adjudicator_id,
        "nonce": nonce,
    }
