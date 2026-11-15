"""Sigil Seal: EIP-712 typed identity/capability envelopes + session-key delegation.

CNP-1. Offchain only — nothing here deploys contracts, moves funds, or
touches private keys outside the isolated :class:`SealEnclave`
(``seal_enclave.py``).

A **SigilSeal** is an EIP-712 typed envelope binding:

- ``agentDid``        — the agent's DID (``did:sincor:...``)
- ``agentAddress``    — the agent's onchain identity (Ethereum address)
- ``capabilities``   — capability grants (``string[]``, e.g. ``auction:bid``)
- ``expiry``          — unix timestamp after which the seal is dead
- ``nonce``           — replay-protection nonce

**Session-key delegation** lets a master key authorize an ephemeral session
key through a signed :class:`DelegationCert` (``DelegationCert`` typed
data: delegator -> sessionKey, scoped permissions, expiry, nonce). A
delegation chain verifies only when:

1. the cert signature recovers to the delegator (master) address,
2. the cert has not expired,
3. the session key has not been revoked,
4. the requested permission is covered by the cert's scoped permissions,
5. the action signature recovers to the session-key address, and
6. the cert's permissions are a subset of the delegator's own grants
   (down-scoping enforced at registration).

Security posture (mirrors ``eip712.py``):

- secp256k1 (eth_account) is the only signature scheme the seal path
  trusts. HMAC exists as a **demo-only** fallback and is **default-deny**:
  verification refuses HMAC unless ``allow_hmac=True`` is passed
  explicitly, and the money-path entry points must always require
  secp256k1.
- The EIP-712 domain is the same ContractNet domain (chainId-bound); a
  seal minted under one chainId does not verify under another.
- Distinct ``primaryType`` names (``SigilSeal`` / ``DelegationCert`` /
  ``Revocation``) give distinct typehashes, so a seal cannot be replayed
  as a delegation cert or vice versa (type-confusion between the three
  typed-data shapes is structurally impossible).

Digest construction is byte-identical to eth_account's ``encode_typed_data``
(the test suite pins this differentially, same as the Bid path).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .eip712 import (
    encode_address,
    encode_uint256,
    sign_digest,
    to_hex,
    verify_hmac,
)
from .keccak import keccak256
from .types import ContractNetConfig, SigType

# ---------------------------------------------------------------------------
# EIP-712 type definitions
# ---------------------------------------------------------------------------

SIGIL_SEAL_TYPE = (
    "SigilSeal(string agentDid,address agentAddress,string[] capabilities,"
    "uint256 expiry,uint256 nonce)"
)
DELEGATION_CERT_TYPE = (
    "DelegationCert(address delegator,address sessionKey,"
    "string[] permissions,uint256 expiry,uint256 nonce)"
)
REVOCATION_TYPE = (
    "Revocation(address revoker,bytes32 target,uint256 revokedAt,uint256 nonce)"
)

SIGIL_SEAL_TYPEHASH = keccak256(SIGIL_SEAL_TYPE.encode("ascii"))
DELEGATION_CERT_TYPEHASH = keccak256(DELEGATION_CERT_TYPE.encode("ascii"))
REVOCATION_TYPEHASH = keccak256(REVOCATION_TYPE.encode("ascii"))

# ``did:sincor:<label>`` — bounded length, safe charset.
_DID_RE = re.compile(r"^did:sincor:[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


# ---------------------------------------------------------------------------
# Typed-data encoding
# ---------------------------------------------------------------------------

def encode_string(value: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"expected str, got {type(value).__name__}")
    return keccak256(value.encode("utf-8"))


def encode_string_array(values: List[str]) -> bytes:
    if not isinstance(values, list):
        raise ValueError(f"string[] must be a list, got {type(values).__name__}")
    return keccak256(b"".join(encode_string(v) for v in values))


def validate_did(agent_did: str) -> str:
    if not isinstance(agent_did, str) or not _DID_RE.match(agent_did):
        raise ValueError(f"invalid agent DID {agent_did!r}: must match did:sincor:<label>")
    return agent_did


def seal_struct_hash(
    *,
    agent_did: str,
    agent_address: str,
    capabilities: List[str],
    expiry: int,
    nonce: int,
) -> bytes:
    return keccak256(
        SIGIL_SEAL_TYPEHASH
        + encode_string(validate_did(agent_did))
        + encode_address(agent_address)
        + encode_string_array(list(capabilities))
        + encode_uint256(expiry)
        + encode_uint256(nonce)
    )


def delegation_struct_hash(
    *,
    delegator: str,
    session_key: str,
    permissions: List[str],
    expiry: int,
    nonce: int,
) -> bytes:
    return keccak256(
        DELEGATION_CERT_TYPEHASH
        + encode_address(delegator)
        + encode_address(session_key)
        + encode_string_array(list(permissions))
        + encode_uint256(expiry)
        + encode_uint256(nonce)
    )


def _encode_revocation_target(target: str) -> bytes:
    """Encode a revocation target as bytes32.

    Accepts a 32-byte seal id as-is, or a 20-byte address left-padded to
    32 bytes (standard address->bytes32). Anything else is rejected.
    """
    raw = bytes.fromhex(target.strip().removeprefix("0x").removeprefix("0X"))
    if len(raw) == 32:
        return raw
    if len(raw) == 20:
        return raw.rjust(32, b"\x00")
    raise ValueError(f"revocation target must be 20 or 32 bytes, got {target!r}")


def revocation_struct_hash(
    *,
    revoker: str,
    target: str,
    revoked_at: int,
    nonce: int,
) -> bytes:
    return keccak256(
        REVOCATION_TYPEHASH
        + encode_address(revoker)
        + _encode_revocation_target(target)
        + encode_uint256(revoked_at)
        + encode_uint256(nonce)
    )


def _domain_separator(config: ContractNetConfig) -> bytes:
    # Same domain as the Bid path: chainId-bound, no silent defaults.
    from .eip712 import domain_separator

    return domain_separator(config)


def seal_typed_data_digest(config: ContractNetConfig, **fields: Any) -> bytes:
    return keccak256(
        b"\x19\x01" + _domain_separator(config) + seal_struct_hash(**fields)
    )


def delegation_typed_data_digest(config: ContractNetConfig, **fields: Any) -> bytes:
    return keccak256(
        b"\x19\x01" + _domain_separator(config) + delegation_struct_hash(**fields)
    )


def revocation_typed_data_digest(config: ContractNetConfig, **fields: Any) -> bytes:
    return keccak256(
        b"\x19\x01" + _domain_separator(config) + revocation_struct_hash(**fields)
    )


def _capability_entries(values: List[str]) -> List[Dict[str, str]]:
    return values


def seal_typed_data_payload(
    config: ContractNetConfig,
    *,
    agent_did: str,
    agent_address: str,
    capabilities: List[str],
    expiry: int,
    nonce: int,
) -> Dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "SigilSeal": [
                {"name": "agentDid", "type": "string"},
                {"name": "agentAddress", "type": "address"},
                {"name": "capabilities", "type": "string[]"},
                {"name": "expiry", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "SigilSeal",
        "domain": {
            "name": config.domain_name,
            "version": config.domain_version,
            "chainId": config.chain_id,
            "verifyingContract": config.verifying_contract,
        },
        "message": {
            "agentDid": agent_did,
            "agentAddress": agent_address,
            "capabilities": _capability_entries(list(capabilities)),
            "expiry": str(expiry),
            "nonce": str(nonce),
        },
    }


def delegation_typed_data_payload(
    config: ContractNetConfig,
    *,
    delegator: str,
    session_key: str,
    permissions: List[str],
    expiry: int,
    nonce: int,
) -> Dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "DelegationCert": [
                {"name": "delegator", "type": "address"},
                {"name": "sessionKey", "type": "address"},
                {"name": "permissions", "type": "string[]"},
                {"name": "expiry", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "DelegationCert",
        "domain": {
            "name": config.domain_name,
            "version": config.domain_version,
            "chainId": config.chain_id,
            "verifyingContract": config.verifying_contract,
        },
        "message": {
            "delegator": delegator,
            "sessionKey": session_key,
            "permissions": _capability_entries(list(permissions)),
            "expiry": str(expiry),
            "nonce": str(nonce),
        },
    }


def revocation_typed_data_payload(
    config: ContractNetConfig,
    *,
    revoker: str,
    target: str,
    revoked_at: int,
    nonce: int,
) -> Dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Revocation": [
                {"name": "revoker", "type": "address"},
                {"name": "target", "type": "bytes32"},
                {"name": "revokedAt", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "Revocation",
        "domain": {
            "name": config.domain_name,
            "version": config.domain_version,
            "chainId": config.chain_id,
            "verifyingContract": config.verifying_contract,
        },
        "message": {
            "revoker": revoker,
            "target": to_hex(_encode_revocation_target(target)),
            "revokedAt": str(revoked_at),
            "nonce": str(nonce),
        },
    }


# ---------------------------------------------------------------------------
# Signing / recovery (eth_account public API; secp256k1 first, HMAC opt-in)
# ---------------------------------------------------------------------------

def _recover_secp256k1(typed_data: Dict[str, Any], signature: str) -> Optional[str]:
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except Exception:
        return None
    try:
        message = encode_typed_data(full_message=typed_data)
        return Account.recover_message(message, signature=signature)
    except Exception:
        return None


def sign_seal(
    config: ContractNetConfig,
    fields: Dict[str, Any],
    *,
    private_key: str = "",
    signing_secret: str = "",
    allow_hmac: bool = False,
) -> Tuple[str, str, bytes]:
    """Sign a SigilSeal. Returns (signature, sig_type, digest).

    secp256k1 is preferred. HMAC is demo-only and requires ``allow_hmac``
    plus a ``signing_secret`` — it is never used silently.
    """
    digest = seal_typed_data_digest(config, **fields)
    if private_key:
        sig, sig_type = sign_digest(digest, private_key=private_key)
        if sig_type == SigType.SECP256K1.value:
            return sig, sig_type, digest
    if not allow_hmac:
        raise ValueError(
            "secp256k1 unavailable and HMAC is default-deny: pass "
            "allow_hmac=True with a signing_secret for demo-only use"
        )
    if not signing_secret:
        raise ValueError("signing_secret required for HMAC (demo-only)")
    sig, sig_type = sign_digest(digest, signing_secret=signing_secret)
    return sig, sig_type, digest


def verify_seal(
    config: ContractNetConfig,
    fields: Dict[str, Any],
    signature: str,
    *,
    sig_type: str,
    expected_address: str,
    signing_secret: str = "",
    allow_hmac: bool = False,
    now: Optional[float] = None,
) -> Tuple[bool, str]:
    """Verify a SigilSeal. Returns (ok, reason).

    ``require``-style: HMAC signatures are rejected unless ``allow_hmac``
    is explicitly True (default-deny). Expired seals are rejected.
    """
    if sig_type == SigType.HMAC.value and not allow_hmac:
        return False, "hmac seal rejected: demo-only, default-deny"
    payload = seal_typed_data_payload(config, **fields)
    digest = seal_typed_data_digest(config, **fields)
    if sig_type == SigType.SECP256K1.value:
        recovered = _recover_secp256k1(payload, signature)
        if recovered is None:
            return False, "secp256k1 recovery failed"
        if recovered.lower() != expected_address.lower():
            return False, "recovered signer != expected agent address"
    elif sig_type == SigType.HMAC.value:
        if not signing_secret or not verify_hmac(digest, signature, signing_secret):
            return False, "hmac verification failed"
    else:
        return False, f"unknown sig_type {sig_type!r}"
    ts = now if now is not None else time.time()
    if int(fields["expiry"]) <= int(ts):
        return False, "seal expired"
    return True, "ok"


# ---------------------------------------------------------------------------
# Capability scoping
# ---------------------------------------------------------------------------

def permission_covered(granted: List[str], required: str) -> bool:
    """True if ``required`` is within the scope of ``granted``.

    Exact match, or the grant is a namespace prefix of the required
    permission with a ``:`` boundary (``auction`` covers ``auction:bid``,
    but ``auctionx`` does not cover ``auction:bid`` and ``auction:bi``
    does not cover ``auction:bid``).
    """
    for g in granted:
        if g == required:
            return True
        if required.startswith(g + ":"):
            return True
    return False


def grants_cover_all(grants: List[str], requested: List[str]) -> bool:
    return all(permission_covered(grants, r) for r in requested)


# ---------------------------------------------------------------------------
# Delegation registry (revocation + chain verification)
# ---------------------------------------------------------------------------

@dataclass
class DelegationRecord:
    delegator: str
    session_key: str
    permissions: List[str]
    expiry: int
    nonce: int
    signature: str
    sig_type: str
    revoked: bool = False
    revoked_at: Optional[int] = None


@dataclass
class SealRecord:
    fields: Dict[str, Any]
    signature: str
    sig_type: str
    seal_id: str
    revoked: bool = False


class SealRegistry:
    """Offchain registry: issued seals, delegation certs, revocations.

    Everything is verified cryptographically; the registry itself holds no
    keys and performs no signing. Timestamps are compared as integers;
    callers may pass ``now`` for determinism (defaults to ``time.time()``).
    """

    def __init__(self, config: ContractNetConfig):
        self.config = config
        self.seals: Dict[str, SealRecord] = {}
        self.delegations: Dict[str, DelegationRecord] = {}  # session_key.lower() -> record

    # -- seals ---------------------------------------------------------------
    def register_seal(
        self,
        fields: Dict[str, Any],
        signature: str,
        *,
        sig_type: str,
        allow_hmac: bool = False,
        now: Optional[float] = None,
    ) -> str:
        expected = fields["agent_address"]
        ok, reason = verify_seal(
            self.config, fields, signature,
            sig_type=sig_type, expected_address=expected,
            allow_hmac=allow_hmac, now=now,
        )
        if not ok:
            raise ValueError(f"refusing to register invalid seal: {reason}")
        seal_id = to_hex(seal_typed_data_digest(self.config, **fields))
        if seal_id in self.seals:
            raise ValueError("seal already registered (replay)")
        self.seals[seal_id] = SealRecord(
            fields=dict(fields), signature=signature,
            sig_type=sig_type, seal_id=seal_id,
        )
        return seal_id

    def revoke_seal(
        self,
        seal_id: str,
        revoker_signature: str,
        *,
        revoked_at: int,
        nonce: int,
        now: Optional[float] = None,
    ) -> None:
        rec = self.seals.get(seal_id)
        if rec is None:
            raise ValueError("unknown seal_id")
        if rec.revoked:
            raise ValueError("seal already revoked")
        agent_address = rec.fields["agent_address"]
        payload = revocation_typed_data_payload(
            self.config, revoker=agent_address, target=seal_id,
            revoked_at=revoked_at, nonce=nonce,
        )
        recovered = _recover_secp256k1(payload, revoker_signature)
        if recovered is None or recovered.lower() != agent_address.lower():
            raise ValueError("revocation must be signed by the seal's agent address")
        rec.revoked = True

    def seal_valid(self, seal_id: str, *, now: Optional[float] = None) -> bool:
        rec = self.seals.get(seal_id)
        if rec is None or rec.revoked:
            return False
        ts = int(now if now is not None else time.time())
        return int(rec.fields["expiry"]) > ts

    # -- delegation ----------------------------------------------------------
    def register_delegation(
        self,
        cert: Dict[str, Any],
        signature: str,
        *,
        sig_type: str,
        delegator_grants: List[str],
        seal_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> str:
        """Register a delegation cert after full chain checks.

        Enforces: cert signed by delegator; cert permissions ⊆ delegator
        grants (down-scoping); cert expiry ≤ delegator seal expiry when a
        ``seal_id`` is given; session key not already delegated.
        """
        if sig_type != SigType.SECP256K1.value:
            raise ValueError(
                "delegation certs require secp256k1: HMAC is never "
                "accepted for delegation (money-adjacent trust path)"
            )
        payload = delegation_typed_data_payload(self.config, **cert)
        recovered = _recover_secp256k1(payload, signature)
        if recovered is None or recovered.lower() != cert["delegator"].lower():
            raise ValueError("delegation cert must be signed by the delegator")
        ts = int(now if now is not None else time.time())
        if int(cert["expiry"]) <= ts:
            raise ValueError("refusing to register expired delegation cert")
        if not grants_cover_all(delegator_grants, cert["permissions"]):
            raise ValueError(
                "delegation permissions exceed delegator grants "
                "(down-scoping violated)"
            )
        if seal_id is not None:
            seal = self.seals.get(seal_id)
            if seal is None:
                raise ValueError("unknown delegator seal_id")
            if int(cert["expiry"]) > int(seal.fields["expiry"]):
                raise ValueError(
                    "delegation expiry exceeds delegator seal expiry"
                )
        key = cert["session_key"].lower()
        if key in self.delegations:
            raise ValueError("session key already delegated (reuse rejected)")
        self.delegations[key] = DelegationRecord(
            delegator=cert["delegator"],
            session_key=cert["session_key"],
            permissions=list(cert["permissions"]),
            expiry=int(cert["expiry"]),
            nonce=int(cert["nonce"]),
            signature=signature,
            sig_type=sig_type,
        )
        return key

    def revoke_session_key(
        self,
        session_key: str,
        revoker_signature: str,
        *,
        revoked_at: int,
        nonce: int,
    ) -> None:
        key = session_key.lower()
        rec = self.delegations.get(key)
        if rec is None:
            raise ValueError("unknown session key")
        if rec.revoked:
            raise ValueError("session key already revoked")
        payload = revocation_typed_data_payload(
            self.config, revoker=rec.delegator, target=rec.session_key,
            revoked_at=revoked_at, nonce=nonce,
        )
        recovered = _recover_secp256k1(payload, revoker_signature)
        if recovered is None or recovered.lower() != rec.delegator.lower():
            raise ValueError(
                "session-key revocation must be signed by the delegator"
            )
        rec.revoked = True
        rec.revoked_at = revoked_at

    # -- chain verification --------------------------------------------------
    def verify_delegation_chain(
        self,
        session_key: str,
        action_payload: Dict[str, Any],
        action_signature: str,
        required_permission: str,
        *,
        now: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Verify master -> session-key -> signed action, end to end."""
        key = session_key.lower()
        rec = self.delegations.get(key)
        if rec is None:
            return False, "unknown session key"
        if rec.revoked:
            return False, "session key revoked"
        ts = int(now if now is not None else time.time())
        if rec.expiry <= ts:
            return False, "delegation cert expired"
        if not permission_covered(rec.permissions, required_permission):
            return False, (
                f"required permission {required_permission!r} not in "
                "delegation scope"
            )
        recovered = _recover_secp256k1(action_payload, action_signature)
        if recovered is None or recovered.lower() != rec.session_key.lower():
            return False, "action signature not from the session key"
        return True, "ok"

    def session_key_live(self, session_key: str, *, now: Optional[float] = None) -> bool:
        rec = self.delegations.get(session_key.lower())
        if rec is None or rec.revoked:
            return False
        ts = int(now if now is not None else time.time())
        return rec.expiry > ts


__all__ = [
    "SIGIL_SEAL_TYPE",
    "DELEGATION_CERT_TYPE",
    "REVOCATION_TYPE",
    "seal_struct_hash",
    "delegation_struct_hash",
    "revocation_struct_hash",
    "seal_typed_data_digest",
    "delegation_typed_data_digest",
    "revocation_typed_data_digest",
    "seal_typed_data_payload",
    "delegation_typed_data_payload",
    "revocation_typed_data_payload",
    "sign_seal",
    "verify_seal",
    "permission_covered",
    "grants_cover_all",
    "DelegationRecord",
    "SealRecord",
    "SealRegistry",
]
