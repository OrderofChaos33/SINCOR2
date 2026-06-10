"""Generic SINCOR adapter — wraps any Python callable as an A2A participant.

Usage
-----
Decorator style (simplest)::

    from adapters.generic_adapter import sincor_agent

    @sincor_agent(
        name="My Research Agent",
        description="Searches the web and summarizes findings.",
        skills=[
            {"id": "web-search", "name": "Web Search",
             "description": "Search and summarize web content.",
             "tags": ["research", "search"]},
        ],
        version="1.0.0",
        tags=["research"],
    )
    def handle_task(task: dict) -> dict:
        # task keys: task_type, payload, metadata, correlation_id
        return {"status": "success", "result": {"summary": "..."}}

    # To register with SINCOR marketplace:
    receipt = handle_task.register(sincor_url="https://getsincor.com",
                                   agent_base_url="https://my-agent.com")

Class style::

    from adapters.generic_adapter import GenericAdapter

    adapter = GenericAdapter(
        name="My Agent",
        handler=my_function,
        skills=[...],
    )
    blueprint = adapter.to_flask_blueprint()  # mount in your Flask app
    receipt   = adapter.register(sincor_url="...", agent_base_url="...")
"""

from __future__ import annotations

import functools
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("sincor.adapters.generic")

SINCOR_URL = os.getenv("SINCOR_PLATFORM_URL", "https://getsincor.com")
REGISTRATION_PATH = "/api/marketplace/register"


