"""Canonical authorization interface for the fee-conversion executor.

This module separates AUTHORIZATION (an owner decision, verified
cryptographically) from EXECUTION (broadcasting, which remains a
founder-gated ceremony that does not exist in this repository).

What this module does NOT do (hard rules):
* It never reads private keys, never signs, never broadcasts.
* It never moves funds, never touches a signer callback.
* Verification is pure: given a record + signatures + a clock, it answers
  yes/no.  Tests use synthetic keys; no real owner key material exists here.

The runbook (docs/ops/FEE_EXECUTOR_RUNBOOK.md) requires "a separately
reviewed canonical authorization interface and explicit owner decisions for
capability, chain/token/target allowlists, limits, approvers/quorum, and
canonical receipt ownership" before live conversion can be reconsidered.
This module IS that interface.  A valid authorization flips a plan from
"denied by design" to "authorized for the exact scope in the record" — the
broadcast itself stays a separate, founder-gated step.

Signature scheme: EIP-191 personal_sign over a domain-separated keccak256
of the canonical record JSON.  Chosen over EIP-712 because every owner EOA
can produce it trivially (cast wallet sign / personal_sign) and the record
is hashed canonically either way.  A Safe-as-approver would need an
EIP-1271 isValidSignature extension; that is documented future work, not
built here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from eth_hash.auto import keccak

logger = logging.getLogger("sincor.treasury.fee_authorization")

# --- constants ---------------------------------------------------------------

#: The only capability this interface can authorize.
FEE_CONVERSION_CAPABILITY = "sincor.fee-conversion.v1"

#: Domain separation for the authorization hash.  Bumped on any incompatible
#: change to the canonical record layout.
AUTH_DOMAIN = b"SINCOR-FEE-AUTH/V1"

#: Ledger status used when an obligation's plan is authorized but nothing has
#: been broadcast.  Mirrors the ConversionLedger status vocabulary.
STATUS_AUTHORIZED = "authorized"


class AuthorizationError(RuntimeError):
    """Raised for any authorization failure.  Fail closed, always."""


# --- address hygiene ---------------------------------------------------------

def _norm_addr(addr: str) -> str:
    """Lowercase hex with 0x prefix; rejects malformed input."""
    a = addr.strip().lower()
    if not (a.startswith("0x") and len(a) == 42):
        raise AuthorizationError(f"malformed address: {addr!r}")
    try:
        int(a[2:], 16)
    except ValueError:
        raise AuthorizationError(f"malformed address: {addr!r}")
    return a


# --- the record --------------------------------------------------------------

@dataclass(frozen=True)
class AuthorizationRecord:
    """One owner decision authorizing fee-conversion runs within a scope.

    All addresses are normalized to lowercase hex on construction.
    ``nonce`` must be unique per record (replay protection across runs).
    ``receipt_owner`` names the canonical owner of conversion receipts
    (the entity whose books the receipts belong to, e.g. the treasury
    operator) — it does not move funds, it labels accountability.
    """

    capability: str
    chain_ids: Tuple[int, ...]
    token_in_allowlist: Tuple[str, ...]
    token_out_allowlist: Tuple[str, ...]
    treasury: str
    max_wei_per_run: int
    max_slippage_bps: int
    not_before: int
    expires_at: int
    nonce: str
    approvers: Tuple[str, ...]
    quorum: int
    receipt_owner: str

    def __post_init__(self) -> None:
        # Normalize addresses; keep everything else byte-identical so the
        # canonical hash is stable regardless of input casing.
        object.__setattr__(self, "treasury", _norm_addr(self.treasury))
        object.__setattr__(self, "receipt_owner", _norm_addr(self.receipt_owner))
        object.__setattr__(
            self, "token_in_allowlist",
            tuple(_norm_addr(a) for a in self.token_in_allowlist))
        object.__setattr__(
            self, "token_out_allowlist",
            tuple(_norm_addr(a) for a in self.token_out_allowlist))
        object.__setattr__(
            self, "approvers", tuple(_norm_addr(a) for a in self.approvers))
        if self.quorum < 1:
            raise AuthorizationError("quorum must be >= 1")
        if self.quorum > len(self.approvers):
            raise AuthorizationError("quorum exceeds approver count")
        if self.expires_at <= self.not_before:
            raise AuthorizationError("expires_at must be after not_before")
        if self.max_wei_per_run <= 0:
            raise AuthorizationError("max_wei_per_run must be positive")
        if not (0 <= self.max_slippage_bps <= 10_000):
            raise AuthorizationError("max_slippage_bps out of range")
        if not self.nonce:
            raise AuthorizationError("nonce is required")

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "approvers": list(self.approvers),
            "capability": self.capability,
            "chain_ids": list(self.chain_ids),
            "expires_at": self.expires_at,
            "max_slippage_bps": self.max_slippage_bps,
            "max_wei_per_run": self.max_wei_per_run,
            "nonce": self.nonce,
            "not_before": self.not_before,
            "quorum": self.quorum,
            "receipt_owner": self.receipt_owner,
            "token_in_allowlist": list(self.token_in_allowlist),
            "token_out_allowlist": list(self.token_out_allowlist),
            "treasury": self.treasury,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_dict(), sort_keys=True,
            separators=(",", ":")).encode("utf-8")

    def auth_hash(self) -> bytes:
        """Domain-separated hash that approvers sign."""
        return keccak(AUTH_DOMAIN + b"\n" + self.canonical_bytes())

    def auth_hash_hex(self) -> str:
        return "0x" + self.auth_hash().hex()


# --- verification (pure) -----------------------------------------------------

def _recover_signer(auth_hash: bytes, signature: str) -> str:
    """Recover the EIP-191 personal_sign signer of auth_hash."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    if not isinstance(signature, str):
        raise AuthorizationError("signature must be a hex string")
    sig = signature.strip()
    if sig.startswith("0x"):
        sig = sig[2:]
    try:
        sig_bytes = bytes.fromhex(sig)
    except ValueError:
        raise AuthorizationError("signature is not hex")
    if len(sig_bytes) != 65:
        raise AuthorizationError(
            f"signature must be 65 bytes, got {len(sig_bytes)}")
    try:
        msg = encode_defunct(hexstr="0x" + auth_hash.hex())
        signer = Account.recover_message(msg, signature=sig_bytes)
    except Exception as exc:
        raise AuthorizationError(f"signature recovery failed: {exc}")
    return signer.lower()


