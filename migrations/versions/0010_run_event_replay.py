"""Journal run snapshots in the same transaction as each run update."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010_run_event_replay"
down_revision = "0009_run_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_runs", sa.Column("event_seq", sa.BigInteger(), server_default="1", nullable=False)
    )
    op.create_table(
        "run_snapshots",
        sa.Column(
            "run_id",
            UUID(),
            sa.ForeignKey(
                "agent_runs.id", ondelete="CASCADE", deferrable=True, initially="DEFERRED"
            ),
            primary_key=True,
        ),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("record", JSONB(), nullable=False),
    )
    op.create_index("ix_run_snapshots_created", "run_snapshots", ["created_at"])
    # Qualify the destination with the owning schema, including in test databases with search_path.
    op.execute("""
        CREATE FUNCTION capture_run_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.record IS NOT DISTINCT FROM OLD.record THEN RETURN NEW; END IF;
                NEW.event_seq := OLD.event_seq + 1;
            ELSE
                NEW.event_seq := 2;
            END IF;
            EXECUTE format('INSERT INTO %I.run_snapshots (run_id, seq, record) VALUES ($1,$2,$3)',
                           TG_TABLE_SCHEMA) USING NEW.id, NEW.event_seq, NEW.record;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""CREATE TRIGGER capture_run_snapshot BEFORE INSERT OR UPDATE OF record
                  ON agent_runs FOR EACH ROW EXECUTE FUNCTION capture_run_snapshot()""")


def downgrade():
    op.execute("DROP TRIGGER capture_run_snapshot ON agent_runs")
    op.execute("DROP FUNCTION capture_run_snapshot()")
    op.drop_table("run_snapshots")
    op.drop_column("agent_runs", "event_seq")
