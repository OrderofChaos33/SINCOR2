"""Vega discovery — Google Places, Yelp, and public meta scrape."""

from __future__ import annotations

import logging
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger("sincor2.webbuilder.discover")

PLACES_FIND = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_TEXT = "https://maps.googleapis.com/maps/api/place/textsearch/json"
YELP_BIZ = "https://api.yelp.com/v3/businesses/"


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.og_title = ""
        self.og_description = ""
        self.h1: list[str] = []
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h1":
            self._in_h1 = True
        attr = dict(attrs)
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            content = attr.get("content") or ""
            if name == "description":
                self.description = content
            if name == "og:title":
                self.og_title = content
            if name == "og:description":
                self.og_description = content
        if tag == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            text = data.strip()
            if text:
                self.h1.append(text)


def _places_key() -> str:
    return (
        os.environ.get("GOOGLE_PLACES_API_KEY")
        or os.environ.get("GOOGLE_PLACES_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    )


def _yelp_key() -> str:
    return os.environ.get("YELP_API_KEY", "")


def _empty_discovery() -> dict[str, Any]:
    return {
        "source": "none",
        "name": "",
        "phone": "",
        "address": "",
        "website": "",
        "rating": None,
        "review_count": None,
        "categories": [],
        "reviews_snippet": "",
        "has_website": False,
        "hours": [],
        "raw_url": "",
    }


def _meta_scrape(url: str, timeout: int = 12) -> dict[str, Any]:
    out = _empty_discovery()
    out["source"] = "meta_scrape"
    out["raw_url"] = url
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SINCOR-Vega/1.0 (+https://getsincor.com)"},
        )
        resp.raise_for_status()
        parser = _MetaParser()
        parser.feed(resp.text[:200_000])
        out["name"] = parser.og_title or (parser.h1[0] if parser.h1 else parser.title)
        out["reviews_snippet"] = parser.og_description or parser.description
        out["has_website"] = True
        out["website"] = url
    except Exception as e:
        logger.warning("[VEGA] meta scrape failed %s: %s", url, e)
        out["reviews_snippet"] = f"Could not scrape: {e}"
    return out


def _google_from_text(query: str) -> dict[str, Any]:
    key = _places_key()
    out = _empty_discovery()
    out["source"] = "google_places"
    if not key:
        out["reviews_snippet"] = "GOOGLE_PLACES_API_KEY not set"
        return out
    try:
        text_resp = requests.get(
            PLACES_TEXT,
            params={"query": query, "key": key},
            timeout=12,
        )
        text_resp.raise_for_status()
        results = text_resp.json().get("results", [])
        if not results:
            find_resp = requests.get(
                PLACES_FIND,
                params={
                    "input": query,
                    "inputtype": "textquery",
                    "fields": "place_id,name,formatted_address",
                    "key": key,
                },
                timeout=12,
            )
            find_resp.raise_for_status()
            candidates = find_resp.json().get("candidates", [])
            if not candidates:
                return out
            place_id = candidates[0]["place_id"]
        else:
            place_id = results[0]["place_id"]

        details = requests.get(
            PLACES_DETAILS,
            params={
                "place_id": place_id,
                "fields": "name,formatted_phone_number,formatted_address,website,rating,user_ratings_total,types,opening_hours,reviews,url",
                "key": key,
            },
            timeout=12,
        )
        details.raise_for_status()
        r = details.json().get("result", {})
        out["name"] = r.get("name", "")
        out["phone"] = r.get("formatted_phone_number", "")
        out["address"] = r.get("formatted_address", "")
        out["website"] = r.get("website", "")
        out["has_website"] = bool(r.get("website"))
        out["rating"] = r.get("rating")
        out["review_count"] = r.get("user_ratings_total")
        out["categories"] = r.get("types", [])[:6]
        reviews = r.get("reviews") or []
        if reviews:
            out["reviews_snippet"] = reviews[0].get("text", "")[:280]
        out["hours"] = (r.get("opening_hours") or {}).get("weekday_text", [])
        out["raw_url"] = r.get("url", "")
    except Exception as e:
        logger.warning("[VEGA] Google Places error: %s", e)
        out["reviews_snippet"] = str(e)
    return out


def _yelp_from_url(url: str) -> dict[str, Any]:
    out = _empty_discovery()
    out["source"] = "yelp"
    out["raw_url"] = url
    key = _yelp_key()
    m = re.search(r"yelp\.com/biz/([^\?/?#]+)", url, re.I)
    biz_id = m.group(1) if m else ""
    if not key or not biz_id:
        if url:
            return _meta_scrape(url)
        return out
    try:
        resp = requests.get(
            f"{YELP_BIZ}{biz_id}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=12,
        )
        resp.raise_for_status()
        b = resp.json()
        out["name"] = b.get("name", "")
        out["phone"] = b.get("phone", "")
        out["address"] = ", ".join(b.get("location", {}).get("display_address", []))
        out["website"] = b.get("url", "")
        out["has_website"] = bool(b.get("url"))
        out["rating"] = b.get("rating")
        out["review_count"] = b.get("review_count")
        out["categories"] = [c.get("title", "") for c in b.get("categories", [])]
    except Exception as e:
        logger.warning("[VEGA] Yelp error: %s", e)
        return _meta_scrape(url)
    return out


def discover_business(
    *,
    name: str,
    territory: str = "",
    source_type: str = "none",
    source_url: str = "",
) -> dict[str, Any]:
    """Run Vega discovery for a WebBuilder project."""
    query = " ".join(x for x in [name, territory] if x).strip()
    st = (source_type or "none").lower()
    url = (source_url or "").strip()

    if st == "google" and url:
        if "google." in url and "place" in url:
            return _google_from_text(name or url)
        return _google_from_text(query or url)

    if st == "yelp" and url:
        return _yelp_from_url(url)

    if st in ("facebook", "website") and url:
        data = _meta_scrape(url)
        if not data.get("name") and name:
            data["name"] = name
        data["source"] = st
        host = urlparse(url).netloc
        data["has_website"] = st == "website" or "facebook" not in host
        if data.get("has_website"):
            try:
                import asyncio

                from sincor2.lead_enrich import extract_emails

                async def _emails():
                    import httpx

                    async with httpx.AsyncClient() as client:
                        return await extract_emails(client, url)

                found = asyncio.run(_emails())
                if found:
                    data["emails"] = found
            except Exception:
                pass
        return data

    if query and _places_key():
        data = _google_from_text(query)
        if data.get("name"):
            return data

    out = _empty_discovery()
    out["name"] = name
    out["reviews_snippet"] = "Built from prompt and niche — no external listing matched."
    return out