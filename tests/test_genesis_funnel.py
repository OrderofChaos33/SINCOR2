from sincor2.genesis.store import application_number, apply_email, connect, increment_converted, leaderboard, mark_verified, parse_ref, stats
import sqlite3

def test_apply_idempotent(tmp_path):
    conn = connect(str(tmp_path / "g.db"))
    a = apply_email(conn, "t1@test.com")
    b = apply_email(conn, "t2@test.com")
    c = apply_email(conn, "t1@test.com")
    assert application_number(a["id"]) == "#000001"
    assert application_number(b["id"]) == "#000002"
    assert c["id"] == a["id"]
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 2

def test_referral_and_flags(tmp_path):
    conn = connect(str(tmp_path / "g.db"))
    parent = apply_email(conn, "t2@test.com")
    child = apply_email(conn, "t3@test.com", referrer_id=parent["id"])
    assert child["referrer_application_id"] == parent["id"]
    assert parse_ref("#000002") == 2
    for _ in range(3):
        increment_converted(conn, parent["id"])
    row = conn.execute("SELECT * FROM applications WHERE id=?", (parent["id"],)).fetchone()
    assert row["priority_id"] == 1
    for _ in range(7):
        increment_converted(conn, parent["id"])
    row = conn.execute("SELECT * FROM applications WHERE id=?", (parent["id"],)).fetchone()
    assert row["founding_badge"] == 1
    mark_verified(conn, child["id"], "0x" + "11" * 20)
    assert leaderboard(conn)[0]["id"] == parent["id"]
    s = stats(conn, holders=3455)
    assert s["signups"] == 2 and s["verifications"] == 1 and s["holders"] == 3455

def test_schema_matches_spec(tmp_path):
    path = str(tmp_path / "g.db")
    connect(path)
    schema = sqlite3.connect(path).execute("SELECT sql FROM sqlite_master WHERE name='applications'").fetchone()[0]
    for col in ("id", "email", "wallet", "referral_code", "referrer_application_id", "priority_id", "founding_badge", "status", "created_at"):
        assert col in schema
