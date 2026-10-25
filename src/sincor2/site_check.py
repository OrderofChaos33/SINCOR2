"""Local-Business Site Check — x402 paid resource (skills gap G8).

One business in (a website URL, or a business name with no site), one
honest site-quality verdict out, priced as the
``local-business-site-check`` resource in ``config/x402_pricing.yaml``
($0.01 USDC on Base — the Bazaar median per-call price,
skills-gap-map-2026-10-24 §2.1 / §4 item 6). The read feeds the
existing $497 rail webbuilder pack (``rail-pack-webbuilder_site``,
TOA-priced, PRs #518-#521) — but ONLY when it honestly recommends a
build (see below).

What machinery is genuinely reused — and what is not:

- The HTML inspection approach follows the repo's existing public
  meta-scrape parser in ``sincor2.webbuilder_discover`` (the Scout /
  Vega discovery module): fetch the page, parse title / meta / h1
  with ``html.parser``. That module's Places/Yelp paths need live
  ``GOOGLE_PLACES_API_KEY`` / ``YELP_API_KEY`` credentials and its
  parser does not extract the signals this check needs (viewport,
  contact links), so this module does NOT call Scout/Places: the
  check runs on a single injectable fetch seam (see
  :func:`fetch_site_page`) plus its own parser. No external API keys,
  no live network in tests — tests stub the seam with fixtures.
- The upsell reference is the REAL rail webbuilder SKU: the
  ``webbuilder_site`` entry of ``sincor2.rail_routes.PACK_SKUS``
  ($497.00, resource ``rail-pack-webbuilder_site``, checkout at
  ``/rail/checkout``). The price is read from that table at serve
  time so this module can never drift from the rail's own price.

HONESTY CORE (the product's reason to exist):

- Verdicts are exactly three: ``build_recommended``,
  ``no_build_needed``, ``unknown``.
- ``build_recommended`` requires NAMED deficiencies, each carrying
  the observation that proves it (e.g. ``missing_viewport`` with
  evidence "no <meta name=viewport> in fetched HTML"). A deficiency
  is never asserted without its evidence.
- A business whose fetched site passes every check gets
  ``no_build_needed`` and NO webbuilder next-step — the check must
  never manufacture a deficiency to upsell the $497 pack.
- Unreachable (transport failure) or insufficient data (empty body)
  is ``unknown`` — never guessed into either other verdict.
- The webbuilder next-step appears in the response ONLY on
  ``build_recommended``.

Checks performed (all deterministic from the fetched HTML / URL):
``no_website`` (business named, no URL supplied),
``site_error_status`` (HTTP >= 400, evidence is the status code),
``not_https`` (URL scheme is http), ``missing_title``,
``missing_meta_description``, ``missing_viewport`` (mobile basics),
``missing_h1``, ``no_contact_path`` (no tel:/mailto: link and no
contact-page link in the fetched HTML).

Payment is NOT handled here: the read sits behind the standard x402
flow (bound challenge -> verify with require_bound_challenge=True ->
single-use access token consumed by ``access_granted`` in the
billing blueprint). Malformed payloads are rejected with 400 AFTER
payment, per the platform's token-consumption pattern (same as the
sibling paid resources).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

RESOURCE_ID = "local-business-site-check"

#: The rail webbuilder SKU this read feeds (rail_routes.PACK_SKUS).
WEBBUILDER_SKU = "webbuilder_site"
WEBBUILDER_RESOURCE = "rail-pack-webbuilder_site"
WEBBUILDER_CHECKOUT = "/rail/checkout"

_ALLOWED_TOP_KEYS = {"url", "business_name"}
_MAX_URL_LEN = 2048
_MAX_NAME_LEN = 200


@dataclass
class SiteFetch:
    """One fetched page, as returned by the fetch seam."""

    status_code: int
    url: str  # final URL after redirects
    html: str


# ---------------------------------------------------------------------------
# SSRF guard (audit P1-2): the buyer's URL is attacker-controlled, so
# every target — the initial URL and EVERY redirect hop — must pass the
# same strict check BEFORE any connection is attempted:
#   * scheme http/https, a real hostname, ports 80/443 only;
#   * no internal hostnames (localhost, *.local, *.internal — which
#     covers *.railway.internal);
#   * the hostname is resolved via socket.getaddrinfo and rejected if
#     ANY resolved address is private, loopback, link-local, multicast,
#     reserved, or unspecified (IP literals are checked directly).
# A name that does not resolve at all is NOT rejected here: nothing
# can connect to it, so the fetch fails honestly as unreachable. The
# fetcher re-runs this check immediately before connecting, which is
# the enforcement point; validate_payload runs it early so a bad
# target is a 400 before any fetch is attempted.
# ---------------------------------------------------------------------------

_ALLOWED_PORTS = {80, 443}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class InvalidTargetHostError(Exception):
    """A fetch target failed the SSRF check (see _target_error)."""


def _target_error(url: str) -> Optional[str]:
    """Return ``"invalid_target_host"`` if ``url`` must not be fetched,
    else ``None``. Pure check — never opens a connection."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        return "invalid_target_host"
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "invalid_target_host"
    if (host == "localhost" or host.endswith(".localhost")
            or host.endswith(".local") or host.endswith(".internal")):
        return "invalid_target_host"
    try:
        port = parts.port
    except ValueError:
        return "invalid_target_host"
    if port is not None and port not in _ALLOWED_PORTS:
        return "invalid_target_host"

    try:
        ips: List[ipaddress._BaseAddress] = [ipaddress.ip_address(host)]
    except ValueError:
        ips = []
        default_port = port or (443 if parts.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, default_port,
                                       proto=socket.IPPROTO_TCP)
        except (OSError, UnicodeError):
            # Unresolvable: no address exists to connect to, so there
            # is nothing to judge — the fetch itself will fail and the
            # serve layer reports an honest "unknown".
            return None
        for info in infos:
            try:
                ips.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    for ip in ips:
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified):
            return "invalid_target_host"
    return None


