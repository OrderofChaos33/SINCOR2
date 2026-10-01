"""P24 agent skill: ``issue_creator_token`` (agent-side caller).

Calls ``POST /v1/a2a/socialfi/issue`` on the SINCOR A2A platform — the
agent-triggered issuance route. This module is a CALLER, never an enforcer:

- Content-policy screening stays server-side (``defi/p24/policy.py`` and the
  route itself); the skill never sees the deny-list phrases and never
  reproduces them.
- The P24 live block stays server-side; the skill cannot flip it.
- The skill never signs, never holds keys, and never broadcasts.

Transport: pass anything with a ``.post(path, payload)`` method that raises
``sincor2.a2a_sdk.SDKError`` on HTTP >= 400 (``SincorAgentSDK`` itself once it
grows ``issue_creator_token`` — preferred when present — or any
``RequestsTransport``/``FlaskTestTransport``).

Acceptance behavior (documented contract):
- policy denial (HTTP 400 with ``ruleset_version``) -> ``IssuanceSkillError``
  naming the ruleset version and the matched deny-list phrase; the submitted
  text is never echoed back in the error.
- live-block refusal (HTTP 403) -> "creator-token issuance is not live yet"
  message telling the caller to use ``dry_run=True``.
- dry-run happy path (HTTP 201) -> ``IssueResult`` carrying the issued symbol.
- route missing (HTTP 404) -> clean "issuance route not available" error, so
  the skill degrades honestly before the route's branch merges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

ISSUE_PATH = "/v1/a2a/socialfi/issue"

_SYMBOL_RE = r"[A-Z0-9]{1,12}"


class IssuanceSkillError(Exception):
    """A clean, caller-safe failure of the issue_creator_token skill.

    ``code`` is machine-readable: one of "validation", "auth", "policy",
    "live_blocked", "duplicate", "route_missing", "transport", "server".
    ``ruleset_version`` is set for policy denials. The offending metadata is
    never included in the message.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "server",
        status: Optional[int] = None,
        ruleset_version: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.ruleset_version = ruleset_version


@dataclass(frozen=True)
class IssueResult:
    """What the platform returned for a successful (dry-run) issuance."""

    status: str
    creator_id: str
    agent_id: str
    name: str
    symbol: str
    issued_at: str
    total_supply_wei: str
    policy_version: str
    screened: bool
    dry_run: bool
    live_blocked: bool
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "IssueResult":
        known = {
            "status", "creator_id", "agent_id", "name", "symbol", "issued_at",
            "total_supply_wei", "policy_version", "screened", "dry_run",
            "live_blocked",
        }
        return cls(
            status=str(body.get("status", "")),
            creator_id=str(body.get("creator_id", "")),
            agent_id=str(body.get("agent_id", "")),
            name=str(body.get("name", "")),
            symbol=str(body.get("symbol", "")),
            issued_at=str(body.get("issued_at", "")),
            total_supply_wei=str(body.get("total_supply_wei", "")),
            policy_version=str(body.get("policy_version", "")),
            screened=bool(body.get("screened", False)),
            dry_run=bool(body.get("dry_run", True)),
            live_blocked=bool(body.get("live_blocked", True)),
            extra={k: v for k, v in body.items() if k not in known},
        )


def _sdk_error_status(err: BaseException) -> Optional[int]:
    return getattr(err, "status", None)


def _sdk_error_body(err: BaseException) -> Dict[str, Any]:
    body = getattr(err, "body", None)
    return body if isinstance(body, dict) else {}


def _map_sdk_error(err: BaseException) -> IssuanceSkillError:
    """Map a transport/SDK error to a clean skill-level error."""
    status = _sdk_error_status(err)
    body = _sdk_error_body(err)
    ruleset_version = body.get("ruleset_version")
    if isinstance(ruleset_version, str) and ruleset_version:
        version = ruleset_version
    else:
        version = None

    if status == 401:
        return IssuanceSkillError(
            "agent authentication required: the calling agent_id must be a "
            "registered A2A agent",
            code="auth", status=status,
        )
    if status == 400 and version is not None:
        phrase = body.get("matched_phrase")
        field_name = body.get("field")
        detail = f" in {field_name!r}" if field_name else ""
        matched = f"; matched deny-list phrase {phrase!r}" if phrase else ""
        return IssuanceSkillError(
            f"content-policy violation{detail}{matched} "
            f"(ruleset version {version}) — revise the metadata and retry",
            code="policy", status=status, ruleset_version=version,
        )
    if status == 400:
        return IssuanceSkillError(
            "issuance request rejected: "
            f"{body.get('error', 'invalid parameters')}",
            code="validation", status=status,
        )
    if status == 403:
        return IssuanceSkillError(
            "creator-token issuance is not live yet: P24 is live-blocked by "
            "catalog design and requires a founder release — "
            "retry with dry_run=True to rehearse",
            code="live_blocked", status=status,
        )
    if status == 409:
        return IssuanceSkillError(
            "creator already registered: this creator_id has already issued "
            "a token",
            code="duplicate", status=status,
        )
    if status == 404:
        return IssuanceSkillError(
            "issuance route not available: POST /v1/a2a/socialfi/issue is not "
            "served by this platform build",
            code="route_missing", status=status,
        )
    return IssuanceSkillError(
        f"issuance failed: {body.get('error', 'platform error')}",
        code="server", status=status,
    )


