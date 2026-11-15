"""Isolated signing service (enclave) for Sigil Seals and delegation certs.

CNP-1. Offchain only.

The enclave is the ONLY place that holds secp256k1 private key material for
seal/delegation signing. Its rules:

- Keys enter via :meth:`import_key` and are returned as opaque ``key_id``
  handles. There is NO API that returns, logs, or serializes raw key
  material. :meth:`destroy` drops the key (handle becomes invalid).
- Signing requests go through strict input validation. Any out-of-policy
  payload is refused with :class:`EnclavePolicyError` — never signed.
- The enclave signs with secp256k1 ONLY. There is no HMAC code path here;
  any request naming another algorithm is refused outright.
- Nonces are tracked per key: a nonce may never be reused for the same
  key (replay protection at issuance).

Policy constants:

- ``MAX_SEAL_TTL``: a seal may not live longer than 30 days.
- ``MAX_DELEGATION_TTL``: a delegation cert may not live longer than 7 days.
- Capabilities/permissions must live inside the allowed namespace
  prefixes; bounded length; no empties; no duplicates.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .eip712 import encode_address
from .sigil_seal import (
    delegation_typed_data_digest,
    seal_typed_data_digest,
    sign_seal,
    validate_did,
)
from .types import ContractNetConfig

MAX_SEAL_TTL = 30 * 24 * 3600          # 30 days
MAX_DELEGATION_TTL = 7 * 24 * 3600     # 7 days
MAX_CAPABILITY_LEN = 128

# Namespace prefixes a capability/permission must live under. A bare
# namespace (``auction``) is also allowed and covers its children.
ALLOWED_CAPABILITY_PREFIXES = ("auction:", "market:", "task:", "a2a:", "seal:")

_DID_RE = re.compile(r"^did:sincor:[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class EnclavePolicyError(ValueError):
    """Raised when a signing request violates enclave policy. Fail closed."""


class EnclaveKeyError(KeyError):
    """Raised for unknown or destroyed key handles."""


@dataclass
class _KeySlot:
    address: str
    account: Any  # eth_account LocalAccount; never exposed
    used_nonces: Set[int] = field(default_factory=set)


class SealEnclave:
    """Isolated signing service. Holds keys; refuses out-of-policy payloads."""

    def __init__(self, config: ContractNetConfig):
        self._config = config
        self._keys: Dict[str, _KeySlot] = {}

    # -- key management ------------------------------------------------------
    def import_key(self, private_key: str) -> str:
        """Import a secp256k1 private key. Returns an opaque key handle.

        The raw key is NEVER returned, logged, or serializable through this
        API.
        """
        try:
            from eth_account import Account
        except Exception as exc:  # pragma: no cover - env without eth_account
            raise EnclaveKeyError("eth_account unavailable") from exc
        if not isinstance(private_key, str) or not private_key:
            raise EnclaveKeyError("private key must be a non-empty hex string")
        try:
            account = Account.from_key(
                private_key if private_key.startswith("0x") else "0x" + private_key
            )
        except Exception as exc:
            raise EnclaveKeyError("invalid secp256k1 private key") from exc
        key_id = "key-" + secrets.token_hex(16)
        self._keys[key_id] = _KeySlot(address=account.address, account=account)
        return key_id

    def key_address(self, key_id: str) -> str:
        return self._slot(key_id).address

    def has_key(self, key_id: str) -> bool:
        return key_id in self._keys

    def destroy(self, key_id: str) -> None:
        slot = self._slot(key_id)
        # Best-effort: drop all references to the account object. (CPython
        # bytes are immutable, so true memory scrubbing is not achievable
        # from pure Python; the guarantee is that no API path can reach the
        # key after destroy.)
        slot.account = None
        del self._keys[key_id]

    def _slot(self, key_id: str) -> _KeySlot:
        try:
            return self._keys[key_id]
        except KeyError:
            raise EnclaveKeyError(f"unknown or destroyed key {key_id!r}") from None

    # -- validation ----------------------------------------------------------
    def _check_nonce(self, slot: _KeySlot, nonce: Any) -> int:
        if not isinstance(nonce, int) or isinstance(nonce, bool):
            raise EnclavePolicyError("nonce must be an integer")
        if nonce < 1 or nonce >= 1 << 256:
            raise EnclavePolicyError("nonce out of uint256 range")
        if nonce in slot.used_nonces:
            raise EnclavePolicyError("nonce already used for this key (replay refused)")
        return nonce

    def _check_expiry(self, expiry: Any, max_ttl: int, *, now: float) -> int:
        if not isinstance(expiry, int) or isinstance(expiry, bool):
            raise EnclavePolicyError("expiry must be an integer unix timestamp")
        if expiry <= int(now):
            raise EnclavePolicyError("expiry must be in the future")
        if expiry - int(now) > max_ttl:
            raise EnclavePolicyError(
                f"expiry exceeds maximum TTL of {max_ttl} seconds"
            )
        return expiry

    def _check_capabilities(self, values: Any, field_name: str) -> List[str]:
        if not isinstance(values, list) or not values:
            raise EnclavePolicyError(f"{field_name} must be a non-empty list")
        seen: Set[str] = set()
        for v in values:
            if not isinstance(v, str) or not v:
                raise EnclavePolicyError(f"{field_name} entries must be non-empty strings")
            if len(v) > MAX_CAPABILITY_LEN:
                raise EnclavePolicyError(f"{field_name} entry too long: {v!r}")
            if not any(v == p[:-1] or v.startswith(p) for p in ALLOWED_CAPABILITY_PREFIXES):
                raise EnclavePolicyError(
                    f"{field_name} entry {v!r} outside allowed namespaces"
                )
            if v in seen:
                raise EnclavePolicyError(f"duplicate {field_name} entry: {v!r}")
            seen.add(v)
        return list(values)

    def _check_address(self, value: Any, field_name: str) -> str:
        try:
            encoded = encode_address(value)  # validates 20-byte hex
        except Exception as exc:
            raise EnclavePolicyError(f"invalid {field_name}: {value!r}") from exc
        del encoded
        return value

    @staticmethod
    def _reject_extra_fields(fields: Dict[str, Any], allowed: Set[str], what: str) -> None:
        extra = set(fields) - allowed
        if extra:
            raise EnclavePolicyError(f"{what}: unexpected fields {sorted(extra)}")
        missing = allowed - set(fields)
        if missing:
            raise EnclavePolicyError(f"{what}: missing fields {sorted(missing)}")

    # -- signing ---------------------------------------------------------------
    def sign_seal(self, key_id: str, fields: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
        """Sign a SigilSeal payload. Returns seal fields + signature metadata.

        Refuses: unknown key, unknown/extra fields, bad DID, bad address,
        bad capability namespace, non-future or over-TTL expiry, bad or
        reused nonce.
        """
        ts = now if now is not None else time.time()
        slot = self._slot(key_id)
        if not isinstance(fields, dict):
            raise EnclavePolicyError("seal fields must be a mapping")
        allowed = {"agent_did", "agent_address", "capabilities", "expiry", "nonce"}
        self._reject_extra_fields(fields, allowed, "SigilSeal")
        agent_did = fields["agent_did"]
        try:
            validate_did(agent_did)
        except ValueError as exc:
            raise EnclavePolicyError(str(exc)) from exc
        agent_address = self._check_address(fields["agent_address"], "agent_address")
        capabilities = self._check_capabilities(fields["capabilities"], "capabilities")
        expiry = self._check_expiry(fields["expiry"], MAX_SEAL_TTL, now=ts)
        nonce = self._check_nonce(slot, fields["nonce"])

        digest = seal_typed_data_digest(
            self._config,
            agent_did=agent_did,
            agent_address=agent_address,
            capabilities=capabilities,
            expiry=expiry,
            nonce=nonce,
        )
        # Enclave signs with secp256k1 only; it never touches HMAC.
        signature = self._unsafe_sign_hash(slot, digest)
        slot.used_nonces.add(nonce)
        return {
            "agent_did": agent_did,
            "agent_address": agent_address,
            "capabilities": capabilities,
            "expiry": expiry,
            "nonce": nonce,
            "signature": signature,
            "sig_type": "eip712-secp256k1",
            "signer": slot.address,
        }

    def sign_delegation(
        self,
        key_id: str,
        fields: Dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Sign a delegation cert (master key -> ephemeral session key).

        Refuses everything :meth:`sign_seal` refuses, plus: session key
        equal to the delegator address (self-delegation is meaningless and
        hides privilege confusion), empty permissions, over-TTL expiry.
        """
        ts = now if now is not None else time.time()
        slot = self._slot(key_id)
        if not isinstance(fields, dict):
            raise EnclavePolicyError("delegation fields must be a mapping")
        allowed = {"session_key", "permissions", "expiry", "nonce"}
        self._reject_extra_fields(fields, allowed, "DelegationCert")
        session_key = self._check_address(fields["session_key"], "session_key")
        if session_key.lower() == slot.address.lower():
            raise EnclavePolicyError(
                "session key must differ from the delegator address"
            )
        permissions = self._check_capabilities(fields["permissions"], "permissions")
        expiry = self._check_expiry(fields["expiry"], MAX_DELEGATION_TTL, now=ts)
        nonce = self._check_nonce(slot, fields["nonce"])

        digest = delegation_typed_data_digest(
            self._config,
            delegator=slot.address,
            session_key=session_key,
            permissions=permissions,
            expiry=expiry,
            nonce=nonce,
        )
        signature = self._unsafe_sign_hash(slot, digest)
        slot.used_nonces.add(nonce)
        return {
            "delegator": slot.address,
            "session_key": session_key,
            "permissions": permissions,
            "expiry": expiry,
            "nonce": nonce,
            "signature": signature,
            "sig_type": "eip712-secp256k1",
        }

    def sign_with_algorithm(self, key_id: str, kind: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Explicit-algorithm entry point. Only secp256k1 is honored.

        Any other algorithm name (including "hmac") is refused: the enclave
        has no HMAC code path by design.
        """
        if kind != "secp256k1":
            raise EnclavePolicyError(
                f"algorithm {kind!r} refused: enclave signs secp256k1 only"
            )
        self._slot(key_id)  # validate the handle exists
        # Dispatch on payload shape without ever branching to another scheme.
        if "agent_did" in fields:
            return self.sign_seal(key_id, fields)
        return self.sign_delegation(key_id, fields)

    @staticmethod
    def _unsafe_sign_hash(slot: _KeySlot, digest: bytes) -> str:
        # Internal only: raw-digest signing through eth_account. The digest
        # is always the enclave-validated EIP-712 typed-data hash.
        signed = slot.account.unsafe_sign_hash(digest)
        sig = signed.signature.hex()
        return sig if sig.startswith("0x") else "0x" + sig


__all__ = [
    "MAX_SEAL_TTL",
    "MAX_DELEGATION_TTL",
    "MAX_CAPABILITY_LEN",
    "ALLOWED_CAPABILITY_PREFIXES",
    "EnclavePolicyError",
    "EnclaveKeyError",
    "SealEnclave",
]
