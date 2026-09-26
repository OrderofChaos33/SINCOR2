"""Onchain anchor wiring: flag-off is today's behavior; flag-on with a
mocked relayer stamps auction ids and fails closed when the open fails."""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task
from sincor2.onchain import auction_relayer as relayer_mod
from sincor2.onchain.auction_relayer import auction_id_for


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.delenv("AUCTION_ONCHAIN_ANCHOR", raising=False)
    monkeypatch.delenv("AUCTION_ONCHAIN_FUND", raising=False)
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


class _FakeRelayer:
    def __init__(self, fail_open=False):
        self.fail_open = fail_open
        self.opened = []

    def open_auction(self, task_id, commit_window_s=300, reveal_window_s=300):
        if self.fail_open:
            raise RuntimeError("rpc down")
        self.opened.append(task_id)
        return {"auction_id": "0x" + auction_id_for(task_id).hex(),
                "tx_hash": "ab" * 32}


def _enable_anchor(monkeypatch, fake):
    monkeypatch.setenv("AUCTION_ONCHAIN_ANCHOR", "1")
    monkeypatch.setattr(relayer_mod, "get_relayer", lambda: fake)


def test_flag_off_no_anchor_id(app_env):
    task = create_task("lead-enrichment", tags=["lead-enrichment"],
                       bounty_axm=1.5, sealed=True)
    assert "auction_id" not in task


def test_flag_on_stamps_auction_id(app_env, monkeypatch):
    fake = _FakeRelayer()
    _enable_anchor(monkeypatch, fake)
    task = create_task("lead-enrichment", tags=["lead-enrichment"],
                       bounty_axm=1.5, sealed=True)
    assert task["auction_id"] == "0x" + auction_id_for(task["task_id"]).hex()
    assert task["onchain_open_tx"] == "ab" * 32
    assert fake.opened == [task["task_id"]]
    # Legacy (non-sealed) tasks are never anchored.
    plain = create_task("lead-enrichment", tags=["lead-enrichment"],
                        bounty_axm=1.5, sealed=False)
    assert "auction_id" not in plain


def test_flag_on_open_failure_is_fail_closed(app_env, monkeypatch):
    before = set(get_fabric().tasks)
    _enable_anchor(monkeypatch, _FakeRelayer(fail_open=True))
    with pytest.raises(RuntimeError, match="onchain auction open failed"):
        create_task("lead-enrichment", tags=["lead-enrichment"],
                    bounty_axm=1.5, sealed=True)
    # The half-created Python task is rolled back.
    assert set(get_fabric().tasks) == before


def test_flag_on_misconfigured_relayer_is_fail_closed(app_env, monkeypatch):
    monkeypatch.setenv("AUCTION_ONCHAIN_ANCHOR", "1")
    monkeypatch.setattr(relayer_mod, "get_relayer", lambda: None)
    before = set(get_fabric().tasks)
    with pytest.raises(RuntimeError, match="onchain auction open failed"):
        create_task("lead-enrichment", tags=["lead-enrichment"],
                    bounty_axm=1.5, sealed=True)
    assert set(get_fabric().tasks) == before
