"""Application-facing, proposal-only shadow runtime.

This module deliberately constructs no provider client, signer, wallet, queue
producer, or live executor.  Its effect entrypoints accept typed EffectIntent
objects and can only return hash-only shadow receipts via LogOnlyAdapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Literal, Mapping, Optional

from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    EffectIntent,
    EffectReceipt,
    LogOnlyAdapter,
    ShadowEffectBoundary,
    build_effect_boundary,
)

EffectEntrypoint = Callable[[EffectIntent], EffectReceipt]


@dataclass(frozen=True, slots=True)
class ShadowRuntime:
    """Read-only capability container installed into the production Flask app.

    ``effect_entrypoints`` is immutable and maps each canonical effect type to
    a type-checking proposal-only handler.  This object intentionally offers
    no method that can execute a live effect.
    """

    mode: Literal["shadow"]
    effect_boundary: ShadowEffectBoundary
    effect_entrypoints: Mapping[str, EffectEntrypoint]
    live_executor: None = None
    signer: None = None
    write_credentials: tuple[str, ...] = ()

    def entrypoint_for(self, effect_type: str) -> EffectEntrypoint:
        """Return a known proposal handler; reject unknown effects closed."""
        try:
            return self.effect_entrypoints[effect_type]
        except KeyError as exc:
            raise ValueError(f"unknown shadow effect type: {effect_type!r}") from exc


def build_shadow_runtime(profile: Optional[str] = None) -> ShadowRuntime:
    """Build the only runtime profile available to agent-facing application code.

    ``profile='live'`` is rejected by ``build_effect_boundary``.  Live execution
    must be a separate artifact/service with independent credentials and must
    never be obtained by changing this process's configuration.
    """
    boundary = build_effect_boundary(profile)
    adapter = LogOnlyAdapter(boundary)
    entrypoints: dict[str, EffectEntrypoint] = {}

    for effect_type in sorted(EFFECT_TYPES):
        def propose_only(
            intent: EffectIntent,
            *,
            expected_effect_type: str = effect_type,
            _adapter: LogOnlyAdapter = adapter,
        ) -> EffectReceipt:
            if not isinstance(intent, EffectIntent):
                raise TypeError("shadow effect entrypoints require an EffectIntent")
            if intent.effect_type != expected_effect_type:
                raise ValueError(
                    "effect type does not match entrypoint: "
                    f"expected {expected_effect_type!r}, got {intent.effect_type!r}"
                )
            return _adapter.execute(intent)

        entrypoints[effect_type] = propose_only

    return ShadowRuntime(
        mode="shadow",
        effect_boundary=boundary,
        effect_entrypoints=MappingProxyType(entrypoints),
    )
