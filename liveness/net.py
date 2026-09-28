"""Shared retrying HTTP helper for the liveness tooling.

urllib-based (stdlib only). Retries transient network failures with
backoff; surfaces HTTP error bodies so 4xx semantics (already committed,
unknown agent, window closed, ...) stay distinguishable from transport
failures.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def call(method: str,
         base_url: str,
         path: str,
         body: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None,
         tries: int = 6,
         timeout: int = 40) -> Tuple[int, Any]:
    """(status, parsed_json_or_text). Retries transport errors only."""
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
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace") if e.fp else ""
            raise HttpError(e.code, raw)
        except Exception as e:  # transport failure: retry with backoff
            last = e
            time.sleep(min(2 ** attempt, 30) + 1)
    raise RuntimeError(f"request failed after {tries} tries: {last}")


def get_json(base_url: str, path: str, **kw) -> Any:
    return call("GET", base_url, path, **kw)[1]


def post_json(base_url: str, path: str, body: Dict[str, Any], **kw) -> Any:
    status, payload = call("POST", base_url, path, body, **kw)
    if status >= 400:
        raise HttpError(status, json.dumps(payload)[:200])
    return payload
