"""Reference verifier + the SIGIL plug-in seam."""
from sincor_x402 import ReferenceVerifier
from sincor_x402.types import sha256_hex


def test_required_keys_pass_and_fail():
    v = ReferenceVerifier()
    spec = {"required_keys": ["answer"]}
    h = sha256_hex(spec)
    assert v.verify(spec=spec, spec_hash=h, result={"answer": 1}).passed
    bad = v.verify(spec=spec, spec_hash=h, result={"junk": 1})
    assert not bad.passed and "missing" in bad.reason


def test_expected_result_hash():
    v = ReferenceVerifier()
    result = {"answer": 42}
    spec = {"expected_result_hash": sha256_hex(result)}
    h = sha256_hex(spec)
    assert v.verify(spec=spec, spec_hash=h, result=result).passed
    assert not v.verify(spec=spec, spec_hash=h,
                        result={"answer": 43}).passed


def test_tampered_spec_fails_closed():
    v = ReferenceVerifier()
    spec = {"required_keys": ["answer"]}
    committed = sha256_hex(spec)
    tampered = {"required_keys": []}  # judged against a weaker spec
    out = v.verify(spec=tampered, spec_hash=committed, result={"x": 1})
    assert not out.passed and "spec_hash" in out.reason


def test_custom_verifier_plugs_into_endpoint(
        requirement, platform, facilitator):
    """A user-supplied Verifier (the SIGIL seam) replaces the reference one."""
    from sincor_x402 import VerifiedEndpoint
    from sincor_x402.verifier import VerificationResult

    class AlwaysPassUnlessCursed:
        def verify(self, *, spec, spec_hash, result):
            ok = "cursed" not in str(result)
            return VerificationResult(ok, "custom rule",
                                      sha256_hex(result))

    def work(**kw):
        return {"anything": "goes"}
    ep = VerifiedEndpoint(work, requirement=requirement,
                          spec={"required_keys": ["never-present"]},
                          platform=platform, facilitator=facilitator,
                          verifier=AlwaysPassUnlessCursed())
    from x402pkg_helpers import make_payment_payload, payment_header
    header = payment_header(make_payment_payload(requirement))
    resp = ep.handle(headers={"X-PAYMENT": header}, kwargs={})
    # Reference verifier would have failed (missing key); custom one passes.
    assert resp.status == 200
    assert platform.released_total > 0
