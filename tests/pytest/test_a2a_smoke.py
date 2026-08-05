

"""A2A integration smoke tests — covers the new skill catalogue, pricing,
quote endpoint, settlement proof, leaderboard, and reputation routing."""

# ── Discovery ────────────────────────────────────────────────────────────────

def test_agent_card_endpoint(client):
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload.get("name")


def test_agent_card_has_12_required_skills(client):
    """The 12 P0 skills from the roadmap must appear in the agent card."""
    required = {
        "lead-enrichment", "competitor-intel", "outreach-sequence",
        "healthcare-credential-check", "dental-billing-scrub",
        "compliance-sbom", "market-forecast", "deal-scoring",
        "content-blog", "cashflow-recovery", "local-business-site-builder",
        "toa-decision",
    }
    response = client.get("/.well-known/agent-card.json")
    payload = response.get_json()
    advertised = {s["id"] for s in payload.get("skills", [])}
    missing = required - advertised
    assert not missing, f"Missing skills in agent card: {missing}"


def test_agent_card_no_security_requirement(client):
    """Agent card must not require an API key (open to all A2A callers)."""
    response = client.get("/.well-known/agent-card.json")
    payload = response.get_json()
    assert payload.get("security") in (None, [], {}), (
        "Agent card should not carry a security requirement"
    )


def test_agent_card_skills_have_pricing(client):
    """Every skill in the agent card must expose axmPriceWei and sincPrice."""
    response = client.get("/.well-known/agent-card.json")
    payload = response.get_json()
    for skill in payload.get("skills", []):
        sid = skill["id"]
        assert "axmPriceWei" in skill, f"skill {sid} missing axmPriceWei"
        assert "sincPrice" in skill, f"skill {sid} missing sincPrice"
        assert "inputSchema" in skill, f"skill {sid} missing inputSchema"
        assert "outputSchema" in skill, f"skill {sid} missing outputSchema"
        assert "estimatedLatencySeconds" in skill, f"skill {sid} missing latency"


# ── Quote endpoint ────────────────────────────────────────────────────────────

def test_a2a_quote_unknown_skill(client):
    response = client.post("/api/a2a/quote", json={"skill_id": "unknown-skill"})
    assert response.status_code == 400


def test_a2a_quote_post_known_skill(client):
    response = client.post("/api/a2a/quote", json={"skill_id": "lead-enrichment"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["skill_id"] == "lead-enrichment"
    assert "axm_price_wei" in data
    assert "sinc_amount" in data
    assert "pay_to" in data
    assert "input_schema" in data
    assert "output_schema" in data
    assert "estimated_latency_seconds" in data


def test_a2a_quote_get_known_skill(client):
    """GET /api/a2a/quote?skill_id=X must also work."""
    response = client.get("/api/a2a/quote?skill_id=competitor-intel")
    assert response.status_code == 200
    data = response.get_json()
    assert data["skill_id"] == "competitor-intel"


def test_a2a_quote_get_unknown_skill(client):
    response = client.get("/api/a2a/quote?skill_id=no-such-skill")
    assert response.status_code == 400


def test_a2a_quote_free_quota_skill(client):
    """Lead-enrichment has a free quota; first call for a new caller should be free."""
    response = client.post(
        "/api/a2a/quote",
        json={"skill_id": "lead-enrichment", "caller_id": "test-caller-free-001"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("is_free") is True
    assert data.get("free_quota_remaining", 0) > 0


def test_a2a_quote_non_free_skill(client):
    """compliance-sbom has no free quota."""
    response = client.post(
        "/api/a2a/quote",
        json={"skill_id": "compliance-sbom", "caller_id": "test-caller-001"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("is_free") is False
    assert int(data["axm_price_wei"]) > 0


# ── Agent registry ────────────────────────────────────────────────────────────

def test_a2a_agents_endpoint(client):
    response = client.get("/api/a2a/agents")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data.get("agents"), list)
    assert len(data["agents"]) >= 12
    # Each agent entry must include live pricing
    for agent in data["agents"]:
        assert "axm_price_wei" in agent
        assert "sinc_price" in agent


# ── Pricing snapshot ──────────────────────────────────────────────────────────

def test_a2a_pricing_endpoint(client):
    response = client.get("/api/a2a/pricing")
    assert response.status_code == 200
    data = response.get_json()
    assert "pricing" in data
    assert "lead-enrichment" in data["pricing"]


# ── Leaderboard ───────────────────────────────────────────────────────────────

def test_a2a_leaderboard_empty(client):
    response = client.get("/api/a2a/leaderboard")
    assert response.status_code == 200
    data = response.get_json()
    assert "leaderboard" in data
    assert isinstance(data["leaderboard"], list)


def test_a2a_leaderboard_limit_param(client):
    response = client.get("/api/a2a/leaderboard?limit=5")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["leaderboard"]) <= 5


# ── Settlement proof ──────────────────────────────────────────────────────────

def test_a2a_settle_unknown_task(client):
    response = client.post(
        "/api/a2a/settle",
        json={"task_id": "nonexistent-task-id", "tx_hash": "0xabc"},
    )
    assert response.status_code == 404


def test_a2a_settle_completed_task(client):
    """Submit a task (free-quota), then call /api/a2a/settle on it."""
    # Submit task using free quota (no payment needed in test env)
    send_body = {
        "method": "message/send",
        "id": 1,
        "params": {
            "skillId": "lead-enrichment",
            "callerId": "settle-test-caller",
            "message": {
                "role": "user",
                "parts": [{"text": "Enrich Acme Corp"}],
                "contextId": "ctx-settle-01",
            },
        },
    }
    send_resp = client.post("/api/a2a", json=send_body)
    assert send_resp.status_code == 200
    result = send_resp.get_json()
    task_id = result.get("result", {}).get("id")
    assert task_id

    settle_resp = client.post(
        "/api/a2a/settle",
        json={"task_id": task_id, "tx_hash": "", "caller_id": "settle-test-caller"},
    )
    assert settle_resp.status_code == 200
    proof = settle_resp.get_json()
    pos = proof.get("proof_of_settlement", {})
    assert pos.get("task_id") == task_id
    assert "result_hash" in pos
    assert "settled_at" in pos

