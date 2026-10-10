#!/usr/bin/env python3
"""
Real tool bindings for AgencyKernel executor.

Replaces the old _simulate_step_execution canned responses with actual:
  - web_search   (DuckDuckGo HTML / requests)
  - python_exec  (restricted eval for simple calculations; requires explicit
                 founder approval via SINCOR_AGENT_EXEC_APPROVED)
  - file_read    (restricted to the agent sandbox dir; secrets redacted)
  - claude_reason / analysis / synthesis (Anthropic via existing ClaudeClient)

P1 audit #10 hardening:
  - python_exec is gated behind a founder approval env flag (it is load-bearing
    for the executor, so it is gated rather than removed). The
    banned-construct filter is defense-in-depth only, not the security boundary.
  - file_read may only read inside data/agent_sandbox/ (or
    SINCOR_AGENT_SANDBOX_DIR); repo root and /tmp are excluded. Resolved
    realpaths are re-checked for containment (symlink/.. escapes denied).
  - All tool outputs are scanned for secret patterns (API keys, private keys,
    JWTs) and redacted before return.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.agency.tools")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _sandbox_root() -> Path:
    """Dedicated agent sandbox dir — the ONLY root file_read may touch.

    P1 audit #10: the old roots (repo root, /tmp, /data) exposed secrets,
    keys, and source to agent-facing reads. Override with
    SINCOR_AGENT_SANDBOX_DIR if needed; otherwise <repo>/data/agent_sandbox.
    The root is resolved fresh on every call so env changes take effect.
    """
    raw = os.environ.get("SINCOR_AGENT_SANDBOX_DIR", "")
    if raw:
        return Path(raw).expanduser().resolve()
    return (_PROJECT_ROOT / "data" / "agent_sandbox").resolve()


def _safe_path(path_str: str) -> Path:
    """Resolve a requested path and enforce sandbox containment.

    - Relative paths resolve against the sandbox root.
    - Absolute paths resolve as given.
    - The resolved realpath is re-checked for containment AFTER resolution,
      which kills symlink escapes (e.g. sandbox/link -> /etc/passwd),
      ".." traversal, and case-variant tricks on case-insensitive filesystems.
    """
    root = _sandbox_root()
    candidate = Path(path_str).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionError(f"Path outside agent sandbox: {path_str}")
    return resolved


# ---------------------------------------------------------------------------
# Secret redaction (P1 audit #10d): scan tool outputs for secret-shaped values
# and redact before returning. Applied to every tool's output plus a final
# sweep over the aggregated step result in run_tools_for_step.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"sk_(live|test)_[A-Za-z0-9]{10,}"), "sk_***REDACTED***"),
    (re.compile(r"rk_(live|test)_[A-Za-z0-9]{10,}"), "rk_***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***REDACTED***"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho_***REDACTED***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github_pat_***REDACTED***"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "xox***REDACTED***"),
    (re.compile(r"\bre_[A-Za-z0-9_]{24,}\b"), "re_***REDACTED***"),
    # JWT: eyJ prefix (base64 of '{"') + three long base64url segments.
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT***REDACTED***"),
    (re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
    ), "***PRIVATE KEY REDACTED***"),
]


def _redact_text(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_value(value: Any) -> Any:
    """Recursively redact secret-shaped strings in dicts/lists/tuples."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def tool_web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Lightweight web search via DuckDuckGo HTML (no API key required)."""
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "SINCOR-AgencyKernel/2.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        results = []
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            link = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title and link.startswith("http"):
                results.append({"title": title[:200], "url": link})
            if len(results) >= max_results:
                break

        return {
            "status": "success",
            "tool": "web_search",
            "query": query,
            "results": _redact_value(results),
            "count": len(results),
        }
    except Exception as err:
        logger.warning("web_search failed: %s", err)
        return {
            "status": "failed",
            "tool": "web_search",
            "query": query,
            "error": str(err),
            "results": [],
        }


