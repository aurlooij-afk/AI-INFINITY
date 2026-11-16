from __future__ import annotations

import production_graph
import studio_ultimate
from ai_infinity import project_state_backend


def test_production_graph_uses_creator_studios_durable_connection_factory(
    monkeypatch, tmp_path
):
    database = tmp_path / "creator-state.sqlite3"
    syncs = []

    monkeypatch.setattr(studio_ultimate, "DB_PATH", database)
    monkeypatch.setattr(
        studio_ultimate, "_DurableConnection", project_state_backend.DurableConnection
    )
    monkeypatch.setattr(
        project_state_backend,
        "schedule_sync",
        lambda *args, **kwargs: syncs.append(True),
    )
    monkeypatch.setattr(production_graph, "dbpath", lambda: database)

    # The production graph must use the same factory as Creator Studio so that
    # committed graph/job/candidate writes enter the durable snapshot scheduler.
    with production_graph.db() as connection:
        assert isinstance(connection, project_state_backend.DurableConnection)
        connection.execute(
            "CREATE TABLE graph_recovery_probe (id INTEGER PRIMARY KEY, value TEXT)"
        )
        connection.execute(
            "INSERT INTO graph_recovery_probe(value) VALUES(?)", ("recovery-proof",)
        )

    assert syncs == [True]

    # A read-only graph connection must not queue an unnecessary snapshot.
    with production_graph.db() as connection:
        row = connection.execute(
            "SELECT value FROM graph_recovery_probe WHERE id=1"
        ).fetchone()
    assert row["value"] == "recovery-proof"
    assert syncs == [True]
