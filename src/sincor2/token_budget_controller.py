"""
SINCOR2 Token Budget Controller — Hard API Killswitches

Provides per-agent daily token budget enforcement that is:
  - Absolute: once the ceiling is hit the agent is blocked immediately
  - Instantaneous: implemented via a thread-safe atomic counter with no grace window
  - Persistent: counts survive in-process restarts via a lightweight JSON ledger
  - Manual-override: an operator can kill any agent's token budget via the dashboard

Design notes:
  * Uses threading.Lock for atomicity — safe under Gunicorn's threaded worker model
  * Ledger resets automatically at UTC midnight (lazy-reset pattern)
  * Manual kills are stored separately so they survive a daily rollover
  * This module has zero external dependencies beyond the stdlib

Usage::

    from sincor2.token_budget_controller import get_controller

    ctrl = get_controller()

    # Before every LLM call:
    if not ctrl.is_allowed("E-auriga-01"):
        raise RuntimeError("daily token ceiling hit or agent killed")

    # After every LLM call, record actual usage:
    ctrl.record_usage("E-auriga-01", tokens_used=1024)

    # Operator hard-kill from dashboard:
    ctrl.kill_agent("E-auriga-01")

    # Reinstate later:
    ctrl.reinstate_agent("E-auriga-01")
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("sincor.token_budget")

_LEDGER_ENV_KEY = "TOKEN_BUDGET_LEDGER_PATH"
_DEFAULT_LEDGER = "data/token_budget_ledger.json"

# Default daily token ceiling per agent when not specified in agent YAML.
_DEFAULT_DAILY_CEILING = int(os.environ.get("DEFAULT_DAILY_TOKEN_CEILING", "50000"))


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------

def _ledger_path() -> Path:
    raw = os.environ.get(_LEDGER_ENV_KEY, _DEFAULT_LEDGER)
    p = Path(raw)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_ledger(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_ledger(path: Path, data: Dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("[TOKEN_BUDGET] ledger write failed: %s", exc)


# ---------------------------------------------------------------------------
# AgentBudgetState
# ---------------------------------------------------------------------------

class AgentBudgetState:
    """Mutable per-agent budget state — thread-safe via external lock."""

    __slots__ = (
        "agent_id",
        "daily_ceiling",
        "tokens_used_today",
        "ledger_date",
        "killed",
        "kill_reason",
        "killed_at",
    )

    def __init__(
        self,
        agent_id: str,
        daily_ceiling: int = _DEFAULT_DAILY_CEILING,
    ) -> None:
        self.agent_id = agent_id
        self.daily_ceiling = daily_ceiling
        self.tokens_used_today: int = 0
        self.ledger_date: str = date.today().isoformat()
        self.killed: bool = False
        self.kill_reason: str = ""
        self.killed_at: str = ""

    # -- Serialisation --

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "daily_ceiling": self.daily_ceiling,
            "tokens_used_today": self.tokens_used_today,
            "ledger_date": self.ledger_date,
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "killed_at": self.killed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentBudgetState":
        obj = cls(d["agent_id"], d.get("daily_ceiling", _DEFAULT_DAILY_CEILING))
        obj.tokens_used_today = d.get("tokens_used_today", 0)
        obj.ledger_date = d.get("ledger_date", date.today().isoformat())
        obj.killed = d.get("killed", False)
        obj.kill_reason = d.get("kill_reason", "")
        obj.killed_at = d.get("killed_at", "")
        return obj

    # -- Business logic --

    def _maybe_roll_day(self) -> None:
        """Reset daily counter if the calendar date has changed (UTC midnight)."""
        today = date.today().isoformat()
        if self.ledger_date != today:
            self.tokens_used_today = 0
            self.ledger_date = today
            logger.debug("[TOKEN_BUDGET] daily rollover agent=%s", self.agent_id)

    def is_allowed(self) -> bool:
        """Return True if the agent may proceed.  Absolute, no grace window."""
        self._maybe_roll_day()
        if self.killed:
            return False
        return self.tokens_used_today < self.daily_ceiling

    def record_usage(self, tokens: int) -> None:
        """Atomically increment today's token count."""
        self._maybe_roll_day()
        self.tokens_used_today += max(0, tokens)
        if self.tokens_used_today >= self.daily_ceiling:
            logger.warning(
                "[TOKEN_BUDGET] ceiling hit agent=%s used=%d ceiling=%d — BLOCKED",
                self.agent_id, self.tokens_used_today, self.daily_ceiling,
            )

    def remaining(self) -> int:
        self._maybe_roll_day()
        return max(0, self.daily_ceiling - self.tokens_used_today)

    def utilisation_pct(self) -> float:
        """0.0–100.0 percentage of daily budget consumed."""
        self._maybe_roll_day()
        if self.daily_ceiling <= 0:
            return 100.0
        return min(100.0, (self.tokens_used_today / self.daily_ceiling) * 100.0)


# ---------------------------------------------------------------------------
# TokenBudgetController
# ---------------------------------------------------------------------------

