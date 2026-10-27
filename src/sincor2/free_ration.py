"""Wave 1 agent hook — universal daily free ration + free-peek limits.

One shared, thread-safe tracker used by BOTH free surfaces:

* A2A (``sincor2.a2a_integration``): the daily ration is a new universal
  layer on top of the existing lifetime top-5 free quota. The lifetime
  quota machinery (``_FreeQuotaTracker`` / ``QuotaStore``) is untouched;
  this module only adds the per-day counters it delegates to.
* x402 (``sincor2.mvp_blueprints.billing``): the 7 skill resources had
  no free tier at all. ``x402_paid_resource`` consults this tracker
  before the paid-token flow and serves free while ration remains.

Rules (the abuse guards ARE these caps — there is no other free gate):

* Per-agent ration: ``DAILY_RATION_LIMIT`` (default 3) free calls per
  identity + skill/resource per UTC day. The day key is the UTC date
  (``YYYY-MM-DD``), so counters reset at UTC midnight by construction —
  a new day is simply a new key; yesterday's rows are never read again.
* Per-IP cap: at most ``PER_IP_DAILY_CAP`` (default 30) free *serves*
  per source IP per UTC day across all skills/resources. This bounds
  identity rotation from one host.
* Peek cap: at most ``PEEK_PER_IP_DAILY_CAP`` (default 60) free-peek
  responses per source IP per UTC day. Peeks are no-auth and no-wallet,
  so the IP is the only identity they have.

Identity keying (x402): the bound payer wallet of a presented access
token when there is one (it was ownership-proven at challenge-bind
time), else the wallet query/body param when present (lowercased),
else ``agent:<agent_id>`` when an agent id is presented, else
``ip:<remote_addr>`` — anonymous callers share one ration bucket
per source IP, which is deliberately conservative. The A2A side keys
the ration on its already-verified identities only (EIP-191 quota
wallet, verified wallet, or task owner wallet — never ``caller_id``).

Storage is process-local in-memory dicts behind one lock, matching the
existing ``_MemoryQuotaStore`` convention; counters reset on restart.
Free serves served through this tracker must never write fee records,
pool allocations, or settlements — the call sites guarantee that by
serving through the read-only skill dispatch only.

The clock is injectable (``on=`` accepts a ``datetime``/``date``/ISO
day string) so tests can prove the UTC-midnight reset boundary without
sleeping.
"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union

DAILY_RATION_LIMIT = int(os.getenv("FREE_RATION_PER_AGENT_PER_DAY", "3"))
PER_IP_DAILY_CAP = int(os.getenv("FREE_RATION_PER_IP_PER_DAY", "30"))
PEEK_PER_IP_DAILY_CAP = int(os.getenv("FREE_PEEK_PER_IP_PER_DAY", "60"))

#: Plain-language hook copy surfaced on the agent card and discovery
#: surfaces. Positive framing only; no invented numbers; the SINCOR
#: token is platform access, never an investment. Sponsored stake is
#: phrased conditionally — whether it applies depends on the existing
#: ``SINCOR_SPONSORED_STAKE_ENABLED`` flag, which this module only reads.
HOOK_COPY = (
    "Every skill carries a free daily ration of 3 calls per agent per "
    "skill per day, and a free peek needs no wallet — try any skill "
    "before you pay. Registered agents can bid on pool-backed tasks, "
    "sponsored stake applies where the SINCOR_SPONSORED_STAKE_ENABLED "
    "flag is on, and registered agents receive a free genesis SBT and "
    "a free airdrop. The SINCOR token is platform access for the agent "
    "economy."
)


def sponsored_stake_enabled() -> bool:
    """Read (never set) the existing sponsored-stake enable flag."""
    try:
        from sincor2.sponsored_stake import sponsored_stake_enabled as _enabled

        return bool(_enabled())
    except Exception:
        return os.getenv("SINCOR_SPONSORED_STAKE_ENABLED", "").strip().lower() in (
            "1", "true", "yes", "on",
        )


def hook_payload() -> Dict[str, Any]:
    """Machine-readable hook offer for agent cards / discovery surfaces."""
    return {
        "free_daily_ration_per_skill": DAILY_RATION_LIMIT,
        "free_peek_no_wallet": True,
        "registered_agents_can_bid_on_pool_backed_tasks": True,
        "sponsored_stake": (
            "applies where the SINCOR_SPONSORED_STAKE_ENABLED flag is on"
        ),
        "sponsored_stake_enabled": sponsored_stake_enabled(),
        "free_genesis_sbt_for_registered_agents": True,
        "free_airdrop_for_registered_agents": True,
        "token_role": "platform access",
        "copy": HOOK_COPY,
    }


def utc_day(on: Union[None, datetime, date, str] = None) -> str:
    """UTC day key (``YYYY-MM-DD``) for ``on`` (default: now)."""
    if on is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(on, str):
        return on[:10]
    if isinstance(on, datetime):
        if on.tzinfo is not None:
            on = on.astimezone(timezone.utc)
        return on.date().isoformat()
    return on.isoformat()


def resolve_caller_identity(
    wallet: Optional[str] = None,
    agent_id: Optional[str] = None,
    ip: Optional[str] = None,
) -> str:
    """Documented x402 free-ration identity rule (see module docstring)."""
    if wallet and str(wallet).strip():
        return str(wallet).strip().lower()
    if agent_id and str(agent_id).strip():
        return f"agent:{str(agent_id).strip()}"
    return f"ip:{ip or 'unknown'}"


class DailyRationTracker:
    """Atomic per-day counters for free ration serves and peeks."""

    def __init__(
        self,
        daily_limit: int = DAILY_RATION_LIMIT,
        per_ip_daily_cap: int = PER_IP_DAILY_CAP,
        peek_per_ip_daily_cap: int = PEEK_PER_IP_DAILY_CAP,
    ) -> None:
        self.daily_limit = int(daily_limit)
        self.per_ip_daily_cap = int(per_ip_daily_cap)
        self.peek_per_ip_daily_cap = int(peek_per_ip_daily_cap)
        self._lock = threading.Lock()
        self._agent: Dict[Tuple[str, str, str], int] = {}
        self._ip: Dict[Tuple[str, str], int] = {}
        self._peek_ip: Dict[Tuple[str, str], int] = {}

    # -- ration -----------------------------------------------------------
    def used(self, identity: str, skill_id: str, *, on=None) -> int:
        if not identity:
            return 0
        with self._lock:
            return self._agent.get((identity, skill_id, utc_day(on)), 0)

    def remaining(self, identity: str, skill_id: str, *, on=None) -> int:
        if not identity:
            return 0
        return max(0, self.daily_limit - self.used(identity, skill_id, on=on))

    def ip_used(self, ip: Optional[str], *, on=None) -> int:
        with self._lock:
            return self._ip.get((ip or "unknown", utc_day(on)), 0)

    def ip_remaining(self, ip: Optional[str], *, on=None) -> int:
        return max(0, self.per_ip_daily_cap - self.ip_used(ip, on=on))

    def try_consume(
        self,
        identity: str,
        skill_id: str,
        *,
        ip: Optional[str] = None,
        on=None,
    ) -> bool:
        """Atomically consume one free serve iff agent + IP caps allow."""
        if not identity or self.daily_limit <= 0:
            return False
        day = utc_day(on)
        ip_key = (ip or "unknown", day)
        agent_key = (identity, skill_id, day)
        with self._lock:
            if self._agent.get(agent_key, 0) >= self.daily_limit:
                return False
            if self._ip.get(ip_key, 0) >= self.per_ip_daily_cap:
                return False
            self._agent[agent_key] = self._agent.get(agent_key, 0) + 1
            self._ip[ip_key] = self._ip.get(ip_key, 0) + 1
            return True

    # -- peek ---------------------------------------------------------------
    def peek_used(self, ip: Optional[str], *, on=None) -> int:
        with self._lock:
            return self._peek_ip.get((ip or "unknown", utc_day(on)), 0)

    def try_consume_peek(self, ip: Optional[str], *, on=None) -> bool:
        """Atomically consume one peek slot for this IP today."""
        if self.peek_per_ip_daily_cap <= 0:
            return False
        key = (ip or "unknown", utc_day(on))
        with self._lock:
            if self._peek_ip.get(key, 0) >= self.peek_per_ip_daily_cap:
                return False
            self._peek_ip[key] = self._peek_ip.get(key, 0) + 1
            return True

    def clear(self) -> None:
        """Drop all counters (tests / ops only)."""
        with self._lock:
            self._agent.clear()
            self._ip.clear()
            self._peek_ip.clear()


#: Process-wide tracker shared by the A2A and x402 surfaces.
daily_ration_tracker = DailyRationTracker()


def truncate_sample(value: Any, max_chars: int = 1200) -> Tuple[Any, bool]:
    """Bound a peek sample: return ``(value, False)`` when it fits, else
    ``({"truncated_text": ...}, True)`` with the JSON text cut to size."""
    import json

    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= max_chars:
        return value, False
    return {"truncated_text": text[:max_chars]}, True
