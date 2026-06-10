"""
Cross-Framework Workflow: Healthcare Crew (CrewAI) + Compliance Checker (LangGraph)
====================================================================================
Demonstrates multi-framework A2A orchestration via SINCOR:

  External client
       │
       ▼
  SINCOR A2A Router ──────────────────────────────────────────────────────────┐
       │                                                                        │
       ▼                                                                        ▼
  Healthcare Crew (CrewAI)                        Compliance Checker (LangGraph)
  - Prior auth verification                       - HIPAA compliance scan
  - Eligibility check                             - Regulatory flag detection
  - Claim submission                              - Audit log generation
       │                                                        │
       └──────────────── SINCOR Orchestrator ──────────────────┘
                                │
                                ▼
                    Combined result + AXIOM settlement

Running this example
--------------------
    # Install optional deps:
    pip install crewai langgraph flask

    # Run (starts a local orchestrator on port 8002):
    PYTHONPATH=src:src/sincor2 python examples/workflows/cross_framework_workflow.py

    # Test via A2A:
    curl -s -X POST http://localhost:8002/api/a2a \\
      -H 'Content-Type: application/json' \\
      -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"parts":[{"text":"Verify eligibility for member M-12345 with BlueCross on 2026-06-10"}]}}}'
"""

from __future__ import annotations

import json
import os
import sys
import uuid

SINCOR_URL = os.getenv("SINCOR_URL", "http://localhost:5000")
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8002")
PORT = int(os.getenv("PORT", "8002"))


# ---------------------------------------------------------------------------
# Healthcare workflow (CrewAI-style, works without crewai installed)
# ---------------------------------------------------------------------------

def healthcare_task_handler(task: dict) -> dict:
    """Handles RCM tasks: eligibility, prior auth, claim submission."""
    payload = task.get("payload", {})
    task_type = task.get("task_type", "eligibility_verification")
    input_text = payload.get("input", "")

    # Route to the real SINCOR healthcare vertical via internal dispatch
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
        from sincor2.vertical_dispatch import dispatch_vertical_task
        result_str, _ = dispatch_vertical_task(
            skill_id="healthcare-rcm",
            input_text=input_text,
            platform_state=None,  # standalone mode — uses mock data
        ) or ("{}", None)
        return json.loads(result_str or "{}")
    except Exception:
        pass

    # Fallback: mock response
    return {
        "status": "success",
        "result": {
            "coverage_status": "active",
            "copay": "35.00",
            "deductible_remaining": "500.00",
            "benefits": ["professional_services", "telehealth"],
            "note": "Mock response — wire to live SINCOR for production.",
            "input_received": input_text[:100],
        },
    }


# ---------------------------------------------------------------------------
# Compliance checker (LangGraph-style state machine, works without langgraph)
# ---------------------------------------------------------------------------

