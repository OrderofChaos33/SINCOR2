"""Cross-chain / L2 execution envelopes (CNP-5).

An execution envelope is a chain-agnostic carrier for a Contract Net Protocol
work order that must be executed on a *different* chain than the one where it
was authorized. The envelope binds, in one EIP-712 signature:

- ``payload_hash`` — keccak256 of the canonical payload bytes
- ``origin_chain_id`` — the chain where the envelope was signed/authorized
- ``dest_chain_id`` — the chain where the envelope may be executed
- ``signer`` — the agent address that authorized the envelope
- ``nonce`` — a per-signer/per-destination-chain unique value
- ``deadline`` — unix timestamp after which the envelope expires

Replay safety across chains comes from the EIP-712 domain separator: the
domain's ``chainId`` is the *destination* chain, so an envelope signed for
chain A cannot be verified or executed on chain B. The nonce additionally
prevents replay within a chain (``EnvelopeValidator`` keeps a consumed-nonce
ledger per ``(dest_chain_id, signer)``).

This module is **offchain only**: it signs, verifies, and validates envelopes.
It defines the relayer interface (``RelayerAdapter``) but performs NO live
bridging, no RPC calls, and no fund movement. Deployment/bridging of any
kind is founder-gated and out of scope.

Security properties (adversarial self-review, 2026-10-02):
- R-1 cross-chain replay: rejected twice — explicitly (``dest_chain_id``
  must equal the validator's local chain id) and implicitly (domain
  separator binds the destination chain, so recovery on the wrong chain
  yields a different signer).
- R-2 chainId confusion: ``verify``/``validate`` require an explicit
  ``local_chain_id``; a value of 0 or a mismatch is a hard error, never a
  warning (mirrors W-3 in ``marketplace/contract_net/eip712.py``).
- R-3 nonce reuse: ``EnvelopeValidator.mark_consumed``/``validate`` reject
  any already-consumed nonce for the same signer on the same chain.
- R-4 signature malleability: signatures must be canonical low-S
  (``s <= secp256k1_n // 2``); eth_account's recovery is wrapped with an
  explicit check so an (r, n-s, v^1) sibling can never verify.
- R-5 payload substitution: the payload is bound only through its
  canonical hash; any mutation changes ``payload_hash`` and the signature
  no longer verifies. Payload canonicalization is key-sorted JSON so
  signer and verifier always hash identical bytes.
- R-6 key confusion: only secp256k1/EIP-712 signatures are accepted. There
  is NO HMAC fallback on this path (unlike the auction demo path) — a
  non-secp256k1 signature is rejected outright.
- R-7 origin forgery: ``origin_chain_id`` is inside the signed struct, so a
  relayer cannot re-stamp an envelope as originating elsewhere.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENVELOPE_DOMAIN_NAME = "SINCOR XChainEnvelope"
ENVELOPE_DOMAIN_VERSION = "1"

# Canonical verifying contract for the envelope registry. Offchain sentinel;
# the onchain registry (if/when the founder authorizes one) must deploy to
# this address for signatures to keep verifying.
DEFAULT_VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000000"

ENVELOPE_TYPE = (
    "Envelope(bytes32 payloadHash,uint256 originChainId,uint256 destChainId,"
    "address signer,uint256 nonce,uint256 deadline)"
)

# secp256k1 group order; canonical (low-S) signatures require s <= n // 2.
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

MAX_UINT256 = (1 << 256) - 1

# Well-known chains (informational only — validation accepts any uint256).
KNOWN_CHAINS: Dict[str, int] = {
    "ethereum": 1,
    "sepolia": 11155111,
    "base": 8453,
    "base_sepolia": 84532,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
}


class EnvelopeError(ValueError):
    """Base error for envelope schema / signature failures."""


class ChainIdMismatchError(EnvelopeError):
    """The envelope's destination chain is not the chain we are on (R-2)."""


class NonceReuseError(EnvelopeError):
    """A nonce already consumed for this signer on this chain (R-3)."""


class ExpiredEnvelopeError(EnvelopeError):
    """The envelope's deadline has passed."""


class InvalidSignatureError(EnvelopeError):
    """Signature is malformed, non-canonical, or recovers to the wrong signer."""


# ---------------------------------------------------------------------------
# Canonical payload hashing
# ---------------------------------------------------------------------------


