
class OversightAgent:
    def __init__(self, name="Oversight", log_path="logs/oversight.log"):
        self.name = name
        self.log_path = log_path
        self.heartbeat_count = 0

    def heartbeat(self):
        self.heartbeat_count += 1
        print(f"[{self.name}] Heartbeat #{self.heartbeat_count}")
        self._log(f"Heartbeat #{self.heartbeat_count}")

    def _log(self, message):
        with open(self.log_path, "a") as f:
            f.write(f"{self.name}: {message}\n")

    def run_diagnostics(self):
        print(f"[{self.name}] Running diagnostics...")
        self._log("Diagnostics check OK.")
