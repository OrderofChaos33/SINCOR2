"""Publishing adapters: WordPress/Farcaster stubs (WP2).

These are INTENTIONALLY NOT WIRED to real publish APIs. Every publish
attempt returns ``blocked_policy`` — external publishing requires an
explicit owner decision (which channels, who approves, is legal review
mandatory) that has not been made.

Design rules:
- No provider client is constructed. No credentials are read.
- No catch-and-continue: policy failures propagate, they do not fall
  through to generation or draft-saving.
- Draft states (generated/reviewed/approved) are distinct from published.
  Nothing here can report "published" — that literal is unrepresentable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from sincor2.shadow_monitor.effect_boundary import (
    EffectIntent,
    EffectReceipt,
    ShadowEffectBoundary,
    default_shadow_policy,
)

logger = logging.getLogger("sincor2.comms.publishing")


class BlockedPublisher:
    """Stub publisher that always blocks.

    ``publish()`` returns a ``blocked_policy`` receipt via the shadow
    boundary (audited). It never contacts a provider.
    """

    adapter_kind = "publishing_stub_blocked"

    def __init__(
        self,
        channel: str,
        boundary: ShadowEffectBoundary | None = None,
    ):
        if channel not in ("wordpress", "farcaster"):
            raise ValueError(f"unknown publishing channel: {channel!r}")
        self.channel = channel
        # Policy: always blocked_policy with a channel-specific reason.
        # The closure captures the reason; the boundary audits it.
        reason = f"publishing_{channel}_not_approved"
        def _always_block(intent: EffectIntent):  # type: ignore[no-untyped-def]
            return ("blocked_policy", [reason])
        self.boundary = boundary or ShadowEffectBoundary(policy_fn=_always_block)

    def publish(
        self,
        post: Dict[str, Any],
        *,
        originator: str = "agent",
        idempotency_key: str = "",
        tenant: str = "",
    ) -> EffectReceipt:
        """Attempt to publish. Always blocked_policy. Never publishes."""
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        intent = EffectIntent.from_payload(
            effect_type="social.post",
            target=f"publishing://{self.channel}",
            payload={"channel": self.channel, "post": post},
            idempotency_key=f"publish-{self.channel}-{idempotency_key}",
            agent_id=originator,
            tenant=tenant,
            risk_tier="high",  # defense in depth; policy blocks regardless
        )
        receipt = self.boundary.dispatch(intent)
        assert receipt.status == "blocked_policy", (
            f"publishing stub must block, got {receipt.status}"
        )
        assert not receipt.executed
        logger.warning(
            "publishing blocked: channel=%s (not approved)", self.channel
        )
        return receipt

    def delivery_report(self) -> Dict[str, str]:
        return {"status": "not sent", "reason": "publishing-not-approved"}


def get_wordpress_publisher(**kwargs: Any) -> BlockedPublisher:
    """Return the WordPress stub (always blocked)."""
    return BlockedPublisher(channel="wordpress", **kwargs)


def get_farcaster_publisher(**kwargs: Any) -> BlockedPublisher:
    """Return the Farcaster stub (always blocked)."""
    return BlockedPublisher(channel="farcaster", **kwargs)
