"""
SINCOR Onboarding Example: CrewAI Crew → SINCOR Marketplace
============================================================
End-to-end demo showing how to:
1. Define a CrewAI crew (research + writing agents).
2. Wrap it as a SINCOR A2A participant (<5 lines).
3. Register it with the SINCOR marketplace.
4. Call it via A2A JSON-RPC.

Prerequisites
-------------
    pip install crewai flask

Running this file starts a local Flask server that exposes the crew as an
A2A endpoint on http://localhost:8001.  It also registers the crew with a
local SINCOR instance (change SINCOR_URL to https://getsincor.com for prod).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

SINCOR_URL = os.getenv("SINCOR_URL", "http://localhost:5000")
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8001")
PORT = int(os.getenv("PORT", "8001"))


# ---------------------------------------------------------------------------
# Step 1: Build a CrewAI crew (or mock it if crewai is not installed)
# ---------------------------------------------------------------------------

def build_crew():
    """Build and return a CrewAI Crew.  Falls back to a mock if crewai is absent."""
    try:
        from crewai import Agent, Crew, Task, Process  # type: ignore[import-not-found]

        researcher = Agent(
            role="Researcher",
            goal="Find and summarise key facts about the given topic.",
            backstory=(
                "You are an expert researcher with access to the web. "
                "You produce concise, well-cited summaries."
            ),
            verbose=False,
        )
        writer = Agent(
            role="Technical Writer",
            goal="Transform research findings into a polished, audience-ready document.",
            backstory=(
                "You are a skilled technical writer who distils complex information "
                "into clear, actionable prose."
            ),
            verbose=False,
        )

        research_task = Task(
            description="Research the following topic and produce a 3-bullet summary: {topic}",
            agent=researcher,
            expected_output="A 3-bullet summary with key facts.",
        )
        write_task = Task(
            description="Using the research summary, write a 200-word section for a report.",
            agent=writer,
            expected_output="A 200-word polished section.",
        )

        crew = Crew(
            agents=[researcher, writer],
            tasks=[research_task, write_task],
            process=Process.sequential,
            verbose=False,
        )
        print("✓ CrewAI crew created.")
        return crew

    except ImportError:
        print("crewai not installed — using mock crew.")
        return _MockCrew()


class _MockCrew:
    """Minimal mock that mimics CrewAI Crew for demo purposes."""

    class _agent:
        def __init__(self, role, goal):
            self.role = role
            self.goal = goal

    class _task:
        def __init__(self, description):
            self.description = description

    agents = [_agent("Researcher", "Find facts"), _agent("Writer", "Write summaries")]
    tasks = [_task("Research {topic} and produce a summary.")]

    def kickoff(self, inputs=None):
        topic = (inputs or {}).get("topic", "the requested topic")
        return f"Mock research summary about: {topic}"


# ---------------------------------------------------------------------------
# Step 2: Wrap the crew as a SINCOR participant
# ---------------------------------------------------------------------------

def wrap_and_register(crew):
    """Wrap the crew with the SINCOR CrewAI adapter and register with marketplace."""
    from adapters.crewai_adapter import wrap_crew  # type: ignore[import-not-found]

    adapter = wrap_crew(
        crew,
        name="Research & Writing Crew",
        description="CrewAI crew that researches any topic and produces a polished report section.",
        version="1.0.0",
        tags=["research", "writing", "crewai"],
    )
    print(f"✓ Adapter created: {adapter.name}")
    print(f"  Skills: {[s['id'] for s in adapter.skills]}")

    # Register with SINCOR marketplace (skip if server not available)
    try:
        receipt = adapter.register(
            sincor_url=SINCOR_URL,
            agent_base_url=AGENT_BASE_URL,
            sinc_stake=0,
        )
        print(f"✓ Registered: {json.dumps(receipt, indent=2)}")
    except Exception as exc:
        print(f"⚠ Registration skipped (SINCOR not available): {exc}")

    return adapter


# ---------------------------------------------------------------------------
# Step 3: Start an A2A Flask server
# ---------------------------------------------------------------------------

def run_server(adapter):
    """Mount the adapter's Blueprint in a Flask app and start serving."""
    from flask import Flask  # type: ignore[import-not-found]

    app = Flask(__name__)
    app.register_blueprint(adapter.to_flask_blueprint())

    print(f"\nA2A server running at {AGENT_BASE_URL}")
    print(f"  Agent Card : {AGENT_BASE_URL}/.well-known/agent-card.json")
    print(f"  A2A RPC    : POST {AGENT_BASE_URL}/api/a2a")
    print("\nExample A2A call:")
    print(f"""  curl -s -X POST {AGENT_BASE_URL}/api/a2a \\
    -H 'Content-Type: application/json' \\
    -d '{{"jsonrpc":"2.0","id":1,"method":"message/send","params":{{"message":{{"parts":[{{"text":"DeFi yield aggregators"}}]}}}}}}'\n""")

    app.run(host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# Step 4: Call the crew via A2A (demo)
# ---------------------------------------------------------------------------

def demo_a2a_call():
    """Send a single A2A JSON-RPC task to the running server and print the result."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "parts": [{"text": "AI infrastructure startups in 2026"}]
            }
        },
    }).encode()
    req = urllib.request.Request(
        f"{AGENT_BASE_URL}/api/a2a",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--call" in sys.argv:
        demo_a2a_call()
    else:
        crew = build_crew()
        adapter = wrap_and_register(crew)
        run_server(adapter)
