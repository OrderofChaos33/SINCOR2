"""EAS-backed decentralized reputation: offchain attestation client + graph.

This module implements the CNP-6 scope from the contract-net-protocol
upgrade backlog:

* **EAS schema design** for reputation attestations, using the EAS
  (Ethereum Attestation Service) schema-UID derivation and the EAS
  offchain ``Attest`` EIP-712 shape (schema, attester, recipient, time,
  expirationTime, revocable, refUID, data, salt, signature).
* **Python EAS client** (``EASClient``) that creates and verifies
  attestations entirely **offchain** with ``eth_account`` secp256k1
  signatures. No network calls, no on-chain writes, no EAS contract
  dependency — the signing domain deliberately uses the zero address as
  the ``verifyingContract`` so a signature produced here can never be
  mistaken for a real EAS-mainnet attestation. Deployment remains
  founder-gated; when/if it happens, the operator pins the real EAS
  contract address via the ``verifying_contract`` constructor argument.
* **Attestation-graph builder + queries** (``AttestationGraph``): an
  offchain index of verified attestations with poisoning-resistant
  aggregation (per-attester contribution caps, self-attestation
  rejection, spam caps) plus vouch-path and cycle queries.

Security model (adversarial self-review baked into the tests):

* Forged attestation by a non-attester: the EIP-712 digest is recovered
  and must equal the claimed attester; the attestation UID is recomputed
  from all fields and must match. Any tamper breaks one of the two.
* Graph poisoning: self-attestations are rejected at ingest;
  per-(attester, subject, schema) and per-attester caps bound flooding;
  aggregation truncates each attester's contribution so a single
  Sybil cannot inflate a subject's score.
* Replay: every attestation carries a random ``salt`` (fresh UID per
  issuance); ingest is idempotent on UID; expired attestations are
  rejected; future-dated ones are rejected beyond a clock-skew bound.
* Schema-id confusion: schema UIDs are derived EAS-style
  (keccak256 of the ABI-encoded ``(schema, resolver, revocable)`` tuple)
  and must be registered before use; data bytes are decoded against the
  registered schema's field layout, so bytes meant for one schema are
  rejected under another.

Shape compatibility note for CNP-2 (ZK KYA attestations, unmerged
branch): every attestation exposes ``summary()`` returning
``(attester, subject, schema_id, payload_hash)`` — the same four-tuple —
without importing anything from that wave's code.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import is_address, keccak, to_checksum_address

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: EAS EIP-712 domain identifiers (the real EAS domain name/version).
EAS_DOMAIN_NAME = "EAS Attestation"
EAS_DOMAIN_VERSION = "1.0.0"

#: Sentinel verifying contract: marks signatures as OFFCHAIN-LOCAL.
#: Never the canonical EAS deployment — a signature minted with this
#: domain cannot validate against real EAS contracts.
OFFCHAIN_VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000000"

ZERO_ADDRESS = OFFCHAIN_VERIFYING_CONTRACT
ZERO_UID = "0x" + "00" * 32

#: Max seconds an attestation's ``time`` may lie in the future.
DEFAULT_CLOCK_SKEW_SEC = 300
#: ``expirationTime == 0`` means "never expires", mirroring EAS semantics.
NO_EXPIRATION = 0

# ---------------------------------------------------------------------------
# Reputation schemas
# ---------------------------------------------------------------------------


def schema_uid(schema: str, resolver: str = ZERO_ADDRESS,
               revocable: bool = True) -> str:
    """Derive an EAS-style schema UID.

    EAS computes ``keccak256(abi.encode(schema, resolver, revocable))``.
    """
    encoded = abi_encode(
        ["string", "address", "bool"],
        [schema, to_checksum_address(resolver), revocable],
    )
    return "0x" + keccak(encoded).hex()


@dataclass(frozen=True)
class ReputationSchema:
    """One registered EAS schema for reputation attestations."""

    name: str
    schema: str  # e.g. "uint8 score,bytes32 task_id,bytes32 payload_hash"
    resolver: str = ZERO_ADDRESS
    revocable: bool = True

    @property
    def uid(self) -> str:
        return schema_uid(self.schema, self.resolver, self.revocable)

    @property
    def fields(self) -> List[Tuple[str, str]]:
        """Parsed ``(type, name)`` pairs in schema order."""
        out: List[Tuple[str, str]] = []
        for part in self.schema.split(","):
            typ, _, name = part.strip().partition(" ")
            if not typ or not name:
                raise ValueError(f"malformed schema field: {part!r}")
            out.append((typ.strip(), name.strip()))
        return out


#: Peer vouch: a score 0..100 for an agent's work on a task.
SCHEMA_VOUCH = ReputationSchema(
    name="SINCOR_REPUTATION_VOUCH",
    schema="uint8 score,bytes32 task_id,bytes32 payload_hash",
)
#: Execution outcome: quality 0..100 plus upheld-dispute count, from a
#: poster, arbitrator, or adjudicator-signed oracle.
SCHEMA_EXECUTION_OUTCOME = ReputationSchema(
    name="SINCOR_EXECUTION_OUTCOME",
    schema="uint8 quality,uint8 upheld_disputes,bytes32 task_id,"
           "bytes32 payload_hash",
)
#: KYA tier binding: shape-compatible with the CNP-2 attestation tuple
#: (attester, subject, schema id, payload hash); no import from that
#: wave's unmerged branch.
SCHEMA_KYA_TIER = ReputationSchema(
    name="SINCOR_KYA_TIER",
    schema="uint8 tier,bytes32 kya_ref,bytes32 payload_hash",
)

#: uid -> schema registry. Unknown UIDs fail closed at verification.
SCHEMAS: Dict[str, ReputationSchema] = {
    s.uid: s for s in (SCHEMA_VOUCH, SCHEMA_EXECUTION_OUTCOME,
                       SCHEMA_KYA_TIER)
}


def _coerce_bytes32(value: Any, field_name: str) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        text = value[2:] if value.startswith("0x") else value
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise ValueError(f"{field_name}: not hex bytes32") from exc
    else:
        raise ValueError(f"{field_name}: bytes32 needs bytes or hex str")
    if len(raw) != 32:
        raise ValueError(f"{field_name}: bytes32 must be 32 bytes, "
                         f"got {len(raw)}")
    return raw


def _validate_int(typ: str, name: str, value: Any) -> int:
    if not typ.startswith("uint"):
        raise ValueError(f"unsupported int type {typ}")
    bits = int(typ[4:])
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}: {typ} needs int")
    if not (0 <= value < 2 ** bits):
        raise ValueError(f"{name}: {value} out of range for {typ}")
    if name in ("score", "quality") and not (0 <= value <= 100):
        raise ValueError(f"{name}: {value} out of 0..100 range")
    if name == "tier" and not (1 <= value <= 3):
        raise ValueError(f"tier: {value} out of 1..3 range")
    return value


def encode_schema_data(schema: ReputationSchema,
                       values: Dict[str, Any]) -> bytes:
    """ABI-encode ``values`` against ``schema``'s field layout.

    Fail-closed: wrong types, out-of-range ints, and score/tier bounds
    raise ``ValueError`` — nothing out-of-schema is ever signed.
    """
    types: List[str] = []
    encoded: List[Any] = []
    for typ, name in schema.fields:
        if name not in values:
            raise ValueError(f"missing field {name} for schema "
                             f"{schema.name}")
        value = values[name]
        if typ.startswith("uint"):
            encoded.append(_validate_int(typ, name, value))
        elif typ == "bytes32":
            encoded.append(_coerce_bytes32(value, name))
        elif typ == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{name}: bool needs bool")
            encoded.append(value)
        elif typ == "string":
            if not isinstance(value, str):
                raise ValueError(f"{name}: string needs str")
            encoded.append(value)
        elif typ == "address":
            if not is_address(value):
                raise ValueError(f"{name}: invalid address")
            encoded.append(to_checksum_address(value))
        else:
            raise ValueError(f"unsupported schema type {typ}")
        types.append(typ)
    return abi_encode(types, encoded)


def decode_schema_data(schema: ReputationSchema,
                       data: bytes) -> Dict[str, Any]:
    """Decode ABI ``data`` against ``schema``; raises on layout mismatch."""
    types = [t for t, _ in schema.fields]
    try:
        raw = abi_decode(types, data)
    except Exception as exc:
        raise ValueError(f"data does not match schema {schema.name}: "
                         f"{exc}") from exc
    out: Dict[str, Any] = {}
    for (typ, name), value in zip(schema.fields, raw):
        if typ == "bytes32":
            out[name] = "0x" + bytes(value).hex()
        elif typ == "address":
            out[name] = to_checksum_address(value)
        else:
            out[name] = value
    return out


# ---------------------------------------------------------------------------
# EIP-712 helpers
# ---------------------------------------------------------------------------

_ATTEST_TYPES = [
    {"name": "schema", "type": "bytes32"},
    {"name": "recipient", "type": "address"},
    {"name": "time", "type": "uint64"},
    {"name": "expirationTime", "type": "uint64"},
    {"name": "revocable", "type": "bool"},
    {"name": "refUID", "type": "bytes32"},
    {"name": "data", "type": "bytes"},
    {"name": "salt", "type": "bytes32"},
]
_DOMAIN_TYPES = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
_REVOKE_TYPES = [
    {"name": "uid", "type": "bytes32"},
]


def _eip712_envelope(chain_id: int, verifying_contract: str,
                     primary: str, message: Dict[str, Any],
                     extra_types: Optional[List[Dict[str, str]]] = None
                     ) -> Dict[str, Any]:
    types: Dict[str, Any] = {"EIP712Domain": _DOMAIN_TYPES}
    if extra_types is not None:
        types[primary] = extra_types
    else:
        types["Attest"] = _ATTEST_TYPES
    return {
        "types": types,
        "primaryType": primary,
        "domain": {
            "name": EAS_DOMAIN_NAME,
            "version": EAS_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": to_checksum_address(verifying_contract),
        },
        "message": message,
    }


def _as_bytes32(value: Any) -> bytes:
    if isinstance(value, bytes) and len(value) == 32:
        return value
    if isinstance(value, str):
        text = value[2:] if value.startswith("0x") else value
        raw = bytes.fromhex(text)
        if len(raw) == 32:
            return raw
    raise ValueError("expected 32-byte value")


def _typed_digest(envelope: Dict[str, Any]) -> bytes:
    """EIP-712 signing digest: keccak256(\\x19\\x01 || domain || struct)."""
    msg = encode_typed_data(full_message=envelope)
    return keccak(b"\x19" + bytes(msg.version) + bytes(msg.header)
                  + bytes(msg.body))


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------


@dataclass
class Attestation:
    """One offchain EAS-shaped reputation attestation.

    ``uid`` is a local tamper-evident identifier:
    ``keccak256(signing_digest || attester)``. It commits to every signed
    field, the attester, and the signing domain. It is NOT an on-chain
    EAS UID — nothing here is ever submitted to a chain.
    """

    schema_uid: str
    attester: str          # checksum address, recovered from signature
    recipient: str         # checksum address of the subject agent
    subject_label: str     # agent id bound to recipient ("" if unregistered)
    attester_label: str    # agent id bound to attester ("" if unregistered)
    time: int
    expiration_time: int
    revocable: bool
    ref_uid: str
    data: bytes = field(repr=False)
    salt: bytes = field(repr=False)
    signature: bytes = field(repr=False)
    uid: str = ""

    def payload_hash(self) -> str:
        """keccak256 of the ABI-encoded schema data."""
        return "0x" + keccak(self.data).hex()

    def summary(self) -> Tuple[str, str, str, str]:
        """CNP-2-compatible shape: (attester, subject, schema_id, payload_hash)."""
        return (self.attester, self.subject_label or self.recipient,
                self.schema_uid, self.payload_hash())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_uid": self.schema_uid,
            "attester": self.attester,
            "recipient": self.recipient,
            "subject_label": self.subject_label,
            "attester_label": self.attester_label,
            "time": self.time,
            "expiration_time": self.expiration_time,
            "revocable": self.revocable,
            "ref_uid": self.ref_uid,
            "data": "0x" + self.data.hex(),
            "salt": "0x" + self.salt.hex(),
            "signature": "0x" + self.signature.hex(),
            "uid": self.uid,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Attestation":
        def hx(key: str) -> bytes:
            text = d[key]
            return bytes.fromhex(text[2:] if text.startswith("0x") else text)

        return cls(
            schema_uid=d["schema_uid"],
            attester=to_checksum_address(d["attester"]),
            recipient=to_checksum_address(d["recipient"]),
            subject_label=str(d.get("subject_label", "")),
            attester_label=str(d.get("attester_label", "")),
            time=int(d["time"]),
            expiration_time=int(d["expiration_time"]),
            revocable=bool(d["revocable"]),
            ref_uid=d["ref_uid"],
            data=hx("data"),
            salt=hx("salt"),
            signature=hx("signature"),
            uid=d["uid"],
        )


@dataclass
class Revocation:
    """Signed revocation of one attestation UID (attester-only)."""

    uid: str
    revoker: str
    signature: bytes = field(repr=False)


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    attester: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class EASClient:
    """Offchain EAS attestation client: create/verify/revoke, no network.

    Agent identities are bound to Ethereum addresses through an explicit
    local registry (``register_agent``). The signing domain is
    offchain-local by default (``verifyingContract`` = zero address);
    cross-domain signatures are rejected at verification because recovery
    runs against this client's domain.
    """

    def __init__(self, chain_id: int = 1,
                 verifying_contract: str = OFFCHAIN_VERIFYING_CONTRACT,
                 clock_skew_sec: int = DEFAULT_CLOCK_SKEW_SEC) -> None:
        if chain_id < 0:
            raise ValueError("chain_id must be non-negative")
        if not is_address(verifying_contract):
            raise ValueError("verifying_contract must be an address")
        self.chain_id = chain_id
        self.verifying_contract = to_checksum_address(verifying_contract)
        self.clock_skew_sec = clock_skew_sec
        self._registry: Dict[str, str] = {}          # agent_id -> address
        self._reverse: Dict[str, str] = {}           # address -> agent_id
        self._seen: Dict[str, Attestation] = {}      # uid -> attestation
        self._revoked: Dict[str, Revocation] = {}    # uid -> revocation

    # -- identity registry -------------------------------------------------

    def register_agent(self, agent_id: str, address: str) -> str:
        """Bind ``agent_id`` to an Ethereum address (idempotent)."""
        agent_id = str(agent_id)
        if not agent_id:
            raise ValueError("agent_id must be non-empty")
        if not is_address(address):
            raise ValueError(f"invalid address for {agent_id}")
        checksum = to_checksum_address(address)
        self._registry[agent_id] = checksum
        self._reverse[checksum] = agent_id
        return checksum

    def address_of(self, agent_id: str) -> str:
        try:
            return self._registry[str(agent_id)]
        except KeyError as exc:
            raise ValueError(f"unknown agent_id: {agent_id}") from exc

    def label_of(self, address: str) -> str:
        return self._reverse.get(to_checksum_address(address), "")

    # -- digest / uid ------------------------------------------------------

    def _message(self, att: Attestation) -> Dict[str, Any]:
        return {
            "schema": _as_bytes32(att.schema_uid),
            "recipient": att.recipient,
            "time": att.time,
            "expirationTime": att.expiration_time,
            "revocable": att.revocable,
            "refUID": _as_bytes32(att.ref_uid),
            "data": att.data,
            "salt": _as_bytes32(att.salt),
        }

    def signing_digest(self, att: Attestation) -> bytes:
        envelope = _eip712_envelope(self.chain_id, self.verifying_contract,
                                    "Attest", self._message(att))
        return _typed_digest(envelope)

    @staticmethod
    def compute_uid(digest: bytes, attester: str) -> str:
        return "0x" + keccak(digest + bytes.fromhex(
            to_checksum_address(attester)[2:])).hex()

    # -- creation ----------------------------------------------------------

    def create_attestation(self, attester_key: str, subject_agent_id: str,
                           schema_uid: str, data: Dict[str, Any],
                           expiration_time: int = NO_EXPIRATION,
                           ref_uid: str = ZERO_UID,
                           revocable: Optional[bool] = None,
                           at: Optional[int] = None) -> Attestation:
        """Sign a new attestation for ``subject_agent_id``.

        ``data`` is validated and ABI-encoded against the registered
        schema before anything is signed (fail-closed).
        """
        schema = SCHEMAS.get(schema_uid)
        if schema is None:
            raise ValueError(f"unknown schema uid: {schema_uid}")
        recipient = self.address_of(subject_agent_id)
        try:
            account = Account.from_key(attester_key)
        except Exception as exc:
            raise ValueError("invalid attester private key") from exc
        attester = account.address
        encoded = encode_schema_data(schema, data)
        now = int(at) if at is not None else int(time.time())
        if expiration_time != NO_EXPIRATION and expiration_time <= now:
            raise ValueError("expiration_time must be in the future")
        att = Attestation(
            schema_uid=schema_uid,
            attester=attester,
            recipient=recipient,
            subject_label=subject_agent_id,
            attester_label=self.label_of(attester),
            time=now,
            expiration_time=int(expiration_time),
            revocable=schema.revocable if revocable is None else bool(revocable),
            ref_uid=ref_uid,
            data=encoded,
            salt=secrets.token_bytes(32),
            signature=b"",
        )
        envelope = _eip712_envelope(self.chain_id, self.verifying_contract,
                                    "Attest", self._message(att))
        att.signature = bytes(Account.sign_message(
            encode_typed_data(full_message=envelope),
            private_key=attester_key).signature)
        digest = self.signing_digest(att)
        att.uid = self.compute_uid(digest, attester)
        self._seen[att.uid] = att
        return att

    # -- verification ------------------------------------------------------

    def verify_attestation(self, att: Attestation,
                           now: Optional[int] = None
                           ) -> VerificationResult:
        """Verify an attestation; fail-closed with a machine-readable reason."""
        moment = int(now) if now is not None else int(time.time())
        schema = SCHEMAS.get(att.schema_uid)
        if schema is None:
            return VerificationResult(False, "unknown_schema_uid")
        for label, value in (("schema_uid", att.schema_uid),
                             ("ref_uid", att.ref_uid)):
            try:
                _as_bytes32(value)
            except ValueError:
                return VerificationResult(False, f"malformed_{label}")
        if len(att.salt) != 32:
            return VerificationResult(False, "malformed_salt")
        if len(att.signature) != 65:
            return VerificationResult(False, "malformed_signature")
        try:
            envelope = _eip712_envelope(self.chain_id,
                                        self.verifying_contract,
                                        "Attest", self._message(att))
            digest = _typed_digest(envelope)
            recovered = Account.recover_message(
                encode_typed_data(full_message=envelope),
                signature=att.signature)
        except Exception:
            return VerificationResult(False, "signature_recovery_failed")
        if recovered != att.attester:
            # Forged attestation: signer is not the claimed attester, or a
            # signature from another domain (chainId/contract) was replayed.
            return VerificationResult(False, "attester_mismatch",
                                      attester=recovered)
        if self.compute_uid(digest, att.attester) != att.uid:
            # Any field (data, time, salt, ...) was tampered with post-sign.
            return VerificationResult(False, "uid_mismatch")
        if att.time > moment + self.clock_skew_sec:
            return VerificationResult(False, "future_dated")
        if (att.expiration_time != NO_EXPIRATION
                and att.expiration_time <= moment):
            return VerificationResult(False, "expired")
        try:
            decode_schema_data(schema, att.data)
        except ValueError:
            return VerificationResult(False, "data_schema_mismatch")
        if att.uid in self._revoked:
            return VerificationResult(False, "revoked")
        return VerificationResult(True, "ok", attester=att.attester)

    # -- revocation --------------------------------------------------------

    def revoke(self, uid: str, revoker_key: str) -> Revocation:
        """Revoke a known attestation. Only the original attester may.

        The revocation itself is a signed ``Revoke(uid)`` statement under
        the same domain, so a third party cannot manufacture one.
        """
        att = self._seen.get(uid)
        if att is None:
            raise ValueError("cannot revoke unknown attestation uid")
        try:
            account = Account.from_key(revoker_key)
        except Exception as exc:
            raise ValueError("invalid revoker private key") from exc
        if account.address != att.attester:
            raise ValueError("only the original attester may revoke")
        envelope = _eip712_envelope(self.chain_id, self.verifying_contract,
                                    "Revoke", {"uid": _as_bytes32(uid)},
                                    extra_types=_REVOKE_TYPES)
        signature = bytes(Account.sign_message(
            encode_typed_data(full_message=envelope),
            private_key=revoker_key).signature)
        rev = Revocation(uid=uid, revoker=account.address,
                         signature=signature)
        self._revoked[uid] = rev
        return rev

    def verify_revocation(self, rev: Revocation) -> bool:
        try:
            envelope = _eip712_envelope(self.chain_id,
                                        self.verifying_contract,
                                        "Revoke",
                                        {"uid": _as_bytes32(rev.uid)},
                                        extra_types=_REVOKE_TYPES)
            recovered = Account.recover_message(
                encode_typed_data(full_message=envelope),
                signature=rev.signature)
        except Exception:
            return False
        return recovered == rev.revoker

    def is_revoked(self, uid: str) -> bool:
        return uid in self._revoked

    def get(self, uid: str) -> Optional[Attestation]:
        return self._seen.get(uid)


# ---------------------------------------------------------------------------
# Attestation graph
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    ok: bool
    reason: str


class AttestationGraph:
    """Offchain index of verified attestations + poisoning-resistant queries.

    Ingest is verify-first and idempotent: every edge in the graph passed
    full signature verification at ingest time. Queries additionally skip
    revoked or expired edges without mutating the index (use ``prune()``
    for explicit cleanup).
    """

    def __init__(self, client: EASClient,
                 max_per_pair: int = 5,
                 max_per_attester: int = 200,
                 per_attester_score_cap: int = 3,
                 allow_self_attestation: bool = False) -> None:
        if max_per_pair < 1 or max_per_attester < 1:
            raise ValueError("caps must be positive")
        self.client = client
        self.max_per_pair = max_per_pair
        self.max_per_attester = max_per_attester
        self.per_attester_score_cap = per_attester_score_cap
        self.allow_self_attestation = allow_self_attestation
        self._edges: Dict[str, Attestation] = {}  # uid -> attestation

    # -- ingest ------------------------------------------------------------

    def ingest(self, att: Attestation,
               now: Optional[int] = None) -> IngestResult:
        """Verify and index one attestation. Never raises on bad input."""
        result = self.client.verify_attestation(att, now=now)
        if not result.ok:
            return IngestResult(False, result.reason)
        if att.uid in self._edges:
            return IngestResult(False, "duplicate")
        if (not self.allow_self_attestation
                and att.attester == att.recipient):
            return IngestResult(False, "self_attestation")
        pair = sum(1 for e in self._edges.values()
                   if e.attester == att.attester
                   and e.recipient == att.recipient
                   and e.schema_uid == att.schema_uid)
        if pair >= self.max_per_pair:
            return IngestResult(False, "pair_cap_exceeded")
        per_attester = sum(1 for e in self._edges.values()
                           if e.attester == att.attester)
        if per_attester >= self.max_per_attester:
            return IngestResult(False, "attester_cap_exceeded")
        self._edges[att.uid] = att
        return IngestResult(True, "ok")

    def __len__(self) -> int:
        return len(self._edges)

    # -- liveness filter ---------------------------------------------------

    def _live(self, now: Optional[int] = None):
        moment = int(now) if now is not None else int(time.time())
        for edge in self._edges.values():
            if self.client.is_revoked(edge.uid):
                continue
            if (edge.expiration_time != NO_EXPIRATION
                    and edge.expiration_time <= moment):
                continue
            yield edge

    def prune(self, now: Optional[int] = None) -> int:
        """Drop revoked/expired edges. Returns the number removed."""
        live_uids = {e.uid for e in self._live(now=now)}
        dead = [uid for uid in self._edges if uid not in live_uids]
        for uid in dead:
            del self._edges[uid]
        return len(dead)

    # -- queries -----------------------------------------------------------

    @staticmethod
    def _subject(edge: Attestation) -> str:
        return edge.subject_label or edge.recipient

    @staticmethod
    def _attester_label(edge: Attestation) -> str:
        return edge.attester_label or edge.attester

    def incoming(self, subject: str,
                 schema_uid: Optional[str] = None,
                 now: Optional[int] = None) -> List[Attestation]:
        return [e for e in self._live(now=now)
                if self._subject(e) == subject
                and (schema_uid is None or e.schema_uid == schema_uid)]

    def outgoing(self, attester: str,
                 schema_uid: Optional[str] = None,
                 now: Optional[int] = None) -> List[Attestation]:
        return [e for e in self._live(now=now)
                if self._attester_label(e) == attester
                and (schema_uid is None or e.schema_uid == schema_uid)]

    def edges_between(self, a: str, b: str,
                      now: Optional[int] = None) -> List[Attestation]:
        return [e for e in self._live(now=now)
                if {self._attester_label(e), self._subject(e)} == {a, b}]

    def decode(self, att: Attestation) -> Dict[str, Any]:
        schema = SCHEMAS[att.schema_uid]
        return decode_schema_data(schema, att.data)

    def aggregate_vouch_score(self, subject: str,
                              now: Optional[int] = None) -> float:
        """Decentralized vouch score in [0, 1].

        Mean of vouch scores (0..100 -> 0..1), truncated to
        ``per_attester_score_cap`` attestations per attester so one
        actor flooding vouches cannot move the needle alone. Expired and
        revoked vouches are excluded.
        """
        vouches = self.incoming(subject, schema_uid=SCHEMA_VOUCH.uid,
                                now=now)
        if not vouches:
            return 0.0
        by_attester: Dict[str, List[float]] = {}
        for edge in vouches:
            score = float(self.decode(edge)["score"]) / 100.0
            by_attester.setdefault(self._attester_label(edge),
                                   []).append(score)
        capped: List[float] = []
        for scores in by_attester.values():
            capped.extend(sorted(scores, reverse=True)
                          [:self.per_attester_score_cap])
        return sum(capped) / len(capped)

    def attester_diversity(self, subject: str,
                           now: Optional[int] = None) -> int:
        """Distinct attesters vouching for ``subject`` (Sybil signal)."""
        return len({self._attester_label(e)
                    for e in self.incoming(
                        subject, schema_uid=SCHEMA_VOUCH.uid, now=now)})

    def vouch_path(self, source: str, target: str,
                   now: Optional[int] = None) -> List[str]:
        """Shortest directed vouch path source -> ... -> target (BFS).

        Edges follow attester -> recipient over vouch-schema edges.
        Empty list means no path.
        """
        adjacency: Dict[str, List[str]] = {}
        for edge in self._live(now=now):
            if edge.schema_uid != SCHEMA_VOUCH.uid:
                continue
            src = self._attester_label(edge)
            dst = self._subject(edge)
            adjacency.setdefault(src, []).append(dst)
        if source == target:
            return [source]
        visited = {source}
        queue: List[Tuple[str, List[str]]] = [(source, [source])]
        while queue:
            node, path = queue.pop(0)
            for nxt in adjacency.get(node, []):
                if nxt in visited:
                    continue
                if nxt == target:
                    return path + [nxt]
                visited.add(nxt)
                queue.append((nxt, path + [nxt]))
        return []

    def mutual_cycles(self, now: Optional[int] = None
                      ) -> List[Tuple[str, str]]:
        """A <-> B mutual-vouch pairs (reciprocal-vouch poisoning signal)."""
        pairs = set()
        for edge in self._live(now=now):
            if edge.schema_uid != SCHEMA_VOUCH.uid:
                continue
            a, b = self._attester_label(edge), self._subject(edge)
            if a == b:
                continue
            pairs.add((a, b))
        # Each mutual pair once, in canonical order.
        return sorted({tuple(sorted((a, b))) for (a, b) in pairs
                       if (b, a) in pairs})

    def top_subjects(self, n: int = 10,
                     now: Optional[int] = None) -> List[Tuple[str, float]]:
        """Subjects ranked by aggregate vouch score, descending."""
        subjects = {self._subject(e) for e in self._live(now=now)
                    if e.schema_uid == SCHEMA_VOUCH.uid}
        ranked = sorted(
            ((s, self.aggregate_vouch_score(s, now=now)) for s in subjects),
            key=lambda item: item[1], reverse=True)
        return ranked[:n]
