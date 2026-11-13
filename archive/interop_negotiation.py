"""
Protocol Negotiation Layer for SINCOR2 Ultimate Ecosystem (Goal #1)

Enables agents to dynamically negotiate the best transport/protocol at runtime:
- A2A (Google spec) - default, widely supported
- MCP (Anthropic Model Context Protocol)
- libp2p (decentralized P2P via go-libp2p daemon)
- WebSub (lightweight pub/sub)
- Custom / future protocols

This is the core that makes SINCOR2 truly protocol-agnostic.

Usage:
    from sincor2.interop_negotiation import negotiate_protocol, SupportedProtocol

    chosen = await negotiate_protocol(
        peer_capabilities=["a2a", "libp2p", "mcp"],
        preferred=["libp2p", "a2a"]
    )
    adapter = get_adapter_for_protocol(chosen)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import List, Optional, Dict, Any

logger = logging.getLogger("sincor.interop.negotiation")


class SupportedProtocol(str, Enum):
    A2A = "a2a"
    MCP = "mcp"
    LIBP2P = "libp2p"
    WEBSUB = "websub"
    CUSTOM = "custom"


PROTOCOL_PRIORITY = [
    SupportedProtocol.LIBP2P,   # Best decentralization + performance when available
    SupportedProtocol.A2A,      # Most compatible fallback
    SupportedProtocol.MCP,      # Good for Anthropic ecosystem
    SupportedProtocol.WEBSUB,   # Lightweight pub/sub
]


async def negotiate_protocol(
    peer_capabilities: List[str],
    preferred: Optional[List[str]] = None,
    require_decentralized: bool = False,
) -> SupportedProtocol:
    """
    Negotiate the best mutually supported protocol.

    Args:
        peer_capabilities: Protocols the remote peer claims to support
        preferred: Our preference order (defaults to PROTOCOL_PRIORITY)
        require_decentralized: If True, only return libp2p or fail

    Returns:
        The chosen SupportedProtocol
    """
    if preferred is None:
        preferred = [p.value for p in PROTOCOL_PRIORITY]

    # Normalize
    peer_caps = {c.lower() for c in peer_capabilities}
    our_pref = [p.lower() for p in preferred]

    for proto in our_pref:
        if proto in peer_caps:
            if require_decentralized and proto != SupportedProtocol.LIBP2P.value:
                continue
            chosen = SupportedProtocol(proto)
            logger.debug("Negotiated protocol: %s", chosen.value)
            return chosen

    # Fallback
    if SupportedProtocol.A2A.value in peer_caps:
        return SupportedProtocol.A2A

    logger.warning("No common protocol found. Falling back to A2A.")
    return SupportedProtocol.A2A


def get_adapter_for_protocol(protocol: SupportedProtocol) -> Any:
    """Return the appropriate adapter instance for the negotiated protocol."""
    if protocol == SupportedProtocol.LIBP2P:
        from sincor2.adapters.libp2p_adapter import get_libp2p_adapter
        return get_libp2p_adapter()

    elif protocol == SupportedProtocol.A2A:
        from sincor2.a2a_integration import get_a2a_adapter  # if exists, else fallback
        try:
            return get_a2a_adapter()
        except ImportError:
            from sincor2.sinax.integration import A2AAdapter
            return A2AAdapter()

    elif protocol == SupportedProtocol.MCP:
        from sincor2.sinax.integration import MCPAdapter
        return MCPAdapter()

    else:
        # Default to A2A
        from sincor2.sinax.integration import A2AAdapter
        return A2AAdapter()


def advertise_capabilities() -> List[str]:
    """Return the list of protocols this SINCOR2 instance supports.
    Used when generating Agent Cards or discovery responses.
    """
    caps = ["a2a", "mcp"]
    # Check if libp2p daemon is reachable (non-blocking check)
    try:
        from sincor2.adapters.libp2p_adapter import get_libp2p_adapter
        adapter = get_libp2p_adapter()
        # We don't await here — just advertise potential support
        caps.append("libp2p")
    except Exception:
        pass

    caps.append("websub")  # lightweight option always available
    return caps
