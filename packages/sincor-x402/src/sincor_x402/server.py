"""Server core — framework-agnostic verified-call handler.

One call through a wrapped endpoint:

    request ──no payment──▶ 402 + accepts[] (server's OWN terms) + escrow terms
    request ──X-PAYMENT───▶ accepts[] hedge ─▶ facilitator verify (with the
                            server's OWN requirement) ─▶ escrow hold ─▶
                            work executes (timeout-bounded) ─▶ result
                            committed + verified against the spec ─▶
                            SEAL release (pass) / refund (fail or timeout)

The accepts[] hedge (live dev-watch finding): a malicious or buggy client
can echo back *rewritten* terms in its payment payload (lower price,
swapped payTo). The server therefore NEVER trusts the client's claimed
terms: it matches the claim against its own retained accepts[] entry on
scheme/network/amount/asset/payTo/timeout, and passes ITS OWN retained
``PaymentRequirement`` object to the facilitator.
"""
from __future__ import annotations

import base64
import concurrent.futures
import json
from dataclasses import dataclass
from typing import Any, Callable

from .errors import AcceptsMismatch, MalformedChallenge
from .facilitator import FacilitatorClient, LocalFacilitator
from .platform import LocalReferencePlatform, PlatformClient
from .types import EscrowTerms, PaymentRequirement
from .verifier import ReferenceVerifier, Verifier


@dataclass
class HandlerResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]


def decode_payment_header(raw: str) -> dict[str, Any]:
    """Decode an X-PAYMENT header: raw JSON or base64(JSON) (x402 uses both)."""
    raw = (raw or "").strip()
    if not raw:
        raise MalformedChallenge("empty payment header")
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:
        raise MalformedChallenge("payment header is not JSON or base64-JSON") from exc


class VerifiedEndpoint:
    """Wraps one callable with escrow + commitment + verified settlement."""

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        requirement: PaymentRequirement,
        spec: dict[str, Any],
        platform: PlatformClient | None = None,
        facilitator: FacilitatorClient | None = None,
        verifier: Verifier | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._func = func
        # The server's OWN retained requirement — the single source of truth
        # for the hedge and for every facilitator call.
        self.requirement = requirement
        self.terms = EscrowTerms(requirement=requirement, spec=spec,
                                 timeout_seconds=timeout_seconds)
        self.platform: PlatformClient = platform or LocalReferencePlatform()
        self.facilitator: FacilitatorClient = facilitator or LocalFacilitator()
        self.verifier: Verifier = verifier or ReferenceVerifier()
        self.timeout_seconds = timeout_seconds

    # -- 402 challenge ---------------------------------------------------

    def challenge(self) -> HandlerResponse:
        body = {
            "x402Version": 1,
            "error": "payment_required",
            "accepts": [self.requirement.to_accepts_dict()],
            "escrow": self.terms.to_dict(),
        }
        return HandlerResponse(402, body, {"Content-Type": "application/json"})

    # -- accepts[] hedge ---------------------------------------------------

    def match_accepted(self, payment_payload: dict[str, Any]) -> PaymentRequirement:
        """Return the server's OWN requirement if the client's claimed
        accepted terms match it exactly; raise AcceptsMismatch otherwise.

        The returned object is always ``self.requirement`` — client data is
        only ever *compared*, never propagated.
        """
        claimed = payment_payload.get("accepted")
        if claimed is None:
            # Tolerated shape: authorization-only payloads carry no claim;
            # the facilitator still receives the server's own requirement.
            return self.requirement
        if not isinstance(claimed, dict):
            raise AcceptsMismatch("accepted must be an object")
        if not self.requirement.matches_claimed(claimed):
            raise AcceptsMismatch(
                "client-claimed accepted terms do not match the server's "
                "retained accepts[] entry")
        return self.requirement

    # -- main entry --------------------------------------------------------

    def handle(self, *, headers: dict[str, str],
               kwargs: dict[str, Any] | None = None) -> HandlerResponse:
        kwargs = kwargs or {}
        payment_header = None
        for k, v in headers.items():
            if k.lower() in ("x-payment", "payment-signature"):
                payment_header = v
                break
        if not payment_header:
            return self.challenge()

        try:
            payment_payload = decode_payment_header(payment_header)
        except MalformedChallenge as exc:
            return HandlerResponse(400, {"ok": False, "error": str(exc)}, {})

        # Hedge FIRST: a mismatched claim is rejected before the facilitator
        # (and therefore before any settlement) is ever touched.
        try:
            requirement = self.match_accepted(payment_payload)
        except AcceptsMismatch as exc:
            return HandlerResponse(
                402, {"ok": False, "error": "accepts_mismatch",
                      "detail": str(exc),
                      "accepts": [self.requirement.to_accepts_dict()]}, {})

        fac = self.facilitator.verify_and_hold(
            payment_payload=payment_payload, requirement=requirement)
        if not fac.ok:
            return HandlerResponse(
                402, {"ok": False, "error": "payment_verification_failed",
                      "detail": fac.error}, {})

        escrow_id = self.platform.open_escrow(
            requirement=requirement, payer=fac.payer,
            payment_ref=fac.payment_ref)

        # Execute the work, timeout-bounded. A timeout refunds — the seller
        # never keeps funds for work that did not deliver in time.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._func, **kwargs)
            result = future.result(timeout=self.timeout_seconds)
        except concurrent.futures.TimeoutError:
            receipt = self.platform.refund(escrow_id)
            return HandlerResponse(
                504, {"ok": False, "error": "execution_timeout",
                      "escrow_id": escrow_id, "settlement": receipt}, {})
        except Exception as exc:  # work raised → refund, never release
            receipt = self.platform.refund(escrow_id)
            return HandlerResponse(
                502, {"ok": False, "error": "execution_failed",
                      "detail": f"{type(exc).__name__}: {exc}",
                      "escrow_id": escrow_id, "settlement": receipt}, {})
        finally:
            executor.shutdown(wait=False)

        # Commit + verify: the result hash is computed over the delivered
        # result and judged against the spec committed before payment.
        verdict = self.verifier.verify(
            spec=self.terms.spec, spec_hash=self.terms.spec_hash, result=result)
        if not verdict.passed:
            receipt = self.platform.refund(escrow_id)
            return HandlerResponse(
                422, {"ok": False, "error": "verification_failed",
                      "detail": verdict.reason,
                      "result_hash": verdict.result_hash,
                      "spec_hash": self.terms.spec_hash,
                      "escrow_id": escrow_id, "settlement": receipt}, {})

        receipt = self.platform.release(escrow_id)
        body = {
            "ok": True,
            "result": result,
            "verification": {"passed": True, "result_hash": verdict.result_hash,
                             "spec_hash": self.terms.spec_hash},
            "settlement": receipt,
        }
        return HandlerResponse(
            200, body,
            {"X-PAYMENT-RESPONSE": json.dumps(
                {"success": True, "escrow_id": escrow_id,
                 "settlement": "released"})})


def verified_function(func: Callable[..., Any], **endpoint_kwargs: Any
                      ) -> VerifiedEndpoint:
    """Wrap a plain function as a VerifiedEndpoint (framework-agnostic)."""
    return VerifiedEndpoint(func, **endpoint_kwargs)
