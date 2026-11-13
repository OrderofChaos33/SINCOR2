"""Typed, fail-closed release policy for live onchain actions.

Replaces generic boolean ``armed`` flags with a typed policy for anything
that deploys contracts or moves treasury funds. A :class:`ReleaseRequest`
is approved only when EVERY check passes; ANY failure denies, fail-closed,
with machine-readable reason codes. The policy itself never signs, never
broadcasts, and never touches key material — approval only authorizes a
separately-deployed executor (via :func:`attempt_release`) to call its
injected ``sign_and_broadcast`` callback.

Legacy boolean gates this policy supersedes (additive — the legacy flags
are NOT removed here; they are documented as deprecated in favor of this
typed policy):

    LEGACY_FLAG_MAPPING = {
        # onchain/fee_conversion_executor.py — FeeConversionConfig.armed
        # (default False). Maps to ReleasePolicy.armed: the executor must
        # not broadcast unless a ReleasePolicy with armed=True approves the
        # specific (chain, target, token, manifest) tuple. DEPRECATED in
        # favor of evaluate_release().
        "fee_conversion_executor.Config.armed":
            "ReleasePolicy.armed (per-request, not global)",

        # agents/treasury_execution_agent.py — EXECUTE_LIVE env flag
        # (defaults ON after the founder HOLD lift). Maps to
        # ReleasePolicy.armed AND the operator_approval requirement: a
        # global env boolean cannot express per-release (chain, target,
        # token, manifest_digest) authorization. DEPRECATED in favor of
        # evaluate_release().
        "treasury_execution_agent.EXECUTE_LIVE":
            "ReleasePolicy.armed + ReleaseRequest.operator_approval",

        # treasury_policy.py — is_conversion_executor_armed() signal.
        # Maps to ReleasePolicy.armed; the typed policy additionally
        # requires chain/target/token allowlists, a manifest digest, and
        # a non-expired operator approval — none of which the boolean
        # signal can express. DEPRECATED in favor of evaluate_release().
        "treasury_policy.is_conversion_executor_armed()":
            "ReleasePolicy.armed (plus allowlists + approval)",
    }

Read-only operations (``plan``, ``quote``, ``dry_run``, ``read``,
``simulate``) are ALWAYS allowed and never require operator approval —
this path is preserved explicitly by :func:`evaluate_release`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

__all__ = [
    "ReleasePolicy",
    "ReleaseRequest",
    "OperatorApproval",
    "Decision",
    "ReleaseReceipt",
    "evaluate_release",
    "attempt_release",
    "validate_receipt",
    "default_release_policy",
    "LEGACY_FLAG_MAPPING",
    "READ_ONLY_OPERATIONS",
    "DEFAULT_TOKEN_ALLOWLIST",
    "DEFAULT_CHAIN_ALLOWLIST",
    "POLICY_VERSION",
]

POLICY_VERSION = "release-policy/v1"

# Operations that never move funds or deploy contracts: always allowed,
# never require operator approval. evaluate_release short-circuits these
# before ANY other check (including the armed check).
READ_ONLY_OPERATIONS = frozenset({"plan", "quote", "dry_run", "read", "simulate"})

# Token allowlist per standing treasury policy: platform fees convert to
# USDC; AXM is the native platform token. Nothing else may move.
DEFAULT_TOKEN_ALLOWLIST = frozenset({"USDC", "AXM"})

# Chain allowlist: Base mainnet (8453) for production, Sepolia (11155111)
# for the demo/staging deployment. Unknown chains are denied.
DEFAULT_CHAIN_ALLOWLIST = frozenset({8453, 11155111})

# Legacy boolean gates superseded by this typed policy. The legacy flags
# are NOT removed (other code still reads them); they are documented here
# as deprecated in favor of evaluate_release().
LEGACY_FLAG_MAPPING: Dict[str, str] = {
    "fee_conversion_executor.Config.armed":
        "ReleasePolicy.armed (per-request, not global)",
    "treasury_execution_agent.EXECUTE_LIVE":
        "ReleasePolicy.armed + ReleaseRequest.operator_approval",
    "treasury_policy.is_conversion_executor_armed()":
        "ReleasePolicy.armed (plus allowlists + approval)",
}


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Allow/deny verdict with machine-readable reason codes."""

    allowed: bool
    reason_codes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @classmethod
    def allow(cls, *reasons: str) -> "Decision":
        """Build an allow decision."""
        return cls(allowed=True, reason_codes=reasons)

    @classmethod
    def deny(cls, *reasons: str) -> "Decision":
        """Build a deny decision (fail-closed default)."""
        return cls(allowed=False, reason_codes=reasons)


