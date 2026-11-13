"""W-96: seeded reputation is disclosed, never mistaken for earned.

``ensure_liveness_cohort()`` seeds the six cohort agents (and
``ensure_platform_agent()``) with ``_internal_reputation=1.0``, bypassing
the "earned-only, new agents start at 0.0" rule. The record carried
``"origin": "internal"`` but the ``reputation`` field itself (1.0) was
indistinguishable from earned reputation in API responses.

Fix (transparency only, no behavior change): records whose reputation was
assigned by the internal platform seed carry ``reputation_seeded=True``,
server-assigned and never read from the request body, persisted across
re-registrations/reseeds, and surfaced in API responses
(``list_agents()`` rows + the register endpoint).

Covered here:
- seeding via ``_internal_reputation`` marks the record ``reputation_seeded=True``
  with reputation 1.0;
- a reseed does not wipe the flag (persistence);
- a normally registered agent (no ``_internal_reputation``) has the flag
  absent/false with reputation 0.0;
- a ``reputation_seeded`` value smuggled in the request body is ignored —
  the flag is server-assigned only;
- the flag is visible in ``list_agents()`` rows (GET /v1/a2a/agents payload);
- the POST /v1/a2a/register response carries the flag.
"""
from __future__ import annotations

from sincor2.a2a_inbound import reset_fabric
from sincor2.a2a_inbound_ext import get_fabric, list_agents, register_agent_record

_SEED_WALLET = "0xC2d20A60B4ea93c0aA68a131205224E159766C62"


def _seed_body(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "W-96 test seed",
        "version": "1.0.0",
        "capability_tags": ["defi"],
        "wallet": _SEED_WALLET,
        "chain_id": 84532,
    }


def _normal_body(agent_id: str, **extra) -> dict:
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["defi"],
    }
    body.update(extra)
    return body


class TestSeededReputationFlag:
    def test_seed_sets_flag_with_reputation_1_0(self):
        reset_fabric()
        snap = register_agent_record(_seed_body("w96-seed-1"),
                                     _internal_reputation=1.0)
        assert snap["reputation"] == 1.0
        assert snap["reputation_seeded"] is True

    def test_reseed_preserves_flag(self):
        reset_fabric()
        register_agent_record(_seed_body("w96-seed-2"),
                              _internal_reputation=1.0)
        # Second seed = reseed path (updates in place).
        snap = register_agent_record(_seed_body("w96-seed-2"),
                                     _internal_reputation=1.0)
        assert snap["reputation"] == 1.0
        assert snap["reputation_seeded"] is True
        fabric = get_fabric()
        with fabric.lock:
            assert fabric.agents["w96-seed-2"]["reputation_seeded"] is True

    def test_normal_registration_flag_false(self):
        reset_fabric()
        snap = register_agent_record(_normal_body("w96-normal-1"))
        assert snap["reputation"] == 0.0
        assert snap.get("reputation_seeded") is False

    def test_body_cannot_set_flag(self):
        # Server-assigned only: a reputation_seeded value smuggled in the
        # request body must be ignored, not honored.
        reset_fabric()
        snap = register_agent_record(
            _normal_body("w96-normal-2", reputation_seeded=True,
                         reputation=1.0))
        assert snap["reputation"] == 0.0
        assert snap.get("reputation_seeded") is False

    def test_list_agents_surfaces_flag(self):
        reset_fabric()
        register_agent_record(_seed_body("w96-seed-3"),
                              _internal_reputation=1.0)
        register_agent_record(_normal_body("w96-normal-3"))
        rows = {a["agent_id"]: a for a in list_agents()}
        assert rows["w96-seed-3"]["reputation"] == 1.0
        assert rows["w96-seed-3"]["reputation_seeded"] is True
        assert rows["w96-normal-3"]["reputation_seeded"] is False

    def test_register_endpoint_returns_flag(self, client):
        reset_fabric()
        resp = client.post("/v1/a2a/register",
                           json=_normal_body("w96-http-1"))
        assert resp.status_code == 201
        payload = resp.get_json()
        assert "reputation_seeded" in payload
        assert payload["reputation_seeded"] is False

    def test_agents_endpoint_row_carries_flag(self, client):
        reset_fabric()
        register_agent_record(_seed_body("w96-seed-4"),
                              _internal_reputation=1.0)
        resp = client.get("/v1/a2a/agents")
        assert resp.status_code == 200
        agents = resp.get_json()["agents"]
        row = next(a for a in agents if a["agent_id"] == "w96-seed-4")
        assert row["reputation"] == 1.0
        assert row["reputation_seeded"] is True
