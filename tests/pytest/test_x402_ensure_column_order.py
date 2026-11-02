"""W-120 residual regression tests: _ensure_column check ordering.

Hunt 36 finding: ``x402_payments._ensure_column`` validated the migration
allowlist BEFORE checking whether the column already exists in the table.
If ``x402_challenges`` already contained a column that is not in the
allowlist, ``_ensure_column`` raised ValueError even though no ALTER was
needed — and since ``init_x402_db()`` runs on every ``_conn()`` call, one
future schema addition that forgets the allowlist entry bricks the whole
x402 path (``X402_AVAILABLE=False``), exactly the W-120 outage mode.

Required order: existence first (existing column -> return, nothing to
gate), allowlist second (it gates only an actual ALTER).
"""

import sqlite3

import pytest

from sincor2.x402_payments import _ensure_column, init_x402_db


def _cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(x402_challenges)")}


class TestExistingColumnReturnsWithoutRaising:
    """(a) A column that already exists needs no migration, so the
    allowlist must never get a chance to raise for it."""

    def test_existing_unallowlisted_column_returns(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute(
            "CREATE TABLE x402_challenges ("
            "challenge_id TEXT PRIMARY KEY, next_migration_col TEXT)"
        )
        conn.execute("INSERT INTO x402_challenges VALUES ('c1', 'v')")
        conn.commit()

        # Must not raise even though 'next_migration_col' is unallowlisted.
        _ensure_column(conn, "x402_challenges", "next_migration_col", "TEXT")

        assert _cols(conn) == {"challenge_id", "next_migration_col"}
        assert conn.execute(
            "SELECT next_migration_col FROM x402_challenges"
        ).fetchone()[0] == "v"
        conn.close()

    def test_existing_column_returns_regardless_of_ddl_argument(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute(
            "CREATE TABLE x402_challenges ("
            "challenge_id TEXT PRIMARY KEY, token_expires_at TEXT)"
        )
        conn.commit()

        # Column exists -> nothing to migrate -> no raise, even with a
        # ddl argument that would fail the allowlist for a real ALTER.
        _ensure_column(conn, "x402_challenges", "token_expires_at", "BLOB")
        conn.close()


class TestMissingColumnStillGated:
    """(b) The allowlist still gates any actual ALTER: a missing,
    unallowlisted column raises ValueError and the table is untouched."""

    def test_missing_unallowlisted_column_raises_and_does_not_alter(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute("CREATE TABLE x402_challenges (challenge_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO x402_challenges VALUES ('c1')")
        conn.commit()
        before = _cols(conn)

        with pytest.raises(ValueError):
            _ensure_column(conn, "x402_challenges", "next_migration_col", "TEXT")

        assert _cols(conn) == before
        assert "next_migration_col" not in _cols(conn)
        assert conn.execute("SELECT COUNT(*) FROM x402_challenges").fetchone()[0] == 1
        conn.close()

    def test_missing_allowlisted_column_with_wrong_ddl_still_raises(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute("CREATE TABLE x402_challenges (challenge_id TEXT PRIMARY KEY)")
        conn.commit()

        with pytest.raises(ValueError):
            _ensure_column(conn, "x402_challenges", "token_expires_at", "BLOB")
        assert "token_expires_at" not in _cols(conn)
        conn.close()


class TestAllowlistedMigrationStillWorks:
    """(c) An allowlisted missing column is still added."""

    def test_allowlisted_missing_column_added(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "x402.db")
        conn.execute("CREATE TABLE x402_challenges (challenge_id TEXT PRIMARY KEY)")
        conn.commit()

        _ensure_column(conn, "x402_challenges", "source_key", "TEXT")

        assert "source_key" in _cols(conn)
        conn.close()


class TestInitStaysIdempotent:
    """(d) Fresh double init_x402_db() still passes."""

    def test_double_init_on_fresh_db(self, tmp_path, monkeypatch):
        db = tmp_path / "orders.db"
        monkeypatch.setenv("ORDERS_DB_PATH", str(db))

        init_x402_db()
        init_x402_db()

        with sqlite3.connect(db) as conn:
            cols = _cols(conn)
        assert {"token_expires_at", "token_consumed_at", "source_key"} <= cols