def _keccak256(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
    except Exception as exc:  # pragma: no cover - eth-hash is a hard dep
        raise EnvelopeError("eth-hash is required for envelope hashing") from exc
    return keccak(data)


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """Canonical JSON encoding of the payload: sorted keys, no whitespace.

    Signer and verifier must hash identical bytes; key-sorted compact JSON
    makes dict ordering irrelevant.
    """
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise EnvelopeError(f"payload is not JSON-serializable: {exc}") from exc
    return text.encode("utf-8")


def payload_hash(payload: Mapping[str, Any]) -> str:
    """keccak256 of the canonical payload bytes, 0x-prefixed hex."""
    return "0x" + _keccak256(canonical_payload_bytes(payload)).hex()


# ---------------------------------------------------------------------------
# Envelope schema
# ---------------------------------------------------------------------------


def _check_uint256(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvelopeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 0 or value > MAX_UINT256:
        raise EnvelopeError(f"{name} out of uint256 range")
    return value


def _check_address(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise EnvelopeError(f"{name} must be a 0x address string")
    raw = value.strip()
    if raw[:2].lower() != "0x" or len(raw) != 42:
        raise EnvelopeError(f"{name} must be a 20-byte 0x address, got {value!r}")
    try:
        bytes.fromhex(raw[2:])
    except ValueError as exc:
        raise EnvelopeError(f"{name} is not valid hex: {value!r}") from exc
    return raw


@dataclass(frozen=True)
class XChainEnvelope:
    """A signed cross-chain execution envelope.

    ``payload`` is the work order (arbitrary JSON object); it is bound to the
    signature through ``payload_hash``. Sign over the envelope with
    :func:`sign_envelope`; verify with :func:`verify_envelope`.
    """

    payload: Dict[str, Any] = field(repr=False)
    origin_chain_id: int
    dest_chain_id: int
    signer: str
    nonce: int
    deadline: int
    payload_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise EnvelopeError("payload must be a JSON object (dict)")
        origin = _check_uint256("origin_chain_id", self.origin_chain_id)
        dest = _check_uint256("dest_chain_id", self.dest_chain_id)
        if origin == 0:
            raise EnvelopeError("origin_chain_id must be nonzero")
        if dest == 0:
            raise EnvelopeError("dest_chain_id must be nonzero")
        _check_address("signer", self.signer)
        _check_uint256("nonce", self.nonce)
        _check_uint256("deadline", self.deadline)
        digest = payload_hash(self.payload)
        if self.payload_digest and self.payload_digest.lower() != digest.lower():
            raise EnvelopeError("payload_digest does not match payload")
        object.__setattr__(self, "payload_digest", digest)

    @property
    def envelope_id(self) -> str:
        """Unique id for replay tracking: hash of the signed fields."""
        return "0x" + _keccak256(
            b"|".join(
                [
                    self.payload_digest.encode("ascii"),
                    str(self.origin_chain_id).encode("ascii"),
                    str(self.dest_chain_id).encode("ascii"),
                    self.signer.lower().encode("ascii"),
                    str(self.nonce).encode("ascii"),
                    str(self.deadline).encode("ascii"),
                ]
            )
        ).hex()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload": self.payload,
            "payload_digest": self.payload_digest,
            "origin_chain_id": self.origin_chain_id,
            "dest_chain_id": self.dest_chain_id,
            "signer": self.signer,
            "nonce": self.nonce,
            "deadline": self.deadline,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "XChainEnvelope":
        try:
            return cls(
                payload=dict(data["payload"]),
                origin_chain_id=data["origin_chain_id"],
                dest_chain_id=data["dest_chain_id"],
                signer=data["signer"],
                nonce=data["nonce"],
                deadline=data["deadline"],
                payload_digest=str(data.get("payload_digest", "")),
            )
        except KeyError as exc:
            raise EnvelopeError(f"envelope missing required field: {exc}") from exc


# ---------------------------------------------------------------------------
# EIP-712 typed data
# ---------------------------------------------------------------------------


def typed_data(
    envelope: XChainEnvelope,
    *,
    verifying_contract: str = DEFAULT_VERIFYING_CONTRACT,
) -> Dict[str, Any]:
    """Build the EIP-712 typed-data dict for an envelope.

    The domain's ``chainId`` is the envelope's *destination* chain — this is
    what makes the signature replay-safe across chains (R-1).
    """
    vc = _check_address("verifying_contract", verifying_contract)
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Envelope": [
                {"name": "payloadHash", "type": "bytes32"},
                {"name": "originChainId", "type": "uint256"},
                {"name": "destChainId", "type": "uint256"},
                {"name": "signer", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "Envelope",
        "domain": {
            "name": ENVELOPE_DOMAIN_NAME,
            "version": ENVELOPE_DOMAIN_VERSION,
            "chainId": envelope.dest_chain_id,
            "verifyingContract": vc,
        },
        "message": {
            "payloadHash": envelope.payload_digest,
            "originChainId": envelope.origin_chain_id,
            "destChainId": envelope.dest_chain_id,
            "signer": envelope.signer,
            "nonce": envelope.nonce,
            "deadline": envelope.deadline,
        },
    }


def sign_envelope(
    envelope: XChainEnvelope,
    private_key: str,
    *,
    verifying_contract: str = DEFAULT_VERIFYING_CONTRACT,
) -> str:
    """Sign an envelope with a secp256k1 private key. Returns 0x signature."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except Exception as exc:
        raise EnvelopeError("eth-account is required for signing") from exc
    if not private_key:
        raise EnvelopeError("private_key is required")
    message = encode_typed_data(full_message=typed_data(envelope, verifying_contract=verifying_contract))
    signed = Account.sign_message(message, private_key=private_key)
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _reject_malleable(signature_hex: str) -> Tuple[int, int, int]:
    """Parse a 65-byte signature and reject non-canonical (high-S) forms (R-4)."""
    if not isinstance(signature_hex, str):
        raise InvalidSignatureError("signature must be a hex string")
    raw = signature_hex.strip()
    if raw[:2].lower() == "0x":
        raw = raw[2:]
    try:
        body = bytes.fromhex(raw)
    except ValueError as exc:
        raise InvalidSignatureError("signature is not valid hex") from exc
    if len(body) != 65:
        raise InvalidSignatureError(f"signature must be 65 bytes, got {len(body)}")
    r = int.from_bytes(body[0:32], "big")
    s = int.from_bytes(body[32:64], "big")
    v = body[64]
    if not (1 <= r < SECP256K1_N):
        raise InvalidSignatureError("signature r out of range")
    if not (1 <= s <= SECP256K1_N // 2):
        # High-S (malleated sibling of a valid signature) is rejected.
        raise InvalidSignatureError("non-canonical (high-S) signature rejected")
    if v not in (27, 28):
        raise InvalidSignatureError(f"invalid recovery id v={v}")
    return r, s, v


def verify_envelope(
    envelope: XChainEnvelope,
    signature: str,
    *,
    local_chain_id: int,
    verifying_contract: str = DEFAULT_VERIFYING_CONTRACT,
) -> str:
    """Verify an envelope signature against a local chain.

    - ``local_chain_id`` is REQUIRED (fail closed); if it does not equal the
      envelope's ``dest_chain_id`` the envelope is rejected (R-1, R-2).
    - Returns the recovered signer address on success. Raises
      :class:`InvalidSignatureError` / :class:`ChainIdMismatchError`.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except Exception as exc:
        raise EnvelopeError("eth-account is required for verification") from exc
    local = _check_uint256("local_chain_id", local_chain_id)
    if local == 0:
        raise ChainIdMismatchError("local_chain_id must be nonzero (fail closed)")
    if local != envelope.dest_chain_id:
        raise ChainIdMismatchError(
            f"envelope dest_chain_id={envelope.dest_chain_id} != local chain "
            f"{local}: cross-chain replay rejected"
        )
    _reject_malleable(signature)  # R-4: hard reject before recovery
    message = encode_typed_data(
        full_message=typed_data(envelope, verifying_contract=verifying_contract)
    )
    try:
        recovered = Account.recover_message(message, signature=signature)
    except Exception as exc:
        raise InvalidSignatureError(f"signature recovery failed: {exc}") from exc
    if recovered.lower() != envelope.signer.lower():
        raise InvalidSignatureError(
            f"recovered signer {recovered} != envelope signer {envelope.signer}"
        )
    return recovered


# ---------------------------------------------------------------------------
# Validator (nonce ledger + expiry + chain binding)
# ---------------------------------------------------------------------------


class EnvelopeValidator:
    """Stateful validator for a single destination chain.

    Keeps a consumed-nonce ledger ``(signer, nonce)`` and a consumed
    envelope-id set so no envelope can execute twice (R-3). In production
    the ledger must be durable (DB); this in-memory implementation is for
    validation and tests.
    """

    def __init__(
        self,
        local_chain_id: int,
        *,
        verifying_contract: str = DEFAULT_VERIFYING_CONTRACT,
        clock: Optional[Any] = None,
    ) -> None:
        if not isinstance(local_chain_id, int) or isinstance(local_chain_id, bool) or local_chain_id <= 0:
            raise ChainIdMismatchError("validator requires an explicit nonzero local_chain_id")
        self.local_chain_id = local_chain_id
        self.verifying_contract = _check_address("verifying_contract", verifying_contract)
        self._clock = clock or time.time
        self._consumed_nonces: Set[Tuple[str, int]] = set()
        self._consumed_envelope_ids: Set[str] = set()

    # -- ledger ---------------------------------------------------------
    def is_nonce_consumed(self, signer: str, nonce: int) -> bool:
        return (signer.lower(), int(nonce)) in self._consumed_nonces

    def mark_consumed(self, envelope: XChainEnvelope) -> None:
        key = (envelope.signer.lower(), envelope.nonce)
        if key in self._consumed_nonces or envelope.envelope_id in self._consumed_envelope_ids:
            raise NonceReuseError(
                f"nonce {envelope.nonce} already consumed for {envelope.signer}"
            )
        self._consumed_nonces.add(key)
        self._consumed_envelope_ids.add(envelope.envelope_id)

    # -- validation -----------------------------------------------------
    def validate(
        self,
        envelope: XChainEnvelope,
        signature: str,
        *,
        mark_consumed: bool = True,
    ) -> str:
        """Full validation: chain binding, signature, expiry, nonce freshness.

        Returns the recovered signer address. With ``mark_consumed=True``
        (default) the nonce is atomically consumed, so the same call path
        that executes an envelope also prevents its replay.
        """
        now = int(self._clock())
        if envelope.deadline <= now:
            raise ExpiredEnvelopeError(
                f"envelope expired at {envelope.deadline} (now {now})"
            )
        recovered = verify_envelope(
            envelope,
            signature,
            local_chain_id=self.local_chain_id,
            verifying_contract=self.verifying_contract,
        )
        key = (envelope.signer.lower(), envelope.nonce)
        if key in self._consumed_nonces or envelope.envelope_id in self._consumed_envelope_ids:
            raise NonceReuseError(
                f"nonce {envelope.nonce} already consumed for {envelope.signer}"
            )
        if mark_consumed:
            self.mark_consumed(envelope)
        return recovered

    def consumed_count(self) -> int:
        return len(self._consumed_nonces)


# ---------------------------------------------------------------------------
# Relayer interface (documented; no live bridging)
# ---------------------------------------------------------------------------


class RelayerAdapter:
    """Interface a bridge relayer implements to ferry envelopes cross-chain.

    This class documents the contract; it performs no network I/O and moves
    no funds. A concrete relayer (out of scope for CNP-5) would:

    1. ``submit(envelope_dict, signature)`` — called on the origin side by the
       agent that signed the envelope. The relayer transports the bytes to the
       destination chain's validator endpoint.
    2. ``deliver(envelope_dict, signature)`` — called on the destination side;
       it MUST run :class:`EnvelopeValidator` ``validate()`` (chain binding +
       signature + expiry + nonce) before executing ``payload``.
    3. ``status(envelope_id)`` — returns ``pending`` / ``delivered`` /
       ``rejected`` / ``expired`` for observability.

    The default methods below raise ``NotImplementedError`` so a half-wired
    relayer fails loudly instead of silently dropping envelopes.
    """

    def submit(self, envelope: Dict[str, Any], signature: str) -> str:
        """Accept a signed envelope for transport. Returns the envelope id."""
        raise NotImplementedError("submit() must be implemented by a concrete relayer")

    def deliver(self, envelope: Dict[str, Any], signature: str) -> Dict[str, Any]:
        """Validate and execute a delivered envelope. Returns a receipt dict."""
        raise NotImplementedError("deliver() must be implemented by a concrete relayer")

    def status(self, envelope_id: str) -> str:
        """Return the transport status of an envelope id."""
        raise NotImplementedError("status() must be implemented by a concrete relayer")


def validate_relayer_payload(
    envelope_dict: Mapping[str, Any], signature: Any
) -> Tuple[XChainEnvelope, str]:
    """Schema-check relayer inputs before any crypto (fail fast on garbage).

    Returns ``(envelope, signature)`` or raises :class:`EnvelopeError`.
    """
    if not isinstance(envelope_dict, Mapping):
        raise EnvelopeError("envelope must be a JSON object")
    if not isinstance(signature, str) or not signature.strip():
        raise EnvelopeError("signature must be a non-empty hex string")
    return XChainEnvelope.from_dict(envelope_dict), signature.strip()
