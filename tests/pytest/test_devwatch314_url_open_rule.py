"""Dev-watch 314 — a server-supplied URL must never reach a shell / OS-open.

CVE-2026-119025 class: an MCP *client* took an ``authorization_endpoint``
(a URL supplied by a possibly-malicious server) and handed it to an OS-open
helper; on Windows that path composes a shell command line, so a
``$(...)`` / backtick payload in the URL executed with the user's rights.

Codebase audit behind this file (2026-10-30, base ``4de2e165``):

* No MCP *client* exists in this repo (no ``ClientSession`` / stdio / SSE
  client) — we ship MCP servers only, so no third-party server's
  ``authorization_endpoint`` is ever consumed here.
* OAuth in ``src/sincor2/mvp_app.py`` is server-side authlib: provider
  endpoints come from authlib's built-in registry and reach the end user
  as an HTTP 302 in *their* browser — never a local shell or OS-open.
* The ONLY OS-open call in the entire repo is
  ``scripts/whitelist_token.py`` ``cmd_open_forms`` ->
  ``webbrowser.open(url)``, and its URLs come from the static, in-source
  ``TARGETS`` table (never from the network). Python's ``webbrowser``
  still has shell-composing controllers (``GenericBrowser`` uses
  ``os.system`` when the browser command contains ``%s``), so even this
  surface now validates before opening — defence in depth, and the exact
  refusal a future server-fed caller must reuse.

The RULE (also in the ``scripts/whitelist_token.py`` module docstring):

    A URL supplied by a server or third party is NEVER passed into a
    shell or an OS-open helper. Scheme + host are validated against an
    allowlist first; opening happens via argv / exec-file style APIs
    only, never via a shell string.

This file enforces it two ways:

1. Functional: the inventoried opener refuses malicious URLs (incl.
   ``$(...)`` / backtick payloads on an *allowlisted* host) BEFORE any
   opener or shell runs — proven with recorder sentinels and a sentinel
   file that shell execution would have created.
2. Static guard: an AST scan of the client/agent modules
   (``src/sincor2/**``) and operator scripts (``scripts/**.py``) fails CI
   if an OS-open of an unvalidated URL is introduced: no ``webbrowser`` /
   ``os.startfile`` outside the inventoried surface, and inside it every
   open must consume the validator's output.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WHITELIST_SCRIPT = REPO_ROOT / "scripts" / "whitelist_token.py"
SCAN_ROOTS = [REPO_ROOT / "src" / "sincor2", REPO_ROOT / "scripts"]
INVENTORIED_OPENER = WHITELIST_SCRIPT  # the one allowed webbrowser surface


def _load_whitelist_module():
    spec = importlib.util.spec_from_file_location("whitelist_token_314", WHITELIST_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Part 1 — functional refusal (sentinel-proven)
# --------------------------------------------------------------------------

MALICIOUS_URLS = [
    # $(...) command substitution riding on an ALLOWLISTED host — the
    # exact CVE-2026-119025 payload shape.
    "https://www.coingecko.com/en/coins/$(touch${IFS}SENTINEL)",
    "https://www.coingecko.com/en/coins/new?x=$(id)",
    # Backtick substitution on an allowlisted host.
    "https://dexscreener.com/base/`touch${IFS}SENTINEL`",
    # Non-allowlisted host, otherwise clean URL.
    "https://evil.example.com/form",
    "https://www.coingecko.com.evil.example/en/coins/new",
    # Scheme downgrade / dangerous schemes.
    "http://www.coingecko.com/en/coins/new",
    "javascript:alert(1)",
    "file:///etc/passwd",
    # Userinfo confusion.
    "https://www.coingecko.com@evil.example/en/coins/new",
    # Whitespace / control-char smuggling.
    "https://www.coingecko.com/en/coins/new value",
    "https://www.coingecko.com/en/coins/new\n$(id)",
    # Shell metacharacters in the path.
    "https://www.coingecko.com/en;rm${IFS}-rf",
    "https://www.coingecko.com/en|id",
]


def test_validator_refuses_every_malicious_url():
    wl = _load_whitelist_module()
    validate = getattr(wl, "_validate_open_url", None)
    assert validate is not None, (
        "scripts/whitelist_token.py must expose _validate_open_url() — "
        "the dev-watch 314 allowlist validator"
    )
    for url in MALICIOUS_URLS:
        assert validate(url) is None, f"malicious URL was accepted: {url!r}"


def test_validator_accepts_every_shipped_target_url():
    wl = _load_whitelist_module()
    validate = getattr(wl, "_validate_open_url", None)
    assert validate is not None
    checked = 0
    for target in wl.TARGETS:
        url = target.get("url")
        if not url:
            continue
        checked += 1
        assert validate(url) == url, f"shipped TARGETS URL refused: {url!r}"
    assert checked >= 8, "expected the shipped TARGETS table to carry URLs"


def test_malicious_server_url_refused_before_any_shell_or_open(tmp_path, monkeypatch):
    """End-to-end through the real opener entry point.

    A 'server' (here: a poisoned TARGETS entry, standing in for any
    server-supplied authorization_endpoint / URL) hands cmd_open_forms a
    $(touch SENTINEL) payload on an allowlisted host. The refusal must
    happen BEFORE webbrowser.open / any subprocess / os.system runs:
    every one of those is rigged to record the call and drop the
    sentinel file a real shell would have created.
    """
    wl = _load_whitelist_module()
    sentinel = tmp_path / "pwned"
    payload = f"https://www.coingecko.com/en/coins/$(touch${{IFS}}{sentinel})"
    legit = "https://www.coingecko.com/en/coins/new"

    calls: list[str] = []

    def recorder(name):
        def _rec(*args, **kwargs):
            calls.append(name)
            sentinel.touch()  # what a real shell spawn would have done
            raise AssertionError(f"{name} must never run for a refused URL")

        return _rec

    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: calls.append(f"open:{url}"))
    monkeypatch.setattr(os, "system", recorder("os.system"))
    monkeypatch.setattr(os, "popen", recorder("os.popen"))
    monkeypatch.setattr(subprocess, "run", recorder("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", recorder("subprocess.Popen"))

    monkeypatch.setattr(
        wl,
        "TARGETS",
        [
            {"name": "malicious server form", "type": "form", "url": payload},
            {"name": "legit form", "type": "form", "url": legit},
        ],
    )
    monkeypatch.setattr(wl, "PR", tmp_path / "no-such-pr-dir")  # no clipboard branch

    rc = wl.cmd_open_forms(argparse.Namespace())
    assert rc == 0
    # The malicious URL never reached the opener; the legit one did —
    # refusal is selective validation, not a dead function.
    assert calls == [f"open:{legit}"], f"unexpected opener/shell calls: {calls}"
    assert not sentinel.exists(), "sentinel created — a shell executed the payload"


# --------------------------------------------------------------------------
# Part 2 — static guard over client/agent modules + operator scripts
# --------------------------------------------------------------------------

_OPENER_ATTRS = {"open", "open_new", "open_new_tab"}
_VALIDATOR_NAME = "_validate_open_url"


def _module_aliases(tree: ast.AST):
    """Return (webbrowser_names, os_names, startfile_names, wb_open_names)."""
    wb_names: set[str] = set()
    os_names: set[str] = set()
    startfile_names: set[str] = set()
    wb_open_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "webbrowser":
                    wb_names.add(alias.asname or "webbrowser")
                elif alias.name == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "webbrowser":
                for alias in node.names:
                    if alias.name in _OPENER_ATTRS:
                        wb_open_names.add(alias.asname or alias.name)
            elif node.module == "os":
                for alias in node.names:
                    if alias.name == "startfile":
                        startfile_names.add(alias.asname or "startfile")
    return wb_names, os_names, startfile_names, wb_open_names


def _opener_call_sites(tree: ast.AST):
    """Yield (kind, node) for every OS-open call shape, incl. aliases/getattr."""
    wb_names, os_names, startfile_names, wb_open_names = _module_aliases(tree)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                # webbrowser.open*(...) under any import alias
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id in wb_names
                    and func.attr in _OPENER_ATTRS
                ):
                    sites.append(("webbrowser", node))
                # os.startfile(...)
                elif (
                    isinstance(func.value, ast.Name)
                    and func.value.id in os_names
                    and func.attr == "startfile"
                ):
                    sites.append(("startfile", node))
            elif isinstance(func, ast.Name) and func.id in wb_open_names | startfile_names:
                sites.append(("opener", node))
            elif isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
                target, attr = node.args[0], node.args[1]
                if isinstance(attr, ast.Constant) and isinstance(target, ast.Name):
                    if target.id in wb_names and attr.value in _OPENER_ATTRS:
                        sites.append(("webbrowser-getattr", node))
                    elif target.id in os_names and attr.value == "startfile":
                        sites.append(("startfile-getattr", node))
    return sites


def scan_source(source: str, *, inventoried: bool) -> list[str]:
    """Return dev-watch 314 violations found in one module's source."""
    tree = ast.parse(source)
    violations: list[str] = []
    sites = _opener_call_sites(tree)

    if sites and not inventoried:
        for kind, node in sites:
            violations.append(
                f"line {node.lineno}: {kind} OS-open outside the inventoried "
                "surface (scripts/whitelist_token.py) — a server-supplied URL "
                "must never reach an OS-open helper (dev-watch 314)"
            )
        return violations

    if inventoried:
        # Every open must consume the validator's output: within the
        # enclosing function, the opened argument must be a name assigned
        # from a _validate_open_url(...) call.
        for kind, node in sites:
            if kind != "webbrowser":
                violations.append(
                    f"line {node.lineno}: {kind} is not an approved opener "
                    "even in the inventoried surface"
                )
                continue
            fn = _enclosing_function(tree, node)
            validated_names = set()
            if fn is not None:
                for sub in ast.walk(fn):
                    if (
                        isinstance(sub, ast.Assign)
                        and isinstance(sub.value, ast.Call)
                        and isinstance(sub.value.func, ast.Name)
                        and sub.value.func.id == _VALIDATOR_NAME
                    ):
                        for tgt in sub.targets:
                            if isinstance(tgt, ast.Name):
                                validated_names.add(tgt.id)
            arg = node.args[0] if node.args else None
            if not (isinstance(arg, ast.Name) and arg.id in validated_names):
                violations.append(
                    f"line {node.lineno}: webbrowser.open() argument is not "
                    f"the output of {_VALIDATOR_NAME}() — unvalidated URL "
                    "reached an OS-open helper (dev-watch 314)"
                )
    # A module that consumes an authorization_endpoint must never open.
    if "authorization_endpoint" in source and sites:
        violations.append(
            "module references authorization_endpoint AND performs an "
            "OS-open — server-supplied endpoints must never be opened "
            "locally (dev-watch 314)"
        )
    return violations