def compliance_checker_handler(task: dict) -> dict:
    """Runs HIPAA/regulatory compliance checks on healthcare workflow outputs."""
    payload = task.get("payload", {})
    input_text = payload.get("input", "")

    # Minimal compliance check: flag PHI-like patterns
    phi_patterns = ["ssn", "date_of_birth", "dob", "member_id", "patient_id"]
    flags = [p for p in phi_patterns if p.lower() in input_text.lower()]

    return {
        "status": "success",
        "result": {
            "compliance_status": "flagged" if flags else "clear",
            "phi_fields_detected": flags,
            "hipaa_audit_required": bool(flags),
            "regulatory_notes": (
                "PHI detected in workflow input — ensure audit logging is enabled."
                if flags else
                "No PHI patterns detected in workflow input."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Orchestrated cross-framework handler
# ---------------------------------------------------------------------------

def orchestrated_handler(task: dict) -> dict:
    """Run healthcare + compliance checks in sequence and combine results."""
    # Step 1: Healthcare RCM
    healthcare_result = healthcare_task_handler(task)

    # Step 2: Compliance scan on the combined input + output
    compliance_input = json.dumps({
        "original_input": task.get("payload", {}).get("input", ""),
        "healthcare_result": healthcare_result,
    })
    compliance_task = {
        "task_type": "compliance_scan",
        "payload": {"input": compliance_input},
    }
    compliance_result = compliance_checker_handler(compliance_task)

    # Step 3: Combine
    return {
        "status": "success",
        "result": {
            "workflow_id": str(uuid.uuid4())[:8],
            "healthcare": healthcare_result.get("result", {}),
            "compliance": compliance_result.get("result", {}),
            "axiom_settlement": {
                "status": "pending",
                "note": "AXIOM settlement triggered post-completion — see marketplace/settlement.py",
            },
        },
    }


# ---------------------------------------------------------------------------
# Build adapters and serve
# ---------------------------------------------------------------------------

def build_adapters():
    """Build SINCOR adapters for both framework agents."""
    from adapters.generic_adapter import GenericAdapter

    healthcare_adapter = GenericAdapter(
        name="Healthcare RCM Crew",
        handler=healthcare_task_handler,
        skills=[
            {
                "id": "healthcare-rcm",
                "name": "Healthcare RCM",
                "description": "Eligibility, prior auth, claims, and payer reconciliation.",
                "tags": ["healthcare", "rcm", "crewai"],
                "examples": ["Verify eligibility for member M-12345 with BlueCross."],
            }
        ],
        description="CrewAI-style healthcare RCM crew for revenue cycle automation.",
        version="1.0.0",
        tags=["healthcare", "crewai"],
    )

    compliance_adapter = GenericAdapter(
        name="Compliance Checker Graph",
        handler=compliance_checker_handler,
        skills=[
            {
                "id": "hipaa-compliance",
                "name": "HIPAA Compliance Check",
                "description": "Scan workflow inputs/outputs for PHI and regulatory flags.",
                "tags": ["compliance", "hipaa", "langgraph"],
                "examples": ["Scan this workflow output for HIPAA compliance."],
            }
        ],
        description="LangGraph-style compliance checker for HIPAA and regulatory scanning.",
        version="1.0.0",
        tags=["compliance", "langgraph"],
    )

    orchestrator_adapter = GenericAdapter(
        name="Healthcare + Compliance Orchestrator",
        handler=orchestrated_handler,
        skills=[
            {
                "id": "healthcare-compliance-workflow",
                "name": "Healthcare Compliance Workflow",
                "description": (
                    "End-to-end: run healthcare RCM tasks through a compliance checker. "
                    "Settles via AXIOM on completion."
                ),
                "tags": ["healthcare", "compliance", "orchestration", "cross-framework"],
                "examples": [
                    "Verify eligibility for member M-12345 and run HIPAA compliance check.",
                ],
            }
        ],
        description=(
            "Cross-framework orchestrator: CrewAI healthcare crew + LangGraph compliance "
            "checker, coordinated by SINCOR and settled in AXIOM."
        ),
        version="1.0.0",
        tags=["orchestration", "healthcare", "compliance"],
    )

    return healthcare_adapter, compliance_adapter, orchestrator_adapter


def register_adapters(adapters):
    """Register all adapters with SINCOR marketplace."""
    names = []
    for adapter in adapters:
        try:
            receipt = adapter.register(
                sincor_url=SINCOR_URL,
                agent_base_url=AGENT_BASE_URL,
            )
            names.append(f"  ✓ {adapter.name}: agent_id={receipt.get('agent_id')}")
        except Exception as exc:
            names.append(f"  ⚠ {adapter.name}: registration skipped ({exc})")
    for n in names:
        print(n)


def run_server(orchestrator_adapter):
    """Serve the orchestrator blueprint on PORT."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(orchestrator_adapter.to_flask_blueprint())

    print(f"\nCross-Framework Orchestrator running at {AGENT_BASE_URL}")
    print(f"  Agent Card : {AGENT_BASE_URL}/.well-known/agent-card.json")
    print(f"  A2A RPC    : POST {AGENT_BASE_URL}/api/a2a")
    print(f"\nTest:\n  curl -s -X POST {AGENT_BASE_URL}/api/a2a \\")
    print(f"    -H 'Content-Type: application/json' \\")
    print(f"    -d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"message/send\",\"params\":{{\"message\":{{\"parts\":[{{\"text\":\"Verify member M-12345\"}}]}}}}}}'")

    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    # Add parent dirs to path for adapter imports
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

    print("Building cross-framework workflow adapters …")
    healthcare_adapter, compliance_adapter, orchestrator_adapter = build_adapters()
    print("  ✓ Healthcare RCM Crew (CrewAI-style)")
    print("  ✓ Compliance Checker (LangGraph-style)")
    print("  ✓ Orchestrator")

    print("\nRegistering with SINCOR marketplace …")
    register_adapters([healthcare_adapter, compliance_adapter, orchestrator_adapter])

    run_server(orchestrator_adapter)
