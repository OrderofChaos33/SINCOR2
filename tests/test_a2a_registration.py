import os
import unittest
from unittest.mock import patch

from flask import Flask

from sincor2 import a2a_integration as a2a


class A2ARegistrationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.router = a2a.A2ARouter()
        self.app.register_blueprint(self.router.blueprint)
        self.client = self.app.test_client()
        with a2a._store_lock:
            a2a._agent_registrations.clear()
            a2a._registration_sequence = 0

    def test_register_agent_gets_reward(self):
        response = self.client.post(
            "/api/a2a/register",
            json={"agent_id": "agent-001", "protocol_binding": "http_json"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["result"]["status"], "registered")
        self.assertTrue(body["result"]["registration"]["reward_eligible"])
        self.assertEqual(
            body["result"]["registration"]["reward_wei"],
            str(100 * 10**18),
        )
        self.assertEqual(body["result"]["registration"]["registration_index"], 1)

    def test_duplicate_registration_is_idempotent(self):
        self.client.post("/api/a2a/register", json={"agent_id": "agent-repeat"})
        response = self.client.post("/api/a2a/register", json={"agent_id": "agent-repeat"})
        body = response.get_json()
        self.assertEqual(body["result"]["status"], "already_registered")
        self.assertEqual(body["result"]["registration_stats"]["total_registered"], 1)

    def test_empty_protocol_binding_rejected(self):
        response = self.client.post(
            "/api/a2a/register",
            json={"agent_id": "agent-xyz", "protocol_binding": "   "},
        )
        body = response.get_json()
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], -32602)

    def test_reward_limit_boundary(self):
        original_limit = a2a.A2A_REGISTRATION_REWARD_LIMIT
        try:
            a2a.A2A_REGISTRATION_REWARD_LIMIT = 2
            results = []
            for i in range(1, 4):
                results.append(a2a._handle_agents_register({"agentId": f"agent-{i}"}))

            first = results[0]["result"]["registration"]
            second = results[1]["result"]["registration"]
            third = results[2]["result"]["registration"]

            self.assertTrue(first["reward_eligible"])
            self.assertTrue(second["reward_eligible"])
            self.assertFalse(third["reward_eligible"])
            self.assertEqual(third["reward_wei"], "0")
            self.assertEqual(results[2]["result"]["registration_stats"]["rewards_remaining"], 0)
        finally:
            a2a.A2A_REGISTRATION_REWARD_LIMIT = original_limit

    def test_agent_card_advertises_protocol_bindings(self):
        with patch.dict(
            os.environ,
            {
                "A2A_HTTP_JSON_URL": "https://getsincor.com/api/a2a/http",
                "A2A_GRPC_URL": "https://getsincor.com/api/a2a/grpc",
            },
            clear=False,
        ):
            card = a2a.build_agent_card().to_dict()
            bindings = {
                interface["protocolBinding"] for interface in card["supportedInterfaces"]
            }
            self.assertIn("JSONRPC", bindings)
            self.assertIn("HTTP+JSON", bindings)
            self.assertIn("GRPC", bindings)
            self.assertIn("agentRegistration", card["capabilities"])


if __name__ == "__main__":
    unittest.main()