def _target_allowed(url: str) -> bool:
    return _target_error(url) is None


# ---------------------------------------------------------------------------
# Fetch seam (tests stub this with fixtures — no network)
# ---------------------------------------------------------------------------

def fetch_site_page(url: str) -> Optional[SiteFetch]:
    """Production fetcher: GET ``url`` and return the page.

    Mirrors the repo's existing meta-scrape in
    ``webbuilder_discover._meta_scrape`` (same UA family, bounded
    body). Returns ``None`` on ANY transport failure — the serve
    layer turns that into an honest ``unknown``, never a guess.

    SSRF: automatic redirects are DISABLED; hops are followed
    manually (max ``_MAX_REDIRECTS``) and every hop's target must
    pass :func:`_target_error` before connecting. A forbidden target
    raises :class:`InvalidTargetHostError` — the serve layer turns
    that into a 400, never an ``unknown``.
    """
    import requests

    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        if not _target_allowed(current):
            raise InvalidTargetHostError(current)
        try:
            resp = requests.get(
                current,
                timeout=12,
                headers={"User-Agent": "SINCOR-SiteCheck/1.0 "
                                      "(+https://getsincor.com)"},
                allow_redirects=False,
            )
        except Exception:
            return None
        if resp.status_code in _REDIRECT_STATUSES:
            location = ""
            headers = getattr(resp, "headers", None) or {}
            try:
                location = (headers.get("Location") or "").strip()
            except Exception:
                location = ""
            if not location:
                # A redirect status with nowhere to go is the final
                # response, same as the old auto-follow behaviour of
                # handing back whatever was served.
                return SiteFetch(status_code=resp.status_code,
                                 url=str(resp.url or current),
                                 html=(resp.text or "")[:200_000])
            current = urljoin(current, location)
            continue
        return SiteFetch(status_code=resp.status_code,
                         url=str(resp.url or current),
                         html=(resp.text or "")[:200_000])
    # Redirect chain longer than the hop budget: unreachable in
    # practice for a real business site — honest failure.
    return None


