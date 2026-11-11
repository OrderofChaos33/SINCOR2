"""sincor-x402 — Verified x402 + MCP wrapper (reference implementation).

Turns a plain function, FastAPI endpoint, or MCP tool into an escrowed,
SIGIL-committed, SEAL-settled paid call: 402 → escrow → verify → release,
refund on fail/timeout. See README.md for the honesty notes: escrow here
runs through a pluggable PlatformClient whose only implementation in this
package is a labelled local reference; no Base-mainnet contract is
deployed by this package.
"""
from .buyer import VerifiedBuyer, VelocityCap
from .errors import (AcceptsMismatch, AllowanceInsufficient, AmbiguousPayment,
                     EscrowTimeout, IdempotencyConflict, MalformedChallenge,
                     PaymentCapExceeded, SettlementReverted, SincorX402Error,
                     UnsupportedPayment, VelocityCapExceeded, VerificationFailed)
from .facilitator import FacilitatorClient, FacilitatorResult, LocalFacilitator
from .hub_engine import (SEAL_SCHEME, HubSettlementEngine,
                         HubSettlementReceipt, SealEnvelope,
                         compute_sigil_commitment, to_bytes32)
from .mcp_adapter import VerifiedTool, verified_tool
from .platform import EscrowRecord, LocalReferencePlatform, PlatformClient
from .server import HandlerResponse, VerifiedEndpoint, verified_function
from .types import (USDC_BASE_ADDRESS, EscrowTerms, PaymentRequirement,
                    sha256_hex)
from .verifier import ReferenceVerifier, VerificationResult, Verifier

__version__ = "0.1.0"

__all__ = [
    "VerifiedEndpoint", "verified_function", "HandlerResponse",
    "VerifiedTool", "verified_tool",
    "VerifiedBuyer", "VelocityCap",
    "PaymentRequirement", "EscrowTerms", "USDC_BASE_ADDRESS", "sha256_hex",
    "PlatformClient", "LocalReferencePlatform", "EscrowRecord",
    "FacilitatorClient", "LocalFacilitator", "FacilitatorResult",
    "Verifier", "ReferenceVerifier", "VerificationResult",
    "HubSettlementEngine", "HubSettlementReceipt", "SealEnvelope",
    "SEAL_SCHEME", "compute_sigil_commitment", "to_bytes32",
    "SincorX402Error", "PaymentCapExceeded", "UnsupportedPayment",
    "MalformedChallenge", "AmbiguousPayment", "IdempotencyConflict",
    "VelocityCapExceeded", "AcceptsMismatch", "VerificationFailed",
    "EscrowTimeout", "AllowanceInsufficient", "SettlementReverted",
]
