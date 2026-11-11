"""Pluggable result verification (the SIGIL seam).

Flow: before work runs, the server commits to a *spec* (what a good result
must satisfy) and publishes ``spec_hash = sha256(canonical(spec))`` in the
402 challenge — that hash commitment is the SIGIL-style commitment in this
package. After work runs, a Verifier checks the delivered result against
the committed spec. Pass → SEAL release. Fail → refund.

How a real SIGIL verification call plugs in
--------------------------------------------
Implement the ``Verifier`` protocol (one method, ``verify``) and pass your
implementation to ``VerifiedEndpoint(..., verifier=MySigilVerifier())``::

    class MySigilVerifier:
        def verify(self, *, spec, spec_hash, result):
            # 1. POST {specHash, resultHash, result} to your SIGIL service
            # 2. return VerificationResult(passed=<its verdict>, ...)

Your verifier receives the *committed* spec and its hash, so it can refuse
if the spec it is asked to judge differs from the spec that was committed
before payment (``sha256_hex(spec) != spec_hash`` → fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .types import sha256_hex


@dataclass
class VerificationResult:
    passed: bool
    reason: str = ""
    result_hash: str = ""


@runtime_checkable
class Verifier(Protocol):
    def verify(self, *, spec: dict[str, Any], spec_hash: str,
               result: Any) -> VerificationResult:
        ...


class ReferenceVerifier:
    """Default local reference verifier. NOT the production SIGIL service.

    Supported spec keys (all optional, all present keys must pass):
      - ``expected_result_hash``: sha256 of the canonical result must equal it
      - ``required_keys``: result dict must contain every key
      - ``equals``: result must deep-equal this value
      - ``predicate``: one of "non_empty" (truthy result), "is_dict", "is_list"

    Always enforces commitment integrity: the judged spec must hash to the
    committed spec_hash, otherwise verification fails closed.
    """

    def verify(self, *, spec: dict[str, Any], spec_hash: str,
               result: Any) -> VerificationResult:
        result_hash = sha256_hex(result)
        if sha256_hex(spec) != spec_hash:
            return VerificationResult(
                False, "spec does not match committed spec_hash", result_hash)
        if "expected_result_hash" in spec:
            if result_hash != str(spec["expected_result_hash"]):
                return VerificationResult(
                    False, "result hash != expected_result_hash", result_hash)
        if "equals" in spec:
            if result != spec["equals"]:
                return VerificationResult(
                    False, "result != spec['equals']", result_hash)
        if "required_keys" in spec:
            if not isinstance(result, dict):
                return VerificationResult(
                    False, "result is not a dict but spec requires keys",
                    result_hash)
            missing = [k for k in spec["required_keys"] if k not in result]
            if missing:
                return VerificationResult(
                    False, f"result missing required keys: {missing}",
                    result_hash)
        pred = spec.get("predicate")
        if pred == "non_empty" and not result:
            return VerificationResult(False, "result is empty", result_hash)
        if pred == "is_dict" and not isinstance(result, dict):
            return VerificationResult(False, "result is not a dict", result_hash)
        if pred == "is_list" and not isinstance(result, list):
            return VerificationResult(False, "result is not a list", result_hash)
        return VerificationResult(True, "reference checks passed", result_hash)
