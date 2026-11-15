"""Shared retrying HTTP helper for the liveness tooling.

curl-based (subprocess). Retries transient network failures with
backoff; surfaces HTTP error bodies so 4xx semantics (already committed,
unknown agent, window closed, ...) stay distinguishable from transport
failures.

Why curl and not urllib: on 2026-09-28 Python's stdlib ``ssl``
ClientHello was being probabilistically killed mid-response by a
middlebox on the egress path (``IncompleteRead`` / ``RemoteDisconnected``
on ~75% of requests), while curl's TLS stack succeeded 37/37 in
controlled A/B tests. Headers, HTTP version, and ALPN were all ruled out
— only the TLS fingerprint differed. ``requests``/``urllib3`` would not
fix it (same Python ``ssl`` underneath).

Two failure modes seen in production (2026-09-28) are handled
explicitly:

* truncated reads (curl exit 18) and connection resets (exits 7, 28,
  52, 56, ...) — retried with backoff;
* silently-truncated bodies: a response that arrives with a 2xx status
  but a cut-off body does NOT always fail the transfer — ``get_json``
  therefore treats an empty or unparseable body as a transient
  truncation and retries it instead of handing a corrupt payload to the
  caller.

``get_all_pages`` fetches paginated listings (``/v1/a2a/tasks``) page by
page and NEVER returns a partial inventory: every page goes through the
retrying ``get_json``, and any page that cannot be fetched after retries
raises instead of being treated as "the end".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class TruncatedResponse(Exception):
    """A 2xx response whose body was cut off or did not parse (transient)."""


_CURL = shutil.which("curl")


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, 30) + 1


def _curl_call(method: str, url: str, data: Optional[bytes],
               headers: Dict[str, str], timeout: int) -> Tuple[int, str]:
    """Single curl attempt. Returns (status, body_text).

    Raises ``HttpError`` for HTTP error statuses (never retried by the
    caller) and ``RuntimeError`` for transport-level failures (retried).
    """
    if _CURL is None:
        raise RuntimeError("curl binary not found; cannot make HTTP requests")
    cmd = [
        _CURL, "-sS", "--max-time", str(timeout),
        "-X", method.upper(),
        "-w", "\n%{http_code}",
    ]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True,
                              timeout=timeout + 10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"curl timed out on {method} {url}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"curl exit {proc.returncode} on {method} {url}: "
                           f"{err[:200]}")
    out = proc.stdout.decode("utf-8", "replace")
    # Last line is the status code appended by -w; everything before is body.
    body, _, status_line = out.rpartition("\n")
    try:
        status = int(status_line.strip())
    except ValueError:
        raise RuntimeError(f"curl returned unparseable status line "
                           f"on {method} {url}: {status_line[:80]!r}")
    if status >= 400:
        raise HttpError(status, body)
    return status, body


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
    url = base_url.rstrip("/") + path
    merged_headers = {"Content-Type": "application/json", **(headers or {})}
    last: Exception | None = None
    for attempt in range(tries):
        try:
            status, raw = _curl_call(method, url, data, merged_headers,
                                     timeout)
            if expect_json:
                if not raw.strip():
                    raise TruncatedResponse(
                        f"empty body on {method} {path}")
                try:
                    return status, json.loads(raw)
                except ValueError:
                    raise TruncatedResponse(
                        f"unparseable JSON ({len(raw)} chars) "
                        f"on {method} {path}")
            try:
                return status, json.loads(raw)
            except ValueError:
                return status, raw
        except HttpError:
            raise
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
