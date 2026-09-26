"""Registration velocity: origin classification, reports, TOA weights."""

import time

from sincor2.registration_velocity import (
    BASE_WEIGHTS,
    classify_origin,
    velocity_report,
    volume_over_vanity_weights,
)

NOW_MS = int(time.time() * 1000)
DAY_MS = 86_400_000


def _agent(agent_id, registered_at, origin=None):
    rec = {"agent_id": agent_id, "registered_at": registered_at}
    if origin:
        rec["origin"] = origin
    return rec


def test_classify_origin_explicit():
    assert classify_origin({"agent_id": "x", "origin": "internal"}) == "internal"
    assert classify_origin({"agent_id": "x", "origin": "EXTERNAL"}) == "external"


def test_classify_origin_backfills():
    assert classify_origin({"agent_id": "sincor-agent-swarm"}) == "internal"
    assert classify_origin({"agent_id": "some-external-bot"}) == "external"
    assert classify_origin({}) == "external"


def test_velocity_report_counts_and_split():
    agents = {
        "sincor-agent-swarm": _agent("sincor-agent-swarm", NOW_MS - 40 * DAY_MS, "internal"),
        "ext-1": _agent("ext-1", NOW_MS - 2 * DAY_MS, "external"),
        "ext-2": _agent("ext-2", NOW_MS - 1 * DAY_MS),  # backfilled external
        "old-ext": _agent("old-ext", NOW_MS - 60 * DAY_MS, "external"),
    }
    report = velocity_report(agents, days=30, now_ms=NOW_MS)
    assert report["total_registered"] == 4
    assert report["external"] == 3
    assert report["internal"] == 1
    assert report["external_share"] == 0.75
    assert report["external_registrations_in_window"] == 2
    assert report["external_per_day"] == round(2 / 30, 4)
    assert len(report["daily_series"]) == 30


def test_velocity_report_empty():
    report = velocity_report({}, days=7, now_ms=NOW_MS)
    assert report["total_registered"] == 0
    assert report["external_per_day"] == 0.0
    assert report["external_share"] == 0.0


def test_weights_scale_with_velocity_and_sum_to_one():
    idle = {"external_per_day": 0.0}
    busy = {"external_per_day": 10.0}   # saturation point
    w_idle = volume_over_vanity_weights(idle)
    w_busy = volume_over_vanity_weights(busy)
    assert abs(sum(w_idle.values()) - 1.0) < 1e-6
    assert abs(sum(w_busy.values()) - 1.0) < 1e-6
    # Idle market keeps the directive's base velocity weight.
    assert w_idle["velocity"] == BASE_WEIGHTS["velocity"]
    # Saturated velocity doubles the velocity weight's SHARE pre-normalization.
    assert w_busy["velocity"] > w_idle["velocity"]
    # Mid velocity lands between.
    w_mid = volume_over_vanity_weights({"external_per_day": 5.0})
    assert w_idle["velocity"] < w_mid["velocity"] < w_busy["velocity"]
