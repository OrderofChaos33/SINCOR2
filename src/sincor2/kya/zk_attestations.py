"""CNP-2: commitment-based ZK-style attestations for KYA state.

Proves "agent is KYA-verified at tier T" or "agent holds capability C" without
revealing the agent's identity (no agent_id, wallet, or kya_id leaves the
issuer).

Honest primitive: this is NOT a real zk-SNARK. It is a commitment + issuer
signature scheme:

* The issuer (a party with read access to the KYA registry) verifies the
  claim off-chain, then signs an attestation over a privacy-preserving
  ``nullifier``: ``keccak256(issuer_salt || agent_id || claim_kind || claim_value)``.
  The nullifier binds the attestation to the agent without naming them.
* The issuer appends a commitment ``keccak256(nullifier || digest || issued_at)``
  to an internal append-only ledger at issuance, and keeps the opening
  (agent_id, salt, claim) private for dispute/audit reveal — the
  commitment+reveal protocol, mirroring ``sincor2.zk_privacy_layer``.
* The verifier checks: schema + version, issuer identity, EIP-191 signature,
  expiry, claim-kind allow-list, and revocation status of the nullifier.
  EVERY failure mode rejects (fail-closed). An unverifiable attestation is
  rejected, never "accepted with warnings".

Privacy boundaries (documented limits, not bugs):
* The nullifier is deterministic per (agent, claim): two attestations for the
  same claim are linkable to each other (but not to the agent without the
  issuer salt). Determinism is what makes revocation checkable.
* Verification of non-revocation requires the issuer's revocation source.
  If the revocation source is unavailable or misbehaves, verification FAILS
  CLOSED. This scheme trusts the issuer's key and availability; the SNARK
  migration path (docs/architecture/CNP2_SNARK_MIGRATION.md) removes that
  trust when the founder approves the dependency.

Security properties:
* Tampering with any signed field (claim, nullifier, issuer, issued_at,
  expires_at, commitment) invalidates the EIP-191 signature -> rejected.
* Forgery requires the issuer's private key (secp256k1, eth_account).
* Revocation: ``issuer.revoke_agent(kya_id)`` marks the registry record
  revoked AND publishes the agent's nullifiers to the revocation set.
  Attestations issued before revocation fail verification afterwards.
* Replay: attestations are bounded by ``expires_at``. Re-presenting a valid
  attestation within its window is harmless (a proof is a public claim, not
  a spend) — there is nothing to double-spend.
* Clock: issuance timestamps are issuer-controlled; the verifier bounds
  future-dated issuance with ``max_clock_skew_sec`` and rejects expired
  attestations outright.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sincor2.kya.registry import MIN_STAKE_WEI, record_hash

# ---------------------------------------------------------------------------
# Hashing primitive (keccak256, same provider pattern as kya/merkle.py)
# ---------------------------------------------------------------------------


def _keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak

        return keccak(data)
    except Exception:
        from sha3 import keccak_256  # type: ignore

        return keccak_256(data).digest()


def _keccak_hex(data: bytes) -> str:
    return "0x" + _keccak(data).hex()


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

ATTESTATION_VERSION = "cnp2-zk-kya-v1"
DOMAIN_SEPARATOR = b"SINCOR-CNP2-KYA-ATTESTATION-V1\x00"
DEFAULT_TTL_SEC = 24 * 3600
MAX_CLOCK_SKEW_SEC = 300

TIER_T1 = "T1"  # listed/bound: identity on file
TIER_T2 = "T2"  # staked: stake >= MIN_STAKE_WEI
TIER_T3 = "T3"  # verified: registry verification complete
TIERS = (TIER_T1, TIER_T2, TIER_T3)

CLAIM_KIND_TIER = "kya_tier"
CLAIM_KIND_CAPABILITY = "kya_capability"
CLAIM_KINDS = (CLAIM_KIND_TIER, CLAIM_KIND_CAPABILITY)

_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _now() -> int:
    return int(time.time())


def validate_capability_name(name: str) -> str:
    """Normalize and validate a capability name. Raises ValueError."""
    n = str(name or "").strip().lower()
    if not _CAPABILITY_RE.match(n):
        raise ValueError(
            "capability must match [a-z0-9][a-z0-9_-]{0,63}, got %r" % (name,)
        )
    return n


def tier_of(record: Dict[str, Any]) -> Optional[str]:
    """Highest KYA tier a registry record qualifies for. None if revoked/unusable."""
    if not record or record.get("revoked"):
        return None
    if record.get("verified") is True and record.get("status") == "verified":
        return TIER_T3
    try:
        staked = int(record.get("stake_axm_wei") or 0) >= MIN_STAKE_WEI
    except (TypeError, ValueError):
        staked = False
    if staked:
        return TIER_T2
    if record.get("status") in ("listed", "bound", "staked", "verified"):
        return TIER_T1
    return None


def tier_satisfies(have: str, want: str) -> bool:
    return TIERS.index(have) >= TIERS.index(want)


# ---------------------------------------------------------------------------
# Attestation object
# ---------------------------------------------------------------------------


def _canonical_body(att: "KYAAttestation") -> bytes:
    """Canonical signed bytes. Single source of truth for sign and verify."""
    body = {
        "version": att.version,
        "claim": att.claim,
        "nullifier": att.nullifier,
        "issuer": att.issuer.lower(),
        "issued_at": att.issued_at,
        "expires_at": att.expires_at,
        "commitment": att.commitment.lower(),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class KYAAttestation:
    """A signed, privacy-preserving KYA state/capability attestation.

    Carries NO agent_id, wallet, or kya_id. The nullifier binds the
    attestation to the prover without naming them.
    """

    attestation_id: str
    version: str
    claim: Dict[str, Any]
    nullifier: str
    issuer: str  # 0x address of the issuing key
    issued_at: int
    expires_at: int
    commitment: str  # 0x keccak256(nullifier || digest || issued_at)
    signature: str  # 0x EIP-191 signature over the canonical digest

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attestation_id": self.attestation_id,
            "version": self.version,
            "claim": dict(self.claim),
            "nullifier": self.nullifier,
            "issuer": self.issuer,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "commitment": self.commitment,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KYAAttestation":
        if not isinstance(data, dict):
            raise ValueError("attestation must be a dict")
        return cls(
            attestation_id=str(data.get("attestation_id") or ""),
            version=str(data.get("version") or ""),
            claim=dict(data.get("claim") or {}),
            nullifier=str(data.get("nullifier") or ""),
            issuer=str(data.get("issuer") or ""),
            issued_at=int(data.get("issued_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            commitment=str(data.get("commitment") or ""),
            signature=str(data.get("signature") or ""),
        )


@dataclass
class VerificationResult:
    valid: bool
    reason: str = ""
    attestation_id: str = ""
    claim: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Issuer
# ---------------------------------------------------------------------------

RevocationCheck = Callable[[str], bool]


class ZKAttestationIssuer:
    """Issues and revokes KYA attestations. Holds the issuer key + salt.

    The registry is consulted at issuance time (fail-closed: an agent that
    does not hold the claimed tier/capability is never issued an
    attestation). The salt and openings never leave the issuer.
    """

    def __init__(
        self,
        registry: Any,
        issuer_private_key: str,
        salt: Optional[bytes] = None,
    ) -> None:
        from eth_account import Account

        key = issuer_private_key.strip()
        if key.startswith("0x"):
            key = key[2:]
        if len(bytes.fromhex(key)) != 32:
            raise ValueError("issuer_private_key must be 32 bytes hex")
        self._account = Account.from_key(bytes.fromhex(key))
        self.issuer_address: str = self._account.address
        self.registry = registry
        self.salt: bytes = salt or secrets.token_bytes(32)
        self.lock = threading.Lock()
        # attestation_id -> commitment record (append-only issuance ledger)
        self.commitment_ledger: Dict[str, Dict[str, Any]] = {}
        # attestation_id -> private opening material (for dispute reveal)
        self._openings: Dict[str, Dict[str, Any]] = {}
        # nullifier -> {"revoked_at": int, "reason": str}
        self._revocations: Dict[str, Dict[str, Any]] = {}

    # -- internal crypto ----------------------------------------------------

    def _nullifier(self, agent_id: str, claim_kind: str, claim_value: str) -> str:
        blob = (
            self.salt
            + b"|"
            + agent_id.encode()
            + b"|"
            + claim_kind.encode()
            + b"|"
            + claim_value.encode()
        )
        return _keccak_hex(blob)

    def _digest(self, att: KYAAttestation) -> bytes:
        return _keccak(DOMAIN_SEPARATOR + _canonical_body(att))

    def _sign(self, att: KYAAttestation) -> str:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        msg = encode_defunct(primitive=self._digest(att))
        signed = Account.sign_message(msg, private_key=self._account.key)
        return "0x" + signed.signature.hex()

    def _commitment_for(self, nullifier: str, digest: bytes, issued_at: int) -> str:
        return _keccak_hex(
            bytes.fromhex(nullifier[2:]) + digest + issued_at.to_bytes(8, "big")
        )

    def _issue(
        self,
        agent_id: str,
        claim: Dict[str, Any],
        claim_value: str,
        ttl_sec: int,
    ) -> KYAAttestation:
        claim_kind = str(claim.get("kind") or "")
        if claim_kind not in CLAIM_KINDS:
            raise ValueError("unknown claim kind: %r" % claim_kind)
        if ttl_sec <= 0:
            raise ValueError("ttl_sec must be positive")
        issued_at = _now()
        nullifier = self._nullifier(agent_id, claim_kind, claim_value)
        att = KYAAttestation(
            attestation_id="cnp2a_" + secrets.token_hex(12),
            version=ATTESTATION_VERSION,
            claim=dict(claim),
            nullifier=nullifier,
            issuer=self.issuer_address,
            issued_at=issued_at,
            expires_at=issued_at + int(ttl_sec),
            commitment="",
            signature="",
        )
        digest = self._digest(att)
        att.commitment = self._commitment_for(nullifier, digest, issued_at)
        # commitment must be bound before signing (signature covers it)
        att.signature = self._sign(att)
        opening = {
            "agent_id": agent_id,
            "salt": "0x" + self.salt.hex(),
            "claim": dict(claim),
            "claim_value": claim_value,
            "record_hash_at_issue": record_hash(
                {"agent_id": agent_id, "claim": claim, "issued_at": issued_at}
            ),
        }
        ledger_row = {
            "attestation_id": att.attestation_id,
            "nullifier": nullifier,
            "commitment": att.commitment,
            "claim": dict(claim),
            "issued_at": issued_at,
            "expires_at": att.expires_at,
        }
        with self.lock:
            self.commitment_ledger[att.attestation_id] = ledger_row
            self._openings[att.attestation_id] = opening
        return att

    # -- public issuance API -------------------------------------------------

    def issue_tier_attestation(
        self, agent_id: str, tier: str, ttl_sec: int = DEFAULT_TTL_SEC
    ) -> KYAAttestation:
        """Attest that an agent holds at least tier T. Fail-closed on lookup."""
        if tier not in TIERS:
            raise ValueError("unknown tier: %r" % (tier,))
        rec = self.registry.lookup(agent_id=agent_id)
        if not rec:
            raise LookupError("agent not in KYA registry: %r" % (agent_id,))
        have = tier_of(rec)
        if have is None:
            raise PermissionError("agent is revoked or has no usable KYA record")
        if not tier_satisfies(have, tier):
            raise PermissionError(
                "agent tier %s does not satisfy requested tier %s" % (have, tier)
            )
        return self._issue(
            agent_id, {"kind": CLAIM_KIND_TIER, "tier": tier}, tier, ttl_sec
        )

    def issue_capability_attestation(
        self, agent_id: str, capability: str, ttl_sec: int = DEFAULT_TTL_SEC
    ) -> KYAAttestation:
        """Attest that an agent holds capability C. Fail-closed on lookup."""
        cap = validate_capability_name(capability)
        rec = self.registry.lookup(agent_id=agent_id)
        if not rec:
            raise LookupError("agent not in KYA registry: %r" % (agent_id,))
        have = tier_of(rec)
        if have is None:
            raise PermissionError("agent is revoked or has no usable KYA record")
        held = [str(c).lower() for c in (rec.get("capabilities") or [])]
        if cap not in held:
            raise PermissionError("agent does not hold capability %r" % (cap,))
        return self._issue(
            agent_id, {"kind": CLAIM_KIND_CAPABILITY, "capability": cap}, cap, ttl_sec
        )

    # -- revocation ----------------------------------------------------------

    def revoke_agent(self, kya_id: str, reason: str = "") -> Dict[str, Any]:
        """Revoke an agent: registry record revoked AND nullifiers published.

        Attestations issued before this call fail verification afterwards.
        """
        rec = self.registry.revoke(kya_id, reason=reason)
        agent_id = str(rec.get("agent_id") or "")
        revoked_at = _now()
        with self.lock:
            for tier in TIERS:
                n = self._nullifier(agent_id, CLAIM_KIND_TIER, tier)
                self._revocations[n] = {"revoked_at": revoked_at, "reason": reason}
            for cap in (rec.get("capabilities") or []):
                n = self._nullifier(
                    agent_id, CLAIM_KIND_CAPABILITY, str(cap).lower()
                )
                self._revocations[n] = {"revoked_at": revoked_at, "reason": reason}
        return rec

    def is_nullifier_revoked(self, nullifier: str) -> bool:
        with self.lock:
            return nullifier in self._revocations

    def revocation_source(self) -> RevocationCheck:
        """Return the revocation callback verifiers must consult."""
        return self.is_nullifier_revoked

    # -- commitment + reveal -------------------------------------------------

    def anchored(self, attestation_id: str) -> bool:
        """True if the issuer's ledger anchors this attestation's commitment."""
        with self.lock:
            return attestation_id in self.commitment_ledger

    def open(self, attestation_id: str) -> Optional[Dict[str, Any]]:
        """Reveal the opening for a dispute/audit. Issuer-only operation."""
        with self.lock:
            opening = self._openings.get(attestation_id)
            return dict(opening) if opening else None

    def verify_opening(
        self, att: KYAAttestation, opening: Dict[str, Any]
    ) -> bool:
        """Check that an opening reproduces the attestation's nullifier+commitment."""
        try:
            salt = bytes.fromhex(str(opening.get("salt") or "").removeprefix("0x"))
            claim = dict(opening.get("claim") or {})
            claim_kind = str(claim.get("kind") or "")
            claim_value = str(opening.get("claim_value") or "")
            agent_id = str(opening.get("agent_id") or "")
            if claim_kind not in CLAIM_KINDS or not agent_id or not salt:
                return False
            nullifier = _keccak_hex(
                salt + b"|" + agent_id.encode() + b"|"
                + claim_kind.encode() + b"|" + claim_value.encode()
            )
            if not secrets.compare_digest(nullifier.lower(), att.nullifier.lower()):
                return False
            # Mirror issuance order: the commitment was computed over the
            # canonical body with the commitment field still empty (it is
            # bound by the signature afterwards).
            probe = KYAAttestation(
                attestation_id=att.attestation_id,
                version=att.version,
                claim=dict(att.claim),
                nullifier=att.nullifier,
                issuer=att.issuer,
                issued_at=att.issued_at,
                expires_at=att.expires_at,
                commitment="",
                signature="",
            )
            digest = self._digest(probe)
            want = self._commitment_for(nullifier, digest, att.issued_at)
            return secrets.compare_digest(want.lower(), att.commitment.lower())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Verifier (fail-closed)
