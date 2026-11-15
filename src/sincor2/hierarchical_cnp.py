"""Hierarchical Contract-Net Protocol (CNP-10): composable subcontracting.

The flat CNP engine (``marketplace.contract_net.engine.ContractNetEngine``)
runs one sealed-bid Vickrey round per task. This module layers a *hierarchical
task graph* on top of it:

- an award winner may **subcontract** (part of) their task; the subcontract is
  itself awarded by reusing the flat CNP engine (recursively, one engine round
  per tree level);
- every award is bound into a **nested seal chain**: each subcontract's seal
  embeds the ``parentSealHash`` of its parent award's seal, so a verifier can
  walk from any leaf seal back to the genesis (depth-0) seal;
- **depth limits** and **cycle prevention** keep the graph a bounded tree.

Seals are EIP-712 typed envelopes signed with secp256k1 (HMAC exists as a
demo-only fallback and is default-deny, mirroring ``eip712.py``).

CNP-1 INTEGRATION POINT
-----------------------
CNP-1 (``SigilSeal``) was built on an unmerged branch and is intentionally
*not* imported here. The ``NestedSeal`` envelope below reuses the CNP-1
``SigilSeal`` field names and semantics verbatim where they overlap
(``agentDid`` / ``agentAddress`` / ``capabilities`` / ``expiry`` / ``nonce``)
and adds ``parentSealHash`` + ``taskId`` + ``depth``. When CNP-1 lands, the
migration is:

1. replace ``mint_nested_seal`` / ``verify_nested_seal`` internals with the
   CNP-1 ``SigilSeal`` type (its ``seal_struct_hash`` covers the identity
   fields; the hierarchical layer then signs ``keccak256(sealDigest ||
   parentSealHash || taskId || depth)`` — or, preferably, extends the
   ``SigilSeal`` typed-data with the three hierarchical fields);
2. route seal signing through the CNP-1 ``SealEnclave`` instead of raw
   ``sign_digest``;
3. keep ``verify_seal_chain`` / ``HierarchicalTaskGraph`` unchanged — they
   only depend on the ``NestedSeal`` *interface* (``seal_id``,
   ``parent_seal_hash``, ``depth``, ``agent_address``, ``expiry``), not on
   how the envelope is constructed.

The distinct ``primaryType`` name ``NestedSeal`` gives a distinct typehash,
so a ``NestedSeal`` can never be replayed as a ``SigilSeal``/``Bid`` and
vice versa (type-confusion across the typed-data shapes is structurally
impossible).

Security posture
----------------
- Iterative (never recursive) seal-chain walks with an absolute iteration
  cap, so a malicious registry cannot cause unbounded recursion / stack
  exhaustion.
- ``seal_id`` is *derived* (``keccak256`` of the EIP-712 digest), never
  self-declared: a seal cannot claim an arbitrary identity.
- Depth must decrease by exactly 1 at every chain step; the genesis seal
  must sit at depth 0 with ``parentSealHash == EMPTY_SEAL_HASH``. This
  defeats depth smuggling and seal-reference cycles (a cycle cannot satisfy
  strict depth decrease, and a visited-set breaks it regardless).
- Subcontracting rights bind to the parent award's winner
  (``require_winner_authorization``): only the winner of the parent task may
  mint the child seal. A forged child seal signed by anyone else is
  rejected even if its signature is cryptographically valid.
- Failed auctions (no winner) can never be sealed or subcontracted.
- Task ids are unique across the graph: the hierarchy is a tree, and a
  task can never become its own ancestor.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    AbstractSet,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from marketplace.contract_net.eip712 import (
    domain_separator,
    encode_address,
    encode_bytes32,
    encode_uint256,
    sign_digest,
    to_hex,
    verify_digest,
)
from marketplace.contract_net.engine import ContractNetEngine
from marketplace.contract_net.keccak import keccak256
from marketplace.contract_net.types import (
    DEFAULT_VERIFYING_CONTRACT,
    Award,
    ContractNetConfig,
    SigType,
    TaskSpec,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NESTED_SEAL_TYPE = (
    "NestedSeal(bytes32 parentSealHash,string agentDid,address agentAddress,"
    "string taskId,string[] capabilities,uint256 expiry,uint256 nonce,"
    "uint256 depth)"
)
NESTED_SEAL_TYPEHASH = keccak256(NESTED_SEAL_TYPE.encode("ascii"))

EMPTY_SEAL_HASH = "0x" + "00" * 32

#: Absolute ceiling on nesting depth. The walk-iteration cap and all
#: recursion guards derive from this; raising it needs a security review.
MAX_DEPTH_HARD_CAP = 8

#: Max capabilities per seal and max length/charset per capability.
MAX_CAPABILITIES = 16
_CAP_RE = re.compile(r"^[A-Za-z0-9:_.\-]{1,64}$")

#: ``did:sincor:<label>`` — same shape as CNP-1 SigilSeal DIDs.
_DID_RE = re.compile(r"^did:sincor:[A-Za-z0-9][A-Za-z0-9._\-]{0,62}$")
_DID_PREFIX = "did:sincor:"

MAX_TASK_ID_LEN = 128
DEFAULT_SEAL_TTL_SECONDS = 24 * 3600
MAX_SEAL_TTL_SECONDS = 30 * 24 * 3600


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HierarchicalCNPError(Exception):
    """Base error for hierarchical CNP failures."""


class SealError(HierarchicalCNPError):
    """A single nested seal is malformed, tampered, expired, or mis-signed."""


class SealChainError(SealError):
    """A seal chain fails to walk back to a valid genesis seal."""


class GraphError(HierarchicalCNPError):
    """A task-graph operation violates hierarchy invariants."""


class DepthLimitExceeded(GraphError):
    """A subcontract would exceed the configured maximum nesting depth."""


class CycleDetected(GraphError):
    """A subcontract would introduce a cycle into the task graph."""


class OrphanedSubcontract(GraphError):
    """A subcontract references a parent that is not a verified graph node."""


class AuthorizationError(GraphError):
    """The seal signer is not authorized to subcontract this task."""


class DuplicateSeal(GraphError):
    """A seal id is already registered in the graph."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HierarchicalConfig:
    """Tuning knobs for hierarchical CNP.

    ``max_depth`` is the number of subcontracting levels allowed *below* the
    root task (root = depth 0, so ``max_depth=3`` permits root → L1 → L2 →
    L3). Hard-capped at :data:`MAX_DEPTH_HARD_CAP`.
    """

    max_depth: int = 3
    #: Demo-only HMAC seals are refused unless this is True. Money-adjacent
    #: seals must use secp256k1.
    allow_hmac_seals: bool = False
    #: When True, a child seal must be signed by the parent task's award
    #: winner. This is the authorization binding that makes parent-chain
    #: references meaningful (anyone can *reference* a public parent seal
    #: hash; only the winner may *extend* the chain).
    require_winner_authorization: bool = True
    #: Optional allowlist of signer addresses (any case). Non-empty means
    #: fail-closed: every seal in a verified chain must be signed by a
    #: listed address.
    authorized_signers: Tuple[str, ...] = ()
    #: Default seal lifetime for graph-minted seals.
    default_seal_ttl_seconds: int = DEFAULT_SEAL_TTL_SECONDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_depth <= MAX_DEPTH_HARD_CAP:
            raise ValueError(
                f"max_depth must be in [1, {MAX_DEPTH_HARD_CAP}], got {self.max_depth!r}"
            )
        if self.default_seal_ttl_seconds <= 0 or self.default_seal_ttl_seconds > MAX_SEAL_TTL_SECONDS:
            raise ValueError("default_seal_ttl_seconds out of range")
        for addr in self.authorized_signers:
            _validate_address(addr)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_did(agent_did: str) -> str:
    if not isinstance(agent_did, str) or not _DID_RE.match(agent_did):
        raise SealError(
            f"invalid agent DID {agent_did!r}: must match did:sincor:<label>"
        )
    return agent_did


