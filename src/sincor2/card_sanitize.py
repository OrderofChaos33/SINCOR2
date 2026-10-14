"""Shared agent-card input sanitization (W-82).

Two choke points feed attacker-controlled agent cards into the platform's
trust anchor:
  * ``a2a_inbound._normalize_registration`` (POST /v1/a2a/register)
  * ``kya_registry.list_from_inbound`` (POST /kya/list takes the raw body)

Both must sanitize the same way, so the logic lives here (stdlib only —
importing it creates no cycles).

URL policy (``safe_url``)
-------------------------
Only ``http``/``https`` URLs with a public host survive. Rejected:
loopback (127.0.0.0/8, ::1), RFC-1918 privates, link-local (169.254/16 —
the cloud metadata range — and fe80::/10), 0.0.0.0/::, multicast, the
``localhost`` hostname, and any URL carrying userinfo credentials.

DNS-rebinding residual (documented, not fully closed): the hostname is
resolved and every returned address is checked, which defeats the naive
``evil.example.com -> 169.254.169.254`` case. But resolve-then-check is
TOCTOU — an attacker who controls DNS can serve a public address at
registration time and a private one when the URL is later fetched. The
platform itself never fetches ``rpc_callback``/``card_url`` (verified:
no fetch of agent-supplied URLs in ``src/``), so the residual sits with
downstream consumers: any consumer that fetches a registry URL MUST
re-validate the resolved address at fetch time (same blocked ranges).

Text policy (``sanitize_card_text``)
-----------------------------------
The render path is JSON APIs (``/v1/a2a/agents``, ``/api/marketplace/agents``)
consumed by external UIs. Sanitization: strip, drop C0/C1 control
characters except TAB/LF/CR, strip bidi overrides/isolates and
zero-width format characters (display-spoofing), neutralize markup by
removing ``<``/``>`` (no tag can form; avoids double-escaping issues
for JSON consumers), then hard-truncate to the field cap.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

NAME_MAX_LEN = 120
DESCRIPTION_MAX_LEN = 2000
VERSION_MAX_LEN = 32
SKILL_FIELD_MAX_LEN = 120
TAG_MAX_LEN = 64

# C0 controls except \t \n \r, plus DEL, C1 controls, and invisible
# format characters abused for display-spoofing in a trust directory:
# bidi overrides/embeds (U+202A-U+202E), bidi isolates (U+2066-U+2069),
# zero-width space/joiners (U+200B-U+200D), word joiner (U+2060), BOM (U+FEFF).
_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f"
    r"\u200b-\u200d\u202a-\u202e\u2060\u2066-\u2069\ufeff]"
)

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),   # loopback v4
    ipaddress.ip_network("10.0.0.0/8"),     # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),  # link-local: cloud metadata IP lives here
    ipaddress.ip_network("0.0.0.0/8"),      # "this network"
    ipaddress.ip_network("224.0.0.0/4"),    # multicast v4
    ipaddress.ip_network("::1/128"),        # loopback v6
    ipaddress.ip_network("fc00::/7"),       # unique-local v6
    ipaddress.ip_network("fe80::/10"),      # link-local v6
    ipaddress.ip_network("::/128"),         # unspecified v6
    ipaddress.ip_network("ff00::/8"),       # multicast v6
)


def sanitize_card_text(value: Any, max_len: int) -> str:
    """Sanitize an agent-card display field. Never raises on weird input."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    s = s.strip()
    s = _CONTROL_RE.sub("", s)
    s = s.replace("<", "").replace(">", "")
    if len(s) > max_len:
        s = s[:max_len]
    return s


def sanitize_skills(skills: Any) -> list:
    """Sanitize the skills list of an agent card. Never raises.

    Each skill's ``id``/``name``/``tags`` are attacker-controlled display
    fields served to external UIs, so they get the same text treatment as
    name/description. Other keys are passed through untouched (structure
    preservation, not a trust boundary).
    """
    out: list = []
    try:
        for skill in (skills or []):
            if isinstance(skill, dict):
                d = dict(skill)
                d["id"] = sanitize_card_text(skill.get("id"), SKILL_FIELD_MAX_LEN)
                d["name"] = sanitize_card_text(skill.get("name"), SKILL_FIELD_MAX_LEN)
                d["tags"] = [
                    t
                    for t in (
                        sanitize_card_text(t, TAG_MAX_LEN)
                        for t in (skill.get("tags") or [])
                    )
                    if t
                ]
                out.append(d)
            else:
                s = sanitize_card_text(skill, SKILL_FIELD_MAX_LEN)
                if s:
                    out.append({"id": s, "name": s})
    except Exception:
        pass
    return out


def _ip_blocked(ip: Any) -> bool:
    # ipaddress containment is version-strict: ::ffff:127.0.0.1 is NOT
    # "in" 127.0.0.0/8, yet on dual-stack hosts it connects to loopback.
    # Unwrap IPv4-mapped (::ffff:a.b.c.d) and 6to4 (2002:a.b.c.d::/48)
    # addresses so they are judged against the IPv4 block list.
    if isinstance(ip, ipaddress.IPv6Address):
        v4 = ip.ipv4_mapped or ip.sixtofour
        if v4 is not None:
            ip = v4
    return any(ip in net for net in _BLOCKED_NETWORKS)


def host_blocked(host: str) -> bool:
    """True if a URL host is loopback/private/link-local/etc.

    Checks IP literals directly, the ``localhost`` hostname, then every
    address the hostname resolves to. Unresolvable hostnames are NOT
    blocked (fail-open: an unresolvable URL is non-functional anyway;
    blocking it would turn DNS blips into registration failures).
    """
    h = (host or "").strip().strip("[]")
    if not h:
        return True
    try:
        return _ip_blocked(ipaddress.ip_address(h))
    except ValueError:
        pass
    hn = h.lower().rstrip(".")
    if hn == "localhost" or hn.endswith(".localhost"):
        return True
    try:
        infos = socket.getaddrinfo(hn, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        return False
    for info in infos:
        try:
            if _ip_blocked(ipaddress.ip_address(info[4][0])):
                return True
        except ValueError:
            continue
    return False


def safe_url(value: Any) -> str:
    """Return the URL if it is public http/https, else ``""``.

    Rejects non-http(s) schemes, missing hosts, embedded credentials,
    and any host that is (or resolves to) a blocked range. Never raises.
    """
    try:
        url = str(value or "").strip()
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ""
        host = parsed.hostname or ""
        if not host:
            return ""
        if parsed.username or parsed.password:
            return ""
        if host_blocked(host):
            return ""
        return url
    except Exception:
        return ""