def verify_authorization(record: AuthorizationRecord,
                         signatures: Sequence[str],
                         now: Optional[int] = None) -> AuthorizationRecord:
    """Verify a record + approver signatures.  Returns the record on success.

    Checks, in order: capability, time window, quorum of DISTINCT approver
    signatures over the domain-separated record hash.  Raises
    AuthorizationError on the first failure.  Pure: no network, no keys.
    """
    now = int(now if now is not None else time.time())

    if record.capability != FEE_CONVERSION_CAPABILITY:
        raise AuthorizationError(
            f"capability {record.capability!r} != {FEE_CONVERSION_CAPABILITY!r}")
    if now < record.not_before:
        raise AuthorizationError(
            f"authorization not yet valid (not_before={record.not_before})")
    if now >= record.expires_at:
        raise AuthorizationError(
            f"authorization expired (expires_at={record.expires_at})")

    auth_hash = record.auth_hash()
    seen: Set[str] = set()
    for sig in signatures:
        signer = _recover_signer(auth_hash, sig)
        if signer in record.approvers:
            seen.add(signer)
    if len(seen) < record.quorum:
        raise AuthorizationError(
            f"quorum not met: {len(seen)} distinct approver signatures, "
            f"need {record.quorum}")

    logger.info("authorization %s verified: %d/%d approvers",
                record.auth_hash_hex()[:18], len(seen), record.quorum)
    return record


