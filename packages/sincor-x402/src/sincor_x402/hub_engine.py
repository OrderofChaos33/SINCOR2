"""Hub settlement engine — client for the SincorSettlementHub interface.

This module targets the ``SincorSettlementHub`` contract interface from the
founder's 2026-11-11 spec drop (vendored reference:
``goals/agent-infra-partnership-outreach/files/spec-drop-2026-11-11/``):

* Buyer side: sign an EIP-712 **SEAL** envelope — ``SealAuthorization``
  under domain ``{name: "SINCOR_SEAL", version: "1", chainId,
  verifyingContract}`` — and encode it as a base64 **X-PAYMENT** header
  with scheme ``"sincor-seal-v1"``. The seal nonce is read from the Hub's
  ``nonces(buyer)``.
* Settler side: ``verify_and_settle`` submits
  ``verifyAndSettleSeal(seal, sigilCommitment, v, r, s)`` through an
  injected relayer signer. Keys never enter or are stored by this package:
  both the buyer SEAL signer and the relayer transaction signer are
  injected callables, exactly like ``VerifiedBuyer``'s signer.

Trust model — read this before believing the word "verified"
----------------------------------------------------------------
**The Hub does not verify work output.** It records whatever
``sigilCommitment`` the settlement submitter hands it, and it never checks
the delivered output against the seal's ``expectedSchemaHash`` on-chain
(the hash is signed, but unenforced). ``isTaskVerified(taskId, sigil)``
on-chain therefore only proves "someone settled this task with this
commitment recorded" — nothing more.

The enforcement layer is THIS engine, off-chain, before submission:
``verify_and_settle`` recomputes the SIGIL commitment from the delivered
result, requires the seal's ``expectedSchemaHash`` to equal the committed
spec hash, and runs the package's ``Verifier`` seam against the committed
spec. **If verification fails, the engine refuses to submit — the Hub is
never called.** A deployment that lets arbitrary callers submit settlements
directly to the Hub bypasses that enforcement entirely; the Hub alone is
not a verification system.

SIGIL canon on the Hub path: ``keccak256(taskId ‖ canonical-JSON output)``
where ``taskId`` is the raw 32-byte task id and the JSON is this package's
canonical form (sorted keys, compact separators). The spec drafts disagreed
(sha256 vs keccak256); keccak256 is canonical here because it is the EVM's
native hash and the Hub's own typehashes are keccak256.

Allowance preflight: the Hub pulls funds with ERC-20 ``transferFrom`` —
**the SEAL signature alone moves nothing, and nothing is escrowed at
authorization time.** Before submitting, the engine checks the buyer's
USDC allowance to the Hub covers the seal amount and raises
``AllowanceInsufficient`` naming the required ``approve`` step otherwise.

Status: the Hub contract is **NOT deployed** (deployment is a founder
gate). This engine is exercised end-to-end against a local test chain in
``tests/test_hub_engine.py`` using a vendored test copy of the Hub whose
``protocolFeeBps`` is set to the locked fee canon, 500 bps (5%) — the spec
drop's hardcoded 100 bps is a known defect (see the fixture header).

Requires the ``hub`` extra (``pip install "sincor-x402[hub]"``): web3 +
eth-account/eth-utils. The core package stays dependency-free; web3 is
imported lazily, only when an engine is constructed.
"""
from __future__ import annotations

import ast
import base64
import binascii
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .errors import (AllowanceInsufficient, MalformedChallenge,
                     SettlementReverted, VerificationFailed)
from .types import canonical_json
from .verifier import ReferenceVerifier, Verifier

SEAL_SCHEME = "sincor-seal-v1"
SEAL_DOMAIN_NAME = "SINCOR_SEAL"
SEAL_DOMAIN_VERSION = "1"
SEAL_PRIMARY_TYPE = "SealAuthorization"