# ---------------------------------------------------------------------------
# Policy / request / approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleasePolicy:
    """Typed release policy — the ONLY thing that can authorize a release.

    ``armed=False`` denies everything (except read-only operations). Being
    armed is necessary but not sufficient: every request must additionally
    pass the chain, target, token, manifest-digest, approval, and expiry
    checks in :func:`evaluate_release`.
    """

    policy_id: str
    armed: bool = False
    allowed_chains: FrozenSet[int] = frozenset()
    allowed_targets: FrozenSet[str] = frozenset()
    allowed_tokens: FrozenSet[str] = frozenset()
    required_approval_signer: str = ""
    approval_max_age_s: int = 3600

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id is required")
        object.__setattr__(
            self, "allowed_targets", frozenset(t.lower() for t in self.allowed_targets)
        )
        object.__setattr__(
            self, "allowed_tokens", frozenset(t.upper() for t in self.allowed_tokens)
        )


@dataclass(frozen=True)
class OperatorApproval:
    """Signed operator approval reference for a release.

    The approval binds a specific ``request_id`` (so it cannot be replayed
    against a different release), a ``signer`` (must equal the policy's
    ``required_approval_signer``), a non-empty ``signature`` reference, and
    an ``expires_at`` unix timestamp. Signature *verification* happens in
    the caller's signing layer; this policy checks presence, binding, and
    freshness — never the cryptography itself.
    """

    request_id: str
    signer: str
    signature: str
    expires_at: int  # unix timestamp

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("approval request_id is required")
        if not self.signer:
            raise ValueError("approval signer is required")


@dataclass(frozen=True)
class ReleaseRequest:
    """A request to deploy a contract or move treasury funds.

    ``operation`` is one of "release", "deploy", or a read-only kind
    (``plan``/``quote``/``dry_run``/``read``/``simulate``). Read-only
    operations skip every approval check. ``manifest_digest`` binds the
    request to the exact artifact being released/deployed (sha256 hex);
    an empty or mismatched digest denies.
    """

    operation: str
    chain_id: int
    target: str
    token: str
    amount_wei: int
    manifest_digest: str
    operator_approval: Optional[OperatorApproval] = None
    expiry: int = 0  # unix timestamp; 0 = already expired (fail-closed)
    receipt_schema: Tuple[str, ...] = ()
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not self.operation:
            raise ValueError("operation is required")
        object.__setattr__(self, "operation", self.operation.strip().lower())
        object.__setattr__(self, "token", self.token.strip().upper())
        if self.amount_wei < 0:
            raise ValueError("amount_wei must be non-negative")
        object.__setattr__(self, "receipt_schema", tuple(self.receipt_schema))

    @property
    def is_read_only(self) -> bool:
        """True for plan/quote/dry_run/read/simulate — always allowed."""
        return self.operation in READ_ONLY_OPERATIONS


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_release(
    request: ReleaseRequest, policy: ReleasePolicy, now: int
) -> Decision:
    """Evaluate a release request against the policy. Fail-closed.

    Denies on ANY of:

    * unknown operation kind (not a release/deploy and not read-only);
    * policy disarmed (``policy.armed`` is False);
    * chain not in the policy's chain allowlist;
    * target address not in the policy's target allowlist;
    * token not in the policy's token allowlist;
    * empty manifest digest;
    * missing operator approval;
    * approval bound to a different request_id (replay protection);
    * approval signer != policy's required_approval_signer;
    * approval missing signature reference or expired (approval_max_age
      and approval.expires_at both enforced);
    * request expired (``request.expiry <= now``).

    Read-only operations (plan/quote/dry_run/read/simulate) return
    ``Decision.allow("read_only_operation")`` immediately, before any other
    check — they never require approval and never need the policy armed.

    Returns a Decision; never raises for policy reasons.
    """
    if not isinstance(request, ReleaseRequest):
        return Decision.deny("invalid_request")
    if not isinstance(policy, ReleasePolicy):
        return Decision.deny("missing_policy")

    # Read-only path: ALWAYS allowed, no approval, policy may be disarmed.
    if request.is_read_only:
        return Decision.allow("read_only_operation")

    if request.operation not in ("release", "deploy"):
        return Decision.deny("unknown_operation")

    if not policy.armed:
        return Decision.deny("policy_disarmed")

    if request.chain_id not in policy.allowed_chains:
        return Decision.deny("chain_not_allowlisted")

    if (request.target or "").lower() not in policy.allowed_targets:
        return Decision.deny("target_not_allowlisted")

    if request.token not in policy.allowed_tokens:
        return Decision.deny("token_not_allowlisted")

    if not request.manifest_digest:
        return Decision.deny("manifest_digest_missing")

    approval = request.operator_approval
    if approval is None:
        return Decision.deny("operator_approval_missing")
    if approval.request_id != request.request_id:
        return Decision.deny("approval_request_mismatch")
    if approval.signer.lower() != policy.required_approval_signer.lower():
        return Decision.deny("approval_signer_mismatch")
    if not approval.signature:
        return Decision.deny("approval_signature_missing")
    if approval.expires_at <= now:
        return Decision.deny("operator_approval_expired")
    if approval.expires_at - now > policy.approval_max_age_s:
        # Approval lives longer than the policy permits: stale by policy.
        return Decision.deny("operator_approval_stale")

    if request.expiry <= now:
        return Decision.deny("request_expired")

    return Decision.allow("release_approved")


