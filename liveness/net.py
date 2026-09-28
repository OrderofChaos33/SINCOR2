"""Shared retrying HTTP helper for the liveness tooling.

urllib-based (stdlib only). Retries transient network failures with
backoff; surfaces HTTP error bodies so 4xx semantics (already committed,
unknown agent, window closed, ...) stay distinguishable from transport
failures.

Two failure modes seen in production (2026-09-28) are handled
explicitly:

* truncated reads (``http.client.IncompleteRead``) and connection resets
  (``RemoteDisconnected`` / ``ConnectionResetError`` / ...) — retried
  with backoff;
* silently-truncated bodies: a response that arrives with a 200 status
  but a cut-off body does NOT always raise — ``resp.read()`` can simply
  return partial bytes.  ``get_json`` therefore treats an empty or
  unparseable body as a transient truncation and retries it instead of
  handing a corrupt payload to the caller.

``get_all_pages`` fetches paginated listings (``/v1/a2a/tasks``) page by
page and NEVER returns a partial inventory: every page goes through the
retrying ``get_json``, and any page that cannot be fetched after retries
raises instead of being treated as "the end".
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class TruncatedResponse(Exception):
    """A 2xx response whose body was cut off or did not parse (transient)."""


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, 30) + 1


def call(method: str,
         base_url: str,
         path: str,
         body: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None,
         tries: int = 6,
         timeout: int = 40,
         expect_json: bool = False) -> Tuple[int, Any]:
    """(status, parsed_json_or_text). Retries transport errors only.

    With ``expect_json=True`` an empty or unparseable body is treated as
    a truncated response and retried, so callers never see corrupt JSON.
    HTTP error statuses are never retried — they raise ``HttpError``.
    """
    data = json.dumps(body).encode() if body is not None else None
    last: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method.upper(),
            headers={"Content-Type": "application/json",
                     **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                if expect_json:
                    if not raw.strip():
                        raise TruncatedResponse(
                            f"empty body on {method} {path}")
                    try:
                        return resp.status, json.loads(raw)
                    except ValueError:
                        raise TruncatedResponse(
                            f"unparseable JSON ({len(raw)} chars) "
                            f"on {method} {path}")
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace") if e.fp else ""
            raise HttpError(e.code, raw)
        except Exception as e:  # transport failure / truncation: retry
            last = e
            time.sleep(_backoff(attempt))
    raise RuntimeError(f"request failed after {tries} tries: {last}")


def get_json(base_url: str, path: str, expect_json: bool = True, **kw) -> Any:
    return call("GET", base_url, path, expect_json=expect_json, **kw)[1]


def post_json(base_url: str, path: str, body: Dict[str, Any], **kw) -> Any:
    status, payload = call("POST", base_url, path, body, **kw)
    if status >= 400:
        raise HttpError(status, json.dumps(payload)[:200])
    return payload


def get_all_pages(base_url: str,
                  path: str,
                  *,
                  per_page: int = 100,
                  items_key: str = "tasks",
                  max_pages: int = 10_000,
                  **kw) -> List[Any]:
    """Fetch every page of a paginated listing; never return a partial one.

    Pages are fetched sequentially (``page=1,2,...``).  Each page goes
    through the retrying ``get_json``, so a truncated page is retried,
    not mistaken for the end of the inventory.  Fetching stops at the
    first page shorter than ``per_page`` (or at the server-reported
    ``pages`` count).  If any page fails after retries, the error
    propagates — a partial inventory is never returned.
    """
    items: List[Any] = []
    page = 1
    while True:
        if page > max_pages:
            raise RuntimeError(
                f"get_all_pages: exceeded {max_pages} pages on {path}")
        sep = "&" if "?" in path else "?"
        paged = f"{path}{sep}per_page={per_page}&page={page}"
        payload = get_json(base_url, paged, **kw)
        if not isinstance(payload, dict):
            raise TruncatedResponse(
                f"page {page} of {path} did not parse as a JSON object")
        batch = payload.get(items_key) or []
        items.extend(batch)
        total_pages = payload.get("pages")
        if len(batch) < per_page:
            return items
        if isinstance(total_pages, int) and page >= max(1, total_pages):
            return items
        page += 1
