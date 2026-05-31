import os
import unittest

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

    def test_reward_limit_boundary(self):
        original_limit = a2a.A2A_REGISTRATION_REWARD_LIMIT
        try:
            a2a.A2A_REGISTRATION_REWARD_LIMIT = 2
            for i in range(1, 4):
                result = a2a._handle_agents_register({"agentId": f"agent-{i}"})
            registration = result["result"]["registration"]
            self.assertFalse(registration["reward_eligible"])
            self.assertEqual(registration["reward_wei"], "0")
            self.assertEqual(result["result"]["registration_stats"]["rewards_remaining"], 0)
        finally:
            a2a.A2A_REGISTRATION_REWARD_LIMIT = original_limit

    def test_agent_card_advertises_protocol_bindings(self):
        os.environ["A2A_HTTP_JSON_URL"] = "https://getsincor.com/api/a2a/http"
        os.environ["A2A_GRPC_URL"] = "https://getsincor.com/api/a2a/grpc"
        try:
            card = a2a.build_agent_card().to_dict()
            bindings = {
                interface["protocolBinding"] for interface in card["supportedInterfaces"]
            }
            self.assertIn("JSONRPC", bindings)
            self.assertIn("HTTP+JSON", bindings)
            self.assertIn("GRPC", bindings)
            self.assertIn("agentRegistration", card["capabilities"])
        finally:
            os.environ.pop("A2A_HTTP_JSON_URL", None)
            os.environ.pop("A2A_GRPC_URL", None)


if __name__ == "__main__":
    unittest.main()
