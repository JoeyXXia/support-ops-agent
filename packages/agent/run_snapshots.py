"""Replayable run snapshots. The registered run remains authoritative after pruning."""

from sqlalchemy import text

RETAINED = "created_at >= clock_timestamp() - interval '24 hours'"


def snapshots(engine, run_id, context, cursor):
    keys = {"id": str(run_id), "owner": context.model_dump_json(), "cursor": cursor}
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        current = conn.execute(
            text("""SELECT event_seq, record FROM agent_runs
                    WHERE id=:id AND owner_context=CAST(:owner AS jsonb)"""),
            keys,
        ).one_or_none()
        if current is None:
            return None
        latest, record = current
        if cursor is None or cursor > latest:
            return latest, [(latest, record)]
        if cursor == latest:
            return latest, [(latest, record)] if record["status"] not in {
                "running",
                "queued",
            } else []
        available = conn.execute(
            text("SELECT 1 FROM run_snapshots WHERE run_id=:id AND seq=:cursor AND " + RETAINED),
            keys,
        ).scalar()
        if not available:
            return latest, [(latest, record)]
        rows = conn.execute(
            text(
                "SELECT seq, record FROM run_snapshots WHERE run_id=:id AND seq>:cursor AND "
                + RETAINED
                + " ORDER BY seq LIMIT 100"
            ),
            keys,
        ).all()
        # A gap from pruning or a legacy snapshot is repaired with the authoritative record.
        if not rows or rows[0].seq != cursor + 1:
            return latest, [(latest, record)]
        return latest, rows


def prune_snapshots(engine):
    with engine.begin() as conn:
        return conn.execute(
            text("""
            DELETE FROM run_snapshots WHERE (run_id, seq) IN (
                SELECT run_id, seq FROM run_snapshots
                WHERE created_at < clock_timestamp() - interval '24 hours'
                ORDER BY created_at LIMIT 5000
            )
        """)
        ).rowcount
