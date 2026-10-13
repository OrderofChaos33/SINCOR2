"""Authz hardening for the SADAS feed: constant-time token compare,
token TTL/rotation/expiry pruning, and paginated feed."""
from __future__ import annotations

import inspect
import os

import pytest

from sincor2.kya import sadas as sadas_mod
from sincor2.kya.sadas import SADASFeed, _now


@pytest.fixture()
def feed(monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    return SADASFeed()


def _publish_n(feed, n):
    for i in range(n):
        feed.publish({"title": f"t{i}", "signal": f"s{i}", "score": i})


def test_valid_token_ungates(feed):
    feed.subscribe("k1", "tok-abc-123")
    out = feed.feed("tok-abc-123")
    assert out["gated"] is False
    assert out["count"] == 0


def test_wrong_token_gated(feed):
    feed.subscribe("k1", "tok-abc-123")
    out = feed.feed("totally-different-token")
    assert out["gated"] is True
    assert out["items"] == []
    assert out["count"] == 0


def test_missing_token_gated(feed):
    feed.subscribe("k1", "tok-abc-123")
    assert feed.feed()["gated"] is True
    assert feed.feed(None)["gated"] is True


def test_expired_token_gated(feed):
    row = feed.subscribe("k1", "tok-abc-123")
    assert row["expires_at"] > _now()
    feed.subs["k1"]["expires_at"] = _now() - 1  # simulate expiry
    out = feed.feed("tok-abc-123")
    assert out["gated"] is True
    assert out["items"] == []


def test_expired_sub_pruned_lazily(feed):
    feed.subscribe("k1", "tok-abc-123")
    feed.subs["k1"]["expires_at"] = _now() - 1
    feed.feed("tok-abc-123")  # triggers lazy prune
    assert "k1" not in feed.subs


def test_rotate_token_swaps(feed):
    feed.subscribe("k1", "old-token")
    row = feed.rotate_token("k1", "new-token")
    assert row["token"] == "new-token"
    assert row["kya_id"] == "k1"
    assert feed.feed("old-token")["gated"] is True
    assert feed.feed("new-token")["gated"] is False


def test_rotate_token_unknown_kya(feed):
    with pytest.raises(KeyError):
        feed.rotate_token("ghost", "tok")


def test_prune_expired_removes(feed):
    feed.subscribe("k1", "tok-1", ttl_s=3600)
    feed.subscribe("k2", "tok-2", ttl_s=3600)
    feed.subs["k2"]["expires_at"] = _now() - 1
    removed = feed.prune_expired()
    assert removed == 1
    assert "k2" not in feed.subs
    assert "k1" in feed.subs
    assert feed.feed("tok-1")["gated"] is False
    assert feed.feed("tok-2")["gated"] is True


def test_prune_expired_none(feed):
    feed.subscribe("k1", "tok-1")
    assert feed.prune_expired() == 0


def test_subscribe_backward_compat(feed):
    # old two-arg call still works; default TTL applied
    row = feed.subscribe("k1", "tok-abc")
    assert row["kya_id"] == "k1"
    assert row["token"] == "tok-abc"
    assert row["expires_at"] > _now()
    assert feed.feed("tok-abc")["gated"] is False


def test_subscribe_env_ttl_default(feed, monkeypatch):
    monkeypatch.setenv("KYA_SADAS_TOKEN_TTL_S", "600")
    before = _now()
    row = feed.subscribe("k1", "tok-abc")
    assert 600 - 2 <= row["expires_at"] - before <= 600


def test_feed_pagination_limit_offset(feed):
    feed.subscribe("k1", "tok")
    _publish_n(feed, 10)
    out = feed.feed("tok")  # default limit=50 -> all 10
    assert out["count"] == 10
    page = feed.feed("tok", limit=3, offset=2)
    assert page["gated"] is False
    assert page["count"] == 3
    # most recent window: items[7:10] minus first `offset`
    assert [i["title"] for i in page["items"]] == ["t5", "t6", "t7"]


def test_feed_limit_clamped_to_200(feed):
    feed.subscribe("k1", "tok")
    _publish_n(feed, 250)
    out = feed.feed("tok", limit=1000)
    assert out["count"] == 200
    assert out["items"][0]["title"] == "t50"
    assert out["items"][-1]["title"] == "t249"


def test_feed_limit_clamped_low_and_offset_floor(feed):
    feed.subscribe("k1", "tok")
    _publish_n(feed, 10)
    out = feed.feed("tok", limit=0)
    assert out["count"] == 1  # clamped to minimum 1
    out2 = feed.feed("tok", limit=5, offset=-99)
    assert out2["count"] == 5  # offset floored at 0


def test_feed_offset_past_end(feed):
    feed.subscribe("k1", "tok")
    _publish_n(feed, 5)
    out = feed.feed("tok", limit=10, offset=99)
    assert out["gated"] is False
    assert out["items"] == []


def test_token_compare_uses_compare_digest():
    src = inspect.getsource(sadas_mod)
    assert "hmac.compare_digest" in src
    # no plaintext == comparison against tokens anywhere in the module
    assert '== token' not in src
    assert '"token") ==' not in src
    assert "s.get(\"token\") ==" not in src


def test_token_compare_unequal_lengths_work(feed):
    feed.subscribe("k1", "a-much-longer-token-value")
    assert feed.feed("a-much-longer-token-value")["gated"] is False
    assert feed.feed("short")["gated"] is True  # no exception, just gated


def test_blueprint_feed_passes_limit_offset(monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    sadas_mod._F = None  # reset singleton to pick up tmp data dir
    try:
        from flask import Flask

        from sincor2.kya.blueprint import mount_kya

        app = Flask(__name__)
        assert mount_kya(app)
        client = app.test_client()
        sadas_mod.get_sadas().subscribe("k1", "tok")
        for i in range(8):
            sadas_mod.get_sadas().publish({"title": f"t{i}"})
        r = client.get("/v1/sadas/feed", query_string={"token": "tok", "limit": 3, "offset": 1})
        body = r.get_json()
        assert body["gated"] is False
        assert body["count"] == 3
        assert [i["title"] for i in body["items"]] == ["t4", "t5", "t6"]
        # defaults unchanged
        r2 = client.get("/v1/sadas/feed", query_string={"token": "tok"})
        assert r2.get_json()["count"] == 8
        # wrong token stays gated
        assert client.get("/v1/sadas/feed", query_string={"token": "nope"}).get_json()["gated"] is True
    finally:
        sadas_mod._F = None