def _exec_approved() -> bool:
    """Founder approval gate for agent-facing code execution (P1 audit #10a).

    python_exec is load-bearing for the AgencyKernel executor, so it is kept
    but gated: it runs ONLY when the founder explicitly sets
    SINCOR_AGENT_EXEC_APPROVED to a truthy value. Checked live on every call
    (never cached, and never taken from a user/agent-supplied parameter).
    """
    return (os.environ.get("SINCOR_AGENT_EXEC_APPROVED", "") or "").strip().lower() in (
        "1", "true", "yes",
    )


def tool_python_exec(code: str) -> Dict[str, Any]:
    """Restricted Python execution for simple calculations / data transforms.

    P1 audit #10a: requires explicit founder approval
    (SINCOR_AGENT_EXEC_APPROVED=true). Without it the call is refused — the
    substring/banned-construct filter below is defense-in-depth only and is
    not relied upon as the security boundary.
    """
    if not _exec_approved():
        return {
            "status": "failed",
            "tool": "python_exec",
            "error": (
                "python_exec disabled: agent code execution requires explicit "
                "founder approval (set SINCOR_AGENT_EXEC_APPROVED=true)"
            ),
        }
    banned = [
        "import os", "import sys", "import subprocess", "__import__",
        "open(", "exec(", "eval(", "compile(", "getattr", "setattr",
        "globals(", "locals(", "breakpoint", "input(",
    ]
    lowered = code.lower()
    for b in banned:
        if b in lowered:
            return {
                "status": "failed",
                "tool": "python_exec",
                "error": f"Banned construct: {b}",
            }

    safe_globals: Dict[str, Any] = {
        "__builtins__": {
            "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
            "range": range, "round": round, "sorted": sorted, "list": list,
            "dict": dict, "str": str, "int": int, "float": float, "bool": bool,
            "print": print,
        }
    }
    local_ns: Dict[str, Any] = {}
    try:
        exec(code, safe_globals, local_ns)  # noqa: S102
        outputs = {k: v for k, v in local_ns.items() if not k.startswith("_")}
        return {
            "status": "success",
            "tool": "python_exec",
            "outputs": _redact_value(outputs),
        }
    except Exception as err:
        return {
            "status": "failed",
            "tool": "python_exec",
            "error": str(err),
        }


