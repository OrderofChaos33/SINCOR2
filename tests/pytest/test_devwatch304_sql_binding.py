"""Dev-watch item 304 — SQL binding guard + injection fuzz.

CVE-2026-118378: TabularisDB shipped SQL injection through its MCP
``describe_table`` tool (CVSS 8.5) — a caller-supplied table name was
interpolated into a query string, so a quote-breaking name executed
attacker SQL.

The hedge, turned into a permanent CI gate:

* no MCP tool (or DB-backed surface those tools reach) may build SQL —
  or PRAGMA, or any query string — by interpolating a caller-supplied
  value: parameter binding only, plus an allowlist of table/object
  names where binding is impossible (identifiers in PRAGMA / ALTER);
* read-only DB surfaces are enforced at the connection/driver level
  (sqlite3 ``mode=ro`` URI + ``PRAGMA query_only``), not by an app flag
  a query can bypass;
* a fuzz suite feeds quote-breaking ``table_name`` / value payloads to
  every DB-backed tool surface found in the audit and asserts no
  stacked statement executes and the data survives.

Audit catalog (base 4de2e165, grounded in the tree):

* ``mcp_server.py`` / ``swarm_command_mcp.py`` / ``swarm_command_board.py``
  — the MCP servers themselves touch NO database (HTTP client of the
  platform API / JSON-file board). The DB risk lives in the platform
  surfaces the tools reach.
* ``x402_payments._ensure_column`` — WAS the TabularisDB pattern:
  ``PRAGMA table_info({table})`` and ``ALTER TABLE {table} ADD COLUMN
  {column} {ddl}`` interpolated generic identifier parameters. Fixed:
  allowlist + one constant SQL string per allowlisted column.
* ``waitlist_system.WaitlistManager.notify_launch`` — WAS an f-string
  UPDATE (placeholder list only; values were bound). Fixed: one bound
  UPDATE per hash, no built query string at all. Every other waitlist
  query already binds values.
* ``daily_ops._revenue_digest`` — self-described "safe read-only" but
  opened the orders DB read-write. Fixed: driver-level read-only.
* ``waitlist_system.WaitlistManager.get_signup_stats`` — aggregates-only
  read surface (PR #568). Fixed: driver-level read-only connection.
* ``persistent_store``, ``genesis_cohort``, ``memory_system`` — values
  already bound (``?`` params); query text is assembled only from
  constant fragments. Covered by the fuzz below.
* Dynamic SET-clause builders elsewhere (``rail_routes``,
  ``rail_subscriptions``, ``platform_payments``) interpolate only
  column names taken from literal kwargs in code — not caller-supplied
  values — and ``rail_subscriptions._claim_sub_settle_flag`` already
  allowlists its column. Noted, not changed.

The static guard is AST-based, in the style of item 300's argv guard:
it only inspects real ``execute``/``executemany``/``executescript``
``Call`` nodes in the guarded files, classifies the SQL argument
(constant / interpolated / unprovable), and fails on interpolated.
Unprovable shapes (e.g. an imported constant DDL name) are conservative
— no flag — exactly as item 300 treats unprovable command shapes.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "sincor2"

# MCP tool modules + the DB-backed surfaces those tools reach.
GUARDED_FILES = [
    "mcp_server.py",
    "swarm_command_mcp.py",
    "swarm_command_board.py",
    "x402_payments.py",
    "waitlist_system.py",
    "daily_ops.py",
    "persistent_store.py",
    "memory_system.py",
    "genesis_cohort.py",
]

_SQL_METHODS = {"execute", "executemany", "executescript"}


# ---------------------------------------------------------------------------
# AST guard (shared by the tree test and the synthetic-source tests)
# ---------------------------------------------------------------------------


def _classify(node: ast.AST, bindings: dict[str, str]) -> str:
    """Classify a SQL expression: "constant", "interpolated" or "other".

    "constant" means the query text is provably built only from string
    literals (including ``+``/``+=`` assembly of literals and a
    constant-to-constant ``.replace`` on such a query) — every value in
    it must arrive via bound parameters. "interpolated" means a value
    is spliced into the text (f-string, ``%``, ``.format``/``.format_map``
    or concatenation with a non-constant operand). Anything else is
    "other": unprovable, treated conservatively (no flag).
    """
    if isinstance(node, ast.Constant):
        return "constant" if isinstance(node.value, str) else "other"
    if isinstance(node, ast.JoinedStr):  # f-string
        return "interpolated"
    if isinstance(node, ast.Name):
        return bindings.get(node.id, "other")
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mod):
            return "interpolated"
        if isinstance(node.op, ast.Add):
            left = _classify(node.left, bindings)
            right = _classify(node.right, bindings)
            if "interpolated" in (left, right):
                return "interpolated"
            if left == right == "constant":
                return "constant"
            if "constant" in (left, right):
                # A provable string literal concatenated with an
                # unprovable value splices that value into the text.
                return "interpolated"
            return "other"
        return "other"
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("format", "format_map"):
            return "interpolated"
        if isinstance(func, ast.Attribute) and func.attr == "replace":
            # Constant-to-constant replacement on a provably constant
            # query (memory_system's SELECT hash -> hash, file_position)
            # cannot introduce a caller value.
            if _classify(func.value, bindings) == "constant" and all(
                isinstance(a, ast.Constant) for a in node.args
            ):
                return "constant"
            return "other"
        return "other"
    if isinstance(node, ast.IfExp):
        body = _classify(node.body, bindings)
        orelse = _classify(node.orelse, bindings)
        if "interpolated" in (body, orelse):
            return "interpolated"
        if body == orelse == "constant":
            return "constant"
        return "other"
    return "other"


def _scope_bindings(scope: ast.AST) -> dict[str, str]:
    """Name -> classification for assignments made directly in ``scope``.

    Later assignments win; an AugAssign keeps the name constant only
    when both the existing binding and the added value are constant.
    Nested scopes are not descended into.
    """
    bindings: dict[str, str] = {}

    def collect(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Assign):
                kind = _classify(child.value, bindings)
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = kind
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and child.value is not None
            ):
                bindings[child.target.id] = _classify(child.value, bindings)
            elif isinstance(child, ast.AugAssign) and isinstance(
                child.target, ast.Name
            ):
                added = _classify(child.value, bindings)
                prior = bindings.get(child.target.id, "other")
                bindings[child.target.id] = (
                    "constant" if prior == added == "constant" else added
                    if added == "interpolated"
                    else "other"
                )
            collect(child)

    collect(scope)
    return bindings


def violations_from_ast(tree: ast.AST, filename: str = "<synthetic>") -> list[str]:
    found: list[str] = []

    def visit(node: ast.AST, scopes: list[dict[str, str]]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = _scope_bindings(node)
            for child in ast.iter_child_nodes(node):
                visit(child, scopes + [inner])
            return
        if isinstance(node, ast.Call):
            func = node.func
            method = None
            if isinstance(func, ast.Attribute) and func.attr in _SQL_METHODS:
                method = func.attr
            elif isinstance(func, ast.Name) and func.id in _SQL_METHODS:
                method = func.id
            if method is not None and node.args:
                bindings: dict[str, str] = {}
                for scope in scopes:
                    bindings.update(scope)
                if _classify(node.args[0], bindings) == "interpolated":
                    found.append(
                        f"{filename}:{node.lineno}: {method}() called with "
                        "SQL built by string interpolation — parameter "
                        "binding only (dev-watch 304)"
                    )
        for child in ast.iter_child_nodes(node):
            visit(child, scopes)

    visit(tree, [_scope_bindings(tree)])
    return found


def violations_from_source(source: str, filename: str = "<synthetic>") -> list[str]:
    return violations_from_ast(ast.parse(source), filename)


# ---------------------------------------------------------------------------
# 1. Static guard over the real tree
# ---------------------------------------------------------------------------


class TestStaticGuard:
    def test_guarded_files_have_no_sql_interpolation(self):
        all_violations: list[str] = []
        for rel in GUARDED_FILES:
            path = SRC_ROOT / rel
            assert path.is_file(), f"guarded file missing: {rel}"
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            all_violations.extend(violations_from_ast(tree, rel))
        assert all_violations == [], (
            "SQL-interpolation violations (dev-watch 304: parameter "
            "binding only, allowlist for identifiers):\n"
            + "\n".join(all_violations)
        )


class TestGuardItself:
    """The guard must catch the CVE class and ignore bound queries."""

    @pytest.mark.parametrize(
        "source",
        [
            # The TabularisDB describe_table pattern, verbatim shape.
            "import sqlite3\ndef d(conn, table_name):\n"
            "    return conn.execute(f\"PRAGMA table_info({table_name})\")\n",
            "import sqlite3\ndef d(conn, t):\n"
            "    return conn.execute('SELECT * FROM ' + t)\n",
            "import sqlite3\ndef d(conn, t):\n"
            "    return conn.execute('SELECT * FROM %s' % t)\n",
            "import sqlite3\ndef d(conn, t):\n"
            "    return conn.execute('SELECT * FROM {}'.format(t))\n",
            "import sqlite3\ndef d(conn, t):\n"
            "    return conn.execute('SELECT * FROM {t}'.format_map({'t': t}))\n",
            # Name bound to an interpolated string, then executed.
            "import sqlite3\ndef d(conn, t):\n"
            "    q = f'SELECT * FROM {t}'\n    return conn.execute(q)\n",
            "import sqlite3\ndef d(conn, t):\n"
            "    q = 'SELECT * FROM ' + t\n    return conn.execute(q)\n",
            # Stacked-statement vector: executescript with interpolation.
            "import sqlite3\ndef d(conn, t):\n"
            "    conn.executescript(f'DROP TABLE {t}')\n",
            # Module-scope binding used inside a function.
            "import sqlite3\nq = f'SELECT * FROM {t}'\n"
            "def d(conn):\n    return conn.execute(q)\n",
        ],
    )
    def test_guard_detects_interpolated_sql(self, source):
        assert violations_from_source(source), f"guard missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            # Bound parameters — the required pattern.
            "import sqlite3\ndef d(conn, v):\n"
            "    return conn.execute('SELECT * FROM t WHERE x = ?', (v,))\n",
            "import sqlite3\ndef d(conn, v):\n"
            "    return conn.execute('SELECT * FROM t WHERE x = :v', {'v': v})\n",
            # Constant SQL, no values at all.
            "import sqlite3\ndef d(conn):\n"
            "    return conn.execute('PRAGMA table_info(waitlist)')\n",
            # Query assembled from constant fragments only, values bound.
            "import sqlite3\ndef d(conn, state, limit):\n"
            "    q = 'SELECT payload FROM a2a_tasks'\n"
            "    if state:\n        q += ' WHERE state=?'\n"
            "    q += ' ORDER BY updated_at DESC LIMIT ?'\n"
            "    return conn.execute(q, (state, limit))\n",
            # Constant-to-constant .replace on a constant-built query.
            "import sqlite3\ndef d(conn, p):\n"
            "    q = 'SELECT hash FROM episodic_index WHERE x = ?'\n"
            "    return conn.execute(q.replace('SELECT hash', 'SELECT hash, fp'), p)\n",
            # An imported constant DDL name is unprovable: conservative.
            "import sqlite3\nfrom x import CONSUMED_TX_DDL\n"
            "def d(conn):\n    conn.execute(CONSUMED_TX_DDL)\n",
            # Mentions in docstrings / comments are not calls.
            '"""Never f-string SQL like f"SELECT {x}" here."""\n'
            "import sqlite3\ndef d(conn, v):\n"
            "    return conn.execute('SELECT 1 WHERE x = ?', (v,))\n",
        ],
    )
    def test_guard_ignores_bound_and_constant_sql(self, source):
        assert violations_from_source(source) == [], f"false positive: {source!r}"


# ---------------------------------------------------------------------------
# 2. Fuzz — quote-breaking table_name / value payloads vs every surface
# ---------------------------------------------------------------------------

EVIL_TABLE_NAMES = [
    "x'); DROP TABLE waitlist;--",
    "waitlist; DROP TABLE waitlist;--",
    "' OR '1'='1",
    'x"); DROP TABLE x402_challenges;--',
    "x402_challenges'); DROP TABLE x402_challenges;--",
    "Robert'); DROP TABLE waitlist;--",
    "' UNION SELECT * FROM waitlist;--",
    "a'b;c\"d",
]

EVIL_VALUES = [
    "x'); DROP TABLE waitlist;--",
    "' OR '1'='1",
    "'); DELETE FROM waitlist;--",
    "' OR 1=1; --",
    "a'b;c\"d--",
]


def _tables(db_path) -> set:
    with sqlite3.connect(db_path) as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


class TestX402EnsureColumnFuzz:
    """_ensure_column is the describe_table analogue: its table_name
    argument is exactly the CVE's injection point."""

    def _conn(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute(
            "CREATE TABLE x402_challenges (challenge_id TEXT PRIMARY KEY)"
        )
        conn.execute("INSERT INTO x402_challenges VALUES ('c1')")
        conn.commit()
        return conn

    def test_allowlisted_migration_still_works(self, tmp_path):
        from sincor2.x402_payments import _ensure_column

        conn = self._conn(tmp_path)
        _ensure_column(conn, "x402_challenges", "token_expires_at", "TEXT")
        _ensure_column(conn, "x402_challenges", "token_consumed_at", "TEXT")
        _ensure_column(conn, "x402_challenges", "token_expires_at", "TEXT")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(x402_challenges)")}
        assert {"token_expires_at", "token_consumed_at"} <= cols
        assert conn.execute("SELECT COUNT(*) FROM x402_challenges").fetchone()[0] == 1
        conn.close()

    @pytest.mark.parametrize("evil", EVIL_TABLE_NAMES)
    def test_evil_table_name_rejected_and_data_survives(self, tmp_path, evil):
        from sincor2.x402_payments import _ensure_column

        path = tmp_path / "x402.db"
        conn = self._conn(tmp_path)
        with pytest.raises(ValueError):
            _ensure_column(conn, evil, "token_expires_at", "TEXT")
        conn.close()
        assert "x402_challenges" in _tables(path)
        with sqlite3.connect(path) as check:
            assert check.execute(
                "SELECT COUNT(*) FROM x402_challenges"
            ).fetchone()[0] == 1

    @pytest.mark.parametrize("evil", EVIL_TABLE_NAMES)
    def test_evil_column_and_ddl_rejected(self, tmp_path, evil):
        from sincor2.x402_payments import _ensure_column

        path = tmp_path / "x402.db"
        conn = self._conn(tmp_path)
        with pytest.raises(ValueError):
            _ensure_column(conn, "x402_challenges", evil, "TEXT")
        with pytest.raises(ValueError):
            _ensure_column(conn, "x402_challenges", "token_expires_at", evil)
        conn.close()
        assert "x402_challenges" in _tables(path)


@pytest.fixture()
def waitlist_manager(tmp_path):
    from sincor2.waitlist_system import WaitlistManager

    return WaitlistManager(db_path=str(tmp_path / "waitlist.db"))


class TestWaitlistFuzz:
    def test_evil_values_are_stored_as_data_not_executed(
        self, waitlist_manager, tmp_path
    ):
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/"):
            for i, evil in enumerate(EVIL_VALUES):
                result = waitlist_manager.add_to_waitlist(
                    {
                        "email": f"user{i}@example.com",
                        "product_interest": evil,
                        "company_name": evil,
                    }
                )
                assert result["success"], result
        db = tmp_path / "waitlist.db"
        assert "waitlist" in _tables(db)
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT product_interest, company_name FROM waitlist "
                "ORDER BY id"
            ).fetchall()
        assert [r[0] for r in rows] == EVIL_VALUES
        assert [r[1] for r in rows] == EVIL_VALUES
        stats = waitlist_manager.get_signup_stats()
        assert stats["total_signups"] == len(EVIL_VALUES)

    @pytest.mark.parametrize("evil", EVIL_VALUES)
    def test_evil_lookup_and_notify_inputs_do_not_execute(
        self, waitlist_manager, tmp_path, evil
    ):
        assert waitlist_manager.get_waitlist_position(evil) is None
        assert waitlist_manager.notify_launch(evil) == 0
        assert "waitlist" in _tables(tmp_path / "waitlist.db")

    def test_notify_launch_marks_matching_row_as_before(self, waitlist_manager):
        # Seed a row whose email_hash is hash_email(encrypted_email) —
        # the exact match notify_launch's UPDATE targets — and prove
        # the bound per-row UPDATE still flips it.
        email_hash = waitlist_manager.hash_email("victim@example.com")
        with sqlite3.connect(waitlist_manager.db_path) as conn:
            conn.execute(
                "INSERT INTO waitlist (email_hash, encrypted_email, "
                "product_interest, is_verified, notification_sent) "
                "VALUES (?, ?, 'Growth Engine', 1, 0)",
                (email_hash, "victim@example.com"),
            )
            conn.commit()
        assert waitlist_manager.notify_launch("Growth Engine") == 1
        with sqlite3.connect(waitlist_manager.db_path) as conn:
            sent = conn.execute(
                "SELECT notification_sent FROM waitlist WHERE email_hash = ?",
                (email_hash,),
            ).fetchone()[0]
        assert sent == 1


