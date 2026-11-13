"""MCP adapter — wrap a plain tool function, no hard MCP SDK dependency.

Adapter pattern: ``VerifiedTool`` owns the escrow/verify/settle flow (via
``VerifiedEndpoint``) and exposes three surfaces:

* ``call_paid(payment_header=..., **kwargs)`` — transport-agnostic core any
  MCP host can call from its own ``tools/call`` handler;
* ``tool_definition()`` — an MCP-shaped tool descriptor (name / description
  / inputSchema) a host can merge into its ``tools/list`` response;
* ``register(server)`` — optional convenience: if the host object has a
  ``tool`` decorator (FastMCP style) it is used; otherwise this is a no-op
  and the host wires ``call_paid`` itself. The ``mcp`` package is never
  imported by this module.
"""
from __future__ import annotations

import functools
from typing import Any, Callable

from .server import HandlerResponse, VerifiedEndpoint
from .types import PaymentRequirement


class VerifiedTool:
    def __init__(self, func: Callable[..., Any], *,
                 requirement: PaymentRequirement, spec: dict[str, Any],
                 name: str | None = None, description: str = "",
                 input_schema: dict[str, Any] | None = None,
                 **endpoint_kwargs: Any) -> None:
        self._endpoint = VerifiedEndpoint(func, requirement=requirement,
                                          spec=spec, **endpoint_kwargs)
        self._func = func
        self.name = name or getattr(func, "__name__", "tool")
        self.description = description or (getattr(func, "__doc__", "") or "")
        self.input_schema = input_schema or {"type": "object", "properties": {}}
        functools.update_wrapper(self, func)

    @property
    def endpoint(self) -> VerifiedEndpoint:
        return self._endpoint

    def tool_definition(self) -> dict[str, Any]:
        """MCP ``tools/list``-shaped descriptor for this wrapped tool."""
        return {
            "name": self.name,
            "description": (self.description
                            + " [Paid via sincor-x402 verified escrow: "
                              "402 → escrow → SIGIL-committed verification → "
                              "SEAL release/refund.]"),
            "inputSchema": self.input_schema,
            "x-sincor-escrow": self._endpoint.terms.to_dict(),
            "x-sincor-accepts": [self._endpoint.requirement.to_accepts_dict()],
        }

    def call_paid(self, *, payment_header: str | None = None,
                  **kwargs: Any) -> HandlerResponse:
        """Run one paid tool call. No payment header → the 402 challenge."""
        headers = {"X-PAYMENT": payment_header} if payment_header else {}
        return self._endpoint.handle(headers=headers, kwargs=kwargs)

    def register(self, server: Any) -> bool:
        """Best-effort registration on FastMCP-style servers.

        Returns True if a ``tool`` decorator was found and used; False if
        the host offers no such surface (host should then route its own
        tools/call handler to ``call_paid``). Never raises on a foreign
        server shape and never imports an MCP SDK.
        """
        tool_decorator = getattr(server, "tool", None)
        if not callable(tool_decorator):
            return False

        def _host_fn(**kwargs: Any) -> Any:
            resp = self.call_paid(**kwargs)
            return {"status": resp.status, **resp.body}

        _host_fn.__name__ = self.name
        tool_decorator(name=self.name, description=self.description)(_host_fn)
        return True


def verified_tool(*, requirement: PaymentRequirement, spec: dict[str, Any],
                  **kwargs: Any) -> Callable[[Callable[..., Any]], VerifiedTool]:
    """Decorator form::

        @verified_tool(requirement=REQ, spec={"required_keys": ["answer"]})
        def my_tool(question: str): ...
    """
    def _wrap(func: Callable[..., Any]) -> VerifiedTool:
        return VerifiedTool(func, requirement=requirement, spec=spec, **kwargs)
    return _wrap