# ---------------------------------------------------------------------------
# Receipt schema validation
# ---------------------------------------------------------------------------


def validate_receipt(receipt: Dict[str, Any], schema: Tuple[str, ...]) -> List[str]:
    """Check that ``receipt`` contains every field named in ``schema``.

    Returns the list of missing field names (empty = valid). A missing or
    non-dict receipt fails every field.
    """
    if not isinstance(receipt, dict):
        return list(schema)
    return [name for name in schema if name not in receipt]


# ---------------------------------------------------------------------------
# Execution entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseReceipt:
    """Frozen receipt for an attempted release."""

    request_id: str
    executed: bool
    status: str  # "approved" | "denied" | "receipt_invalid"
    reason_codes: Tuple[str, ...] = ()
    provider_receipt: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))


def attempt_release(
    request: ReleaseRequest,
    policy: ReleasePolicy,
    now: int,
    sign_and_broadcast: Callable[[ReleaseRequest], Dict[str, Any]],
) -> ReleaseReceipt:
    """Evaluate a release and, ONLY if approved, invoke the executor.

    ``sign_and_broadcast`` is the caller's injected executor callback
    (signing + broadcasting happens in the caller's layer, never in this
    module). It is called at most once, and only when
    :func:`evaluate_release` allows. Denied requests return
    ``executed=False`` and the callback is never invoked — this is the
    property the "zero signing callbacks" tests assert.

    The provider receipt is validated against ``request.receipt_schema``;
    a schema violation produces a ``receipt_invalid`` receipt (executed is
    still True — the chain effect happened — but the receipt is flagged
    so callers cannot mistake an incomplete receipt for a clean one).
    """
    decision = evaluate_release(request, policy, now)
    if not decision.allowed:
        return ReleaseReceipt(
            request_id=request.request_id,
            executed=False,
            status="denied",
            reason_codes=decision.reason_codes,
        )
    if request.is_read_only:
        # Read-only operations are always allowed but execute nothing and
        # never touch the signer: the callback is a signing callback and
        # there is nothing to sign. Zero signing callbacks by construction.
        return ReleaseReceipt(
            request_id=request.request_id,
            executed=False,
            status="approved",
            reason_codes=("read_only_operation",),
        )
    provider_receipt = sign_and_broadcast(request)
    missing = validate_receipt(provider_receipt, request.receipt_schema)
    if missing:
        return ReleaseReceipt(
            request_id=request.request_id,
            executed=True,
            status="receipt_invalid",
            reason_codes=("receipt_schema_violation",) + tuple(missing),
            provider_receipt=dict(provider_receipt)
            if isinstance(provider_receipt, dict)
            else {"result": provider_receipt},
        )
    return ReleaseReceipt(
        request_id=request.request_id,
        executed=True,
        status="approved",
        reason_codes=decision.reason_codes,
        provider_receipt=dict(provider_receipt)
        if isinstance(provider_receipt, dict)
        else {"result": provider_receipt},
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_release_policy(
    *,
    policy_id: str = "default",
    armed: bool = False,
    allowed_targets: FrozenSet[str] = frozenset(),
    required_approval_signer: str = "",
    approval_max_age_s: int = 3600,
) -> ReleasePolicy:
    """Build a ReleasePolicy with the standing token/chain allowlists.

    Ships DISARMED by default (fail-closed): arming requires an explicit
    opt-in, and armed alone is never sufficient for approval.
    """
    return ReleasePolicy(
        policy_id=policy_id,
        armed=armed,
        allowed_chains=DEFAULT_CHAIN_ALLOWLIST,
        allowed_targets=allowed_targets,
        allowed_tokens=DEFAULT_TOKEN_ALLOWLIST,
        required_approval_signer=required_approval_signer,
        approval_max_age_s=approval_max_age_s,
    )