def _validate_inputs(
    agent_id: Any, name: Any, symbol: Any, dry_run: Any,
) -> Dict[str, Any]:
    """Fail fast on malformed skill inputs (shape only — policy stays server-side)."""
    agent_id = str(agent_id or "").strip()
    name = str(name or "").strip()
    symbol = str(symbol or "").strip().upper()
    if not agent_id:
        raise IssuanceSkillError(
            "agent_id is required", code="validation")
    if not name or len(name) > 64:
        raise IssuanceSkillError(
            "name must be 1-64 characters", code="validation")
    import re

    if not re.fullmatch(_SYMBOL_RE, symbol):
        raise IssuanceSkillError(
            "symbol must be 1-12 uppercase alphanumeric characters",
            code="validation",
        )
    if not isinstance(dry_run, bool):
        raise IssuanceSkillError(
            "dry_run must be a boolean", code="validation")
    return {"agent_id": agent_id, "name": name, "symbol": symbol,
            "dry_run": dry_run}


def issue_creator_token(
    client: Any,
    *,
    agent_id: str,
    name: str,
    symbol: str,
    description: str = "",
    bio: str = "",
    creator_id: Optional[str] = None,
    dry_run: bool = True,
) -> IssueResult:
    """Issue (dry-run) a P24 creator token via the A2A issuance route.

    ``client``: a ``SincorAgentSDK`` (preferred once it carries
    ``issue_creator_token``) or any transport with ``.post(path, payload)``
    raising ``SDKError`` on HTTP >= 400. ``dry_run`` defaults True; passing
    False hits the server-side live-block gate and returns a clean
    "not live yet" error until the founder releases P24.
    """
    from sincor2.a2a_sdk import SDKError

    checked = _validate_inputs(agent_id, name, symbol, dry_run)

    sdk_issue: Optional[Callable[..., Dict[str, Any]]] = getattr(
        client, "issue_creator_token", None)
    if callable(sdk_issue):
        try:
            body = sdk_issue(
                agent_id=checked["agent_id"],
                name=checked["name"],
                symbol=checked["symbol"],
                creator_id=creator_id,
                description=description,
                bio=bio,
                dry_run=checked["dry_run"],
            )
        except SDKError as err:
            raise _map_sdk_error(err) from err
        except IssuanceSkillError:
            raise
        except Exception as err:  # transport-level failure, never leak internals
            raise IssuanceSkillError(
                "issuance transport failed", code="transport") from err
    else:
        post = getattr(client, "post", None)
        if not callable(post):
            raise IssuanceSkillError(
                "client must expose issue_creator_token() or post()",
                code="validation",
            )
        payload: Dict[str, Any] = {
            "agent_id": checked["agent_id"],
            "name": checked["name"],
            "symbol": checked["symbol"],
            "description": description,
            "bio": bio,
            "dry_run": checked["dry_run"],
        }
        if creator_id:
            payload["creator_id"] = creator_id
        try:
            body = post(ISSUE_PATH, payload)
        except SDKError as err:
            raise _map_sdk_error(err) from err
        except IssuanceSkillError:
            raise
        except Exception as err:
            raise IssuanceSkillError(
                "issuance transport failed", code="transport") from err

    if not isinstance(body, dict) or body.get("status") != "issued":
        raise IssuanceSkillError(
            "unexpected issuance response from platform", code="server")
    return IssueResult.from_body(body)


# ---------------------------------------------------------------------------
# AgentCard catalogue entry (mirrors the SINCOR_SKILLS registration below).
# Kept here so the skill's advertised contract lives next to its caller.
# ---------------------------------------------------------------------------

SKILL_ID = "issue-creator-token"

CATALOG_DESCRIPTION = (
    "Issue a screened P24 creator token for a registered agent via "
    "POST /v1/a2a/socialfi/issue. Metadata is screened against the "
    "content-policy deny-list before issuance; issuance is dry-run only "
    "until the founder releases the P24 live block. Backed by the "
    "issue_creator_token agent skill."
)

CATALOG_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["agent_id", "name", "symbol"],
    "properties": {
        "agent_id": {
            "type": "string",
            "description": "Registered A2A agent id calling the skill",
        },
        "name": {
            "type": "string", "minLength": 1, "maxLength": 64,
            "description": "Token display name",
        },
        "symbol": {
            "type": "string", "pattern": "^[A-Z0-9]{1,12}$",
            "description": "Token symbol: 1-12 uppercase alphanumeric characters",
        },
        "description": {
            "type": "string", "maxLength": 500,
            "description": "Token description (content-policy screened)",
        },
        "bio": {
            "type": "string", "maxLength": 500,
            "description": "Creator bio (content-policy screened)",
        },
        "creator_id": {
            "type": "string",
            "description": "Creator identity; defaults to agent_id",
        },
        "dry_run": {
            "type": "boolean",
            "description": "Default true. False requires the P24 live block "
                           "to be released.",
        },
    },
}

CATALOG_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "symbol": {"type": "string"},
        "name": {"type": "string"},
        "creator_id": {"type": "string"},
        "total_supply_wei": {"type": "string"},
        "policy_version": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "live_blocked": {"type": "boolean"},
    },
}
