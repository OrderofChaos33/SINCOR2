from sincor2.plan_gate import allow, require


def test_anon_blocked_from_paid_features() -> None:
    gate = allow(None, "starter_agents")
    assert gate.allowed is False
    assert "buy?plan=starter" in gate.checkout_url


def test_starter_cannot_use_enterprise() -> None:
    assert allow("starter", "enterprise_agents").allowed is False
    assert allow("enterprise", "enterprise_agents").allowed is True


def test_require_raises() -> None:
    try:
        require("anon", "underwrite_live_spend")
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_canon_freshness_holders() -> None:
    from sincor2.metrics_freshness import freshness_report
    from sincor2.onchain import live_snapshot

    live_snapshot._cache["payload"] = {
        "verified_at": "2026-09-19T14:20:00Z",
        "sinc": {"holders": 3455},
        "axiom": {"holders": 3354},
    }
    live_snapshot._cache["fetched_at"] = 10**12
    report = freshness_report()
    assert report["sinc_holders"] == 3455
