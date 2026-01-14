"""
Sentinel Node - Central Oversight Coordination for SINCOR DAO
Acts as the synthetic 'executive' monitoring subsystem for task flow, agent health, and compliance status.
"""

import time
from agents.oversight.oversight_agent import OversightAgent
from agents.oversight.build_coordination_agent import BuildCoordinationAgent

class SentinelNode:
    def __init__(self):
        self.oversight = OversightAgent()
        self.builder = BuildCoordinationAgent()
        self.running = True

    def heartbeat(self):
        print("[SentinelNode] System online. Monitoring agents...")
        while self.running:
            self.oversight.run_diagnostics()
            self.builder.coordinate_builds()
            self.audit_cycle()
            time.sleep(60)  # Run every 60 seconds

    def audit_cycle(self):
        print("[SentinelNode] Running synthetic audit cycle.")
        # Placeholder for any additional oversight logic
        # Future: consensus simulation, report dispatch, external alerts

    def shutdown(self):
        print("[SentinelNode] Shutdown signal received. Exiting.")
        self.running = False

if __name__ == "__main__":
    try:
        node = SentinelNode()
        node.heartbeat()
    except KeyboardInterrupt:
        node.shutdown()
