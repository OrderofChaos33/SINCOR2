"""MetaMask Agent Wallet adapter for SINCOR2 personal agents.

Provides policy-enforced, simulation-scanned, MEV-protected on-chain execution
for SINCOR agents (TOA, revenue, DeFi loops, SINC/AXM settlement) without
ever exposing private keys to the agent process.

Design rules (match existing execution_adapter):
- Dry-run by default. Live only when METAMASK_AGENT_LIVE=true and mm is
  authenticated + initialized.
- Respects the global bankroll kill switch.
- Never logs secrets. Session lives in ~/.metamask/ (or container volume).
- Structured results compatible with OrderResult patterns.

Setup (one-time on the host that runs personal SINCOR/OpenClaw agents):

    npm install -g @metamask/agent-wallet@latest
    npx skills add MetaMask/agent-skills   # install metamask-agent-wallet when prompted
    mm doctor
    mm init --wallet server-wallet --mode guard

Then tighten policy for Base + SINC + AXM + treasury (see docs/METAMASK_AGENT_WALLET.md).

Environment
-----------
METAMASK_AGENT_LIVE          "true" to allow real txs (default: false)
MM_CLI                       path to mm binary (default: mm on PATH)
METAMASK_AGENT_TIMEOUT       seconds (default: 120)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sincor2.bankroll import get_bankroll
from sincor2.execution_adapter import KillSwitchTripped, OrderResult, kill_switch_tripped

logger = logging.getLogger("sincor.adapters.metamask_agent_wallet")

from sincor2.onchain.constants import AXIOM_TOKEN, BASE_CHAIN_ID, SINC_TOKEN, TREASURY as _TREASURY

# Canonical SINCOR addresses on Base (8453) — sincor2.onchain.constants
SINC_TOKEN = SINC_TOKEN
AXM_TOKEN = AXIOM_TOKEN
TREASURY = _TREASURY
BASE_CHAIN_ID = BASE_CHAIN_ID


class MetaMaskAgentNotReady(RuntimeError):
    """mm CLI missing, not authenticated, or not initialized."""


def _extract_tx_hash(data: Any) -> str:
    """Total tx-hash extraction from an mm CLI payload.

    The CLI's exit code is the execution signal: ``_run`` returns only
    after a 0 exit, and what it returns is raw ``json.loads(stdout)`` —
    ANY JSON value, not necessarily a dict. Extraction therefore guards
    every level with isinstance and coerces the hash to str-or-empty, so
    no parse outcome can throw (and be swallowed by the caller's broad
    except into a failure result for a trade that already executed) or
    leak a wrong-typed ``order_id`` downstream. Hash-less success with an
    empty order_id is the pinned design for every unparseable/hash-less
    shape.
    """
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return ""
    # Consult the fallback properly: a truthy non-string txHash must not
    # shadow a valid string ``hash`` (an ``or`` chain would pick the
    # wrong-typed value and the coercion below would discard both).
    tx_hash = payload.get("txHash")
    if not isinstance(tx_hash, str):
        tx_hash = payload.get("hash")
    return tx_hash if isinstance(tx_hash, str) else ""


@dataclass
class WalletStatus:
    authenticated: bool = False
    initialized: bool = False
    address: Optional[str] = None
    trading_mode: Optional[str] = None
    cli_version: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class MetaMaskAgentWallet:
    """Thin, safe wrapper around the MetaMask Agent Wallet CLI (`mm`).

    Agents call methods on this class. The CLI enforces simulation, Blockaid
    threat scanning, MEV protection, spend limits, and 2FA on policy breaches.
    """

    def __init__(
        self,
        timeout: Optional[float] = None,
        mm_bin: Optional[str] = None,
    ) -> None:
        self.timeout = float(timeout or os.getenv("METAMASK_AGENT_TIMEOUT", "120"))
        self.mm_bin = mm_bin or os.getenv("MM_CLI", "mm")
        self.bankroll = get_bankroll()

    # ------------------------------------------------------------------
    # Live gate
    # ------------------------------------------------------------------

    def is_live(self) -> bool:
        return os.getenv("METAMASK_AGENT_LIVE", "false").lower() == "true"

    def _ensure_mm(self) -> None:
        if not shutil.which(self.mm_bin) and not os.path.isfile(self.mm_bin):
            raise MetaMaskAgentNotReady(
                f"MetaMask Agent Wallet CLI not found ({self.mm_bin}). "
                "Install: npm install -g @metamask/agent-wallet@latest"
            )

    # ------------------------------------------------------------------
    # Low-level runner
    # ------------------------------------------------------------------

    def _run(self, *args: str, wait: bool = True, allow_dry: bool = True) -> Any:
        # Returns the CLI's parsed stdout UNVALIDATED: on a 0 exit that is
        # raw json.loads(stdout) — any JSON value, not necessarily a dict.
        # Consumers must extract defensively (see _extract_tx_hash).
        self._ensure_mm()
        if kill_switch_tripped():
            raise KillSwitchTripped("bankroll / POLYCLAW kill switch is active")

        cmd: List[str] = [self.mm_bin, *args, "--json"]
        if wait:
            cmd.append("--wait")

        logger.debug("mm cmd: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                # Decode tolerantly: with text=True, decoding happens
                # INSIDE subprocess.run, so non-UTF-8 stream bytes would
                # raise UnicodeDecodeError instead of returning the
                # CompletedProcess — hiding a 0 exit and converting an
                # executed trade into a reported failure (the decode
                # happens for stderr too, whose bytes are irrelevant on
                # a 0 exit). errors="replace" guarantees a completed
                # process always reaches the returncode check below.
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"mm timed out after {self.timeout}s") from exc

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            msg = stderr or stdout or f"exit {result.returncode}"
            raise RuntimeError(f"mm failed: {msg}")

        if not stdout:
            return {"ok": True, "data": {}}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Some older paths may emit non-JSON; surface raw for debugging
            return {"ok": True, "data": {"raw": stdout}}

    # ------------------------------------------------------------------
    # Health / setup
    # ------------------------------------------------------------------

    def doctor(self) -> Dict[str, Any]:
        """Run mm doctor. Does not require live mode."""
        return self._run("doctor", wait=False)

    def status(self) -> WalletStatus:
        """Return authentication + initialization status."""
        doc = self.doctor()
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, dict):
            # Non-dict doctor payload (or non-dict "data"): the CLI did
            # not report a usable status. Report not-ready instead of
            # raising — readiness failure flows through require_ready()
            # so the write methods can surface it as a failure result.
            data = {}
        address = None
        trading_mode = None
        try:
            addr_resp = self._run("wallet", "address", wait=False)
            address = (addr_resp.get("data") or {}).get("address") or addr_resp.get("address")
        except Exception:
            pass
        try:
            mode_resp = self._run("wallet", "trading-mode", "get", wait=False)
            trading_mode = (mode_resp.get("data") or {}).get("mode") or mode_resp.get("mode")
        except Exception:
            pass
        return WalletStatus(
            authenticated=bool(data.get("authenticated")),
            initialized=bool(data.get("initialized")),
            address=address,
            trading_mode=trading_mode,
            cli_version=str(data.get("cli") or ""),
            raw=data,
        )

    def require_ready(self) -> WalletStatus:
        st = self.status()
        if not st.authenticated or not st.initialized:
            raise MetaMaskAgentNotReady(
                "MetaMask Agent Wallet not authenticated/initialized. "
                "Run: mm init --wallet server-wallet --mode guard"
            )
        return st

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def address(self) -> str:
        st = self.require_ready()
        if st.address:
            return st.address
        data = self._run("wallet", "address", wait=False)
        addr = (data.get("data") or {}).get("address") or data.get("address")
        if not addr:
            raise RuntimeError("could not resolve wallet address")
        return str(addr)

    def balance(self, chain_id: int = BASE_CHAIN_ID, token: Optional[str] = None) -> Dict[str, Any]:
        args = ["wallet", "balance", f"--chain-ids={chain_id}"]
        if token:
            args += ["--token", token]
        return self._run(*args, wait=False)

    def policy(self) -> Dict[str, Any]:
        return self._run("wallet", "policy", "get", wait=False)

    # ------------------------------------------------------------------
    # Write paths (guarded)
    # ------------------------------------------------------------------

    def transfer(
        self,
        to: str,
        amount: str,
        token: str = "native",
        chain_id: int = BASE_CHAIN_ID,
        *,
        force_live: bool = False,
    ) -> OrderResult:
        """Transfer native or ERC-20. Dry-run unless live."""
        if kill_switch_tripped():
            return OrderResult(False, simulated=True, error="kill switch tripped")

        if not (self.is_live() or force_live):
            logger.info(
                "[DRY RUN] transfer %s %s -> %s on chain %s "
                "(set METAMASK_AGENT_LIVE=true to go live)",
                amount, token, to, chain_id,
            )
            return OrderResult(
                True,
                simulated=True,
                size_usd=0.0,
            )

        try:
            # Inside the try on purpose: readiness failure (including a
            # non-dict doctor payload reported as not-ready by status())
            # must surface as a failure OrderResult, never raise — the
            # write methods are exception-total end-to-end.
            self.require_ready()
            data = self._run(
                "transfer",
                f"--to={to}",
                f"--amount={amount}",
                f"--token={token}",
                f"--chain-id={chain_id}",
            )
            tx_hash = _extract_tx_hash(data)
            logger.info("MetaMask TRANSFER executed: tx=%s", tx_hash or None)
            return OrderResult(
                True,
                simulated=False,
                order_id=tx_hash,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("MetaMask transfer failed")
            return OrderResult(False, simulated=False, error=str(exc))

    def swap_quote(
        self,
        *,
        from_token: str,
        to_token: str,
        amount: str,
        from_chain_id: int = BASE_CHAIN_ID,
        slippage: Optional[str] = None,
    ) -> Dict[str, Any]:
        args = [
            "swap",
            "quote",
            f"--from={from_token}",
            f"--to={to_token}",
            f"--amount={amount}",
            f"--from-chain-id={from_chain_id}",
        ]
        if slippage:
            args += ["--slippage", slippage]
        return self._run(*args, wait=False)

    def swap_execute(self, quote_id: str, *, force_live: bool = False) -> OrderResult:
        if kill_switch_tripped():
            return OrderResult(False, simulated=True, error="kill switch tripped")

        if not (self.is_live() or force_live):
            logger.info("[DRY RUN] swap execute quote_id=%s", quote_id)
            return OrderResult(True, simulated=True)

        try:
            # See transfer(): readiness failure returns a failure
            # OrderResult through this except, never raises.
            self.require_ready()
            data = self._run("swap", "execute", f"--quote-id={quote_id}", "--yes")
            tx_hash = _extract_tx_hash(data)
            logger.info("MetaMask SWAP executed: quote_id=%s tx=%s", quote_id, tx_hash or None)
            return OrderResult(True, simulated=False, order_id=tx_hash)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MetaMask swap execute failed")
            return OrderResult(False, simulated=False, error=str(exc))

    def send_to_treasury(self, amount: str, token: str = "native") -> OrderResult:
        """Convenience: send to the SINCOR treasury address on Base."""
        return self.transfer(to=TREASURY, amount=amount, token=token, chain_id=BASE_CHAIN_ID)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: Optional[MetaMaskAgentWallet] = None


def get_metamask_agent_wallet() -> MetaMaskAgentWallet:
    global _instance
    if _instance is None:
        _instance = MetaMaskAgentWallet()
    return _instance