def _validate_address(addr: str) -> str:
    try:
        encode_address(addr)
    except ValueError as exc:
        raise SealError(f"invalid agent address {addr!r}") from exc
    return addr


def _validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not task_id or len(task_id) > MAX_TASK_ID_LEN:
        raise SealError(f"invalid task id {task_id!r}")
    return task_id


def _validate_capabilities(capabilities: Sequence[str]) -> Tuple[str, ...]:
    caps = tuple(capabilities or ())
    if len(caps) > MAX_CAPABILITIES:
        raise SealError(f"too many capabilities: {len(caps)} > {MAX_CAPABILITIES}")
    for cap in caps:
        if not isinstance(cap, str) or not _CAP_RE.match(cap):
            raise SealError(f"invalid capability {cap!r}")
    return caps


def _validate_bytes32_hex(value: str, field_name: str) -> str:
    try:
        encode_bytes32(value)
    except ValueError as exc:
        raise SealError(f"invalid {field_name} {value!r}: must be bytes32 hex") from exc
    return value


def _validate_uint256(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SealError(f"{field_name} must be an int, got {value!r}")
    if not 0 <= value < 1 << 256:
        raise SealError(f"{field_name} out of uint256 range")
    return value


def _validate_depth(depth: int, max_depth: int) -> int:
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise SealError(f"depth must be an int, got {depth!r}")
    if not 0 <= depth <= max_depth:
        raise SealError(f"depth {depth} out of range [0, {max_depth}]")
    return depth


def _normalize_seal_id(seal_id: str) -> str:
    return _validate_bytes32_hex(seal_id, "seal id").lower()


# ---------------------------------------------------------------------------
# EIP-712 encoding for NestedSeal
# ---------------------------------------------------------------------------


def _encode_string(value: str) -> bytes:
    if not isinstance(value, str):
        raise SealError(f"expected str, got {type(value).__name__}")
    return keccak256(value.encode("utf-8"))


def _encode_string_array(values: Sequence[str]) -> bytes:
    return keccak256(b"".join(_encode_string(v) for v in values))


def nested_seal_struct_hash(
    *,
    parent_seal_hash: str,
    agent_did: str,
    agent_address: str,
    task_id: str,
    capabilities: Sequence[str],
    expiry: int,
    nonce: int,
    depth: int,
) -> bytes:
    """EIP-712 struct hash for the ``NestedSeal`` typed data.

    Every field is validated first: hostile inputs fail as :class:`SealError`,
    never as a bare ``ValueError`` from the ABI encoders.
    """
    return keccak256(
        NESTED_SEAL_TYPEHASH
        + encode_bytes32(_validate_bytes32_hex(parent_seal_hash, "parent seal hash"))
        + _encode_string(_validate_did(agent_did))
        + encode_address(_validate_address(agent_address))
        + _encode_string(_validate_task_id(task_id))
        + _encode_string_array(_validate_capabilities(capabilities))
        + encode_uint256(_validate_uint256(expiry, "expiry"))
        + encode_uint256(_validate_uint256(nonce, "nonce"))
        + encode_uint256(_validate_depth(depth, MAX_DEPTH_HARD_CAP))
    )


def nested_seal_domain_separator(net_config: ContractNetConfig) -> bytes:
    """Reuse the ContractNet EIP-712 domain (chainId-bound)."""
    return domain_separator(net_config)


def nested_seal_digest(
    net_config: ContractNetConfig,
    *,
    parent_seal_hash: str,
    agent_did: str,
    agent_address: str,
    task_id: str,
    capabilities: Sequence[str],
    expiry: int,
    nonce: int,
    depth: int,
) -> bytes:
    """The canonical EIP-712 digest: ``keccak256("\\x19\\x01" || domain || structHash)``."""
    return keccak256(
        b"\x19\x01"
        + nested_seal_domain_separator(net_config)
        + nested_seal_struct_hash(
            parent_seal_hash=parent_seal_hash,
            agent_did=agent_did,
            agent_address=agent_address,
            task_id=task_id,
            capabilities=capabilities,
            expiry=expiry,
            nonce=nonce,
            depth=depth,
        )
    )


def nested_seal_typed_data(
    net_config: ContractNetConfig,
    *,
    parent_seal_hash: str,
    agent_did: str,
    agent_address: str,
    task_id: str,
    capabilities: Sequence[str],
    expiry: int,
    nonce: int,
    depth: int,
) -> Dict[str, object]:
    """Full typed-data payload (used by eth_account recovery and audits)."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "NestedSeal": [
                {"name": "parentSealHash", "type": "bytes32"},
                {"name": "agentDid", "type": "string"},
                {"name": "agentAddress", "type": "address"},
                {"name": "taskId", "type": "string"},
                {"name": "capabilities", "type": "string[]"},
                {"name": "expiry", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "depth", "type": "uint256"},
            ],
        },
        "primaryType": "NestedSeal",
        "domain": {
            "name": net_config.domain_name,
            "version": net_config.domain_version,
            "chainId": net_config.chain_id,
            "verifyingContract": net_config.verifying_contract,
        },
        "message": {
            "parentSealHash": parent_seal_hash,
            "agentDid": agent_did,
            "agentAddress": agent_address,
            "taskId": task_id,
            "capabilities": list(capabilities),
            "expiry": str(expiry),
            "nonce": str(nonce),
            "depth": str(depth),
        },
    }


# ---------------------------------------------------------------------------
# NestedSeal: mint + verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NestedSeal:
    """A signed hierarchical seal.

    ``seal_id`` is *derived* (``to_hex`` of the EIP-712 digest) — it is
    recomputed on every verification and can never be self-declared.
    """

    seal_id: str
    parent_seal_hash: str
    agent_did: str
    agent_address: str
    task_id: str
    capabilities: Tuple[str, ...]
    expiry: int
    nonce: int
    depth: int
    digest: str
    signature: str
    sig_type: str
    typed_data: Dict[str, object] = field(default_factory=dict, compare=False)


def mint_nested_seal(
    net_config: ContractNetConfig,
    *,
    agent_did: str,
    agent_address: str,
    task_id: str,
    capabilities: Sequence[str] = (),
    expiry: int,
    nonce: int,
    depth: int,
    parent_seal_hash: str = EMPTY_SEAL_HASH,
    private_key: str = "",
    signing_secret: str = "",
    max_depth: int = MAX_DEPTH_HARD_CAP,
    now: Optional[int] = None,
) -> NestedSeal:
    """Mint and sign a ``NestedSeal``.

    All fields are validated before signing; the seal id is derived from the
    digest. ``expiry`` must be in the future and within
    :data:`MAX_SEAL_TTL_SECONDS` of ``now`` (fail closed on indefinite seals).
    """
    now = int(now if now is not None else time.time())
    _validate_did(agent_did)
    _validate_address(agent_address)
    _validate_task_id(task_id)
    caps = _validate_capabilities(capabilities)
    _validate_bytes32_hex(parent_seal_hash, "parent seal hash")
    _validate_depth(depth, max_depth)
    expiry = _validate_uint256(expiry, "expiry")
    if expiry <= now:
        raise SealError("seal expiry must be in the future")
    if expiry - now > MAX_SEAL_TTL_SECONDS:
        raise SealError("seal TTL exceeds the 30-day cap")
    nonce = _validate_uint256(nonce, "nonce")

    digest = nested_seal_digest(
        net_config,
        parent_seal_hash=parent_seal_hash,
        agent_did=agent_did,
        agent_address=agent_address,
        task_id=task_id,
        capabilities=caps,
        expiry=expiry,
        nonce=nonce,
        depth=depth,
    )
    signature, sig_type = sign_digest(
        digest, private_key=private_key, signing_secret=signing_secret
    )
    typed_data = nested_seal_typed_data(
        net_config,
        parent_seal_hash=parent_seal_hash,
        agent_did=agent_did,
        agent_address=agent_address,
        task_id=task_id,
        capabilities=caps,
        expiry=expiry,
        nonce=nonce,
        depth=depth,
    )
    return NestedSeal(
        seal_id=to_hex(digest),
        parent_seal_hash=parent_seal_hash,
        agent_did=agent_did,
        agent_address=agent_address,
        task_id=task_id,
        capabilities=caps,
        expiry=expiry,
        nonce=nonce,
        depth=depth,
        digest=to_hex(digest),
        signature=signature,
        sig_type=sig_type,
        typed_data=typed_data,
    )


def verify_nested_seal(
    seal: NestedSeal,
    net_config: ContractNetConfig,
    *,
    now: int,
    allow_hmac: bool = False,
    max_depth: int = MAX_DEPTH_HARD_CAP,
    hmac_secret: str = "",
) -> None:
    """Verify one seal. Raises :class:`SealError` with a reason on failure.

    Checks, in order: field validity → digest recomputation (tamper-evident)
    → seal-id derivation → signature (secp256k1 recovery must match
    ``agent_address``; HMAC is default-deny) → expiry → depth bounds.
    """
    if not isinstance(seal, NestedSeal):
        raise SealError(f"expected NestedSeal, got {type(seal).__name__}")
    # Field validity first: malformed fields fail before any crypto.
    _validate_did(seal.agent_did)
    _validate_address(seal.agent_address)
    _validate_task_id(seal.task_id)
    _validate_capabilities(seal.capabilities)
    _validate_bytes32_hex(seal.parent_seal_hash, "parent seal hash")
    _validate_depth(seal.depth, max_depth)
    # Range-check before any encoding: a hostile seal must fail as SealError,
    # never as a bare ValueError from the ABI encoder.
    _validate_uint256(seal.expiry, "expiry")
    _validate_uint256(seal.nonce, "nonce")

    expected_digest = nested_seal_digest(
        net_config,
        parent_seal_hash=seal.parent_seal_hash,
        agent_did=seal.agent_did,
        agent_address=seal.agent_address,
        task_id=seal.task_id,
        capabilities=seal.capabilities,
        expiry=seal.expiry,
        nonce=seal.nonce,
        depth=seal.depth,
    )
    if seal.digest.lower() != to_hex(expected_digest).lower():
        raise SealError("digest mismatch: seal fields were tampered with")
    if seal.seal_id.lower() != to_hex(expected_digest).lower():
        # seal_id is derived, never trusted as declared.
        raise SealError("seal id is not derived from the seal digest")

    if seal.sig_type == SigType.HMAC.value and not allow_hmac:
        raise SealError("HMAC seals are demo-only and not allowed here")
    digest_bytes = bytes.fromhex(expected_digest.hex())
    if not verify_digest(
        digest_bytes,
        seal.signature,
        sig_type=seal.sig_type,
        expected_address=seal.agent_address,
        signing_secret=hmac_secret,
        typed_data=seal.typed_data or None,
    ):
        raise SealError("bad signature")

    if seal.expiry <= now:
        raise SealError("seal expired")


def verify_seal_chain(
    leaf: NestedSeal,
    registry: Mapping[str, NestedSeal],
    net_config: ContractNetConfig,
    *,
    now: int,
    max_depth: int,
    allow_hmac: bool = False,
    authorized_signers: AbstractSet[str] = frozenset(),
    hmac_secret_resolver: Optional[Callable[[str], str]] = None,
) -> List[NestedSeal]:
    """Walk a seal chain from ``leaf`` back to the genesis seal.

    Iterative with an absolute iteration cap (no recursion → no stack
    exhaustion from a hostile registry). Returns the chain root-first.

    Each step requires: the seal verifies on its own; an optional
    fail-closed signer allowlist; the parent is registered under the exact
    referenced hash; depth decreases by exactly 1 (defeats depth smuggling
    and reference cycles); the genesis seal sits at depth 0 with an empty
    parent hash. A repeated seal id is a cycle and is rejected.
    """
    allow = {addr.lower() for addr in authorized_signers}
    # Normalize registry keys once; lookups are case-insensitive.
    normalized = {_normalize_seal_id(k): v for k, v in registry.items()}
    iteration_cap = 4 * (max_depth + 2)

    chain: List[NestedSeal] = []
    visited: set[str] = set()
    current = leaf
    steps = 0
    while True:
        steps += 1
        if steps > iteration_cap:
            raise SealChainError(
                f"chain walk exceeded iteration cap ({iteration_cap}): "
                "possible cycle or hostile registry"
            )
        secret = ""
        if current.sig_type == SigType.HMAC.value and hmac_secret_resolver is not None:
            secret = hmac_secret_resolver(current.agent_address) or ""
        try:
            verify_nested_seal(
                current,
                net_config,
                now=now,
                allow_hmac=allow_hmac,
                max_depth=max_depth,
                hmac_secret=secret,
            )
        except SealError as exc:
            # A bad link breaks the chain: surface it as a chain error with
            # the depth of the offending link for auditability.
            raise SealChainError(
                f"chain link at depth {current.depth} is invalid: {exc}"
            ) from exc
        if allow and current.agent_address.lower() not in allow:
            raise SealChainError(
                f"signer {current.agent_address} is not an authorized sealer"
            )
        current_id = _normalize_seal_id(current.seal_id)
        if current_id in visited:
            raise SealChainError("seal chain contains a cycle")
        visited.add(current_id)
        chain.append(current)

        if current.depth == 0:
            if current.parent_seal_hash.lower() != EMPTY_SEAL_HASH.lower():
                raise SealChainError(
                    "genesis seal must reference the empty parent hash"
                )
            chain.reverse()
            return chain
        parent = normalized.get(current.parent_seal_hash.lower())
        if parent is None:
            raise SealChainError(
                f"parent seal {current.parent_seal_hash} is not registered: "
                "broken parent chain"
            )
        if parent.depth != current.depth - 1:
            raise SealChainError(
                f"depth must decrease by exactly 1 per level "
                f"(got parent depth {parent.depth} for child depth {current.depth})"
            )
        current = parent


# ---------------------------------------------------------------------------
# Hierarchical task graph
# ---------------------------------------------------------------------------


@dataclass
class TaskNode:
    node_id: str
    task: TaskSpec
    award: Award
    seal: NestedSeal
    parent_node_id: str  # "" for the root
    depth: int
    child_node_ids: List[str] = field(default_factory=list)


#: Callable resolving an agent id to ``(private_key, signing_secret)`` for
#: seal minting. Production wiring goes through the CNP-1 SealEnclave;
#: tests supply an in-memory table.
SealKeyResolver = Callable[[str], Tuple[str, str]]


class HierarchicalTaskGraph:
    """A bounded tree of CNP tasks with nested seal chains.

    The flat :class:`ContractNetEngine` is reused recursively: every tree
    level runs its own sealed-bid Vickrey round via ``engine.run``. The
    graph itself only enforces hierarchy invariants (depth, acyclicity,
    seal-chain integrity, winner authorization) — it never touches the
    money path beyond calling the engine.
    """

    def __init__(
        self,
        engine: ContractNetEngine,
        config: Optional[HierarchicalConfig] = None,
        key_resolver: Optional[SealKeyResolver] = None,
    ) -> None:
        self.engine = engine
        self.config = config or HierarchicalConfig()
        self._key_resolver = key_resolver
        self._nodes: Dict[str, TaskNode] = {}
        self._seals: Dict[str, NestedSeal] = {}  # normalized seal_id -> seal
        self._task_index: Dict[str, str] = {}  # task_id -> node_id
        self._seal_nonces: Dict[str, int] = {}
        # HMAC secrets used when the graph itself minted seals (demo/test
        # path). Production uses secp256k1, so this stays empty there.
        self._hmac_secrets: Dict[str, str] = {}

    # -- introspection ----------------------------------------------------

    def __len__(self) -> int:
        return len(self._nodes)

    def get_node(self, node_id: str) -> TaskNode:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise GraphError(f"unknown node {node_id!r}") from None

    def node_ids(self) -> List[str]:
        return list(self._nodes)

    def lineage(self, node_id: str) -> List[TaskNode]:
        """Root-first path from the genesis task to ``node_id`` (iterative)."""
        path: List[TaskNode] = []
        seen: set[str] = set()
        current_id = node_id
        guard = MAX_DEPTH_HARD_CAP + 4
        while current_id:
            if current_id in seen:
                raise GraphError("task graph contains a cycle")
            if len(path) > guard:
                raise GraphError("lineage walk exceeded depth guard")
            seen.add(current_id)
            node = self.get_node(current_id)
            path.append(node)
            current_id = node.parent_node_id
        path.reverse()
        return path

    # -- seal bookkeeping -------------------------------------------------

    def next_seal_nonce(self, agent_address: str) -> int:
        key = agent_address.lower()
        value = self._seal_nonces.get(key, 0) + 1
        self._seal_nonces[key] = value
        return value

    def _register_seal(self, seal: NestedSeal) -> None:
        seal_id = _normalize_seal_id(seal.seal_id)
        if seal_id in self._seals:
            raise DuplicateSeal(f"seal {seal.seal_id} is already registered")
        self._seals[seal_id] = seal

    def _default_hmac_secret_resolver(self, address: str) -> str:
        return self._hmac_secrets.get(address.lower(), "")

    def _chain_kwargs(self, hmac_secret_resolver=None) -> dict:
        return {
            "allow_hmac": self.config.allow_hmac_seals,
            "authorized_signers": frozenset(self.config.authorized_signers),
            "hmac_secret_resolver": hmac_secret_resolver
            or self._default_hmac_secret_resolver,
        }

    def verify_node_chain(self, node: TaskNode, *, now: int,
                          hmac_secret_resolver=None) -> List[NestedSeal]:
        """Re-verify a node's full seal chain against the graph registry."""
        return verify_seal_chain(
            node.seal,
            self._seals,
            self.engine.config,
            now=now,
            max_depth=self.config.max_depth,
            **self._chain_kwargs(hmac_secret_resolver),
        )

    # -- low-level registration (no engine run) ----------------------------

    def register_root(
        self,
        task: TaskSpec,
        award: Award,
        seal: NestedSeal,
        *,
        now: int,
        hmac_secret_resolver: Optional[Callable[[str], str]] = None,
    ) -> TaskNode:
        """Register a genesis task with its depth-0 seal. No auction is run."""
        return self._register_node(
            task, award, seal, parent_node_id="",
            now=now, hmac_secret_resolver=hmac_secret_resolver,
        )

    def register_subcontract(
        self,
        parent_node_id: str,
        task: TaskSpec,
        award: Award,
        seal: NestedSeal,
        *,
        now: int,
        hmac_secret_resolver: Optional[Callable[[str], str]] = None,
    ) -> TaskNode:
        """Register a subcontract node without running an auction.

        Used by adversarial tests and by external award pipelines; the
        high-level :meth:`subcontract` runs the engine and delegates here.
        """
        return self._register_node(
            task, award, seal, parent_node_id=parent_node_id,
            now=now, hmac_secret_resolver=hmac_secret_resolver,
        )

    def _register_node(
        self,
        task: TaskSpec,
        award: Award,
        seal: NestedSeal,
        *,
        parent_node_id: str,
        now: int,
        hmac_secret_resolver: Optional[Callable[[str], str]] = None,
    ) -> TaskNode:
        # Fail fast on seal replay: a seal id may only ever be registered once.
        if _normalize_seal_id(seal.seal_id) in self._seals:
            raise DuplicateSeal(f"seal {seal.seal_id} is already registered")
        if not award.winner_id:
            raise GraphError("cannot seal an auction with no winner")
        if award.task_id != task.task_id:
            raise GraphError("award task id does not match the registered task")
        if seal.task_id != task.task_id:
            # The digest binds taskId, but nothing else compares the seal's
            # task to the node's task — enforce it here so a seal minted for
            # one task can never be attached to another.
            raise SealChainError("seal task id does not match the registered task")
        duplicate_task = task.task_id in self._task_index

        if parent_node_id:
            parent = self.get_node(parent_node_id)  # KeyError -> GraphError
            depth = parent.depth + 1
            if depth > self.config.max_depth:
                raise DepthLimitExceeded(
                    f"subcontract depth {depth} exceeds max_depth {self.config.max_depth}"
                )
            # Cycle prevention: the new task id must not appear anywhere on
            # the ancestor chain (a task can never become its own ancestor).
            # This check runs before the global-duplicate check so ancestor
            # reuse reports CycleDetected, not a generic duplicate.
            ancestor_task_ids = {node.task.task_id for node in self.lineage(parent_node_id)}
            if task.task_id in ancestor_task_ids:
                raise CycleDetected(
                    f"task {task.task_id!r} is already an ancestor: cycle rejected"
                )
            if duplicate_task:
                raise GraphError(f"task {task.task_id!r} is already in the graph")
            # The parent's own chain must still be intact *right now* before
            # it can authorize a child (orphaned/forged parents rejected).
            try:
                self.verify_node_chain(parent, now=now,
                                       hmac_secret_resolver=hmac_secret_resolver)
            except SealChainError as exc:
                raise OrphanedSubcontract(
                    f"parent node {parent_node_id} seal chain is broken: {exc}"
                ) from exc
            # The child seal must extend *this* parent's seal, at the next depth.
            if seal.parent_seal_hash.lower() != parent.seal.seal_id.lower():
                raise SealChainError(
                    "child seal does not reference the parent node's seal"
                )
            if seal.depth != depth:
                raise SealChainError(
                    f"child seal depth {seal.depth} != expected {depth}"
                )
            # Only the parent award's winner may authorize the subcontract.
            if self.config.require_winner_authorization and \
                    seal.agent_address.lower() != (parent.award.winner_wallet or "").lower():
                raise AuthorizationError(
                    "child seal must be signed by the parent task's award winner"
                )
        else:
            if duplicate_task:
                raise GraphError(f"task {task.task_id!r} is already in the graph")
            depth = 0
            if seal.depth != 0:
                raise SealChainError("genesis seal must sit at depth 0")
            if seal.parent_seal_hash.lower() != EMPTY_SEAL_HASH.lower():
                raise SealChainError("genesis seal must reference the empty parent hash")

        # The full chain (leaf → genesis) must verify against the registry.
        # For a root this is a 1-link chain; the registry gains the new seal
        # only after the chain verifies, so self-reference is impossible.
        chain = verify_seal_chain(
            seal,
            self._seals,
            self.engine.config,
            now=now,
            max_depth=self.config.max_depth,
            **self._chain_kwargs(hmac_secret_resolver),
        )
        if chain[-1].seal_id.lower() != seal.seal_id.lower():
            raise SealChainError("chain leaf mismatch (internal error)")
        if len(chain) - 1 != seal.depth:
            raise SealChainError(
                f"chain length {len(chain)} inconsistent with seal depth {seal.depth}"
            )

        self._register_seal(seal)
        node_id = f"htn-{uuid.uuid4().hex[:12]}"
        node = TaskNode(
            node_id=node_id,
            task=task,
            award=award,
            seal=seal,
            parent_node_id=parent_node_id,
            depth=depth,
        )
        self._nodes[node_id] = node
        self._task_index[task.task_id] = node_id
        if parent_node_id:
            self._nodes[parent_node_id].child_node_ids.append(node_id)
        return node

    # -- high-level flows (engine runs the auction) ------------------------

    def _resolve_keys(self, agent_id: str) -> Tuple[str, str]:
        if self._key_resolver is None:
            raise GraphError(
                "no seal key resolver configured: use register_root / "
                "register_subcontract with explicit seals"
            )
        return self._key_resolver(agent_id)

    def _mint_graph_seal(
        self,
        *,
        winner_agent_id: str,
        winner_wallet: str,
        task_id: str,
        capabilities: Sequence[str],
        depth: int,
        parent_seal_hash: str,
        now: int,
        ttl_seconds: int,
    ) -> NestedSeal:
        private_key, signing_secret = self._resolve_keys(winner_agent_id)
        if signing_secret:
            # Remember demo-path secrets so the graph can verify seals it minted.
            self._hmac_secrets[winner_wallet.lower()] = signing_secret
        return mint_nested_seal(
            self.engine.config,
            agent_did=f"{_DID_PREFIX}{winner_agent_id}",
            agent_address=winner_wallet,
            task_id=task_id,
            capabilities=capabilities,
            expiry=now + ttl_seconds,
            nonce=self.next_seal_nonce(winner_wallet),
            depth=depth,
            parent_seal_hash=parent_seal_hash,
            private_key=private_key,
            signing_secret=signing_secret,
            max_depth=self.config.max_depth,
            now=now,
        )

    def run_root(
        self,
        task: TaskSpec,
        agents: Sequence[AgentProfile],
        *,
        capabilities: Sequence[str] = ("subcontract:award",),
        now: Optional[int] = None,
        seed: Optional[int] = None,
        ttl_seconds: Optional[int] = None,
        **run_kwargs,
    ) -> TaskNode:
        """Run the genesis auction via the flat CNP engine and seal it."""
        now = int(now if now is not None else time.time())
        award = self.engine.run(task, agents, seed=seed, now=now, **run_kwargs)
        if not award.winner_id:
            raise GraphError(f"root auction for {task.task_id!r} failed: no winner")
        seal = self._mint_graph_seal(
            winner_agent_id=award.winner_id,
            winner_wallet=award.winner_wallet,
            task_id=task.task_id,
            capabilities=capabilities,
            depth=0,
            parent_seal_hash=EMPTY_SEAL_HASH,
            now=now,
            ttl_seconds=ttl_seconds or self.config.default_seal_ttl_seconds,
        )
        return self.register_root(task, award, seal, now=now)

    def subcontract(
        self,
        parent_node_id: str,
        task: TaskSpec,
        agents: Sequence[AgentProfile],
        *,
        capabilities: Sequence[str] = ("subcontract:award",),
        now: Optional[int] = None,
        seed: Optional[int] = None,
        ttl_seconds: Optional[int] = None,
        **run_kwargs,
    ) -> TaskNode:
        """Award a subcontract by reusing the flat CNP engine recursively.

        Runs one sealed-bid Vickrey round for the child task, mints the
        child seal (signed by the *parent* award's winner, chaining to the
        parent seal), and registers the node. All hierarchy invariants are
        enforced by :meth:`register_subcontract`.
        """
        now = int(now if now is not None else time.time())
        parent = self.get_node(parent_node_id)
        depth = parent.depth + 1
        if depth > self.config.max_depth:
            raise DepthLimitExceeded(
                f"subcontract depth {depth} exceeds max_depth {self.config.max_depth}"
            )
        award = self.engine.run(task, agents, seed=seed, now=now, **run_kwargs)
        if not award.winner_id:
            raise GraphError(f"subcontract auction for {task.task_id!r} failed: no winner")
        # The subcontract is authorized by the parent award's winner: the
        # child seal is minted in the parent winner's name.
        seal = self._mint_graph_seal(
            winner_agent_id=parent.award.winner_id,
            winner_wallet=parent.award.winner_wallet,
            task_id=task.task_id,
            capabilities=capabilities,
            depth=depth,
            parent_seal_hash=parent.seal.seal_id,
            now=now,
            ttl_seconds=ttl_seconds or self.config.default_seal_ttl_seconds,
        )
        return self.register_subcontract(
            parent_node_id, task, award, seal, now=now
        )

    # -- whole-graph audit --------------------------------------------------

    def verify_graph(self, *, now: int,
                     hmac_secret_resolver: Optional[Callable[[str], str]] = None) -> int:
        """Audit every node: seal chains, depth consistency, tree structure.

        Returns the number of nodes verified. Raises on the first violation.
        Iterative — safe against hostile graph shapes.
        """
        verified = 0
        for node_id, node in self._nodes.items():
            chain = self.verify_node_chain(
                node, now=now, hmac_secret_resolver=hmac_secret_resolver
            )
            if len(chain) - 1 != node.depth:
                raise GraphError(
                    f"node {node_id}: chain length inconsistent with depth"
                )
            if node.parent_node_id:
                parent = self.get_node(node.parent_node_id)
                if node.depth != parent.depth + 1:
                    raise GraphError(f"node {node_id}: depth inconsistent with parent")
                if node.seal.parent_seal_hash.lower() != parent.seal.seal_id.lower():
                    raise GraphError(f"node {node_id}: parent seal reference broken")
                if node_id not in parent.child_node_ids:
                    raise GraphError(f"node {node_id}: missing from parent's child list")
            else:
                if node.depth != 0:
                    raise GraphError(f"root node {node_id} has nonzero depth")
            verified += 1
        return verified
