"""AXM buyback executor — DISARMED-BY-DEFAULT skeleton for the AXM Activity Engine.

Purpose
-------
One day the founder may seed an AXM liquidity pool and authorize the treasury
to buy back AXM.  This module is the skeleton that makes that SAFE.  It is not
the execution: it plans and (only when fully armed) drives an *injected*
adapter.  This repository ships NO real broadcast adapter — the only
implementation that exists is the ``FakeBuybackAdapter`` used by tests.

Consistent with ``onchain/fee_conversion_executor.py`` (live conversion held
per 2026-10-11 safety work), this module:

* NEVER reads private keys and NEVER signs anything.  There is no signer
  callback, no key-custody integration, no ``Web3``/``eth_account``/``requests``
  import anywhere in this file.  A "dry-run that signs" is impossible here —
  there is no signing code to reach.
* NEVER touches the network.  All inputs are operator config + a founder
  approval record; all outputs are plans, refusals, or adapter calls.
* Holds no hardcoded addresses.  Pool/token addresses come from operator config
  only (``BUYBACK_POOL_*`` env vars), and are recorded verbatim in logs/plans.

Triple gate
-----------
``execute_buyback`` refuses cleanly unless ALL three hold:

  (a) Seeded pool detected: the operator has pinned an AXM pool in config
      (``BUYBACK_POOL_TOKEN_A``, ``BUYBACK_POOL_TOKEN_B``, ``BUYBACK_POOL_FEE``,
      ``BUYBACK_POOL_TICK_SPACING``), which the runbook only sets after a
      seeded pool is verified.  Gate A checks config presence, not chain state —
      the pinning IS the detection.
  (b) Explicit armed flag: env ``BUYBACK_ARMED=true`` (case-insensitive).
      Absent or anything else = refused.
  (c) Founder approval record: a JSON approval entry with scope, approver, and
      expiry, loaded from ``BUYBACK_APPROVAL_PATH`` (or an in-memory dict in
      tests).  It must carry ``scope`` covering ``"axm-buyback"``, an
      ``approver``, and an ``expires_at`` strictly in the future.

Any missing gate -> ``BuybackRefused`` with a logged, machine-readable reason.
No partial execution.  Refusals happen BEFORE the adapter is touched.

Even when all three gates pass, execution happens only through the injected
adapter interface (``BuybackAdapter``).  There is no default adapter and no
fallback broadcast path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger("sincor.treasury.buyback")

# --- env names --------------------------------------------------------------
ENV_ARMED = "BUYBACK_ARMED"
ENV_APPROVAL_PATH = "BUYBACK_APPROVAL_PATH"
ENV_POOL_TOKEN_A = "BUYBACK_POOL_TOKEN_A"
ENV_POOL_TOKEN_B = "BUYBACK_POOL_TOKEN_B"
ENV_POOL_FEE = "BUYBACK_POOL_FEE"
ENV_POOL_TICK_SPACING = "BUYBACK_POOL_TICK_SPACING"
ENV_MAX_BUY_WEI = "BUYBACK_MAX_BUY_WEI"
ENV_SLIPPAGE_BPS = "BUYBACK_SLIPPAGE_BPS"

REQUIRED_SCOPE = "axm-buyback"


# --- refusal ---------------------------------------------------------------

class BuybackRefused(RuntimeError):
    """Clean refusal: the executor declined to execute a buyback.

    ``gates`` maps gate name -> bool.  ``reasons`` lists the human-readable
    failed checks in evaluation order.  Raising this is the ONLY refusal path;
    callers can rely on it instead of inspecting return values.
    """

    def __init__(self, gates: Mapping[str, bool], reasons: List[str]):
        self.gates = dict(gates)
        self.reasons = list(reasons)
        super().__init__(
            "buyback refused: " + "; ".join(reasons))


# --- config ------------------------------------------------------------------

@dataclass(frozen=True)
class PoolPin:
    """Operator-pinned AXM pool.  Addresses come from config only — the
    runbook records them when the seeded pool is verified and pinned."""

    token_a: str
    token_b: str
    fee: int
    tick_spacing: int

    def as_dict(self) -> Dict[str, Any]:
        return {"token_a": self.token_a, "token_b": self.token_b,
                "fee": self.fee, "tick_spacing": self.tick_spacing}


@dataclass
class BuybackConfig:
    """Operator config for one executor instance."""

    pool: Optional[PoolPin] = None          # None = no seeded pool detected
    max_buy_wei: int = 0                   # 0 = no buybacks allowed by size
    slippage_bps: int = 100                # 1 % default
    approval: Optional[Mapping[str, Any]] = None  # in-memory approval (tests)

    @staticmethod
    def from_env() -> "BuybackConfig":
        """Build config from env.  Refuses to guess anything: unset values
        leave the gates failed."""
        token_a = os.environ.get(ENV_POOL_TOKEN_A, "").strip()
        token_b = os.environ.get(ENV_POOL_TOKEN_B, "").strip()
        fee = os.environ.get(ENV_POOL_FEE, "").strip()
        spacing = os.environ.get(ENV_POOL_TICK_SPACING, "").strip()
        pool = None
        if token_a and token_b and fee and spacing:
            try:
                pool = PoolPin(token_a=token_a, token_b=token_b,
                               fee=int(fee), tick_spacing=int(spacing))
            except ValueError:
                logger.warning("buyback: ignoring malformed pool pin in env")
        max_buy = 0
        try:
            max_buy = int(os.environ.get(ENV_MAX_BUY_WEI, "0").strip() or "0")
        except ValueError:
            pass
        slippage = 100
        try:
            slippage = int(os.environ.get(ENV_SLIPPAGE_BPS, "100").strip()
                           or "100")
        except ValueError:
            pass
        return BuybackConfig(pool=pool, max_buy_wei=max_buy,
                             slippage_bps=slippage)


# --- founder approval ---------------------------------------------------------

@dataclass(frozen=True)
class FounderApproval:
    """A founder approval entry.  Explicit scope + expiry — never a bare bool."""

    approver: str
    scope: str
    issued_at: str
    expires_at: str
    note: str = ""

    def is_live(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > now

    @staticmethod
    def from_mapping(data: Mapping[str, Any]) -> "FounderApproval":
        for key in ("approver", "scope", "issued_at", "expires_at"):
            if not data.get(key):
                raise ValueError(f"approval record missing {key!r}")
        return FounderApproval(
            approver=str(data["approver"]),
            scope=str(data["scope"]),
            issued_at=str(data["issued_at"]),
            expires_at=str(data["expires_at"]),
            note=str(data.get("note", "")),
        )


def load_approval(config: BuybackConfig) -> Optional[FounderApproval]:
    """Load the founder approval: in-memory mapping first (tests), else the
    JSON file at ``BUYBACK_APPROVAL_PATH``.  Malformed records -> None."""
    if config.approval is not None:
        try:
            return FounderApproval.from_mapping(config.approval)
        except (ValueError, AttributeError, TypeError) as exc:
            logger.warning("buyback: malformed in-memory approval (%s)", exc)
            return None
    path = os.environ.get(ENV_APPROVAL_PATH, "").strip()
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return FounderApproval.from_mapping(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("buyback: cannot load approval from %s (%s)", path, exc)
        return None


# --- gates -------------------------------------------------------------------

def _armed_flag() -> bool:
    return os.environ.get(ENV_ARMED, "").strip().lower() == "true"


def check_gates(config: BuybackConfig,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Evaluate the triple gate.  Pure: no adapter touch, no side effects.

    Returns {"pass": bool, "gates": {name: bool}, "reasons": [...]}.
    """
    gates: Dict[str, bool] = {}
    reasons: List[str] = []

    # Gate (a): seeded pool pinned in config.
    if config.pool is not None:
        gates["pool_seeded"] = True
    else:
        gates["pool_seeded"] = False
        reasons.append(
            "gate(pool_seeded): no AXM pool pinned in config — "
            "seed and pin a pool per the runbook first")

    # Gate (b): explicit armed flag.
    if _armed_flag():
        gates["armed_flag"] = True
    else:
        gates["armed_flag"] = False
        reasons.append(
            f"gate(armed_flag): {ENV_ARMED} is not 'true' — "
            "executor stays disarmed")

    # Gate (c): live founder approval with buyback scope.
    approval = load_approval(config)
    if approval is None:
        gates["founder_approval"] = False
        reasons.append(
            "gate(founder_approval): no readable approval record "
            f"({ENV_APPROVAL_PATH} unset/unreadable/malformed)")
    elif REQUIRED_SCOPE not in approval.scope:
        gates["founder_approval"] = False
        reasons.append(
            f"gate(founder_approval): approval scope {approval.scope!r} does "
            f"not cover {REQUIRED_SCOPE!r}")
    elif not approval.is_live(now):
        gates["founder_approval"] = False
        reasons.append(
            f"gate(founder_approval): approval expired at "
            f"{approval.expires_at}")
    else:
        gates["founder_approval"] = True

    return {"pass": all(gates.values()) and len(gates) == 3,
            "gates": gates, "reasons": reasons}