def check_plan_scope(record: AuthorizationRecord, *,
                     chain_id: int,
                     token_in: str,
                     token_out: str,
                     treasury: str,
                     amount_wei: int,
                     slippage_bps: int) -> None:
    """Enforce that a concrete plan sits inside the authorized scope.

    Raises AuthorizationError on any violation.  Pure.
    """
    if chain_id not in record.chain_ids:
        raise AuthorizationError(
            f"chain {chain_id} not in authorized {list(record.chain_ids)}")
    if _norm_addr(token_in) not in record.token_in_allowlist:
        raise AuthorizationError(f"token_in {token_in} not allowlisted")
    if _norm_addr(token_out) not in record.token_out_allowlist:
        raise AuthorizationError(f"token_out {token_out} not allowlisted")
    if _norm_addr(treasury) != record.treasury:
        raise AuthorizationError(
            f"treasury {treasury} != authorized {record.treasury}")
    if amount_wei <= 0:
        raise AuthorizationError(
            f"amount {amount_wei} must be > 0")
    if amount_wei > record.max_wei_per_run:
        raise AuthorizationError(
            f"amount {amount_wei} exceeds max_wei_per_run "
            f"{record.max_wei_per_run}")
    if slippage_bps > record.max_slippage_bps:
        raise AuthorizationError(
            f"slippage {slippage_bps} bps exceeds max {record.max_slippage_bps}")
    logger.info("plan scope OK: chain=%d %s->%s amount=%d",
                chain_id, token_in, token_out, amount_wei)


# --- authorized dry-run (the full loop, no broadcast) -------------------------

