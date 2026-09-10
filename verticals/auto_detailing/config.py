"""CHROMA shop config and environment flags.

Outbound is dry-run unless CHROMA_LIVE_SEND=true. Demo mode preloads the
shop dashboard for Loom recordings.

Default shop is Clinton Auto Detailing (Clinton, IA) — Court's live bay.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict

from .protocols import PACKAGES

DEFAULT_SHOP: Dict[str, Any] = {
    "shop_name": "Clinton Auto Detailing",
    "city": "Clinton",
    "region": "IA",
    "zip": "52732",
    "address": "715 Park Pl",
    "phone": "(815) 718-8936",
    "url": "https://www.clintondetailing.com",
    "gbp_url": "https://share.google/9XSiKOKfhFPwivAU0",
    "calendly_handle": "clinton-auto-detailing",
    "calendly_url": "https://calendly.com/clinton-auto-detailing",
    "email": "court@clintondetailing.com",
    "hours": "Mon–Fri 8:00–4:30 · Sat 9:00–1:00 · Sun closed",
    "appointment_only": True,
    "service_area": "Clinton, Fulton, Camanche & Morrison",
    "since": 2014,
    "tagline": "Correct. Reflect. Protect.",
}


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def live_send_enabled() -> bool:
    """Nothing leaves the building without this flag AND an explicit approve."""
    return env_flag("CHROMA_LIVE_SEND", False)


def demo_mode() -> bool:
    return env_flag("CHROMA_DEMO", False)


def _strip_secret(value: str | None) -> str:
    """Railway sometimes wraps secrets in extra quotes."""
    return (value or "").strip().strip('"').strip("'")


def admin_identities() -> set[str]:
    names: set[str] = set()
    for key in ("ADMIN_USERNAME", "ADMIN_EMAIL"):
        v = _strip_secret(os.environ.get(key)).lower()
        if v:
            names.add(v)
    if not names:
        names.add("admin")
    return names


def admin_password() -> str:
    return _strip_secret(os.environ.get("ADMIN_PASSWORD"))


def default_packages() -> Dict[str, Dict[str, Any]]:
    return copy.deepcopy(PACKAGES)


def calendly_handle_from_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if "calendly.com/" in text:
        return text.split("calendly.com/", 1)[1].split("/", 1)[0]
    return text or str(DEFAULT_SHOP["calendly_handle"])
