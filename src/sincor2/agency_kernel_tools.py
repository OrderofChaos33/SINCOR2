#!/usr/bin/env python3
"""
Real tool bindings for AgencyKernel executor.

Replaces the old _simulate_step_execution canned responses with actual:
  - web_search   (DuckDuckGo HTML / requests)
  - python_exec  (restricted eval for simple calculations)
  - file_read    (safe path-restricted read)
  - claude_reason / analysis / synthesis (Anthropic via existing ClaudeClient)
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

# Paths allowed for file_read (project + data volume)
_ALLOWED_ROOTS = [
    Path("/data").resolve(),
    Path(__file__).resolve().parent.parent.parent,  # repo root
    Path("/tmp"),
]


def _safe_path(path_str: str) -> Path:
    p = Path(path_str).expanduser().resolve()
    for root in _ALLOWED_ROOTS:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise PermissionError(f"Path outside allowed roots: {path_str}")


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
            "results": results,
            "count": len(results),
        }
    except Exception as exc:
        logger.warning("web_search failed: %s", e xc)
        return {
            "status": "failed",
            "tool": "web_search",
            "query": query,
            "error": str(e xc),
            "results": [],
        }


def tool_python_exec(code: str) -> Dict[str, Any]:
    """Restricted Python execution for simple calculations / data transforms."""
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
            "outputs": outputs,
        }
    except Exception as e xc:
        return {
            "status": "failed",
            "tool": "python_exec",
            "error": str(e xc),
        }


def tool_file_read(path: str, max_bytes: int = 50_000) -> Dict[str, Any]:
    """Read a file from an allowed root."""
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
            "content": text,
            "bytes_read": len(data),
        }
    except Exception as e xc:
        return {"status": "failed", "tool": "file_read", "error": str(e xc)}


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
            "output": text,
        }
    except Exception as e xc:
        logger.warning("claude_reason failed: %s", e xc)
        return {
            "status": "failed",
            "tool": "claude_reason",
            "error": str(e xc),
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
        except Exception as e xc:
            errors.append(f"{tool_name}: {e xc}")
            logger.exception("Tool %s raised", tool_name)

    status = "success" if not errors else ("partial" if outputs else "failed")
    confidence = 0.85 if status == "success" else (0.45 if status == "partial" else 0.1)

    return {
        "status": status,
        "outputs": outputs,
        "evidence": evidence,
        "citations": citations,
        "confidence": confidence,
        "resource_usage": {"tool_calls": tool_calls, "tokens": tool_calls * 200},
        "errors": errors,
    }