class GenericAdapter:
    """Wraps any Python callable as an A2A-compliant SINCOR participant.

    Parameters
    ----------
    name:
        Human-readable agent name (used in the Agent Card).
    handler:
        Python callable that receives a task dict and returns a result dict.
        Expected keys in the input dict: ``task_type``, ``payload``,
        ``metadata``, ``correlation_id``.
        Must return a dict with at least a ``status`` key.
    skills:
        List of skill dicts.  Each skill must have ``id`` and ``name``; the
        optional keys ``description``, ``tags``, ``examples`` are recommended.
    description:
        Agent description for the Agent Card.
    version:
        Semantic version string (default ``"1.0.0"``).
    tags:
        Top-level agent tags for discovery.
    provider_name:
        Provider organisation name (defaults to the agent name).
    provider_url:
        Provider URL (defaults to ``SINCOR_PLATFORM_URL``).
    """

    def __init__(
        self,
        name: str,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        skills: List[Dict[str, Any]],
        description: str = "",
        version: str = "1.0.0",
        tags: Optional[List[str]] = None,
        provider_name: Optional[str] = None,
        provider_url: Optional[str] = None,
    ) -> None:
        self.name = name
        self.handler = handler
        self.skills = skills
        self.description = description or f"A2A agent: {name}"
        self.version = version
        self.tags = tags or []
        self.provider_name = provider_name or name
        self.provider_url = provider_url or SINCOR_URL

    # ------------------------------------------------------------------
    # Agent Card
    # ------------------------------------------------------------------

    def build_agent_card(self, base_url: str = "") -> Dict[str, Any]:
        """Return an A2A v1.0.1 compliant Agent Card dict."""
        rpc_url = f"{base_url.rstrip('/')}/api/a2a" if base_url else "/api/a2a"
        enriched_skills = []
        for skill in self.skills:
            s = dict(skill)
            s.setdefault("description", skill.get("name", ""))
            s.setdefault("tags", self.tags)
            s.setdefault("examples", [])
            s.setdefault("inputModes", ["text/plain", "application/json"])
            s.setdefault("outputModes", ["text/plain", "application/json"])
            enriched_skills.append(s)
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "supportedInterfaces": [
                {
                    "url": rpc_url,
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0.1",
                }
            ],
            "provider": {
                "organization": self.provider_name,
                "url": self.provider_url,
            },
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": enriched_skills,
        }

    # ------------------------------------------------------------------
    # Flask Blueprint
    # ------------------------------------------------------------------

    def to_flask_blueprint(self, url_prefix: str = ""):
        """Return a Flask Blueprint that serves the A2A endpoint for this agent.

        Mount the returned blueprint in your Flask app::

            from flask import Flask
            app = Flask(__name__)
            app.register_blueprint(adapter.to_flask_blueprint())
        """
        try:
            from flask import Blueprint, jsonify, request as flask_request
        except ImportError as exc:
            raise ImportError(
                "Flask is required to use to_flask_blueprint(). "
                "Install it with: pip install flask"
            ) from exc

        safe_name = self.name.lower().replace(" ", "_")
        bp = Blueprint(f"sincor_adapter_{safe_name}", __name__, url_prefix=url_prefix)

        agent_card = self.build_agent_card()

        @bp.get("/.well-known/agent-card.json")
        def agent_card_endpoint():  # type: ignore[return-value]
            return jsonify(agent_card)

        @bp.get("/.well-known/agent.json")
        def agent_card_legacy():  # type: ignore[return-value]
            return jsonify(agent_card)

        handler_ref = self.handler

        @bp.post("/api/a2a")
        def a2a_rpc():  # type: ignore[return-value]
            body = flask_request.get_json(silent=True) or {}
            method = body.get("method", "")
            rpc_id = body.get("id")
            params = body.get("params", {})

            if method == "message/send":
                message = params.get("message", {})
                parts = message.get("parts", [])
                input_text = " ".join(
                    p.get("text", "") for p in parts if p.get("kind") == "text" or "text" in p
                )
                skill_id = (
                    params.get("configuration", {}).get("skill")
                    or (self.skills[0]["id"] if self.skills else "default")
                )
                task_result = handler_ref(
                    {
                        "task_type": skill_id.replace("-", "_"),
                        "payload": {"input": input_text, "params": params},
                        "correlation_id": str(rpc_id),
                    }
                )
                task_id = str(uuid.uuid4())
                return jsonify(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "id": task_id,
                            "status": {"state": "completed"},
                            "artifacts": [
                                {
                                    "artifactId": str(uuid.uuid4()),
                                    "parts": [
                                        {
                                            "kind": "text",
                                            "text": json.dumps(task_result),
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                )

            # Unrecognised method
            return jsonify(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not supported by this adapter.",
                    },
                }
            ), 400

        return bp

    # ------------------------------------------------------------------
    # Self-registration
    # ------------------------------------------------------------------

    def register(
        self,
        sincor_url: str = SINCOR_URL,
        agent_base_url: str = "",
        sinc_stake: int = 0,
        api_key: Optional[str] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """Register this adapter as a marketplace participant.

        Parameters
        ----------
        sincor_url:
            SINCOR platform base URL.
        agent_base_url:
            The public base URL where this agent's A2A endpoint is hosted.
        sinc_stake:
            Optional SINC token amount to stake for routing priority.
        api_key:
            SINCOR API key (or ``SINCOR_API_KEY`` env var).
        """
        api_key = api_key or os.getenv("SINCOR_API_KEY", "")
        card = self.build_agent_card(base_url=agent_base_url)
        payload = json.dumps(
            {
                "agent_card": card,
                "agent_url": agent_base_url,
                "sinc_stake": sinc_stake,
            }
        ).encode()
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["X-API-Key"] = api_key

        url = f"{sincor_url.rstrip('/')}{REGISTRATION_PATH}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"SINCOR registration failed (HTTP {exc.code}): {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach SINCOR at {url}: {exc.reason}"
            ) from exc


# ---------------------------------------------------------------------------
# Decorator helper
# ---------------------------------------------------------------------------

def sincor_agent(
    name: str,
    skills: List[Dict[str, Any]],
    description: str = "",
    version: str = "1.0.0",
    tags: Optional[List[str]] = None,
    provider_name: Optional[str] = None,
    provider_url: Optional[str] = None,
):
    """Decorator that turns any function into a SINCOR-registered A2A agent.

    The wrapped function gains:
    - ``.adapter`` — the underlying :class:`GenericAdapter` instance.
    - ``.build_agent_card(base_url)`` — returns the A2A Agent Card dict.
    - ``.to_flask_blueprint()`` — returns a Flask Blueprint.
    - ``.register(sincor_url, agent_base_url, sinc_stake)`` — registers with SINCOR.

    Example::

        @sincor_agent(
            name="Currency Converter",
            skills=[{"id": "convert-currency", "name": "Convert Currency"}],
        )
        def handle(task):
            return {"status": "success", "result": {"converted": 42.0}}

        handle.register(agent_base_url="https://my-agent.example.com")
    """

    def decorator(func: Callable[[Dict[str, Any]], Dict[str, Any]]):
        adapter = GenericAdapter(
            name=name,
            handler=func,
            skills=skills,
            description=description,
            version=version,
            tags=tags or [],
            provider_name=provider_name,
            provider_url=provider_url,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            return func(*args, **kwargs)

        wrapper.adapter = adapter  # type: ignore[attr-defined]
        wrapper.build_agent_card = adapter.build_agent_card  # type: ignore[attr-defined]
        wrapper.to_flask_blueprint = adapter.to_flask_blueprint  # type: ignore[attr-defined]
        wrapper.register = adapter.register  # type: ignore[attr-defined]
        return wrapper

    return decorator