class TokenBudgetController:
    """
    Central per-agent token budget registry.

    Thread-safe.  All public methods acquire a single re-entrant lock so the
    controller is safe to share across Flask request threads and background
    scheduler threads simultaneously.
    """

    def __init__(self, ledger_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._path = ledger_path or _ledger_path()
        self._agents: Dict[str, AgentBudgetState] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        raw = _load_ledger(self._path)
        for agent_id, d in raw.items():
            try:
                self._agents[agent_id] = AgentBudgetState.from_dict(d)
            except (KeyError, TypeError) as exc:
                logger.warning("[TOKEN_BUDGET] skipping corrupt ledger entry %s: %s", agent_id, exc)

    def _persist(self) -> None:
        data = {aid: state.to_dict() for aid, state in self._agents.items()}
        _save_ledger(self._path, data)

    # ------------------------------------------------------------------
    # State retrieval
    # ------------------------------------------------------------------

    def _get_or_create(self, agent_id: str, ceiling: Optional[int] = None) -> AgentBudgetState:
        """Return (and create if needed) the state for *agent_id*."""
        if agent_id not in self._agents:
            c = ceiling if ceiling is not None else _DEFAULT_DAILY_CEILING
            self._agents[agent_id] = AgentBudgetState(agent_id, c)
        elif ceiling is not None and ceiling != self._agents[agent_id].daily_ceiling:
            self._agents[agent_id].daily_ceiling = ceiling
        return self._agents[agent_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_allowed(self, agent_id: str, ceiling: Optional[int] = None) -> bool:
        """
        Return True if *agent_id* may proceed with an API call.

        This is the hot-path guard — call before every LLM invocation.
        Absolute: if ``False`` the caller must not proceed.
        """
        with self._lock:
            state = self._get_or_create(agent_id, ceiling)
            return state.is_allowed()

    def record_usage(
        self, agent_id: str, tokens_used: int, ceiling: Optional[int] = None
    ) -> None:
        """
        Record *tokens_used* for *agent_id* after a completed API call.

        Persists the ledger after every call so usage survives a crash.
        """
        with self._lock:
            state = self._get_or_create(agent_id, ceiling)
            state.record_usage(tokens_used)
            self._persist()

    def kill_agent(self, agent_id: str, reason: str = "operator_override") -> None:
        """
        Hard-kill *agent_id* immediately.  Subsequent ``is_allowed()`` calls
        return False until ``reinstate_agent()`` is called.
        """
        with self._lock:
            state = self._get_or_create(agent_id)
            state.killed = True
            state.kill_reason = reason
            state.killed_at = datetime.now(timezone.utc).isoformat()
            self._persist()
        logger.warning(
            "[TOKEN_BUDGET] agent KILLED agent=%s reason=%s", agent_id, reason
        )

    def reinstate_agent(self, agent_id: str) -> None:
        """Remove the manual kill flag for *agent_id*."""
        with self._lock:
            state = self._get_or_create(agent_id)
            state.killed = False
            state.kill_reason = ""
            state.killed_at = ""
            self._persist()
        logger.info("[TOKEN_BUDGET] agent reinstated agent=%s", agent_id)

    def get_status(self, agent_id: str) -> Dict[str, Any]:
        """Return a snapshot dict for dashboard display."""
        with self._lock:
            state = self._get_or_create(agent_id)
            state._maybe_roll_day()
            return {
                "agent_id": agent_id,
                "daily_ceiling": state.daily_ceiling,
                "tokens_used_today": state.tokens_used_today,
                "tokens_remaining": state.remaining(),
                "utilisation_pct": round(state.utilisation_pct(), 1),
                "is_allowed": state.is_allowed(),
                "killed": state.killed,
                "kill_reason": state.kill_reason,
                "killed_at": state.killed_at,
                "ledger_date": state.ledger_date,
            }

    def get_all_statuses(self) -> Dict[str, Dict[str, Any]]:
        """Return status snapshots for all tracked agents."""
        with self._lock:
            return {aid: self.get_status(aid) for aid in list(self._agents.keys())}

    def register_agent(self, agent_id: str, daily_ceiling: int) -> None:
        """Pre-register an agent with a specific daily ceiling (idempotent)."""
        with self._lock:
            self._get_or_create(agent_id, daily_ceiling)
            self._persist()
        logger.debug(
            "[TOKEN_BUDGET] registered agent=%s ceiling=%d", agent_id, daily_ceiling
        )

    def reset_agent_daily_count(self, agent_id: str) -> None:
        """Reset an agent's daily counter to zero (admin use only)."""
        with self._lock:
            state = self._get_or_create(agent_id)
            state.tokens_used_today = 0
            state.ledger_date = date.today().isoformat()
            self._persist()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_controller: Optional[TokenBudgetController] = None
_controller_lock = threading.Lock()


def get_controller() -> TokenBudgetController:
    """Return (and lazily create) the process-wide TokenBudgetController singleton."""
    global _controller
    if _controller is None:
        with _controller_lock:
            if _controller is None:
                _controller = TokenBudgetController()
    return _controller


def bootstrap_from_agent_yamls(agents_dir: str = "agents") -> None:
    """
    Read every E-*.yaml under *agents_dir* and register each agent's
    ``budgets.daily_tokens`` as their token ceiling.

    Call once at application startup so the controller knows about every
    agent before any work begins.
    """
    import glob as _glob
    try:
        import yaml as _yaml
    except ImportError:
        logger.warning("[TOKEN_BUDGET] PyYAML not available — skipping YAML bootstrap")
        return

    ctrl = get_controller()
    pattern = os.path.join(agents_dir, "E-*.yaml")
    registered = 0
    for path in _glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = _yaml.safe_load(fh)
            agent_id = data.get("id", "")
            ceiling = int(
                (data.get("budgets") or {}).get("daily_tokens", _DEFAULT_DAILY_CEILING)
            )
            if agent_id:
                ctrl.register_agent(agent_id, ceiling)
                registered += 1
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.warning("[TOKEN_BUDGET] skipping %s: %s", path, exc)

    logger.info("[TOKEN_BUDGET] bootstrap complete (%s agents)", registered)
