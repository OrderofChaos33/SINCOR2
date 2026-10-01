"""web3.py client for the SINCOR2 stake/slash contracts (wiring prep — NOT deployed).

Wraps ``contracts/StakeSlashManager.sol``: transaction *builders* (unsigned
dicts — the caller signs and broadcasts with their own signer), the
adjudicator-ruling struct-hash builder (byte-identical to the on-chain
``slash()`` digest), read helpers, and eth_call dry-run.

Safety rules (hard):
  * This module NEVER reads private keys and NEVER imports eth_account.
    ``build_*_tx`` return unsigned transaction dicts; sign them with
    eth_account (or your HSM) and broadcast with ``w3.eth.send_raw_transaction``
    in the calling code. The adjudicator signs ruling digests with their own
    tooling; this module only *builds* the digest bytes.
  * No mainnet calls are made here. Every public method raises
    :class:`StakeSlashNotConfiguredError` unless ``STAKE_SLASH_ADDRESS``
    and ``STAKE_RPC_URL`` are both set.
  * Actual deployment is founder-authorized only; see
    ``docs/ops/STAKE_SLASH_DEPLOY_RUNBOOK.md``.

ABI lives in ``src/sincor2/onchain/abis/StakeSlashManager.json`` (compiled
from ``contracts/`` with solc 0.8.24 --via-ir --optimize --optimize-runs 200;
see ``scripts/gen_stake_slash_abi.py``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ABIS_DIR = Path(__file__).resolve().parent / "abis"

STAKE_SLASH_ENV_ADDRESS = "STAKE_SLASH_ADDRESS"
RPC_ENV_URL = "STAKE_RPC_URL"
CHAIN_ID_ENV = "STAKE_CHAIN_ID"

DEFAULT_CHAIN_ID = 84532  # Base Sepolia (deploy target)

# Event names worth watching, grouped by operational concern.
STAKE_EVENTS = ("Staked", "UnstakeRequested", "Unstaked")
SLASH_EVENTS = ("SlashExecuted", "AdjudicatorRotated", "MinStakeUpdated")

# Minimal ERC-20 surface used for approve/balanceOf.
_ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

REASON_GHOST = "ghost"
REASON_QUALITY = "quality"
_REASONS = {REASON_GHOST: True, REASON_QUALITY: True}

# Must match contracts/StakeSlashManager.sol: keccak256("SINCOR-SLASH").
# Computed lazily via _ruling_typehash() (needs eth_hash at call time).


def _keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
        return keccak(data)
    except Exception:
        from sha3 import keccak_256  # type: ignore
        return keccak_256(data).digest()


def _ruling_typehash() -> bytes:
    return _keccak(b"SINCOR-SLASH")


class StakeSlashNotConfiguredError(RuntimeError):
    """Raised when stake/slash wiring is used without deployment addresses/RPC."""


@dataclass
class StakeSlashConfig:
    contract_address: Optional[str] = None
    rpc_url: Optional[str] = None
    chain_id: int = DEFAULT_CHAIN_ID


@dataclass
class SlashRuling:
    """Adjudicator ruling that authorizes one on-chain slash.

    The adjudicator signs ``slash_struct_hash(...)`` with EIP-191
    (``encode_defunct(hexstr=...)``) using their own tooling; the (v, r, s)
    triple travels with the ruling to ``StakeSlashManager.slash``.
    """
    agent: str
    poster: str
    amount_wei: int
    treasury_cut_wei: int
    nonce: int
    expiry: int  # unix seconds
    reason: str  # "ghost" | "quality"

    def validate(self) -> None:
        if self.reason not in _REASONS:
            raise ValueError(f"reason must be one of {sorted(_REASONS)}")
        for name in ("agent", "poster"):
            addr = str(getattr(self, name) or "")
            if not (addr.startswith("0x") and len(addr) == 42):
                raise ValueError(f"{name} must be a 0x address")
        if int(self.amount_wei) <= 0:
            raise ValueError("amount_wei must be positive")
        if int(self.treasury_cut_wei) < 0 or int(self.treasury_cut_wei) > int(self.amount_wei):
            raise ValueError("treasury_cut_wei must be within [0, amount_wei]")
        if int(self.nonce) < 0 or int(self.expiry) <= 0:
            raise ValueError("nonce/expiry must be non-negative/positive")


def slash_struct_hash(
    contract_address: str,
    chain_id: int,
    ruling: SlashRuling,
) -> bytes:
    """Build the exact struct hash ``StakeSlashManager.slash`` verifies.

    keccak256(abi.encode(RULING_TYPEHASH, chainid, address(this), agent,
    poster, amount, treasuryCut, nonce, expiry, reason)). Byte-identical to
    the on-chain digest; any deviation fails signature verification.
    """
    ruling.validate()
    from eth_abi import encode as abi_encode

    reason_hash = _keccak(ruling.reason.encode("utf-8"))
    return _keccak(
        abi_encode(
            ["bytes32", "uint256", "address", "address", "address",
             "uint256", "uint256", "uint64", "uint64", "bytes32"],
            [
                _ruling_typehash(),
                int(chain_id),
                contract_address,
                ruling.agent,
                ruling.poster,
                int(ruling.amount_wei),
                int(ruling.treasury_cut_wei),
                int(ruling.nonce),
                int(ruling.expiry),
                reason_hash,
            ],
        )
    )


def canonicalize_signature(
    signature: Tuple[int, str, str],
) -> Tuple[int, str, str]:
    """Force a (v, r, s) triple into low-S canonical form.

    ``StakeSlashManager._recover`` rejects high-S signatures (malleability
    guard), and common signers (e.g. eth_account) do NOT canonicalize s.
    The adjudicator's signing flow MUST pass signatures through here before
    submitting a ruling, otherwise ~50 % of rulings revert on-chain with
    ``BadSignature``. Flipping s -> n - s preserves the signer; v toggles.
    """
    v, r_hex, s_hex = signature
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    r = int(r_hex, 16)
    s = int(s_hex, 16)
    if s > n // 2:
        s = n - s
        v = 28 if v == 27 else 27
    return v, "0x" + hex(r)[2:].zfill(64), "0x" + hex(s)[2:].zfill(64)


def _load_abi(name: str) -> List[Dict[str, Any]]:
    path = _ABIS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["abi"]


class StakeSlashBridge:
    """Unsigned-tx builder + dry-run for StakeSlashManager. Never signs."""

    def __init__(self, config: Optional[StakeSlashConfig] = None, w3: Any = None):
        self.config = config or StakeSlashConfig(
            contract_address=os.environ.get(STAKE_SLASH_ENV_ADDRESS),
            rpc_url=os.environ.get(RPC_ENV_URL),
            chain_id=int(os.environ.get(CHAIN_ID_ENV, str(DEFAULT_CHAIN_ID))),
        )
        self._w3 = w3

    @property
    def w3(self) -> Any:
        if self._w3 is None:
            from web3 import Web3

            cfg = self._require_configured()
            self._w3 = Web3(Web3.HTTPProvider(cfg.rpc_url))
        return self._w3

    def _require_configured(self) -> StakeSlashConfig:
        cfg = self.config
        if not cfg.contract_address or not cfg.rpc_url:
            raise StakeSlashNotConfiguredError(
                f"set {STAKE_SLASH_ENV_ADDRESS} and {RPC_ENV_URL}"
            )
        return cfg

    @staticmethod
    def _checksum(addr: str) -> str:
        from web3 import Web3

        return Web3.to_checksum_address(addr)

    def contract(self) -> Any:
        cfg = self._require_configured()
        return self.w3.eth.contract(
            address=self._checksum(cfg.contract_address),
            abi=_load_abi("StakeSlashManager"),
        )

    def token_contract(self, token_address: str) -> Any:
        return self.w3.eth.contract(
            address=self._checksum(token_address), abi=_ERC20_ABI
        )

    def build_transaction(
        self,
        contract_function: Any,
        sender: str,
        value_wei: int = 0,
        *,
        nonce: Optional[int] = None,
        gas: Optional[int] = None,
        gas_price_wei: Optional[int] = None,
        chain_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build an unsigned tx dict for ``contract_function``.

        ``nonce``/``gas`` are fetched from the RPC when omitted (pass them
        explicitly for fully offline encoding). The dict is ready for
        ``eth_account`` signing — this module never sees keys.
        """
        cfg = self._require_configured()
        w3 = self.w3
        sender = self._checksum(sender)
        if nonce is None:
            nonce = w3.eth.get_transaction_count(sender)
        if gas is None:
            gas = contract_function.estimate_gas({"from": sender, "value": value_wei})
        tx_params: Dict[str, Any] = {
            "from": sender,
            "value": int(value_wei),
            "nonce": nonce,
            "gas": gas,
            "chainId": chain_id or cfg.chain_id,
        }
        if gas_price_wei is not None:
            tx_params["gasPrice"] = int(gas_price_wei)
        return contract_function.build_transaction(tx_params)

    # -- staking ------------------------------------------------------------
    def build_approve_tx(
        self, token: str, spender: str, amount_wei: int, sender: str, **tx_kw
    ) -> Dict[str, Any]:
        fn = self.token_contract(token).functions.approve(
            self._checksum(spender), int(amount_wei)
        )
        return self.build_transaction(fn, sender, **tx_kw)

    def build_stake_tx(
        self, agent_wallet: str, amount_wei: int, sender: str, **tx_kw
    ) -> Dict[str, Any]:
        """stake() pulls collateral from msg.sender via transferFrom.

        ``agent_wallet`` is informational (the staker is ``sender``); approve
        the manager for ``amount_wei`` first via build_approve_tx.
        """
        fn = self.contract().functions.stake(int(amount_wei))
        return self.build_transaction(fn, sender or agent_wallet, **tx_kw)

    def build_request_unstake_tx(
        self, agent_wallet: str, amount_wei: int, sender: str, **tx_kw
    ) -> Dict[str, Any]:
        fn = self.contract().functions.requestUnstake(int(amount_wei))
        return self.build_transaction(fn, sender or agent_wallet, **tx_kw)

    def build_finalize_unstake_tx(
        self, agent_wallet: str, sender: str, **tx_kw
    ) -> Dict[str, Any]:
        fn = self.contract().functions.finalizeUnstake()
        return self.build_transaction(fn, sender or agent_wallet, **tx_kw)

    # -- slashing -----------------------------------------------------------
    def build_slash_tx(
        self,
        ruling: SlashRuling,
        signature: Tuple[int, str, str],
        sender: str,
        **tx_kw,
    ) -> Dict[str, Any]:
        """Build the unsigned slash tx. ``signature`` is (v, r, s) as
        produced by the adjudicator's own signing tooling over
        ``slash_struct_hash(...)``. Anyone may submit; the contract enforces
        the adjudicator signature on-chain."""
        ruling.validate()
        v, r, s = signature
        fn = self.contract().functions.slash(
            self._checksum(ruling.agent),
            self._checksum(ruling.poster),
            int(ruling.amount_wei),
            int(ruling.treasury_cut_wei),
            int(ruling.nonce),
            int(ruling.expiry),
            _keccak(ruling.reason.encode("utf-8")),
            int(v),
            r,
            s,
        )
        return self.build_transaction(fn, sender, **tx_kw)

    def dry_run(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        """eth_call dry-run of an unsigned tx dict. Returns
        {"ok": True, "returndata": ...} or raises with the revert reason."""
        cfg = self._require_configured()
        call = dict(tx)
        call.setdefault("to", self._checksum(cfg.contract_address))
        try:
            ret = self.w3.eth.call(call)
            return {"ok": True, "returndata": ret.hex()}
        except Exception as exc:
            raise RuntimeError(f"dry-run reverted: {exc}") from exc

    # -- reads --------------------------------------------------------------
    def stake_of(self, agent_wallet: str) -> int:
        return int(
            self.contract().functions.stakeOf(self._checksum(agent_wallet)).call()
        )

    def slash_nonce_of(self, agent_wallet: str) -> int:
        return int(
            self.contract().functions.slashNonceOf(self._checksum(agent_wallet)).call()
        )

    def can_participate(self, agent_wallet: str) -> bool:
        return bool(
            self.contract().functions.canParticipate(
                self._checksum(agent_wallet)
            ).call()
        )

    def adjudicator(self) -> str:
        return str(self.contract().functions.adjudicator().call())