def tool_file_read(path: str, max_bytes: int = 50_000) -> Dict[str, Any]:
    """Read a file from the agent sandbox (the only allowed root).

    P1 audit #10: repo root and /tmp are no longer readable; output is
    secret-redacted before return.
    """
    try:
        p = _safe_path(path)
        if not p.is_file():
            return {"status": "failed", "tool": "file_read", "error": "not a file"}
        data = p.read_bytes()[:max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        return {
            "status": "success",
            "tool": "file_read",
            "path": str(p),
            "content": _redact_text(text),
            "bytes_read": len(data),
        }
    except Exception as err:
        return {"status": "failed", "tool": "file_read", "error": str(err)}


def tool_claude_reason(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 2000,
) -> Dict[str, Any]:
    """Call Claude via the existing Cortecs ClaudeClient (sync)."""
    try:
        from sincor2.cortecs_core import ClaudeClient
        client = ClaudeClient()
        if not client.client:
            return {
                "status": "failed",
                "tool": "claude_reason",
                "error": "ANTHROPIC_API_KEY not set",
            }
        text = client.complete_sync(
            prompt=prompt,
            max_tokens=max_tokens,
            system=system or (
                "You are a specialist agent inside the SINCOR AgencyKernel. "
                "Be precise, cite assumptions, and return actionable output."
            ),
        )
        return {
            "status": "success",
            "tool": "claude_reason",
            "output": _redact_text(text or ""),
        }
    except Exception as err:
        logger.warning("claude_reason failed: %s", err)
        return {
            "status": "failed",
            "tool": "claude_reason",
            "error": str(err),
        }


TOOL_REGISTRY = {
    "web_search": tool_web_search,
    "data_scraping": tool_web_search,
    "search": tool_web_search,
    "python_exec": tool_python_exec,
    "execution": tool_python_exec,
    "file_read": tool_file_read,
    "analysis": lambda **kw: tool_claude_reason(
        prompt=kw.get("prompt") or kw.get("query") or str(kw),
        system="You are an analytical agent. Produce structured findings.",
    ),
    "summarization": lambda **kw: tool_claude_reason(
        prompt=kw.get("prompt") or kw.get("query") or str(kw),
        system="You are a concise summarizer. Return clear bullet points.",
    ),
    "synthesis": lambda **kw: tool_claude_reason(
        prompt=kw.get("prompt") or kw.get("query") or str(kw),
        system="You are a synthesis agent. Merge inputs into a coherent conclusion.",
    ),
    "claude_reason": tool_claude_reason,
    "validation": lambda **kw: tool_claude_reason(
        prompt=kw.get("prompt") or kw.get("query") or str(kw),
        system="You are a validation agent. Check claims against provided evidence.",
    ),
    "cross_reference": lambda **kw: tool_claude_reason(
        prompt=kw.get("prompt") or kw.get("query") or str(kw),
        system="You are a cross-reference agent. Flag inconsistencies.",
    ),
}


def run_tools_for_step(
    tools_required: List[str],
    step_description: str,
    step_inputs: Dict[str, Any],
    tools_available: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute the tools declared by a PlanStep and return aggregated outputs.
    """
    registry = dict(TOOL_REGISTRY)
    if tools_available:
        for name, fn in tools_available.items():
            if callable(fn):
                registry[name] = fn

    outputs: Dict[str, Any] = {}
    evidence: List[str] = []
    citations: List[str] = []
    errors: List[str] = []
    tool_calls = 0

    default_prompt = (
        f"Step: {step_description}\n"
        f"Inputs: {json.dumps(step_inputs, default=str)[:2000]}\n"
        "Produce the expected outputs for this step."
    )

    for tool_name in tools_required:
        fn = registry.get(tool_name)
        if fn is None:
            errors.append(f"Unknown tool: {tool_name}")
            continue

        tool_calls += 1
        try:
            if tool_name in ("web_search", "data_scraping", "search"):
                query = (
                    step_inputs.get("query")
                    or step_inputs.get("goal")
                    or step_description
                )
                result = fn(query=str(query))
            elif tool_name in ("python_exec", "execution"):
                code = step_inputs.get("code") or step_inputs.get("python") or ""
                if not code:
                    code = step_inputs.get("expression", "result = None")
                result = fn(code=str(code))
            elif tool_name == "file_read":
                path = step_inputs.get("path") or step_inputs.get("file") or ""
                result = fn(path=str(path))
            else:
                result = fn(
                    prompt=step_inputs.get("prompt") or default_prompt,
                    query=step_inputs.get("query") or step_description,
                )

            outputs[tool_name] = result
            if result.get("status") == "success":
                evidence.append(f"{tool_name} succeeded")
                if "results" in result and isinstance(result["results"], list):
                    for r in result["results"][:3]:
                        if isinstance(r, dict) and r.get("url"):
                            citations.append(r["url"])
                if result.get("output"):
                    evidence.append(str(result["output"])[:300])
            else:
                errors.append(result.get("error", f"{tool_name} failed"))
        except Exception as err:
            errors.append(f"{tool_name}: {err}")
            logger.exception("Tool %s raised", tool_name)

    status = "success" if not errors else ("partial" if outputs else "failed")
    confidence = 0.85 if status == "success" else (0.45 if status == "partial" else 0.1)

    # Final sweep: redact any secret-shaped values that slipped through
    # individual tools (evidence strings, errors, merged outputs).
    return {
        "status": status,
        "outputs": _redact_value(outputs),
        "evidence": _redact_value(evidence),
        "citations": citations,
        "confidence": confidence,
        "resource_usage": {"tool_calls": tool_calls, "tokens": tool_calls * 200},
        "errors": _redact_value(errors),
    }