class TestPersistentStoreFuzz:
    @pytest.mark.parametrize("evil", EVIL_VALUES)
    def test_evil_keys_and_filters_are_data(self, tmp_path, evil):
        from sincor2.persistent_store import PersistentStore

        store = PersistentStore(db_path=tmp_path / "store.db")
        store.kv_set(evil, evil)
        assert store.kv_get(evil) == evil
        assert store.list_tasks(state=evil) == []
        assert store.chain_events_since(event_name=evil) == []
        store.upsert_task({"id": evil, "state": "open"})
        assert store.get_task(evil)["id"] == evil
        assert {"kv", "a2a_tasks"} <= _tables(tmp_path / "store.db")


class TestGenesisCohortFuzz:
    @pytest.mark.parametrize("evil", EVIL_VALUES)
    def test_evil_lookups_return_none_and_claims_survive(
        self, tmp_path, monkeypatch, evil
    ):
        monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
        from sincor2 import genesis_cohort

        assert genesis_cohort.get_member(evil) is None
        assert genesis_cohort.get_member_by_wallet(evil) is None
        assert genesis_cohort.is_genesis_member(evil) is False
        claimed = genesis_cohort.claim("member@example.com", "password-123")
        assert claimed["ok"]
        # The evil probes changed nothing: the real member still reads.
        assert genesis_cohort.get_member("member@example.com") is not None
        assert genesis_cohort.genesis_count() == 1


