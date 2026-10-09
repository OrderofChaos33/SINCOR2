"""WP3 capability manifest: every queue task kind declares its effects.

Fail-closed by design:
- Unknown task kinds are REJECTED (no manifest = no execution).
- Handlers whose effects include value-moving types require
  originator="operator" (D5: head orchestration under supervision).
- The manifest is frozen at import; runtime registration of new kinds
  requires an explicit manifest entry (no silent capability expansion).

Contract: src/sincor2/shadow_monitor/contract.py (WP0, frozen).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from sincor2.shadow_monitor.contract import (
    EFFECT_VOCABULARY,
    VALUE_MOVING_EFFECTS,
    RISK_TIERS,
)


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Declared capabilities for one queue task kind.

    effect_types: subset of the WP0 EFFECT_VOCABULARY that this handler
        may produce (directly or via downstream calls).
    max_risk_tier: highest risk tier this handler may operate at.
    requires_operator: True if any effect is value-moving (D5 rule).
    description: human-readable scope note for audit.
    """

    kind: str
    effect_types: frozenset[str]
    max_risk_tier: str
    description: str = ""

    def __post_init__(self) -> None:
        unknown = self.effect_types - EFFECT_VOCABULARY
        if unknown:
            raise ValueError(
                f"manifest for {self.kind!r} declares unknown effect types: {sorted(unknown)}"
            )
        if self.max_risk_tier not in RISK_TIERS:
            raise ValueError(f"unknown risk_tier: {self.max_risk_tier!r}")
        if not self.kind:
            raise ValueError("manifest kind is required")

    @property
    def requires_operator(self) -> bool:
        """D5: value-moving effects require operator originator."""
        return bool(self.effect_types & VALUE_MOVING_EFFECTS)

    @property
    def is_read_only(self) -> bool:
        return not self.effect_types


# ---------------------------------------------------------------------------
# Registered task-kind manifests (audited 2026-10-09)
# ---------------------------------------------------------------------------

_MANIFESTS: dict[str, CapabilityManifest] = {
    # Agent execution entry point. Dispatches tasks to the swarm; agents
    # communicate via message.send. contract.call is declared because the
    # on-chain anchor/fund paths exist in a2a_inbound_market (gated OFF
    # by AUCTION_ONCHAIN_ANCHOR / AUCTION_ONCHAIN_FUND per D4, but the
    # capability must be declared so the manifest check can enforce the
    # operator-originator rule if those flags are ever enabled).
    "a2a.execute": CapabilityManifest(
        kind="a2a.execute",
        effect_types=frozenset({"message.send", "contract.call"}),
        max_risk_tier="critical",
        description="A2A task dispatch to agent swarm; may reach on-chain "
        "anchor/fund paths (flag-gated OFF per D4).",
    ),
    # Content generation. generate_blog_post + save_post are local; the
    # do_publish path reaches WordPressPublisher.publish (external post).
    "content.generate": CapabilityManifest(
        kind="content.generate",
        effect_types=frozenset({"social.post"}),
        max_risk_tier="medium",
        description="Blog content generation; WordPress publish on do_publish.",
    ),
    # WebBuilder autonomous run. run_autonomous_phases can drive the
    # project to live via publish_live (GHL republish to production).
    "webbuilder.run": CapabilityManifest(
        kind="webbuilder.run",
        effect_types=frozenset({"social.post"}),
        max_risk_tier="high",
        description="Autonomous webbuilder phases; may publish live lane.",
    ),
    # WebBuilder draft rebuild. Draft-only; does not touch live until
    # explicit republish (separate operator action).
    "webbuilder.rebuild": CapabilityManifest(
        kind="webbuilder.rebuild",
        effect_types=frozenset(),
        max_risk_tier="medium",
        description="Draft rebuild only; live lane untouched.",
    ),
}

# Frozen public view — no runtime mutation.
MANIFESTS: Mapping[str, CapabilityManifest] = MappingProxyType(_MANIFESTS)


class UnknownTaskKindError(RuntimeError):
    """Raised when a task kind has no capability manifest. Fail closed."""


class OperatorRequiredError(PermissionError):
    """Raised when a value-moving handler is invoked without operator origin."""


def get_manifest(kind: str) -> CapabilityManifest:
    """Return the manifest for kind, or raise UnknownTaskKindError."""
    manifest = _MANIFESTS.get(kind)
    if manifest is None:
        raise UnknownTaskKindError(
            f"no capability manifest for task kind {kind!r}; "
            "refusing to execute (fail closed)"
        )
    return manifest


def check_dispatch(kind: str, originator: str) -> CapabilityManifest:
    """Gate a queue dispatch through the capability manifest.

    1. Unknown kind -> UnknownTaskKindError (fail closed).
    2. Value-moving effects without operator originator ->
       OperatorRequiredError (D5 head-orchestration rule).
    3. Returns the manifest on success.
    """
    manifest = get_manifest(kind)
    if manifest.requires_operator and originator != "operator":
        raise OperatorRequiredError(
            f"task kind {kind!r} declares value-moving effects "
            f"{sorted(manifest.effect_types & VALUE_MOVING_EFFECTS)}; "
            f"originator must be 'operator', got {originator!r} (D5)"
        )
    return manifest