# --- adapter interface --------------------------------------------------------

class BuybackAdapter:
    """Swap adapter interface.  The executor never broadcasts itself; it only
    calls the adapter the operator injected.

    This repository ships NO real implementation.  Production wiring would
    implement this against the pool key, but that adapter lives outside this
    skeleton (founder-gated, separately reviewed).
    """

    def swap_exact_input_for_axm(self, *, pool: PoolPin, amount_in_wei: int,
                                 amount_out_min_wei: int,
                                 slippage_bps: int) -> Mapping[str, Any]:
        """Request an exact-input swap into AXM.  Returns a receipt-ish mapping.

        Must NOT sign or broadcast unless it is a production adapter built for
        the armed pipeline — which does not exist in this repository.
        """
        raise NotImplementedError


class FakeBuybackAdapter(BuybackAdapter):
    """Test adapter.  Records calls, broadcasts nothing, signs nothing."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def swap_exact_input_for_axm(self, *, pool: PoolPin, amount_in_wei: int,
                                 amount_out_min_wei: int,
                                 slippage_bps: int) -> Mapping[str, Any]:
        self.calls.append({
            "pool": pool.as_dict(),
            "amount_in_wei": amount_in_wei,
            "amount_out_min_wei": amount_out_min_wei,
            "slippage_bps": slippage_bps,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return {"status": "fake", "adapter": "FakeBuybackAdapter",
                "calls": len(self.calls)}


# --- executor -----------------------------------------------------------------

@dataclass
class BuybackPlan:
    """An approved plan.  Plans are unsigned intent, not transactions —
    there is no tx-building code in this skeleton."""

    amount_in_wei: int
    amount_out_min_wei: int
    pool: PoolPin
    slippage_bps: int
    approval_approver: str
    approval_expires_at: str

    def as_dict(self) -> Dict[str, Any]:
        return {"amount_in_wei": self.amount_in_wei,
                "amount_out_min_wei": self.amount_out_min_wei,
                "pool": self.pool.as_dict(),
                "slippage_bps": self.slippage_bps,
                "approval_approver": self.approval_approver,
                "approval_expires_at": self.approval_expires_at,
                "signing": "none — this skeleton contains no signing code",
                "broadcast": "adapter-injected only"}


class BuybackExecutor:
    """Triple-gated buyback executor skeleton.  Disarmed by default."""

    def __init__(self, config: BuybackConfig):
        self.config = config

    # -- planning ---------------------------------------------------------
    def plan(self, amount_in_wei: int) -> BuybackPlan:
        """Build an unsigned buyback plan.  Refuses before planning if any
        gate fails; refuses oversized buys against max_buy_wei."""
        result = check_gates(self.config)
        if not result["pass"]:
            logger.warning("buyback plan refused: %s",
                           "; ".join(result["reasons"]))
            raise BuybackRefused(result["gates"], result["reasons"])
        if amount_in_wei <= 0:
            raise BuybackRefused(result["gates"],
                                 ["amount_in_wei must be positive"])
        if self.config.max_buy_wei <= 0 or amount_in_wei > self.config.max_buy_wei:
            raise BuybackRefused(
                result["gates"],
                [f"buy of {amount_in_wei} wei exceeds max_buy_wei "
                 f"{self.config.max_buy_wei}"])
        approval = load_approval(self.config)
        assert approval is not None  # gate (c) passed
        # amount_out_min_wei is filled by the adapter's quote in a live
        # pipeline; the skeleton records the bound as zero and lets the
        # adapter report its own minimum.  No onchain quote is performed.
        return BuybackPlan(
            amount_in_wei=amount_in_wei,
            amount_out_min_wei=0,
            pool=self.config.pool,  # type: ignore[arg-type]
            slippage_bps=self.config.slippage_bps,
            approval_approver=approval.approver,
            approval_expires_at=approval.expires_at,
        )

    # -- execution --------------------------------------------------------
    def execute_buyback(self, amount_in_wei: int,
                        adapter: BuybackAdapter) -> Dict[str, Any]:
        """Execute a buyback through the injected adapter.

        Refuses (``BuybackRefused``) unless the triple gate passes.  The
        adapter is never touched on refusal — no partial execution.
        """
        if not isinstance(adapter, BuybackAdapter):
            raise TypeError("adapter must implement BuybackAdapter")
        plan = self.plan(amount_in_wei)  # gates checked here, before adapter
        logger.info("buyback executing via %s: %s",
                    type(adapter).__name__, plan.as_dict())
        receipt = adapter.swap_exact_input_for_axm(
            pool=plan.pool,
            amount_in_wei=plan.amount_in_wei,
            amount_out_min_wei=plan.amount_out_min_wei,
            slippage_bps=plan.slippage_bps,
        )
        return {"status": "executed",
                "plan": plan.as_dict(),
                "adapter": type(adapter).__name__,
                "receipt": dict(receipt)}
