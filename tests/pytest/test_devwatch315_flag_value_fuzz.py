"""Dev-watch item 315 — flag-value command-injection fuzz (CVE-2026-117561).

sf-mcp's ``escapeValue()`` helper failed to neutralize backticks and
``$()`` inside CLI *flag values* reaching ``execSync``: the value was
escaped for free-text positions but a value sitting in a flag position
(``--flag=<value>`` / ``--flag <value>``) still broke out into the shell.
The lesson: escaping is not a control — argv arrays are.

Item 300 (merged, #550) pinned the tree-wide argv-only AST guard and fed
poisoned values through positional / free-text argument positions. This
file extends the corpus to **flag-value positions** of every
CLI-wrapping surface that exists on main:

* ``adapters/metamask_agent_wallet.py`` — the ``mm`` CLI runner. Every
  caller-controlled flag of ``balance`` / ``transfer`` / ``swap_quote`` /
  ``swap_execute``, in both spellings (``--flag=value`` and
  ``--flag value``), is fed the item-300 poison corpus and must stay
  exactly one argv element, verbatim.
* ``sinax/lemma_discovery.py`` — the Lean verifier. Its argv is the
  constant pair ``["lean", "--stdin"]``; caller data (statement /
  proof sketch) travels via stdin only. Poisoned statements must appear
  in the stdin payload and in *no* argv element.
* ``acceptance_status.py`` — ``run_whitelist_prepare``: argv is fully
  constant / module-derived (interpreter, script path, ``prepare``);
  pinned to that exact shape.
* ``daily_ops.py`` — ``_buyer_scan``: same, argv is
  ``[interpreter, tools/curve_buyers.py]``; pinned to that exact shape.

Plus the standing rule, per covered module: each module individually
passes the item-300 AST guard (no ``shell=True``-style / ``execSync``-
style occurrence — a string command, a non-constant-False ``shell=``,
``os.system`` / ``os.popen``, dynamic dispatch — in any of them).

And the sentinel-file negative test at real-execution strength: the
adapter's REAL ``subprocess.run`` drives a fake ``mm`` binary while flag
values carry ``$(touch <sentinel>)`` / backtick payloads. The values
must arrive at the binary as exactly one argv element each, and the
sentinel file must never appear — if any layer ever handed the command
to a shell, the payload would create it.

Scope note — a pre-existing defect this corpus exposed, deliberately
NOT fixed here and NOT asserted around: the adapter's live/dry-run
``transfer`` and ``swap_execute`` returns pass ``side=`` / ``raw=``
kwargs that ``execution_adapter.OrderResult`` does not accept (fields:
success, order_id, simulated, price, size_usd, size_tokens, error), so
those paths raise ``TypeError`` *after* the subprocess invocation (dry
run) or swallow it into a failure result (live). The flag-value
assertions below therefore target the subprocess invocation itself —
which happens before result construction — and do not assert
``result.success`` on the live paths. The defect is reported
separately for its own fix cycle.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "sincor2"

# ---------------------------------------------------------------------------
# Reuse the item-300 guard + corpus (loaded by path: import-mode proof)
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "devwatch300_guard", Path(__file__).with_name("test_devwatch300_argv_only.py")
)
guard300 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard300)

POISONED_VALUES = guard300.POISONED_VALUES
violations_from_ast = guard300.violations_from_ast

COVERED_MODULES = [
    "adapters/metamask_agent_wallet.py",
    "acceptance_status.py",
    "daily_ops.py",
    "sinax/lemma_discovery.py",
]


# ---------------------------------------------------------------------------
# Standing rule: every covered module passes the item-300 guard on its own
# ---------------------------------------------------------------------------


class TestCoveredModulesPassGuard:
    @pytest.mark.parametrize("rel_path", COVERED_MODULES)
    def test_covered_module_has_no_shell_execution(self, rel_path):
        path = SRC_ROOT / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert violations_from_ast(tree, rel_path) == [], (
            f"{rel_path}: shell-execution violation (dev-watch 315 "
            "standing rule: argv arrays only, no shell=True-style spawn)"
        )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_shell_ever(monkeypatch):
    """Any shell primitive reached during a test is an immediate failure."""
    calls: list[str] = []

    def _boom(name):
        def _fn(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            calls.append(name)
            raise AssertionError(f"{name} reached a shell: {args!r} {kwargs!r}")

        return _fn

    monkeypatch.setattr("os.system", _boom("os.system"))
    monkeypatch.setattr("os.popen", _boom("os.popen"))
    return calls


def _assert_equals_flag(cmd, kwargs, flag, poison):
    """``--flag=<poison>`` is exactly one element, verbatim, and the only
    element carrying the poison; the command is an argv list run without
    a shell."""
    assert isinstance(cmd, list), f"command is not an argv list: {cmd!r}"
    assert kwargs.get("shell") is not True
    assert [el for el in cmd if poison in el] == [f"{flag}={poison}"]


def _assert_separate_flag(cmd, kwargs, flag, poison):
    """``--flag <poison>``: the element after the flag is the poison,
    verbatim, and no other element carries it."""
    assert isinstance(cmd, list), f"command is not an argv list: {cmd!r}"
    assert kwargs.get("shell") is not True
    assert cmd[cmd.index(flag) + 1] == poison
    assert [el for el in cmd if poison in el] == [poison]


# ---------------------------------------------------------------------------
# MetaMask adapter — flag-value positions (mock capture)
# ---------------------------------------------------------------------------


class TestMetamaskFlagValueFuzz:
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

    def _live_wallet(self, monkeypatch):
        wallet, captured = self._wallet(monkeypatch)
        monkeypatch.setattr(wallet, "require_ready", lambda: None)
        return wallet, captured

    # -- balance ------------------------------------------------------

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_balance_chain_ids_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.balance(chain_id=poison)
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--chain-ids", poison
        )
        assert no_shell_ever == []

    # -- swap_quote ----------------------------------------------------

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_swap_quote_to_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.swap_quote(from_token="native", to_token=poison, amount="1")
        _assert_equals_flag(captured["cmd"], captured["kwargs"], "--to", poison)
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_swap_quote_amount_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.swap_quote(from_token="native", to_token="USDC", amount=poison)
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--amount", poison
        )
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_swap_quote_from_chain_id_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.swap_quote(
            from_token="native",
            to_token="USDC",
            amount="1",
            from_chain_id=poison,
        )
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--from-chain-id", poison
        )
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_swap_quote_slippage_flag_value_separate_form(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._wallet(monkeypatch)
        wallet.swap_quote(
            from_token="native", to_token="USDC", amount="1", slippage=poison
        )
        _assert_separate_flag(
            captured["cmd"], captured["kwargs"], "--slippage", poison
        )
        assert no_shell_ever == []

    # -- transfer (live path) ------------------------------------------

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_transfer_to_flag_value(self, monkeypatch, no_shell_ever, poison):
        wallet, captured = self._live_wallet(monkeypatch)
        wallet.transfer(to=poison, amount="1", token="native", force_live=True)
        _assert_equals_flag(captured["cmd"], captured["kwargs"], "--to", poison)
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_transfer_amount_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._live_wallet(monkeypatch)
        wallet.transfer(
            to="0x" + "11" * 20, amount=poison, token="native", force_live=True
        )
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--amount", poison
        )
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_transfer_token_flag_value_equals_form(
        self, monkeypatch, no_shell_ever, poison
    ):
        """Item 300 covered ``--token`` in balance's separate-arg form;
        transfer spells it ``--token=<value>`` — a distinct position."""
        wallet, captured = self._live_wallet(monkeypatch)
        wallet.transfer(
            to="0x" + "11" * 20, amount="1", token=poison, force_live=True
        )
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--token", poison
        )
        assert no_shell_ever == []

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_transfer_chain_id_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._live_wallet(monkeypatch)
        wallet.transfer(
            to="0x" + "11" * 20,
            amount="1",
            token="native",
            chain_id=poison,
            force_live=True,
        )
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--chain-id", poison
        )
        assert no_shell_ever == []

    # -- swap_execute (live path) ---------------------------------------

    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_swap_execute_quote_id_flag_value(
        self, monkeypatch, no_shell_ever, poison
    ):
        wallet, captured = self._live_wallet(monkeypatch)
        wallet.swap_execute(poison, force_live=True)
        _assert_equals_flag(
            captured["cmd"], captured["kwargs"], "--quote-id", poison
        )
        assert "--yes" in captured["cmd"]  # literal flag untouched
        assert no_shell_ever == []


# ---------------------------------------------------------------------------
# Lean verifier — caller data travels via stdin, never argv
# ---------------------------------------------------------------------------


class TestLeanVerifierFlagSurface:
    @pytest.mark.parametrize("poison", POISONED_VALUES)
    def test_poisoned_statement_travels_via_stdin_only(
        self, monkeypatch, no_shell_ever, poison
    ):
        from sincor2.sinax import config as sinax_cfg
        from sincor2.sinax.graph_store import VerificationResult
        from sincor2.sinax.lemma_discovery import Verifier

        monkeypatch.setattr(sinax_cfg, "VERIFIER_BACKEND", "lean")
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = Verifier().verify(poison, proof_sketch=poison)
        assert result is VerificationResult.VERIFIED
        # argv is the constant pair — no flag carries a caller value, and
        # no argv element carries any fragment of the poison.
        assert captured["cmd"] == ["lean", "--stdin"]
        assert captured["kwargs"].get("shell") is not True
        # The poison arrives exactly where it belongs: the stdin payload.
        assert poison in captured["kwargs"]["input"]
        assert no_shell_ever == []


# ---------------------------------------------------------------------------
# Constant-argv surfaces — acceptance_status / daily_ops
# ---------------------------------------------------------------------------


class TestConstantArgvSurfaces:
    def test_whitelist_prepare_argv_is_constant(self, monkeypatch, no_shell_ever):
        from sincor2 import acceptance_status

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(acceptance_status.subprocess, "run", fake_run)
        out = acceptance_status.run_whitelist_prepare()
        assert out["ok"] is True
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert captured["kwargs"].get("shell") is not True
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("scripts/whitelist_token.py")
        assert cmd[2:] == ["prepare"]  # no caller-controlled element exists
        assert no_shell_ever == []

    def test_buyer_scan_argv_is_constant(self, monkeypatch, no_shell_ever):
        from sincor2 import daily_ops

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(daily_ops.subprocess, "run", fake_run)
        out = daily_ops._buyer_scan()
        assert out["ok"] is True
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert captured["kwargs"].get("shell") is not True
        assert cmd[0] == sys.executable
        assert len(cmd) == 2
        assert cmd[1].endswith("tools/curve_buyers.py")
        assert no_shell_ever == []


# ---------------------------------------------------------------------------
# Sentinel-file negative test — REAL subprocess execution via the adapter
# ---------------------------------------------------------------------------


def _sentinel_poisons(sentinel: Path) -> list[str]:
    """Payloads that create ``sentinel`` iff a shell ever interprets them."""
    return [
        f"$(touch {sentinel})",
        f"`touch {sentinel}`",
        f"x; touch {sentinel}",
        f"x && touch {sentinel}",
        f"x | touch {sentinel}",
        f"x\ntouch {sentinel}",
    ]


@pytest.fixture()
def fake_mm(tmp_path, monkeypatch):
    """A real executable standing in for the ``mm`` binary: it records
    the argv it received (one JSON list per invocation) and answers with
    the canned JSON the adapter's live paths need."""
    dump = tmp_path / "argv.jsonl"
    script = tmp_path / "fake-mm"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['MM_ARGV_DUMP'], 'a') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'ok': True, 'data': {"
        "'authenticated': True, 'initialized': True, "
        "'address': '0x' + '11' * 20, 'mode': 'guard', "
        "'txHash': '0x' + 'ab' * 32}}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("MM_ARGV_DUMP", str(dump))

    def invocations() -> list[list[str]]:
        if not dump.exists():
            return []
        return [
            json.loads(line)
            for line in dump.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return script, invocations


class TestSentinelRealExecution:
    def _wallet(self, monkeypatch, fake_mm):
        from sincor2.adapters import metamask_agent_wallet as mmw

        monkeypatch.setattr(mmw, "kill_switch_tripped", lambda: False)
        return mmw.MetaMaskAgentWallet(mm_bin=str(fake_mm[0]))

    @pytest.mark.parametrize("shape", ["balance", "quote", "transfer", "execute"])
    def test_flag_value_payloads_never_execute(
        self, monkeypatch, tmp_path, fake_mm, shape
    ):
        sentinel = tmp_path / "pwned"
        wallet = self._wallet(monkeypatch, fake_mm)
        _, invocations = fake_mm
        for poison in _sentinel_poisons(sentinel):
            before = len(invocations())
            if shape == "balance":
                wallet.balance(token=poison)
                expected = poison  # separate-arg form: verbatim element
            elif shape == "quote":
                wallet.swap_quote(
                    from_token="native", to_token=poison, amount="1"
                )
                expected = f"--to={poison}"
            elif shape == "transfer":
                wallet.transfer(
                    to=poison, amount="1", token="native", force_live=True
                )
                expected = f"--to={poison}"
            else:
                wallet.swap_execute(poison, force_live=True)
                expected = f"--quote-id={poison}"
            new = invocations()[before:]
            assert new, f"{shape}: fake mm was never invoked"
            # The poison arrived, as exactly one argv element, verbatim,
            # in the invocation that carries the subcommand.
            carriers = [
                argv
                for argv in new
                if any(poison in el for el in argv)
                and (shape != "transfer" or argv[0] == "transfer")
                and (shape != "execute" or argv[:2] == ["swap", "execute"])
            ]
            assert carriers, f"{shape}: poison never reached the binary"
            argv = carriers[-1]
            assert [el for el in argv if poison in el] == [expected]
            # The negative assertion: no shell ever interpreted the value.
            assert not sentinel.exists(), (
                f"shell executed a flag-value payload via {shape}: {poison!r}"
            )
