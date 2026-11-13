"""FastAPI adapter — OPTIONAL. FastAPI is never imported at package import
time; importing this module without FastAPI installed raises a clear
ImportError telling you to ``pip install sincor-x402[fastapi]``.

Usage (the honest 2-liner, see README)::

    app.post("/summarize")(verified_route(summarize, requirement=REQ, spec=SPEC))

or as a decorator::

    @app.post("/summarize")
    @sincor_verified(requirement=REQ, spec=SPEC)
    def summarize(payload: dict): ...

The route handler returns the VerifiedEndpoint's status/body verbatim, so
the 402 challenge, verification failures, and settlement receipts reach
the client unchanged.
"""
from __future__ import annotations

from typing import Any, Callable

try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover - exercised only w/o fastapi
    raise ImportError(
        "sincor_x402.fastapi_adapter requires FastAPI: "
        "pip install 'sincor-x402[fastapi]'") from exc

from .server import VerifiedEndpoint
from .types import PaymentRequirement


def sincor_verified(*, requirement: PaymentRequirement, spec: dict[str, Any],
                    **endpoint_kwargs: Any) -> Callable:
    """Decorator: wrap a sync endpoint function in verified escrow flow."""
    def _wrap(func: Callable[..., Any]) -> Callable:
        endpoint = VerifiedEndpoint(func, requirement=requirement, spec=spec,
                                    **endpoint_kwargs)

        async def _route(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            headers = {k: v for k, v in request.headers.items()}
            resp = endpoint.handle(headers=headers, kwargs=payload)
            return JSONResponse(status_code=resp.status, content=resp.body,
                                headers=resp.headers)
        _route.__name__ = getattr(func, "__name__", "sincor_route")
        _route.sincor_endpoint = endpoint  # type: ignore[attr-defined]
        return _route
    return _wrap


def verified_route(func: Callable[..., Any], *, requirement: PaymentRequirement,
                   spec: dict[str, Any], **endpoint_kwargs: Any) -> Callable:
    return sincor_verified(requirement=requirement, spec=spec,
                           **endpoint_kwargs)(func)
