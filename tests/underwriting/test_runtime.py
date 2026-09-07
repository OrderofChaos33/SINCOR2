from sincor2.underwriting.runtime import boot


def test_boot_formula_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDERWRITE_TOA", "formula")
    monkeypatch.setenv("UNDERWRITE_DATA_DIR", str(tmp_path))
    rt = boot(str(tmp_path))
    assert rt.toa_backend == "formula"
    assert rt.tap_name == "ledger_sim"
    m = rt.mandates.issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="10.00",
        max_single_tx_usd="1.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    env = rt.envelopes.request(
        m, requested_usd="1.00", skill_id="underwrite.echo", payee="sincor:skill:underwrite.echo"
    )
    assert env.status == "active"
