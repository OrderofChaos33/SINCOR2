#!/usr/bin/env python3
"""
register_agent.py — SINCOR Marketplace Self-Registration
=========================================================
Any external agent can join the SINCOR marketplace in <5 lines:

    from scripts.register_agent import register_to_sincor
    receipt = register_to_sincor("https://my-agent.example.com", sinc_stake=0)
    print(receipt)

Or as a CLI:

    python scripts/register_agent.py --agent-url https://my-agent.example.com \\
           --sincor-url https://getsincor.com --sinc-stake 100

Flow
----
1. Fetch ``/.well-known/agent-card.json`` from the external agent.
2. Validate A2A v1.0.1 compliance (required fields + at least one skill).
3. POST the Agent Card to the SINCOR marketplace registration endpoint.
4. Return a registration receipt with agent_id, SINC stake, and routing status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

SINCOR_URL = os.getenv("SINCOR_PLATFORM_URL", "https://getsincor.com")
REGISTRATION_PATH = "/api/marketplace/register"
_REQUIRED_CARD_FIELDS = ("name", "description", "version")
_REQUIRED_SKILLS_FIELD = "skills"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class A2AValidationError(ValueError):
    """Raised when an Agent Card does not meet A2A v1.0.1 minimum requirements."""


def validate_agent_card(card: Dict[str, Any]) -> None:
    """Raise :exc:`A2AValidationError` if *card* is not A2A v1.0.1 compliant.

    Minimum requirements:
    - ``name``, ``description``, ``version`` fields present and non-empty.
    - At least one entry in ``skills`` (each with ``id`` and ``name``).
    - ``supportedInterfaces`` present with at least one entry containing ``url``.
    """
    for field in _REQUIRED_CARD_FIELDS:
        if not card.get(field):
            raise A2AValidationError(
                f"Agent Card missing required field '{field}'."
            )

    skills = card.get("skills", [])
    if not skills:
        raise A2AValidationError(
            "Agent Card must advertise at least one skill in 'skills'."
        )
    for idx, skill in enumerate(skills):
        if not skill.get("id") or not skill.get("name"):
            raise A2AValidationError(
                f"Skill at index {idx} must have 'id' and 'name' fields."
            )

    interfaces = card.get("supportedInterfaces", [])
    if not interfaces or not interfaces[0].get("url"):
        raise A2AValidationError(
            "Agent Card must include 'supportedInterfaces' with at least one "
            "entry containing a 'url'."
        )


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def fetch_agent_card(agent_url: str, timeout: int = 10) -> Dict[str, Any]:
    """Fetch the A2A Agent Card from ``{agent_url}/.well-known/agent-card.json``.

    Falls back to ``{agent_url}/.well-known/agent.json`` for legacy agents.
    """
    agent_url = agent_url.rstrip("/")
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        url = f"{agent_url}{path}"
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise RuntimeError(f"HTTP {exc.code} fetching Agent Card from {url}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach agent at {url}: {exc.reason}") from exc

    raise RuntimeError(
        f"No A2A Agent Card found at {agent_url}. "
        "Expected /.well-known/agent-card.json or /.well-known/agent.json."
    )


def register_to_sincor(
    agent_url: str,
    sincor_url: str = SINCOR_URL,
    sinc_stake: int = 0,
    api_key: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """Register an external A2A agent with the SINCOR marketplace.

    Parameters
    ----------
    agent_url:
        Public base URL of the external agent (must serve
        ``/.well-known/agent-card.json``).
    sincor_url:
        SINCOR platform base URL (default: ``https://getsincor.com`` or
        ``SINCOR_PLATFORM_URL`` env var).
    sinc_stake:
        Optional SINC token amount (integer, whole tokens) to stake for
        reputation boost.  Stake is recorded off-chain; on-chain staking
        requires a separate transaction.
    api_key:
        Optional SINCOR API key (``SINCOR_API_KEY`` env var is also checked).
    timeout:
        HTTP timeout in seconds for all requests.

    Returns
    -------
    dict
        Registration receipt with ``agent_id``, ``status``, ``sinc_stake``,
        ``routing_priority``, and ``marketplace_url``.
    """
    api_key = api_key or os.getenv("SINCOR_API_KEY", "")

    # Step 1: fetch and validate Agent Card
    card = fetch_agent_card(agent_url, timeout=timeout)
    validate_agent_card(card)

    # Step 2: submit to SINCOR marketplace
    payload = json.dumps(
        {
            "agent_card": card,
            "agent_url": agent_url,
            "sinc_stake": sinc_stake,
        }
    ).encode()

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-API-Key"] = api_key

    registration_url = f"{sincor_url.rstrip('/')}{REGISTRATION_PATH}"
    req = urllib.request.Request(
        registration_url,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            receipt = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"SINCOR registration failed (HTTP {exc.code}): {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach SINCOR at {registration_url}: {exc.reason}"
        ) from exc

    return receipt


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Register an external A2A-compliant agent with the SINCOR "
            "marketplace. Fetches the agent's Agent Card, validates A2A "
            "compliance, and submits it for discovery + routing."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  # Register with default SINCOR URL (https://getsincor.com)
  python scripts/register_agent.py --agent-url https://my-agent.example.com

  # Register with a SINC stake for routing priority boost
  python scripts/register_agent.py \\
      --agent-url https://my-agent.example.com \\
      --sinc-stake 500 \\
      --api-key sk-...

  # Register against a local SINCOR instance
  python scripts/register_agent.py \\
      --agent-url http://localhost:8001 \\
      --sincor-url http://localhost:5000
        """,
    )
    parser.add_argument(
        "--agent-url",
        required=True,
        help="Public base URL of the external agent.",
    )
    parser.add_argument(
        "--sincor-url",
        default=SINCOR_URL,
        help="SINCOR platform base URL (default: https://getsincor.com).",
    )
    parser.add_argument(
        "--sinc-stake",
        type=int,
        default=0,
        help="SINC token amount to stake for routing priority boost.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="SINCOR API key (or set SINCOR_API_KEY env var).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Fetch and validate the Agent Card without registering.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    print(f"Fetching Agent Card from {args.agent_url} …")
    try:
        card = fetch_agent_card(args.agent_url)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  Agent: {card.get('name')} v{card.get('version')}")
    print(f"  Skills: {len(card.get('skills', []))}")

    try:
        validate_agent_card(card)
        print("  A2A compliance: PASS ✓")
    except A2AValidationError as exc:
        print(f"  A2A compliance: FAIL ✗ — {exc}", file=sys.stderr)
        sys.exit(1)

    if args.validate_only:
        print("Validation complete. Skipping registration (--validate-only).")
        return

    print(f"\nRegistering with SINCOR at {args.sincor_url} …")
    try:
        receipt = register_to_sincor(
            agent_url=args.agent_url,
            sincor_url=args.sincor_url,
            sinc_stake=args.sinc_stake,
            api_key=args.api_key,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nRegistration receipt:")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