def _enclosing_function(tree: ast.AST, target: ast.AST):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if sub is target:
                    if best is None or node.lineno >= best.lineno:
                        best = node
    return best


def _iter_scanned_files():
    for root in SCAN_ROOTS:
        yield from sorted(root.rglob("*.py"))


def test_no_unvalidated_os_open_anywhere():
    problems: list[str] = []
    scanned = 0
    for path in _iter_scanned_files():
        if path.resolve() == Path(__file__).resolve():
            continue
        scanned += 1
        source = path.read_text(encoding="utf-8", errors="replace")
        for violation in scan_source(source, inventoried=(path == INVENTORIED_OPENER)):
            problems.append(f"{path.relative_to(REPO_ROOT)}: {violation}")
    assert scanned > 100, f"guard scanned suspiciously few files: {scanned}"
    assert not problems, "dev-watch 314 violations:\n" + "\n".join(problems)


# --------------------------------------------------------------------------
# Part 3 — guard-the-guard: the scanner must catch evasions
# --------------------------------------------------------------------------

EVASIONS = [
    "import webbrowser\nwebbrowser.open(u)\n",
    "import webbrowser as wb\nwb.open_new_tab(u)\n",
    "from webbrowser import open as o\no(u)\n",
    "import os\nos.startfile(u)\n",
    "from os import startfile\nstartfile(u)\n",
    "import webbrowser\ngetattr(webbrowser, 'open')(u)\n",
    "import os\ngetattr(os, 'startfile')(u)\n",
]


@pytest.mark.parametrize("source", EVASIONS)
def test_guard_catches_opener_evasions(source):
    assert scan_source(source, inventoried=False), f"evasion not caught: {source!r}"


def test_guard_catches_unvalidated_open_in_inventoried_surface():
    bad = "import webbrowser\ndef f(t):\n    webbrowser.open(t['url'])\n"
    assert scan_source(bad, inventoried=True)
    good = (
        "import webbrowser\n"
        "def f(t):\n"
        "    safe = _validate_open_url(t['url'])\n"
        "    if safe is not None:\n"
        "        webbrowser.open(safe)\n"
    )
    assert scan_source(good, inventoried=True) == []


def test_guard_ignores_innocent_sources():
    assert scan_source("import subprocess\nsubprocess.run(['ls'])\n", inventoried=False) == []
    assert scan_source('"""Never use webbrowser.open on a URL."""\n', inventoried=False) == []