SEAL_MESSAGE_TYPES: dict[str, list[dict[str, str]]] = {
    "SealAuthorization": [
        {"name": "taskId", "type": "bytes32"},
        {"name": "buyer", "type": "address"},
        {"name": "provider", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "expectedSchemaHash", "type": "bytes32"},
        {"name": "deadline", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
    ]
}

# signer callables — same injection pattern as buyer.SignerFn.
SealSignerFn = Callable[[dict[str, Any]], str]
"""Signs an EIP-712 typed-data dict, returns a 0x-prefixed 65-byte sig hex."""

TxSignerFn = Callable[[dict[str, Any]], Any]
"""Signs an unsigned transaction dict, returns raw signed tx (bytes/hex)."""

# Minimal ABIs: exactly the Hub surface this engine uses (plus its custom
# errors, which help web3 surface revert data on real providers).
HUB_ABI: list[dict[str, Any]] = [
    {"inputs": [{"internalType": "address", "name": "", "type": "address"}],
     "name": "nonces", "outputs": [{"internalType": "uint256", "name": "",
     "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "usdc",
     "outputs": [{"internalType": "contract IERC20", "name": "",
     "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "feeTreasury",
     "outputs": [{"internalType": "address", "name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "protocolFeeBps",
     "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "taskId", "type": "bytes32"},
                {"internalType": "bytes32", "name": "expectedSigil",
                 "type": "bytes32"}],
     "name": "isTaskVerified",
     "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [
        {"components": [
            {"internalType": "bytes32", "name": "taskId", "type": "bytes32"},
            {"internalType": "address", "name": "buyer", "type": "address"},
            {"internalType": "address", "name": "provider", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "bytes32", "name": "expectedSchemaHash",
             "type": "bytes32"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            {"internalType": "uint256", "name": "nonce", "type": "uint256"}],
         "internalType": "struct SincorSettlementHub.SealAuthorization",
         "name": "seal", "type": "tuple"},
        {"internalType": "bytes32", "name": "sigilCommitment",
         "type": "bytes32"},
        {"internalType": "uint8", "name": "v", "type": "uint8"},
        {"internalType": "bytes32", "name": "r", "type": "bytes32"},
        {"internalType": "bytes32", "name": "s", "type": "bytes32"}],
     "name": "verifyAndSettleSeal",
     "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"anonymous": False,
     "inputs": [
         {"indexed": True, "internalType": "bytes32", "name": "taskId",
          "type": "bytes32"},
         {"indexed": True, "internalType": "address", "name": "buyer",
          "type": "address"},
         {"indexed": True, "internalType": "address", "name": "provider",
          "type": "address"},
         {"indexed": False, "internalType": "uint256", "name": "netPayout",
          "type": "uint256"},
         {"indexed": False, "internalType": "uint256", "name": "protocolFee",
          "type": "uint256"},
         {"indexed": False, "internalType": "bytes32",
          "name": "sigilCommitment", "type": "bytes32"}],
     "name": "SealSettled", "type": "event"},
    {"inputs": [], "name": "SealExpired", "type": "error"},
    {"inputs": [], "name": "InvalidNonce", "type": "error"},
    {"inputs": [], "name": "InvalidSealSignature", "type": "error"},
    {"inputs": [], "name": "SigilCommitmentZero", "type": "error"},
    {"inputs": [], "name": "UsdcTransferFailed", "type": "error"},
]

ERC20_ABI: list[dict[str, Any]] = [
    {"inputs": [{"internalType": "address", "name": "owner", "type": "address"},
                {"internalType": "address", "name": "spender",
                 "type": "address"}],
     "name": "allowance",
     "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "account",
                 "type": "address"}],
     "name": "balanceOf",
     "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]

_HUB_ERROR_NAMES = ("SealExpired", "InvalidNonce", "InvalidSealSignature",
                    "SigilCommitmentZero", "UsdcTransferFailed")


def _keccak256(data: bytes) -> bytes:
    """keccak256 (NOT sha3-256) via eth_utils, falling back to pycryptodome."""
    try:
        from eth_utils import keccak
        return keccak(data)
    except ImportError:
        from Crypto.Hash import keccak as _crypto_keccak
        h = _crypto_keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()


def _checksum(address: str) -> str:
    try:
        from eth_utils import to_checksum_address
        return to_checksum_address(address)
    except ImportError:
        return address


def to_bytes32(value: Any) -> bytes:
    """Normalize a task id / hash to exactly 32 bytes.

    Accepts: 32 raw bytes; a ``0x``-prefixed 64-hex string; a bare 64-hex
    string (treated as a raw digest — this is how the package's sha256
    ``spec_hash`` hex maps onto the seal's ``expectedSchemaHash``); any
    other string is hashed with keccak256 of its UTF-8 bytes.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) != 32:
            raise ValueError(f"bytes value must be exactly 32 bytes, got {len(raw)}")
        return raw
    if isinstance(value, str):
        text = value[2:] if value.startswith(("0x", "0X")) else value
        if len(text) == 64:
            try:
                return bytes.fromhex(text)
            except ValueError:
                pass
        return _keccak256(value.encode("utf-8"))
    raise TypeError(f"cannot convert {type(value).__name__} to bytes32")


def compute_sigil_commitment(task_id: Any, result: Any) -> str:
    """SIGIL commitment, Hub-path canon: keccak256(taskId ‖ canonical JSON).

    ``taskId`` is the raw 32-byte task id (see ``to_bytes32``); the output
    is serialized with this package's canonical JSON (sorted keys, compact
    separators). Returns a 0x-prefixed hex string.
    """
    return "0x" + _keccak256(to_bytes32(task_id) + canonical_json(result)).hex()


def _decode_hub_error(exc: BaseException) -> Optional[str]:
    """Best-effort decode of a Hub custom error from a revert exception.

    Providers surface revert data differently (eth-tester embeds the raw
    4-byte selector in a ``b'...'`` repr, RPC providers return 0x-hex), so
    several shapes are probed against the Hub's known error selectors.
    """
    blobs: list[bytes] = []
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, (bytes, bytearray)):
            blobs.append(bytes(arg))
    data = getattr(exc, "data", None)
    if isinstance(data, (bytes, bytearray)):
        blobs.append(bytes(data))
    text = str(exc)
    for m in re.finditer(r"0x[0-9a-fA-F]{8,}", text):
        try:
            blobs.append(bytes.fromhex(m.group(0)[2:]))
        except ValueError:
            pass
    m = re.search(r"b'((?:[^'\\]|\\.)*)'", text)
    if m:
        try:
            blobs.append(ast.literal_eval("b'" + m.group(1) + "'"))
        except (ValueError, SyntaxError):
            pass
    if blobs:
        for name in _HUB_ERROR_NAMES:
            selector = _keccak256(f"{name}()".encode())[:4]
            if any(selector in blob for blob in blobs):
                return name
    return None


def _split_signature(signature: Any) -> tuple[int, bytes, bytes]:
    """Split a 0x-prefixed (or bare-hex / bytes) 65-byte signature."""
    if isinstance(signature, (bytes, bytearray)):
        raw = bytes(signature)
    elif isinstance(signature, str):
        text = signature[2:] if signature.startswith(("0x", "0X")) else signature
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise MalformedChallenge("signer returned a non-hex signature") from exc
    else:
        raise MalformedChallenge("signer returned an unsupported signature type")
    if len(raw) != 65:
        raise MalformedChallenge(
            f"signature must be 65 bytes, got {len(raw)}")
    return raw[64], raw[:32], raw[32:64]


@dataclass
class SealEnvelope:
    """A parsed sincor-seal-v1 X-PAYMENT envelope (the SEAL authorization)."""
    task_id: bytes
    buyer: str
    provider: str
    amount: int
    expected_schema_hash: bytes
    deadline: int
    nonce: int
    v: int
    r: bytes
    s: bytes

    def seal_tuple(self) -> tuple:
        """The SealAuthorization struct as a positional tuple for web3."""
        return (self.task_id, _checksum(self.buyer), _checksum(self.provider),
                int(self.amount), self.expected_schema_hash,
                int(self.deadline), int(self.nonce))

    @classmethod
    def parse(cls, payment_header_b64: str) -> "SealEnvelope":
        try:
            raw = base64.b64decode(payment_header_b64, validate=True)
            envelope = json.loads(raw.decode("utf-8"))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise MalformedChallenge(
                "X-PAYMENT header is not base64-encoded JSON") from exc
        if not isinstance(envelope, dict):
            raise MalformedChallenge("X-PAYMENT envelope is not a JSON object")
        scheme = envelope.get("scheme")
        if scheme != SEAL_SCHEME:
            raise MalformedChallenge(
                f"unsupported X-PAYMENT scheme {scheme!r}; expected {SEAL_SCHEME!r}")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise MalformedChallenge("X-PAYMENT envelope has no payload object")
        try:
            return cls(
                task_id=to_bytes32(payload["taskId"]),
                buyer=str(payload["buyer"]),
                provider=str(payload["provider"]),
                amount=int(payload["amount"]),
                expected_schema_hash=to_bytes32(payload["expectedSchemaHash"]),
                deadline=int(payload["deadline"]),
                nonce=int(payload["nonce"]),
                v=int(payload["v"]),
                r=to_bytes32(payload["r"]),
                s=to_bytes32(payload["s"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedChallenge(
                f"X-PAYMENT payload is missing/invalid seal fields: {exc}") from exc


@dataclass
class HubSettlementReceipt:
    """Parsed from the Hub's SealSettled event after a successful submit."""
    tx_hash: str
    task_id: str            # 0x hex
    buyer: str
    provider: str
    amount: int             # gross seal amount (atomic units)
    net_payout: int         # provider's take, per the SealSettled event
    protocol_fee: int       # treasury's take, per the SealSettled event
    sigil_commitment: str   # 0x hex, as recorded on-chain


class HubSettlementEngine:
    """Signs SEAL envelopes and settles them through a SincorSettlementHub.

    See the module docstring for the trust model: verification is enforced
    HERE, before submission — never assumed of the Hub.
    """

    def __init__(
        self,
        *,
        hub_address: str,
        relayer_address: str,
        relayer_signer: TxSignerFn,
        verifier: Verifier | None = None,
        w3: Any = None,
        rpc_url: str | None = None,
        usdc_address: str | None = None,
        chain_id: int | None = None,
        gas: int = 400_000,
    ) -> None:
        if w3 is None:
            if not rpc_url:
                raise ValueError("pass either w3 or rpc_url")
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.w3 = w3
        self.hub_address = _checksum(hub_address)
        self.relayer_address = _checksum(relayer_address)
        self._relayer_signer = relayer_signer
        self.verifier: Verifier = verifier or ReferenceVerifier()
        self.chain_id = int(chain_id) if chain_id is not None \
            else int(w3.eth.chain_id)
        self.gas = int(gas)
        self._hub = w3.eth.contract(address=self.hub_address, abi=HUB_ABI)
        if usdc_address is None:
            usdc_address = self._hub.functions.usdc().call()
        self.usdc_address = _checksum(str(usdc_address))
        self._usdc = w3.eth.contract(address=self.usdc_address, abi=ERC20_ABI)

    # -- reads ---------------------------------------------------------------

    def current_nonce(self, buyer_address: str) -> int:
        """The buyer's next seal nonce, from the Hub's ``nonces(buyer)``."""
        return int(self._hub.functions.nonces(_checksum(buyer_address)).call())

    def is_task_verified(self, task_id: Any, sigil_commitment: Any) -> bool:
        return bool(self._hub.functions.isTaskVerified(
            to_bytes32(task_id), to_bytes32(sigil_commitment)).call())

    # -- buyer side ------------------------------------------------------------

    def build_seal_typed_data(
        self,
        *,
        buyer_address: str,
        task_id: Any,
        provider_address: str,
        amount_atomic: int,
        expected_schema_hash: Any,
        deadline: int,
        nonce: int,
    ) -> dict[str, Any]:
        """The exact EIP-712 typed-data dict the Hub's digest commits to."""
        return {
            "types": SEAL_MESSAGE_TYPES,
            "domain": {
                "name": SEAL_DOMAIN_NAME,
                "version": SEAL_DOMAIN_VERSION,
                "chainId": self.chain_id,
                "verifyingContract": self.hub_address,
            },
            "primaryType": SEAL_PRIMARY_TYPE,
            "message": {
                "taskId": to_bytes32(task_id),
                "buyer": _checksum(buyer_address),
                "provider": _checksum(provider_address),
                "amount": int(amount_atomic),
                "expectedSchemaHash": to_bytes32(expected_schema_hash),
                "deadline": int(deadline),
                "nonce": int(nonce),
            },
        }

    def create_buyer_seal_header(
        self,
        *,
        buyer_signer: SealSignerFn,
        buyer_address: str,
        task_id: Any,
        provider_address: str,
        amount_atomic: int,
        expected_schema_hash: Any,
        ttl_seconds: int = 600,
        deadline: int | None = None,
    ) -> str:
        """Sign a SEAL envelope → base64 X-PAYMENT header (scheme sincor-seal-v1).

        The nonce is read fresh from the Hub (``nonces(buyer)``); each seal
        is single-use. The buyer's key stays with the injected
        ``buyer_signer`` — this method never sees key material.
        """
        buyer = _checksum(buyer_address)
        nonce = self.current_nonce(buyer)
        if deadline is None:
            deadline = int(time.time()) + int(ttl_seconds)
        typed_data = self.build_seal_typed_data(
            buyer_address=buyer, task_id=task_id,
            provider_address=provider_address, amount_atomic=amount_atomic,
            expected_schema_hash=expected_schema_hash, deadline=deadline,
            nonce=nonce)
        signature = buyer_signer(typed_data)
        if not signature:
            raise MalformedChallenge("buyer_signer returned no signature")
        v, r, s = _split_signature(signature)
        msg = typed_data["message"]
        envelope = {
            "x402Version": 1,
            "scheme": SEAL_SCHEME,
            "network": f"eip155:{self.chain_id}",
            "payload": {
                "taskId": "0x" + msg["taskId"].hex(),
                "buyer": buyer,
                "provider": _checksum(provider_address),
                "amount": int(amount_atomic),
                "expectedSchemaHash": "0x" + msg["expectedSchemaHash"].hex(),
                "deadline": int(deadline),
                "nonce": int(nonce),
                "v": int(v),
                "r": "0x" + r.hex(),
                "s": "0x" + s.hex(),
            },
        }
        return base64.b64encode(
            json.dumps(envelope).encode("utf-8")).decode("ascii")

    # -- preflight -------------------------------------------------------------

    def check_allowance(self, buyer_address: str, amount_atomic: int) -> int:
        """Fail-closed allowance preflight. Returns the current allowance.

        The SEAL signature alone moves nothing — the Hub pulls via
        transferFrom — so a buyer who has not approved the Hub cannot
        settle. The error names the exact approval step required.
        """
        buyer = _checksum(buyer_address)
        allowance = int(self._usdc.functions.allowance(
            buyer, self.hub_address).call())
        if allowance < int(amount_atomic):
            raise AllowanceInsufficient(
                f"buyer {buyer} has approved only {allowance} atomic USDC "
                f"to SincorSettlementHub {self.hub_address}, but the seal "
                f"requires {int(amount_atomic)}. Required step: the buyer "
                f"must first call approve({self.hub_address}, >= "
                f"{int(amount_atomic)}) on the USDC contract "
                f"{self.usdc_address} (ERC-20 approval) — the SEAL "
                f"signature alone moves nothing; the Hub pulls funds via "
                f"transferFrom at settlement time.")
        return allowance

    # -- settlement ------------------------------------------------------------

    def verify_and_settle(
        self,
        *,
        payment_header_b64: str,
        spec: dict[str, Any],
        spec_hash: str,
        result: Any,
        sigil_commitment: str | None = None,
    ) -> HubSettlementReceipt:
        """Verify the delivered result off-chain, THEN settle on the Hub.

        Order of operations is the security property:

        1. Parse the SEAL envelope.
        2. Recompute the SIGIL commitment (keccak canon) from the envelope's
           taskId and the delivered result; a caller-supplied commitment
           that disagrees is refused.
        3. Require the seal's ``expectedSchemaHash`` to equal the committed
           ``spec_hash`` — the buyer signed for THIS spec, no other.
        4. Run the ``Verifier`` seam against the committed spec. **On
           failure, raise VerificationFailed and never touch the Hub** —
           this refusal is the enforcement layer, because the Hub itself
           records any commitment it is handed.
        5. Allowance preflight (``AllowanceInsufficient``).
        6. Simulate the Hub call, then submit via the injected relayer
           signer and parse the ``SealSettled`` event.
        """
        envelope = SealEnvelope.parse(payment_header_b64)

        computed_sigil = compute_sigil_commitment(envelope.task_id, result)
        if sigil_commitment is not None:
            supplied = "0x" + to_bytes32(sigil_commitment).hex()
            if supplied.lower() != computed_sigil.lower():
                raise VerificationFailed(
                    "supplied sigil_commitment does not match the "
                    "commitment recomputed from the delivered result; "
                    "refusing to submit a commitment for unverified output")
        if to_bytes32(spec_hash) != envelope.expected_schema_hash:
            raise VerificationFailed(
                "seal expectedSchemaHash does not match the committed "
                "spec_hash; the buyer did not sign for this spec")

        verdict = self.verifier.verify(
            spec=spec, spec_hash=spec_hash, result=result)
        if not verdict.passed:
            raise VerificationFailed(
                verdict.reason or "result failed verification")

        self.check_allowance(envelope.buyer, envelope.amount)

        seal = envelope.seal_tuple()
        sigil_b32 = to_bytes32(computed_sigil)
        call = self._hub.functions.verifyAndSettleSeal(
            seal, sigil_b32, envelope.v, envelope.r, envelope.s)
        # Simulate first: a reverting settlement fails here with the Hub's
        # custom error decoded, before any relayer gas is spent.
        try:
            call.call({"from": self.relayer_address})
        except Exception as exc:
            name = _decode_hub_error(exc)
            raise SettlementReverted(
                f"Hub settlement would revert"
                f"{f' ({name})' if name else ''}: {exc}") from exc

        tx = call.build_transaction({
            "from": self.relayer_address,
            "nonce": self.w3.eth.get_transaction_count(self.relayer_address),
            "chainId": self.chain_id,
            "gas": self.gas,
        })
        raw = self._relayer_signer(tx)
        if isinstance(raw, str):
            raw = bytes.fromhex(raw[2:] if raw.startswith(("0x", "0X")) else raw)
        if not raw:
            raise SettlementReverted("relayer_signer returned no signed transaction")
        try:
            tx_hash = self.w3.eth.send_raw_transaction(bytes(raw))
        except Exception as exc:
            name = _decode_hub_error(exc)
            raise SettlementReverted(
                f"Hub settlement submission failed"
                f"{f' ({name})' if name else ''}: {exc}") from exc
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if int(receipt["status"]) != 1:
            raise SettlementReverted(
                f"settlement transaction {bytes(tx_hash).hex()} reverted on-chain")
        events = self._parse_settled_events(receipt)
        if not events:
            raise SettlementReverted(
                "settlement transaction succeeded but no SealSettled event "
                "was found; treating the outcome as unproven")
        args = events[0]["args"]
        return HubSettlementReceipt(
            tx_hash="0x" + bytes(receipt["transactionHash"]).hex(),
            task_id="0x" + bytes(args["taskId"]).hex(),
            buyer=str(args["buyer"]),
            provider=str(args["provider"]),
            amount=envelope.amount,
            net_payout=int(args["netPayout"]),
            protocol_fee=int(args["protocolFee"]),
            sigil_commitment="0x" + bytes(args["sigilCommitment"]).hex(),
        )

    def _parse_settled_events(self, receipt: Any) -> list:
        try:
            from web3.logs import DISCARD
            return list(self._hub.events.SealSettled().process_receipt(
                receipt, errors=DISCARD))
        except Exception:
            return list(self._hub.events.SealSettled().process_receipt(receipt))
