

def test_agent_card_endpoint(client):
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload.get("name")


def test_a2a_quote_unknown_skill(client):
    response = client.post("/api/a2a/quote", json={"skill_id": "unknown-skill"})
    assert response.status_code == 400