# ---------------------------------------------------------------------------
# HTML inspection (deterministic; the parser only records observations)
# ---------------------------------------------------------------------------

class _SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta_description = ""
        self.has_viewport = False
        self.h1: List[str] = []
        self._in_h1 = False
        self.hrefs: List[str] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        elif tag == "meta":
            name = (attr.get("name") or "").strip().lower()
            content = (attr.get("content") or "").strip()
            if name == "viewport":
                self.has_viewport = True
            elif name == "description" and content:
                self.meta_description = content
        elif tag == "a":
            href = (attr.get("href") or "").strip()
            if href:
                self.hrefs.append(href)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        if self._in_h1:
            self.h1.append(text)


def _contact_paths(hrefs: List[str]) -> List[str]:
    out = []
    for href in hrefs:
        low = href.lower()
        if low.startswith(("tel:", "mailto:")) or "contact" in low:
            out.append(href)
    return out


# ---------------------------------------------------------------------------
# Payload validation (strict, fail closed — runs AFTER payment)
# ---------------------------------------------------------------------------

def _err(code: str, **extra: Any) -> Tuple[int, Dict[str, Any]]:
    body: Dict[str, Any] = {"ok": False, "error": code,
                            "resource": RESOURCE_ID}
    body.update(extra)
    return 400, body


