from __future__ import annotations

import sqlite3

from sincor2.memory_system import MemorySystem


def test_record_episode_updates_sqlite_index_and_query(tmp_path) -> None:
    memory = MemorySystem("E-memory-01", memory_dir=str(tmp_path / "memory"))

    first = memory.record_episode(
        event_type="goal_accepted",
        content={"goal": "sell competitive intel"},
        confidence=0.9,
    )
    second = memory.record_episode(
        event_type="goal_accepted",
        content={"goal": "sell competitive intel"},
        confidence=0.8,
    )

    conn = sqlite3.connect(memory.episodic_index_db)
    rows = conn.execute(
        "SELECT event_type, agent_id, file_position, hash FROM episodic_index ORDER BY timestamp ASC"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0][0] == "goal_accepted"
    assert rows[0][1] == "E-memory-01"
    assert rows[0][2] >= 0
    assert rows[0][3] == first.hash
    assert rows[1][3] == second.hash
    assert first.hash != second.hash

    hits = memory.query_episodes(event_type="goal_accepted", limit=5)
    assert [event.hash for event in hits] == [second.hash, first.hash]