class TestMemorySystemFuzz:
    @pytest.mark.parametrize("evil", EVIL_VALUES)
    def test_evil_fact_filters_are_data(self, tmp_path, evil):
        from sincor2.memory_system import MemorySystem

        memory = MemorySystem("dw304", memory_dir=str(tmp_path / "memory"))
        assert memory.query_semantic_facts(subject=evil) == []
        assert memory.query_episodes(event_type=evil) == []
        tables = _tables(memory.semantic_db)
        assert "facts" in tables


# ---------------------------------------------------------------------------
# 3. Read-only enforcement at the driver level
# ---------------------------------------------------------------------------


class TestReadOnlyDriverEnforcement:
    def test_daily_ops_revenue_digest_is_driver_readonly(self, tmp_path, monkeypatch):
        from sincor2 import daily_ops

        db = tmp_path / "orders.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE orders (amount REAL, payment_status TEXT)"
            )
            conn.executemany(
                "INSERT INTO orders VALUES (?, ?)",
                [(10.0, "completed"), (5.0, "pending")],
            )
        monkeypatch.setenv("ORDERS_DB_PATH", str(db))
        digest = daily_ops._revenue_digest()
        assert digest == {
            "ok": True,
            "total_orders": 2,
            "completed_revenue_usd": 10.0,
            "pending_revenue_usd": 5.0,
        }
        # A write attempted on the read-only connection is refused by
        # SQLite itself, not by any app-layer check.
        conn = daily_ops._connect_readonly(db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DROP TABLE orders")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE orders SET amount = 0")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO orders VALUES (1.0, 'completed')")
        conn.close()
        with sqlite3.connect(db) as check:
            assert check.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2

    def test_daily_ops_readonly_missing_file_fails_at_driver(self, tmp_path):
        from sincor2 import daily_ops

        with pytest.raises(sqlite3.OperationalError):
            daily_ops._connect_readonly(tmp_path / "missing.db")
        # mode=ro must not have created the file as a side effect.
        assert not (tmp_path / "missing.db").exists()

    def test_waitlist_stats_connection_is_driver_readonly(self, waitlist_manager):
        stats = waitlist_manager.get_signup_stats()
        assert stats["total_signups"] == 0
        conn = waitlist_manager._connect_readonly()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DROP TABLE waitlist")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO waitlist (email_hash, encrypted_email, "
                "product_interest) VALUES ('h', 'e', 'p')"
            )
        conn.close()
        assert "waitlist" in _tables(waitlist_manager.db_path)
        # The hash of the seeded helper row above never landed.
        assert waitlist_manager.get_signup_stats()["total_signups"] == 0
