"""Web Search + Page Read — x402 paid resource (skills gap G5 / #8).

One buyer payload in — ``{"mode": "search", "query": "..."}`` or
``{"mode": "read", "url": "..."}`` — one sourced, timestamped
result out, priced as the ``web-search-read`` resource in
``config/x402_pricing.yaml`` ($0.01 USDC on Base for BOTH modes —
the winning resellers' price point, skills-gap-map-2026-10-24
§2.1 / §4 item 8; the gap map's most-paid search listings resell
Exa at $0.01).

What machinery exists on main — and what does not:

- There is NO web-search provider in this repo. The Scout
  archetype's discovery machinery (``sincor2.webbuilder_discover``,
  the Vega/Scout pipeline) wraps Google Places / Yelp (both need
  live API keys) plus a keyless single-page meta scrape
  (``_meta_scrape``, ``requests.get`` + ``html.parser``). It has no
  search endpoint and no result-list shape to reuse.
- So this module does NOT invent a provider and does NOT fake one.
  Search and read both run against a PROVIDER SEAM (see
  :func:`search_provider` / :func:`read_provider`): the production
  adapters call the Exa API — the provider the gap map names as
  the resold engine behind the winning listings (§2.1) — with the
  key read from the ``EXA_API_KEY`` environment variable. When the
  key is unset the adapters raise :class:`ProviderNotConfigured`
  and the serve layer fails closed with an honest
  ``503 provider_not_configured`` — never fixture results, never a
  guessed answer. Tests stub the seam with fixtures (the same
  fetch-seam pattern as the sibling ``site_check`` /
  ``market_data_read`` resources): hermetic, no network, no keys.
- The read adapter mirrors ``webbuilder_discover._meta_scrape``
  conventions when it parses Exa Contents output the same way the
  serve layer bounds any provider text (see below). Page text is
  extracted by the provider; THIS module owns the honesty rules:
  bounding, timestamps, and source attribution.

MARGIN GATE (gap map §4 item 8: per-call cost must be measured
against the $0.01 price so a negative-margin route is caught
before launch):

- Documented provider cost model (Exa published pricing, read
  2026-10-24 from exa.ai/pricing as summarised by third-party
  pricing trackers verified 2026-10-20): Search with contents
  $7 per 1,000 requests = **$0.007 per search call**; Contents
  standalone $1 per 1,000 pages = **$0.001 per read call**. These
  are :data:`SEARCH_COST_USD` / :data:`READ_COST_USD`.
- :func:`per_call_cost_usd` is the cost-accounting hook: it
  returns the modeled all-in provider cost for a mode, and every
  served response carries ``price_usd``, ``provider_cost_usd``
  and ``margin_usd`` so the spread is logged with the call, not
  assumed.
- The serve path REFUSES with ``503 margin_guard`` when the
  modeled cost for the requested mode is >= the configured price
  (cost >= price means zero or negative margin). With the real
  figures ($0.007 / $0.001 against $0.01) the guard passes; if the
  provider reprices at or above $0.01, this resource stops serving
  instead of quietly losing money on every call.

Honesty rules:

- Every search item carries ``title``, ``url``, ``snippet``,
  ``source`` and ``retrieved_at``; every response carries a
  top-level ``retrieved_at`` (ISO-8601 UTC, the moment of serving).
- No results is an honest ``200`` with ``results: []`` and
  ``count: 0`` — never padded, never fabricated.
- Read text is BOUNDED: at most :data:`MAX_TEXT_CHARS` (20,000)
  characters are returned, with ``text_chars`` (the full extracted
  length), ``truncated`` and ``text_cap_chars`` stated in the
  response so a buyer can see the cap instead of guessing.
- Provider transport failure is ``503 provider_unavailable`` —
  never an empty result dressed as "nothing found".

Payment is NOT handled here: the read sits behind the standard
x402 flow (bound challenge -> verify with
require_bound_challenge=True -> single-use access token consumed
by ``access_granted`` in the billing blueprint). Malformed
payloads are rejected with 400 AFTER payment, per the platform's
token-consumption pattern (same as the sibling paid resources).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

RESOURCE_ID = "web-search-read"

#: Configured price for BOTH modes (must match
#: config/x402_pricing.yaml: amount_usdc 0.01).
PRICE_USD = 0.01

#: Provider identity behind the seam (see module docstring).
PROVIDER_NAME = "exa"
PROVIDER_KEY_ENV = "EXA_API_KEY"
_EXA_BASE = "https://api.exa.ai"

#: Documented per-call provider cost model — Exa published
#: pricing (exa.ai/pricing, read 2026-10-24): Search with
#: contents $7 / 1,000 requests; Contents $1 / 1,000 pages.
SEARCH_COST_USD = 0.007
READ_COST_USD = 0.001

#: Read-mode extraction cap, in characters (stated in responses).
MAX_TEXT_CHARS = 20_000
#: Fetch bound for provider page bodies, mirroring the repo's
#: 200k meta-scrape bound in webbuilder_discover.
_MAX_PROVIDER_BODY = 200_000

_MAX_QUERY_LEN = 500
_MAX_URL_LEN = 2048
_MAX_RESULTS = 10
_ALLOWED_TOP_KEYS = {"mode", "query", "url"}

_MODES = ("search", "read")


class ProviderNotConfigured(Exception):
    """The provider seam has no API key configured."""


class ProviderUnavailable(Exception):
    """The provider call failed in transport or shape."""


# ---------------------------------------------------------------------------
# Cost accounting hook (the margin gate's data source)
# ---------------------------------------------------------------------------

def per_call_cost_usd(mode: str) -> float:
    """Modeled all-in provider cost of one call, in USD.

    The documented cost model (module docstring): search costs
    :data:`SEARCH_COST_USD`, read costs :data:`READ_COST_USD`.
    Read at call time from the module constants so a repricing is
    a one-line change that the margin guard immediately enforces.
    """
    if mode == "search":
        return SEARCH_COST_USD
    if mode == "read":
        return READ_COST_USD
    raise ValueError(f"unknown mode: {mode!r}")


def margin_guard_tripped(mode: str, price_usd: float = PRICE_USD) -> bool:
    """True when serving ``mode`` at ``price_usd`` would lose money
    (modeled cost >= price). The serve path refuses in that case."""
    return per_call_cost_usd(mode) >= price_usd


# ---------------------------------------------------------------------------
# Provider seam (tests stub these — no network, no keys)
# ---------------------------------------------------------------------------

def _provider_key() -> str:
    return os.environ.get(PROVIDER_KEY_ENV, "").strip()


def _exa_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """One authenticated Exa API call. Raises
    :class:`ProviderNotConfigured` with no key, and
    :class:`ProviderUnavailable` on any transport/shape failure —
    the serve layer turns both into honest 503s."""
    key = _provider_key()
    if not key:
        raise ProviderNotConfigured(
            f"{PROVIDER_KEY_ENV} is not set; web search provider "
            "is not configured")
    import requests

    try:
        resp = requests.post(
            f"{_EXA_BASE}{path}",
            json=payload,
            timeout=15,
            headers={
                "x-api-key": key,
                "Content-Type": "application/json",
                "User-Agent": "SINCOR-WebSearch/1.0 "
                              "(+https://getsincor.com)",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise ProviderUnavailable(str(exc)) from exc
    if not isinstance(data, dict):
        raise ProviderUnavailable("provider returned a non-object body")
    return data


def search_provider(query: str, limit: int = _MAX_RESULTS) -> List[Dict[str, Any]]:
    """Production search adapter (Exa /search).

    Returns raw result dicts with whatever of ``title`` / ``url`` /
    ``text`` / ``source`` the provider supplied; normalisation into
    the served shape happens in the serve layer so fixture results
    and provider results take the exact same path. Raises
    :class:`ProviderNotConfigured` / :class:`ProviderUnavailable`.
    """
    data = _exa_post("/search", {
        "query": query,
        "numResults": max(1, min(limit, _MAX_RESULTS)),
        "contents": {"highlights": True},
    })
    results = data.get("results")
    if not isinstance(results, list):
        raise ProviderUnavailable("provider search body has no results list")
    return [r for r in results if isinstance(r, dict)]


def read_provider(url: str) -> Dict[str, Any]:
    """Production page-read adapter (Exa /contents).

    Returns ``{"url": ..., "title": ..., "text": ...}`` for the
    first matching page. Raises :class:`ProviderNotConfigured` /
    :class:`ProviderUnavailable` (including "provider extracted
    nothing for this URL" — an honest failure, not an empty page).
    """
    data = _exa_post("/contents", {
        "urls": [url],
        "text": True,
    })
    results = data.get("results")
    if not isinstance(results, list) or not results \
            or not isinstance(results[0], dict):
        raise ProviderUnavailable("provider extracted no page for this URL")
    page = results[0]
    return {
        "url": str(page.get("url") or url),
        "title": str(page.get("title") or ""),
        "text": str(page.get("text") or "")[:_MAX_PROVIDER_BODY],
    }


# ---------------------------------------------------------------------------
# Payload validation (strict, fail closed — runs AFTER payment)
# ---------------------------------------------------------------------------

def _err(code: str, status: int = 400, **extra: Any) -> Tuple[int, Dict[str, Any]]:
    body: Dict[str, Any] = {"ok": False, "error": code,
                            "resource": RESOURCE_ID}
    body.update(extra)
    return status, body


def validate_payload(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    """Validate a buyer payload. Returns ``(parsed, None)`` or
    ``(None, (status, body))``. ``parsed`` carries ``mode`` plus
    ``query`` (search) or ``url`` (read)."""
    if not isinstance(payload, dict):
        return None, _err("payload_must_be_object")
    if set(payload) - _ALLOWED_TOP_KEYS:
        return None, _err("unknown_payload_fields")

    mode = payload.get("mode") or ""
    if not isinstance(mode, str) or mode not in _MODES:
        return None, _err("mode_invalid", modes=list(_MODES))

    if mode == "search":
        query = payload.get("query") or ""
        if not isinstance(query, str):
            return None, _err("query_invalid")
        query = query.strip()
        if not query:
            return None, _err("query_required")
        if len(query) > _MAX_QUERY_LEN:
            return None, _err("query_too_long")
        return {"mode": mode, "query": query}, None

    url = payload.get("url") or ""
    if not isinstance(url, str):
        return None, _err("url_invalid")
    url = url.strip()
    if not url:
        return None, _err("url_required")
    if len(url) > _MAX_URL_LEN:
        return None, _err("url_too_long")
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, _err("url_invalid")
    return {"mode": mode, "url": url}, None


# ---------------------------------------------------------------------------
# Serve path (called by the billing blueprint ONLY after the x402
# access token has been consumed)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_for(item: Dict[str, Any], url: str) -> str:
    source = item.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return urlparse(url).netloc or PROVIDER_NAME


def _normalise_item(item: Dict[str, Any], retrieved_at: str) -> Optional[Dict[str, Any]]:
    url = str(item.get("url") or "").strip()
    if not url:
        return None  # a result without a source URL is not a result
    snippet = item.get("snippet") or item.get("text") or ""
    highlights = item.get("highlights")
    if not snippet and isinstance(highlights, list) and highlights:
        snippet = str(highlights[0])
    return {
        "title": str(item.get("title") or ""),
        "url": url,
        "snippet": str(snippet)[:1_000],
        "source": _source_for(item, url),
        "retrieved_at": retrieved_at,
    }


def serve_web_search_read(
    payload: Any,
    *,
    search: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    read: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Run one paid web search or page read.

    ``search`` / ``read`` are the provider seams (defaulting to
    :func:`search_provider` / :func:`read_provider`); tests inject
    fixtures through them. Returns 200 with sourced, timestamped
    results (or an honest empty list), 400 on a malformed payload,
    or 503 when the provider is not configured, is unavailable, or
    the margin guard trips.
    """
    parsed, error = validate_payload(payload)
    if error is not None:
        return error
    assert parsed is not None
    mode = parsed["mode"]

    # Margin gate FIRST: never spend provider money on a call the
    # configured price cannot cover.
    cost = per_call_cost_usd(mode)
    if margin_guard_tripped(mode):
        return _err("margin_guard", 503, mode=mode,
                    price_usd=PRICE_USD, provider_cost_usd=cost)

    accounting = {
        "provider": PROVIDER_NAME,
        "price_usd": PRICE_USD,
        "provider_cost_usd": cost,
        "margin_usd": round(PRICE_USD - cost, 6),
    }
    retrieved_at = _now_iso()

    if mode == "search":
        search_fn = search if search is not None else search_provider
        try:
            raw = search_fn(parsed["query"])
        except ProviderNotConfigured:
            return _err("provider_not_configured", 503, mode=mode,
                        provider=PROVIDER_NAME)
        except Exception:
            return _err("provider_unavailable", 503, mode=mode,
                        provider=PROVIDER_NAME)
        results = []
        for item in (raw or [])[:_MAX_RESULTS]:
            if isinstance(item, dict):
                norm = _normalise_item(item, retrieved_at)
                if norm is not None:
                    results.append(norm)
        return 200, {
            "ok": True,
            "resource": RESOURCE_ID,
            "mode": "search",
            "query": parsed["query"],
            "results": results,
            "count": len(results),
            "retrieved_at": retrieved_at,
            **accounting,
        }

    read_fn = read if read is not None else read_provider
    try:
        page = read_fn(parsed["url"])
    except ProviderNotConfigured:
        return _err("provider_not_configured", 503, mode=mode,
                    provider=PROVIDER_NAME)
    except Exception:
        return _err("provider_unavailable", 503, mode=mode,
                    provider=PROVIDER_NAME)
    if not isinstance(page, dict):
        return _err("provider_unavailable", 503, mode=mode,
                    provider=PROVIDER_NAME)
    full_text = str(page.get("text") or "")
    text = full_text[:MAX_TEXT_CHARS]
    page_url = str(page.get("url") or parsed["url"])
    return 200, {
        "ok": True,
        "resource": RESOURCE_ID,
        "mode": "read",
        "url": page_url,
        "title": str(page.get("title") or ""),
        "text": text,
        "text_chars": len(full_text),
        "truncated": len(full_text) > MAX_TEXT_CHARS,
        "text_cap_chars": MAX_TEXT_CHARS,
        "source": _source_for(page, page_url),
        "retrieved_at": retrieved_at,
        **accounting,
    }