def authorized_dry_run(executor: Any,
                       obligation_id: str,
                       record: AuthorizationRecord,
                       signatures: Sequence[str],
                       *,
                       target: Optional[str] = None,
                       manifest_dir: Optional[str] = None,
                       used_nonces: Set[str],
                       now: Optional[int] = None) -> Dict[str, Any]:
    """Run the full conversion loop under authorization — without broadcast.

    Steps: verify authorization -> plan (quote + unsigned txs) ->
    scope-check the concrete plan -> dry-run simulation of every step ->
    ledger marks the obligation AUTHORIZED -> manifest written.

    There is deliberately NO sign/broadcast parameter: this function cannot
    broadcast even if called wrong.  The returned manifest is the artifact a
    future founder-gated broadcast ceremony would consume.

    ``used_nonces`` is REQUIRED: nonce replay protection is mandatory, not
    opt-in.  Pass a durable journal (a set the caller persists across runs);
    reuse of a nonce raises AuthorizationError and the run is recorded as
    AUTHORIZED only after the nonce is journaled.

    ``now`` defaults to the wall clock for tests/dry-runs only.  Production
    callers MUST pass a trusted clock value for ``now``: the authorization
    time window (not_before / expires_at) is enforced against it, and a
    skewed or attacker-controlled clock would widen or bypass the window.

    Raises AuthorizationError (auth/scope/nonce) or ExecutorError
    (planning/simulation) — both fail closed, nothing is broadcast.
    """
    now = int(now if now is not None else time.time())

    # 1. Authorization first: nothing else happens without it.
    verify_authorization(record, signatures, now=now)

    # 2. Replay protection across runs: the record's own contract requires
    #    the nonce to be unique per record; reuse fails closed.
    if record.nonce in used_nonces:
        raise AuthorizationError(
            f"authorization nonce {record.nonce!r} already used")
    # 3. Plan: quote (read-only) + unsigned txs.  No broadcast.
    plan = executor.plan(obligation_id, target=target)
    quote = plan["quote"]

    # 4. Scope-check the CONCRETE plan values, not the request.
    #    Token symbols from the quote resolve through config.token_address(),
    #    which falls back to TOKEN_ADDRESSES when token_addresses is None
    #    (the Base-mainnet default).  A raw dict lookup here would leak the
    #    bare symbol ("WETH") into _norm_addr and fail closed incorrectly.
    check_plan_scope(
        record,
        chain_id=executor.config.chain_id,
        token_in=executor.config.token_address(quote["token_in"]),
        token_out=executor.config.token_address(quote["token_out"]),
        treasury=executor.config.treasury,
        amount_wei=int(quote["amount_in_wei"]),
        slippage_bps=int(quote["slippage_bps"]),
    )

    # 5. Dry-run simulation of every step (eth_call from the forwarder).
    #    execute_plan(dry_run=True) never touches a signer and never marks
    #    the obligation executed — it only simulates.
    sim = executor.execute_plan(plan, _no_signer, _no_receipt, dry_run=True)

    # 6. Ledger: authorized, not executed.  The manifest is the receipt.
    manifest = {
        "kind": "fee-conversion-authorized-manifest",
        "version": 1,
        "authorization_hash": record.auth_hash_hex(),
        "authorization": record.canonical_dict(),
        "obligation_id": obligation_id,
        "chain_id": executor.config.chain_id,
        "quote": quote,
        "unsigned_txs": plan["txs"],
        "simulation": sim,
        "forwarder": plan["forwarder"],
        "treasury": plan["treasury"],
        "broadcast": False,
        "broadcast_note": ("authorized but NOT broadcast: broadcasting is a "
                           "separate founder-gated ceremony"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    manifest_path = None
    if manifest_dir:
        os.makedirs(manifest_dir, exist_ok=True)
        manifest_path = os.path.join(
            manifest_dir, f"authorized-{obligation_id}.json")
        tmp = manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        os.replace(tmp, manifest_path)
        manifest["manifest_path"] = manifest_path

    executor.ledger.transition(
        obligation_id, STATUS_AUTHORIZED,
        {"authorization_hash": record.auth_hash_hex(),
         "manifest_path": manifest_path})

    used_nonces.add(record.nonce)

    logger.info("obligation %s authorized (not broadcast); manifest=%s",
                obligation_id, manifest_path)
    return manifest


def _no_signer(unsigned: Mapping[str, Any], kind: str) -> str:
    raise AuthorizationError(
        "authorized_dry_run never signs: no broadcast path exists here")


def _no_receipt(tx_hash: str) -> Mapping[str, Any]:
    raise AuthorizationError(
        "authorized_dry_run never waits on receipts: nothing was broadcast")


# --- synthetic fixtures (tests / dry-runs only) ------------------------------
# NOTE: synthetic_approvers lives in tests/pytest/fee_auth_test_helpers.py,
# not here: deterministic key generation does not belong in a production
# security module.  sign_record/demo_record stay: they are the record-signing
# machinery exercised by tests and dry-runs.

def sign_record(record: AuthorizationRecord, account: Any) -> str:
    """EIP-191 personal_sign of the record hash.  Tests/dry-runs only."""
    from eth_account.messages import encode_defunct

    msg = encode_defunct(hexstr="0x" + record.auth_hash().hex())
    return "0x" + account.sign_message(msg).signature.hex()


def demo_record(*,
                chain_id: int,
                token_in: str,
                token_out: str,
                treasury: str,
                approvers: Sequence[Any],
                quorum: int = 2,
                max_wei_per_run: int = 10**21,
                max_slippage_bps: int = 200,
                ttl_secs: int = 3600,
                nonce: str = "demo-nonce-1",
                receipt_owner: Optional[str] = None,
                now: Optional[int] = None) -> AuthorizationRecord:
    """Build a synthetic authorization record.  Tests/dry-runs only."""
    now = int(now if now is not None else time.time())
    addrs = [a.address for a in approvers]
    return AuthorizationRecord(
        capability=FEE_CONVERSION_CAPABILITY,
        chain_ids=(chain_id,),
        token_in_allowlist=(_norm_addr(token_in),),
        token_out_allowlist=(_norm_addr(token_out),),
        treasury=_norm_addr(treasury),
        max_wei_per_run=max_wei_per_run,
        max_slippage_bps=max_slippage_bps,
        not_before=now - 60,
        expires_at=now + ttl_secs,
        nonce=nonce,
        approvers=tuple(addrs),
        quorum=quorum,
        receipt_owner=_norm_addr(receipt_owner or addrs[0]),
    )