# ---------------------------------------------------------------------------


def verify_attestation(
    data: Dict[str, Any],
    issuer_address: str,
    revocation_check: Optional[RevocationCheck],
    now: Optional[int] = None,
    max_clock_skew_sec: int = MAX_CLOCK_SKEW_SEC,
) -> VerificationResult:
    """Verify an attestation. Returns valid=False for ANY failure (fail-closed).

    ``revocation_check`` must be a callable nullifier -> bool. If it is
    missing, raises, or returns a non-bool, the attestation is REJECTED:
    non-revocation must be positively established, never assumed.
    """
    try:
        att = KYAAttestation.from_dict(data)
    except Exception as exc:
        return VerificationResult(False, "malformed-attestation: %s" % exc)

    if not att.attestation_id or not att.signature or not att.nullifier:
        return VerificationResult(False, "missing-fields", att.attestation_id)
    if att.version != ATTESTATION_VERSION:
        return VerificationResult(False, "unsupported-version", att.attestation_id)

    claim_kind = str((att.claim or {}).get("kind") or "")
    if claim_kind not in CLAIM_KINDS:
        return VerificationResult(False, "unknown-claim-kind", att.attestation_id)
    if claim_kind == CLAIM_KIND_TIER and (att.claim.get("tier") not in TIERS):
        return VerificationResult(False, "bad-tier-claim", att.attestation_id)
    if claim_kind == CLAIM_KIND_CAPABILITY:
        try:
            validate_capability_name(att.claim.get("capability"))
        except ValueError:
            return VerificationResult(False, "bad-capability-claim", att.attestation_id)

    if not str(issuer_address or "").strip():
        return VerificationResult(False, "issuer-unknown", att.attestation_id)
    if att.issuer.lower() != issuer_address.strip().lower():
        return VerificationResult(False, "issuer-mismatch", att.attestation_id)

    ts = now if now is not None else _now()
    if att.issued_at > ts + max_clock_skew_sec:
        return VerificationResult(False, "issued-in-future", att.attestation_id)
    if ts > att.expires_at:
        return VerificationResult(False, "expired", att.attestation_id)
    if att.expires_at <= att.issued_at:
        return VerificationResult(False, "invalid-validity-window", att.attestation_id)

    # Signature: recompute digest over the canonical body and recover signer.
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        digest = _keccak(DOMAIN_SEPARATOR + _canonical_body(att))
        sig_bytes = bytes.fromhex(att.signature.removeprefix("0x"))
        recovered = Account.recover_message(
            encode_defunct(primitive=digest), signature=sig_bytes
        )
    except Exception:
        return VerificationResult(False, "bad-signature", att.attestation_id)
    if recovered.lower() != att.issuer.lower():
        return VerificationResult(False, "bad-signature", att.attestation_id)

    # Revocation: positively required. Any ambiguity -> reject.
    if revocation_check is None:
        return VerificationResult(False, "revocation-unverifiable", att.attestation_id)
    try:
        revoked = revocation_check(att.nullifier)
    except Exception:
        return VerificationResult(False, "revocation-unverifiable", att.attestation_id)
    if not isinstance(revoked, bool):
        return VerificationResult(False, "revocation-unverifiable", att.attestation_id)
    if revoked:
        return VerificationResult(False, "revoked", att.attestation_id)

    return VerificationResult(True, "ok", att.attestation_id, dict(att.claim))


def batch_verify(
    items: List[Dict[str, Any]],
    issuer_address: str,
    revocation_check: Optional[RevocationCheck],
    now: Optional[int] = None,
) -> List[VerificationResult]:
    """Verify many attestations; each result is independent (fail-closed each)."""
    return [
        verify_attestation(d, issuer_address, revocation_check, now=now) for d in items
    ]
