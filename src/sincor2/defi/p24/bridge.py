"""P24 issuance bridge: exact CreatorTokenFactory.issue(...) calldata +
eth_call dry-run + caller-supplied signing.

Wiring prep — the P24 contracts are NOT deployed. Entry points the issuance
flow can call once the factory deploys on Base Sepolia:

  - :func:`build_issue_calldata` — exact ``issue(...)`` calldata for
    ``onchain/src/p24/CreatorTokenFactory.sol``, client-side validated
    against the onchain gates (admin-only, ``screened=true``, unique
    symbol, nonzero addresses).
  - :func:`dry_run_issue` — ``eth_call`` simulation of the exact call;
    raises :class:`BridgeSimulationError` on revert.
  - :func:`issue_creator_token` — dry-run, then sign with the caller's
    signing account and broadcast; returns the tx hash plus the issued
    token address (parsed from the ``TokenIssued`` event). With
    ``dry_run=True`` nothing is signed or broadcast.

Safety rules (hard — mirrors ``sincor2.auction_bridge``):

  * This module NEVER reads, stores, or derives private keys, and it NEVER
    imports ``eth_account``. Callers pass an account object exposing the
    public signing API (``.address`` + ``.sign_transaction``); signing
    uses secp256k1 — the ratified money path.
  * Every state-changing intent is preceded by an ``eth_call`` dry-run;
    a reverting simulation raises :class:`BridgeSimulationError` before
    anything is signed or broadcast.
  * Client-side gates mirror the contract: ``screened`` must be true
    (the content-policy guard runs offchain in ``onboarding.py`` before
    issuance and its ruleset version is recorded on the token), the
    signer must be the factory admin, and the symbol must be unused.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class BridgeError(RuntimeError):
    """Base error for P24 issuance-bridge failures."""


class BridgeSimulationError(BridgeError):
    """Raised when the pre-sign eth_call dry-run reverts."""


# Embedded ABI of onchain/src/p24/CreatorTokenFactory.sol (solc 0.8.24).
# Kept in-module so the bridge needs no compiler at runtime.
_FACTORY_ABI_JSON = (
    '[{"inputs": [{"internalType": "address", "name": "_admin", "type": "address"}],'
    '"stateMutability": "nonpayable", "type": "constructor"},'
    '{"inputs": [], "name": "NotAdmin", "type": "error"},'
    '{"inputs": [], "name": "NotScreened", "type": "error"},'
    '{"inputs": [], "name": "SymbolTaken", "type": "error"},'
    '{"anonymous": false, "inputs": ['
    '{"indexed": true, "internalType": "address", "name": "token", "type": "address"},'
    '{"indexed": false, "internalType": "string", "name": "symbol", "type": "string"},'
    '{"indexed": true, "internalType": "address", "name": "creator", "type": "address"},'
    '{"indexed": false, "internalType": "string", "name": "policyVersion", "type": "string"}],'
    '"name": "TokenIssued", "type": "event"},'
    '{"inputs": [], "name": "admin", "outputs": [{"internalType": "address", "name": "", "type": "address"}],'
    '"stateMutability": "view", "type": "function"},'
    '{"inputs": ['
    '{"internalType": "string", "name": "name_", "type": "string"},'
    '{"internalType": "string", "name": "symbol_", "type": "string"},'
    '{"internalType": "address", "name": "creator", "type": "address"},'
    '{"internalType": "address", "name": "curveInventory", "type": "address"},'
    '{"internalType": "string", "name": "policyVersion", "type": "string"},'
    '{"internalType": "bool", "name": "screened", "type": "bool"}],'
    '"name": "issue", "outputs": [{"internalType": "address", "name": "token", "type": "address"}],'
    '"stateMutability": "nonpayable", "type": "function"},'
    '{"inputs": [{"internalType": "string", "name": "", "type": "string"}], "name": "tokenBySymbol",'
    '"outputs": [{"internalType": "address", "name": "", "type": "address"}],'
    '"stateMutability": "view", "type": "function"}]'
)


def factory_abi() -> List[Dict[str, Any]]:
    """ABI of ``CreatorTokenFactory`` (embedded; no compiler needed)."""
    return json.loads(_FACTORY_ABI_JSON)


def bind_factory(w3: Any, address: str, abi: Optional[List[Dict[str, Any]]] = None) -> Any:
    """Bind a web3 contract instance to a deployed factory address."""
    return w3.eth.contract(address=_checksum(w3, address), abi=abi or factory_abi())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _checksum(w3: Any, address: str) -> str:
    try:
        return w3.to_checksum_address(address)
    except Exception as exc:
        raise BridgeError(f"invalid address {address!r}: {exc}") from exc


def _require_nonzero(name: str, address: str) -> str:
    if int(address, 16) == 0:
        raise BridgeError(f"{name} must be a nonzero address")
    return address


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise BridgeError(f"{name} must be a non-empty string")
    return value


def _encode_data(fn: Any) -> str:
    """Calldata as a 0x-prefixed hex string (web3 v8 returns HexStr)."""
    data = fn._encode_transaction_data()
    if isinstance(data, (bytes, bytearray)):
        return "0x" + bytes(data).hex()
    text = str(data)
    return text if text.startswith("0x") else "0x" + text


def factory_admin(w3: Any, factory: Any) -> str:
    """Checksummed admin address read from the onchain factory."""
    return _checksum(w3, factory.functions.admin().call())


def symbol_taken(w3: Any, factory: Any, symbol: str) -> bool:
    """True when ``symbol`` is already issued (tokenBySymbol != zero)."""
    token = factory.functions.tokenBySymbol(symbol).call()
    return int(token, 16) != 0


def _issue_fn(
    factory: Any,
    name: str,
    symbol: str,
    creator: str,
    curve_inventory: str,
    policy_version: str,
    screened: bool,
) -> Any:
    return factory.functions.issue(
        name, symbol, creator, curve_inventory, policy_version, bool(screened)
    )


def _validate_issue_params(
    w3: Any,
    factory: Any,
    name: str,
    symbol: str,
    creator: str,
    curve_inventory: str,
    policy_version: str,
    screened: bool,
) -> Dict[str, str]:
    """Client-side mirror of the onchain gates. Raises BridgeError."""
    name = _require_text("name", name)
    symbol = _require_text("symbol", symbol)
    creator = _require_nonzero("creator", _checksum(w3, creator))
    curve_inventory = _require_nonzero("curve_inventory", _checksum(w3, curve_inventory))
    policy_version = _require_text("policy_version", policy_version)
    if not screened:
        raise BridgeError(
            "issuance requires a passed content-policy screen (screened=true); "
            "the offchain guard in onboarding.py runs before issuance"
        )
    if symbol_taken(w3, factory, symbol):
        raise BridgeError(f"symbol {symbol!r} already issued (SymbolTaken)")
    return {
        "name": name,
        "symbol": symbol,
        "creator": creator,
        "curve_inventory": curve_inventory,
        "policy_version": policy_version,
    }


def _dry_run_call(w3: Any, fn: Any, call_kwargs: Dict[str, Any], label: str) -> None:
    """eth_call simulation; raises BridgeSimulationError on revert."""
    try:
        fn.call(call_kwargs)
    except Exception as exc:
        raise BridgeSimulationError(f"dry-run reverted for {label}: {exc}") from exc


def _build_tx(
    w3: Any,
    fn: Any,
    sender: str,
    value_wei: int = 0,
    *,
    nonce: Optional[int] = None,
    gas_limit: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
) -> Dict[str, Any]:
    tx: Dict[str, Any] = {
        "from": _checksum(w3, sender),
        "to": _checksum(w3, fn.address),
        "data": _encode_data(fn),
        "value": int(value_wei),
        "nonce": nonce if nonce is not None else w3.eth.get_transaction_count(sender, "pending"),
        "chainId": w3.eth.chain_id,
    }
    if max_fee_per_gas is not None:
        tx["maxFeePerGas"] = int(max_fee_per_gas)
        tx["maxPriorityFeePerGas"] = int(max_priority_fee_per_gas or 0)
    else:
        tx["gasPrice"] = int(gas_price_wei) if gas_price_wei is not None else w3.eth.gas_price
    if gas_limit is not None:
        tx["gas"] = int(gas_limit)
    else:
        # estimate_gas needs the fee fields present for a faithful estimate
        try:
            tx["gas"] = int(w3.eth.estimate_gas(
                {k: v for k, v in tx.items() if k != "nonce"}) * 1.2)
        except Exception as exc:
            raise BridgeError(
                f"gas estimation failed (transaction would revert): {exc}"
            ) from exc
    return tx


def _sign_and_send(w3: Any, tx: Dict[str, Any], signer: Any, timeout_s: int = 300) -> Dict[str, Any]:
    """Sign with the caller's account object and broadcast.

    ``signer`` must expose ``.address`` and ``.sign_transaction`` (the
    public signing API). This module never sees key material.
    """
    if not hasattr(signer, "sign_transaction") or not hasattr(signer, "address"):
        raise BridgeError(
            "signer must expose .address and .sign_transaction (caller-supplied account)"
        )
    signed = signer.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_s)
    if int(receipt.get("status", 0)) != 1:
        raise BridgeError(f"transaction reverted onchain: {tx_hash.hex()}")
    return {"tx_hash": "0x" + tx_hash.hex(), "status": int(receipt.status),
            "receipt": dict(receipt)}


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def build_issue_calldata(
    w3: Any,
    factory: Any,
    *,
    name: str,
    symbol: str,
    creator: str,
    curve_inventory: str,
    policy_version: str,
    screened: bool,
) -> str:
    """Exact ``issue(...)`` calldata, client-side validated.

    Validates the onchain gates before encoding (admin/signer binding is
    checked in :func:`issue_creator_token`; the symbol-uniqueness read
    happens here). Returns 0x-prefixed calldata hex.
    """
    params = _validate_issue_params(
        w3, factory, name, symbol, creator, curve_inventory, policy_version, screened
    )
    fn = _issue_fn(
        factory, params["name"], params["symbol"], params["creator"],
        params["curve_inventory"], params["policy_version"], True,
    )
    return _encode_data(fn)


def dry_run_issue(
    w3: Any,
    factory: Any,
    *,
    name: str,
    symbol: str,
    creator: str,
    curve_inventory: str,
    policy_version: str,
    screened: bool,
    admin: Optional[str] = None,
) -> Dict[str, str]:
    """eth_call simulation of the exact ``issue(...)`` call.

    Raises :class:`BridgeSimulationError` when the call would revert
    (non-admin sender, unscreened, duplicate symbol). Returns the
    validated params on success; nothing is signed or broadcast.
    """
    admin_c = _checksum(w3, admin) if admin is not None else factory_admin(w3, factory)
    params = _validate_issue_params(
        w3, factory, name, symbol, creator, curve_inventory, policy_version, screened
    )
    fn = _issue_fn(
        factory, params["name"], params["symbol"], params["creator"],
        params["curve_inventory"], params["policy_version"], True,
    )
    _dry_run_call(
        w3, fn,
        {"from": admin_c, "to": factory.address},
        "issue",
    )
    logger.info("[dry-run] issue simulated OK (symbol=%s admin=%s)",
                params["symbol"], admin_c)
    return {"symbol": params["symbol"], "admin": admin_c,
            "data": _encode_data(fn)}


def issue_creator_token(
    w3: Any,
    factory: Any,
    *,
    name: str,
    symbol: str,
    creator: str,
    curve_inventory: str,
    policy_version: str,
    screened: bool,
    signer: Any,
    dry_run: bool = False,
    gas_limit: Optional[int] = None,
    gas_price_wei: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    nonce: Optional[int] = None,
    timeout_s: int = 300,
) -> Dict[str, Any]:
    """Issue a creator token through the onchain factory.

    ``issue()`` is admin-only: the signer must be the factory admin.
    Flow: client-side gate checks → ``eth_call`` dry-run → sign with the
    caller's account → broadcast → parse the ``TokenIssued`` event.

    Returns ``{"tx_hash", "token", "symbol", "admin", "dry_run"}``. With
    ``dry_run=True`` nothing is signed or broadcast; the unsigned
    transaction is returned instead.
    """
    admin = factory_admin(w3, factory)
    if not hasattr(signer, "sign_transaction") or not hasattr(signer, "address"):
        raise BridgeError(
            "signer must expose .address and .sign_transaction "
            "(caller-supplied account; this module never handles key material)"
        )
    sender = _checksum(w3, signer.address)
    if sender != admin:
        raise BridgeError(
            f"signer {sender} is not the factory admin {admin} (NotAdmin)"
        )
    sim = dry_run_issue(
        w3, factory, name=name, symbol=symbol, creator=creator,
        curve_inventory=curve_inventory, policy_version=policy_version,
        screened=screened, admin=admin,
    )
    fn = _issue_fn(
        factory, name, symbol, _checksum(w3, creator),
        _checksum(w3, curve_inventory), policy_version, True,
    )
    tx = _build_tx(
        w3, fn, sender, 0,
        nonce=nonce, gas_limit=gas_limit, gas_price_wei=gas_price_wei,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
    )
    result: Dict[str, Any] = {
        "symbol": sim["symbol"],
        "admin": admin,
        "data": sim["data"],
        "dry_run": bool(dry_run),
    }
    if dry_run:
        result["unsigned_tx"] = tx
        return result
    sent = _sign_and_send(w3, tx, signer, timeout_s=timeout_s)
    token: Optional[str] = None
    try:
        events = factory.events.TokenIssued().process_receipt(sent["receipt"])
        if events:
            token = _checksum(w3, events[0]["args"]["token"])
    except Exception as exc:  # event parsing must not mask a confirmed issuance
        logger.warning("TokenIssued event parse failed: %s", exc)
    result.update({"tx_hash": sent["tx_hash"], "token": token, "dry_run": False})
    logger.info("issue confirmed: %s (symbol=%s token=%s)",
                sent["tx_hash"], symbol, token)
    return result
