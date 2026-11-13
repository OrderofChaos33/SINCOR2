"""Buyer client — pays wrapped endpoints without ever double-paying.

The bug this design makes impossible: a real x402 retry loop once produced
40 payments for one request (retrying after ambiguous responses, each retry
signing a *fresh* authorization). Defences, in order:

1. **Instruction-scoped idempotency.** Every paid call carries an
   ``instruction_id`` (the caller's unit of work). The journal maps
   instruction_id → (request fingerprint, signed payload, outcome).
   Repeating a call with the same instruction_id and the same request
   returns the recorded outcome and NEVER signs again. Reusing the id with
   a *different* request raises IdempotencyConflict instead of paying.
   The journal is DURABLE (W-147): it is file-backed (``journal_path``,
   a sensible per-user default), every state change is persisted
   atomically before the buyer acts on it, and the instruction is
   claimed — and the claim persisted — BEFORE anything is signed. A
   fresh buyer instance (a process restart) therefore sees the prior
   instance's payments, outcomes and ambiguity locks, and the claim is
   held under a lock across the whole check-then-sign sequence so two
   concurrent calls for one instruction cannot both sign.
2. **One signature per instruction, reused verbatim.** HTTP-level retries
   resend the SAME signed payload (same EIP-3009 nonce), which the chain /
   facilitator can only settle once — a resend is not a new payment.
3. **Per-payee velocity cap.** Distinct instructions to the same payTo are
   capped by count and total amount inside a sliding window; the cap is
   checked BEFORE signing, so a capped attempt creates no payment at all.
4. **No blind retry on ambiguity** (inherited from the platform buyer):
   a retry response that is neither clear success nor clear rejection
   raises AmbiguousPayment and the journal keeps the instruction locked.

Keys never enter this module: the EIP-712 signer is injected, exactly as
in the platform's ``x402_buyer.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import (AmbiguousPayment, IdempotencyConflict, MalformedChallenge,
                     PaymentCapExceeded, UnsupportedPayment,
                     VelocityCapExceeded, SincorX402Error)
from .types import USDC_BASE_ADDRESS, canonical_json

SignerFn = Callable[[dict[str, Any]], str]
# transport(method, url, body, headers) -> (status, headers, raw_bytes)
TransportFn = Callable[[str, str, Optional[dict], dict], tuple]


def _urllib_transport(method: str, url: str, body: Optional[dict],
                      headers: dict) -> tuple:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(),
                                 headers=headers)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


@dataclass
class _JournalEntry:
    fingerprint: str
    payment_payload: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    ambiguous: bool = False
    # Set (and persisted) before signing starts (W-147 claim-then-sign):
    # a claimed entry with no payload and no outcome means a previous
    # attempt died mid-flight — fail closed (ambiguous), never re-sign.
    claimed: bool = False


def default_journal_path() -> Path:
    """Sensible default for the durable instruction journal (W-147).

    ``$SINCOR_X402_BUYER_JOURNAL_PATH`` wins; otherwise an XDG-style
    per-user data file. The journal must outlive the process, so it
    never lives in a temp directory.
    """
    env = os.environ.get("SINCOR_X402_BUYER_JOURNAL_PATH")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "sincor-x402" / "buyer-journal.json"


# One re-entrant lock per journal path, shared by every buyer instance in
# this process: two instances pointing at the same journal must not be
# able to claim-and-sign the same instruction concurrently either.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path | None) -> threading.RLock:
    key = str(path) if path is not None else "<memory>"
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@dataclass
class VelocityCap:
    """Per-payee cap: at most `max_payments` totalling `max_amount_atomic`
    per `window_seconds` (sliding)."""
    max_payments: int = 5
    max_amount_atomic: int = 1_000_000   # $1.00 of USDC by default
    window_seconds: float = 60.0
    _events: dict[str, list] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check_and_record(self, pay_to: str, amount_atomic: int) -> None:
        now = time.time()
        key = pay_to.lower()
        with self._lock:
            events = [(t, a) for (t, a) in self._events.get(key, [])
                      if now - t < self.window_seconds]
            if len(events) >= self.max_payments:
                raise VelocityCapExceeded(
                    f"payee {pay_to}: {len(events)} payments in window "
                    f"(cap {self.max_payments})")
            if sum(a for _, a in events) + amount_atomic > self.max_amount_atomic:
                raise VelocityCapExceeded(
                    f"payee {pay_to}: window amount cap "
                    f"{self.max_amount_atomic} would be exceeded")
            events.append((now, amount_atomic))
            self._events[key] = events


class VerifiedBuyer:
    """Pays sincor-x402 / x402 endpoints. See module docstring."""

    def __init__(
        self,
        wallet_address: str,
        signer_fn: SignerFn,
        *,
        max_payment_usd: float = 1.00,
        velocity_cap: VelocityCap | None = None,
        transport: TransportFn | None = None,
        journal_path: str | Path | None = None,
    ) -> None:
        if not wallet_address.startswith("0x"):
            raise ValueError("wallet_address must be a 0x address")
        self.wallet_address = wallet_address
        self._signer_fn = signer_fn
        self.max_payment_usd = max_payment_usd
        self.velocity_cap = velocity_cap or VelocityCap()
        self._transport = transport or _urllib_transport
        # Durable journal (W-147). ``journal_path=None`` selects the
        # sensible default (see default_journal_path()); pass a path to
        # isolate a journal, or ":memory:" for a volatile in-memory
        # journal (tests / ephemeral callers that accept restart re-pay
        # risk — never the default).
        if journal_path is None:
            self.journal_path: Path | None = default_journal_path()
        elif str(journal_path) == ":memory:":
            self.journal_path = None
        else:
            self.journal_path = Path(journal_path).expanduser()
        self._journal: dict[str, _JournalEntry] = {}
        # Re-entrant + shared per journal path: fetch() holds this lock
        # across check-claim-sign-send, and the helpers it calls re-enter.
        self._lock = _lock_for_path(self.journal_path)
        self.signatures_created = 0   # observability: tests assert == 1 in storms
        with self._lock:
            self._load_journal()

    # -- durable journal (W-147) ------------------------------------------

    def _load_journal(self) -> None:
        """(Re)read the journal file into memory. Caller holds the lock.

        A corrupt journal fails closed: overwriting unreadable payment
        history could silently re-enable a second payment, so a journal
        that cannot be parsed raises instead of being discarded.
        """
        if self.journal_path is None or not self.journal_path.exists():
            return
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
            entries = raw["entries"]
            loaded: dict[str, _JournalEntry] = {}
            for instruction_id, data in entries.items():
                loaded[instruction_id] = _JournalEntry(
                    fingerprint=str(data["fingerprint"]),
                    payment_payload=data.get("payment_payload"),
                    outcome=data.get("outcome"),
                    ambiguous=bool(data.get("ambiguous", False)),
                    claimed=bool(data.get("claimed", False)))
        except Exception as exc:
            raise SincorX402Error(
                f"buyer journal at {self.journal_path} is unreadable; "
                "refusing to pay with no idempotency history") from exc
        self._journal = loaded

    def _persist_journal(self) -> None:
        """Atomically write the journal. Caller holds the lock.

        Write-to-temp + os.replace in the same directory: a crash leaves
        either the old or the new journal, never a torn one.
        """
        if self.journal_path is None:
            return
        data = {"version": 1, "entries": {
            instruction_id: {
                "fingerprint": entry.fingerprint,
                "payment_payload": entry.payment_payload,
                "outcome": entry.outcome,
                "ambiguous": entry.ambiguous,
                "claimed": entry.claimed,
            } for instruction_id, entry in self._journal.items()}}
        path = self.journal_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        # No sort_keys: the signed payload is stored and resent VERBATIM,
        # so its key order must survive the round-trip byte-for-byte.
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
            fh.flush()
            # fsync before replace: os.replace alone survives a process
            # crash but not OS crash / power loss — a persisted claim
            # must be durable before the buyer acts on it.
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # fsync the directory so the rename itself is durable too.
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    @contextmanager
    def _journal_guard(self):
        """Serialize check-claim-sign-send for this journal (W-147).

        Holds the per-path lock for the whole fetch, and — for a
        file-backed journal — an OS file lock as well, so the atomicity
        also holds between processes sharing the journal file. The file
        lock is best-effort (POSIX ``flock``); where it is unavailable
        the persisted claim-then-sign ordering still fails closed after
        a crash.
        """
        with self._lock:
            fh = None
            try:
                if self.journal_path is not None:
                    try:
                        import fcntl
                        lock_path = self.journal_path.with_name(
                            self.journal_path.name + ".lock")
                        lock_path.parent.mkdir(parents=True, exist_ok=True)
                        fh = open(lock_path, "a+")  # noqa: SIM115
                        fcntl.flock(fh, fcntl.LOCK_EX)
                    except (ImportError, OSError):
                        if fh is not None:
                            fh.close()
                        fh = None
                yield
            finally:
                if fh is not None:
                    try:
                        import fcntl
                        fcntl.flock(fh, fcntl.LOCK_UN)
                    except (ImportError, OSError):
                        pass
                    fh.close()

    # -- challenge handling -------------------------------------------------

    @staticmethod
    def _select_requirement(body: dict[str, Any]) -> dict[str, Any]:
        accepts = body.get("accepts")
        if isinstance(accepts, list) and accepts:
            for entry in accepts:
                if (str(entry.get("asset", "")).lower()
                        == USDC_BASE_ADDRESS.lower()
                        and str(entry.get("network", "")) in ("base", "eip155:8453")):
                    return entry
            raise UnsupportedPayment("no supported accepts[] entry (USDC/Base)")
        if isinstance(body.get("payment"), dict):   # platform seller shape
            return body["payment"]
        raise MalformedChallenge("402 body has no accepts[]")

    def _build_signed_payload(self, requirement: dict[str, Any]) -> dict[str, Any]:
        try:
            amount_atomic = int(requirement.get("maxAmountRequired"))
        except (TypeError, ValueError) as exc:
            raise MalformedChallenge("accepts entry missing maxAmountRequired") from exc
        amount_usd = amount_atomic / 1_000_000
        if amount_usd > self.max_payment_usd:
            raise PaymentCapExceeded(
                f"402 demands ${amount_usd:.4f} > cap ${self.max_payment_usd:.2f}")
        pay_to = str(requirement.get("payTo") or "")
        if not pay_to:
            raise MalformedChallenge("accepts entry missing payTo")
        # Velocity gate BEFORE signing: a capped attempt is not a payment.
        self.velocity_cap.check_and_record(pay_to, amount_atomic)
        now = int(time.time())
        authorization = {
            "from": self.wallet_address,
            "to": pay_to,
            "value": amount_atomic,
            "validAfter": 0,
            "validBefore": now + 600,
            "nonce": "0x" + secrets.token_hex(32),
        }
        typed_data = {
            "types": {"TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"}]},
            "domain": {"name": "USD Coin", "version": "2", "chainId": 8453,
                       "verifyingContract": USDC_BASE_ADDRESS},
            "primaryType": "TransferWithAuthorization",
            "message": authorization,
        }
        signature = self._signer_fn(typed_data)
        if not signature or not str(signature).startswith("0x"):
            raise SincorX402Error("signer_fn did not return a 0x signature")
        with self._lock:
            self.signatures_created += 1
        return {
            "x402Version": 1,
            "scheme": str(requirement.get("scheme", "exact")),
            "network": str(requirement.get("network", "base")),
            # Echo the accepted terms EXACTLY as the server offered them —
            # the server hedge compares this claim against its own copy.
            "accepted": requirement,
            "payload": {"signature": str(signature),
                        "authorization": authorization},
        }

    # -- main entry -----------------------------------------------------------

    @staticmethod
    def _fingerprint(url: str, method: str, body: Optional[dict]) -> str:
        return hashlib.sha256(canonical_json(
            {"url": url, "method": method.upper(), "body": body})).hexdigest()

    def fetch(self, url: str, *, instruction_id: str, method: str = "POST",
              body: Optional[dict] = None) -> dict[str, Any]:
        """Pay-and-fetch one instruction. Safe to call any number of times
        with the same instruction_id: at most one payment is ever created.
        """
        if not instruction_id:
            raise ValueError("instruction_id is required (idempotency scope)")
        fingerprint = self._fingerprint(url, method, body)
        # W-147: the journal guard is held across the ENTIRE
        # check-claim-sign-send sequence — there is no window in which a
        # second caller (thread, instance, or process sharing this
        # journal) can observe "no payment yet" and sign its own.
        with self._journal_guard():
            # Pick up claims/outcomes persisted by other instances or
            # processes since this instance was constructed.
            self._load_journal()
            entry = self._journal.get(instruction_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    raise IdempotencyConflict(
                        f"instruction {instruction_id!r} reused with a "
                        "different request")
                if entry.outcome is not None:
                    return entry.outcome
                if entry.ambiguous:
                    raise AmbiguousPayment(
                        f"instruction {instruction_id!r} already paid with "
                        "an ambiguous outcome; refusing to pay again")
                if entry.payment_payload is None:
                    # Claimed but no payload was ever persisted: a prior
                    # attempt died between claim and signing. Whether a
                    # signature escaped is unknowable — fail closed.
                    raise AmbiguousPayment(
                        f"instruction {instruction_id!r} was claimed by an "
                        "earlier attempt that never recorded a payment; "
                        "refusing to sign a second one")
                # A signed payload exists but no outcome (transport died
                # mid-flight): fall through and RESEND THE SAME PAYLOAD.
                signed_payload = entry.payment_payload
            else:
                signed_payload = None
                # Claim-then-sign: the claim is persisted BEFORE any
                # transport or signing, so a crash/restart after this
                # point can never lead a fresh instance to sign anew for
                # this instruction (see the claimed-with-no-payload case
                # above).
                self._journal[instruction_id] = _JournalEntry(
                    fingerprint, claimed=True)
                self._persist_journal()

            headers = {"Idempotency-Key": instruction_id}
            if signed_payload is None:
                try:
                    status, _, raw = self._transport(method, url, body,
                                                     headers)
                    if status == 200:
                        outcome = {"ok": True, "paid": False,
                                   "data": json.loads(raw or b"{}")}
                        self._journal[instruction_id].outcome = outcome
                        self._persist_journal()
                        return outcome
                    if status != 402:
                        raise SincorX402Error(
                            f"unexpected status {status} unpaid")
                    challenge = json.loads(raw or b"{}")
                    requirement = self._select_requirement(challenge)
                    signed_payload = self._build_signed_payload(requirement)
                except Exception:
                    # Everything above happens BEFORE a signed payload
                    # exists, so no payment can have been created (cap /
                    # malformed challenge / signer failure / unpaid
                    # transport error): release the claim so the
                    # instruction is not locked by an attempt that
                    # provably never paid, and let the caller retry.
                    self._journal.pop(instruction_id, None)
                    self._persist_journal()
                    raise
                # The payload is durable BEFORE it is ever sent: a crash
                # after this point retries by resending this exact
                # payload (same nonce), never by signing a new one.
                self._journal[instruction_id].payment_payload = signed_payload
                self._persist_journal()

            payment_header = json.dumps(signed_payload)
            status2, _, raw2 = self._transport(
                method, url, body,
                {**headers, "X-PAYMENT": payment_header,
                 "PAYMENT-SIGNATURE": payment_header})
            if status2 == 200:
                outcome = {"ok": True, "paid": True,
                           "data": json.loads(raw2 or b"{}")}
                self._journal[instruction_id].outcome = outcome
                self._persist_journal()
                return outcome
            # Anything else is ambiguous for payment purposes: the payment
            # may have settled. Lock the instruction — durably; NEVER sign
            # a second one.
            self._journal[instruction_id].ambiguous = True
            self._persist_journal()
            raise AmbiguousPayment(
                f"paid retry returned {status2}; instruction locked, "
                "no re-pay")