def validate_payload(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    """Validate a buyer payload. Returns ``(parsed, None)`` or
    ``(None, (status, body))``. ``parsed`` has ``url`` (may be "")
    and ``business_name`` (may be ""). At least one is required."""
    if not isinstance(payload, dict):
        return None, _err("payload_must_be_object")
    if set(payload) - _ALLOWED_TOP_KEYS:
        return None, _err("unknown_payload_fields")

    url = payload.get("url") or ""
    if not isinstance(url, str):
        return None, _err("url_invalid")
    url = url.strip()
    if len(url) > _MAX_URL_LEN:
        return None, _err("url_too_long")
    if url:
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None, _err("url_invalid")
        # SSRF guard (audit P1-2): internal hostnames, non-80/443
        # ports, and loopback/private/link-local/metadata targets
        # are refused before any fetch is attempted.
        if _target_error(url) is not None:
            return None, _err("invalid_target_host")

    name = payload.get("business_name") or ""
    if not isinstance(name, str):
        return None, _err("business_name_invalid")
    name = name.strip()
    if len(name) > _MAX_NAME_LEN:
        return None, _err("business_name_too_long")

    if not url and not name:
        return None, _err("url_or_business_name_required")
    return {"url": url, "business_name": name}, None


# ---------------------------------------------------------------------------
# Serve path (called by the billing blueprint ONLY after the x402
# access token has been consumed)
# ---------------------------------------------------------------------------

def _webbuilder_next_step() -> Dict[str, Any]:
    """The upsell reference — read from the REAL rail SKU table so the
    price can never drift from what /rail/checkout will charge."""
    from sincor2.rail_routes import PACK_SKUS

    price_cents = PACK_SKUS[WEBBUILDER_SKU]["price_cents"]
    return {
        "sku": WEBBUILDER_SKU,
        "resource": WEBBUILDER_RESOURCE,
        "price_usdc": price_cents / 100,
        "checkout": WEBBUILDER_CHECKOUT,
    }


def serve_site_check(
    payload: Optional[Dict[str, Any]],
    *,
    fetch: Optional[Callable[[str], Optional[SiteFetch]]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Run one paid local-business site check.

    ``fetch`` is the fetch seam (defaults to :func:`fetch_site_page`);
    tests inject fixture pages through it. Returns 200 with one of
    the three honest verdicts, or 400 on a malformed payload.
    """
    parsed, error = validate_payload(payload)
    if error is not None:
        return error
    assert parsed is not None
    url = parsed["url"]
    name = parsed["business_name"]

    base: Dict[str, Any] = {
        "ok": True,
        "resource": RESOURCE_ID,
        "business_name": name,
        "url": url,
        "checks": ["no_website", "site_error_status", "not_https",
                   "missing_title", "missing_meta_description",
                   "missing_viewport", "missing_h1", "no_contact_path"],
    }

    # (a) No site at all: the one deficiency provable without a fetch.
    if not url:
        base.update({
            "verdict": "build_recommended",
            "has_website": False,
            "deficiencies": [{
                "code": "no_website",
                "evidence": "no website URL supplied for this business",
            }],
            "observations": {},
            "next_step": _webbuilder_next_step(),
        })
        return 200, base

    fetch_fn = fetch if fetch is not None else fetch_site_page
    try:
        page = fetch_fn(url)
    except InvalidTargetHostError:
        # A redirect hop (or the target itself, re-checked at fetch
        # time) failed the SSRF guard: that is a client error about
        # the requested target — a 400, never an "unknown" verdict.
        return _err("invalid_target_host")
    except Exception:
        page = None

    # Unreachable -> unknown. Never a guess in either direction.
    if page is None:
        base.update({
            "verdict": "unknown",
            "has_website": None,
            "deficiencies": [],
            "observations": {},
            "reason": "site_unreachable",
        })
        return 200, base

    if not (page.html or "").strip():
        base.update({
            "verdict": "unknown",
            "has_website": True,
            "deficiencies": [],
            "observations": {"status_code": page.status_code},
            "reason": "insufficient_data",
        })
        return 200, base

    deficiencies: List[Dict[str, str]] = []

    if page.status_code >= 400:
        deficiencies.append({
            "code": "site_error_status",
            "evidence": f"site returned HTTP {page.status_code}",
        })

    parts = urlparse(url)
    if parts.scheme == "http":
        deficiencies.append({
            "code": "not_https",
            "evidence": "site URL scheme is http, not https",
        })

    parser = _SiteParser()
    parser.feed(page.html[:200_000])
    contacts = _contact_paths(parser.hrefs)

    if not parser.title:
        deficiencies.append({
            "code": "missing_title",
            "evidence": "no <title> text in fetched HTML",
        })
    if not parser.meta_description:
        deficiencies.append({
            "code": "missing_meta_description",
            "evidence": "no meta description content in fetched HTML",
        })
    if not parser.has_viewport:
        deficiencies.append({
            "code": "missing_viewport",
            "evidence": "no <meta name=\"viewport\"> in fetched HTML",
        })
    if not parser.h1:
        deficiencies.append({
            "code": "missing_h1",
            "evidence": "no <h1> text in fetched HTML",
        })
    if not contacts:
        deficiencies.append({
            "code": "no_contact_path",
            "evidence": "no tel:, mailto:, or contact link among "
                        f"{len(parser.hrefs)} links in fetched HTML",
        })

    observations = {
        "status_code": page.status_code,
        "final_url": page.url,
        "https": parts.scheme == "https",
        "title": parser.title,
        "has_meta_description": bool(parser.meta_description),
        "has_viewport": parser.has_viewport,
        "h1": parser.h1[:3],
        "contact_paths": contacts[:5],
        "link_count": len(parser.hrefs),
    }

    if deficiencies:
        base.update({
            "verdict": "build_recommended",
            "has_website": True,
            "deficiencies": deficiencies,
            "observations": observations,
            "next_step": _webbuilder_next_step(),
        })
    else:
        # The honesty core: a good site gets NO upsell. There is
        # deliberately no "next_step" key in this branch.
        base.update({
            "verdict": "no_build_needed",
            "has_website": True,
            "deficiencies": [],
            "observations": observations,
        })
    return 200, base
