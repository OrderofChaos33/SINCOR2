from datetime import timedelta
import sys
import types

from flask import Blueprint, Flask

from sincor2 import a2a_bootstrap
from sincor2.underwriting.blueprint import mount_underwriting
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.types import IntentMandate, SpendEnvelope, iso, new_id, utcnow


def test_underwriting_blueprint_import_and_health_route():
    app = Flask(__name__)
    assert mount_underwriting(app) is True
    client = app.test_client()
    response = client.get("/v1/underwriting/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True


def test_register_a2a_mounts_underwriting(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(a2a_bootstrap, "install", lambda: None)

    fake_a2a = types.ModuleType("sincor2.a2a_integration")

    class _Router:
        def __init__(self):
            self.blueprint = Blueprint("a2a", __name__)

    fake_a2a.A2ARouter = _Router  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sincor2.a2a_integration", fake_a2a)

    fake_kya = types.ModuleType("sincor2.kya_bootstrap")
    fake_kya.mount_kya_stack = lambda _app: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sincor2.kya_bootstrap", fake_kya)

    fake_chroma = types.ModuleType("verticals.auto_detailing.blueprint")
    fake_chroma.register_chroma = lambda _app: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "verticals.auto_detailing.blueprint", fake_chroma)

    assert a2a_bootstrap.register_a2a(app) is True
    response = app.test_client().get("/v1/underwriting/health")
    assert response.status_code == 200


def test_store_legacy_runtime_api_with_string_root(tmp_path):
    store = UnderwriteStore(str(tmp_path))
    now = utcnow()
    mandate = IntentMandate(
        mandate_id=new_id(),
        issued_at=iso(now),
        expires_at=iso(now + timedelta(hours=1)),
        operator_id="court",
        controller_wallet="0x" + "11" * 20,
        agent_id="agent-1",
        asset="USDC",
        chain_id=8453,
        max_notional_usd="100.00",
        max_single_tx_usd="10.00",
    )
    store.write_mandate(mandate)
    assert store.get_mandate(mandate.mandate_id) is not None
    assert store.active_mandate_for("agent-1") is not None

    envelope = SpendEnvelope(
        envelope_id=new_id(),
        mandate_id=mandate.mandate_id,
        agent_id="agent-1",
        issued_at=mandate.issued_at,
        expires_at=mandate.expires_at,
        status="active",
        asset="USDC",
        chain_id=8453,
        amount_usd="5.00",
        remaining_usd="5.00",
        ttl_seconds=300,
        reason_codes=["OK_BASELINE"],
        tap="ledger_sim",
    )
    store.write_envelope(envelope)
    assert store.get_envelope(envelope.envelope_id) is not None

    store.audit("smoke", "agent-1", {"ok": True}, mandate.mandate_id, envelope.envelope_id)
    assert list(store.iter_audit())
