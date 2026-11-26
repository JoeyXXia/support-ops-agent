"""Short PostgreSQL transactions for the durable worker queue."""

import json
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text

from packages.tools.schemas import AuthContext

from .persistence import RunRepository

MAX_ATTEMPTS = 3
TERMINAL = {"completed", "stopped", "failed"}
ACTIVE_LEASE = """
    WHERE id=:id AND job_pending AND job_lease_id=:lease
      AND job_lease_until > clock_timestamp()
"""


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class Job:
    run_id: UUID
    lease_id: UUID
    context: AuthContext

    @property
    def params(self):
        return {"id": self.run_id, "lease": self.lease_id}


class RunQueue:
    def __init__(self, engine):
        self.engine = engine

    def enqueue(self, run_id, context, *, allow_paused=False, connection=None):
        transaction = self.engine.begin() if connection is None else nullcontext(connection)
        with transaction as conn:
            owned = (
                conn.execute(
                    text("""SELECT record, job_pending FROM agent_runs
                        WHERE id=:id AND owner_context=CAST(:owner AS jsonb)
                        FOR UPDATE"""),
                    {"id": str(run_id), "owner": context.model_dump_json()},
                )
                .mappings()
                .one_or_none()
            )
            if owned is None:
                raise LookupError("run_not_found")
            record = owned["record"]
            if record["status"] in TERMINAL:
                return record
            if owned["job_pending"]:
                if connection is not None:
                    return record
                raise OverflowError("busy")
            if record["status"] == "awaiting_approval" and not allow_paused:
                return record
            record.update(status="queued", reason="")
            conn.execute(
                text("""UPDATE agent_runs
                        SET record=CAST(:record AS jsonb), job_pending=true, job_attempts=0,
                            job_lease_id=NULL, job_lease_until=NULL
                        WHERE id=:id"""),
                {"id": str(run_id), "record": json.dumps(record)},
            )
            return record

    def claim(self, lease_seconds):
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    text("""SELECT id, owner_context, record, job_attempts FROM agent_runs
                        WHERE job_pending AND
                            (job_lease_until IS NULL OR job_lease_until <= clock_timestamp())
                        ORDER BY job_lease_until NULLS LAST, record->>'created_at', id
                        FOR UPDATE SKIP LOCKED LIMIT 1""")
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if row["job_attempts"] >= MAX_ATTEMPTS:
                record = row["record"] | {"status": "failed", "reason": "worker_retry_exhausted"}
                conn.execute(
                    text("""UPDATE agent_runs SET record=CAST(:record AS jsonb),
                            job_pending=false, job_lease_id=NULL, job_lease_until=NULL
                            WHERE id=:id"""),
                    {"id": row["id"], "record": json.dumps(record)},
                )
                return None
            context = AuthContext.model_validate_json(json.dumps(row["owner_context"]))
            job = Job(row["id"], uuid4(), context)
            conn.execute(
                text("""UPDATE agent_runs SET job_lease_id=:lease,
                        job_lease_until=clock_timestamp() + :seconds * interval '1 second',
                        job_attempts=job_attempts+1 WHERE id=:id"""),
                job.params | {"seconds": lease_seconds},
            )
            return job

    def heartbeat(self, job, lease_seconds):
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    text(
                        """UPDATE agent_runs
                        SET job_lease_until=clock_timestamp() + :seconds * interval '1 second'
                        """
                        + ACTIVE_LEASE
                    ),
                    job.params | {"seconds": lease_seconds},
                ).rowcount
                == 1
            )

    def save(self, job, record, *, finished):
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    text(
                        """UPDATE agent_runs
                        SET record=CAST(:record AS jsonb), job_pending=NOT :finished,
                            job_lease_id=CASE WHEN :finished THEN NULL ELSE job_lease_id END,
                            job_lease_until=CASE WHEN :finished THEN NULL ELSE job_lease_until END
                        """
                        + ACTIVE_LEASE
                    ),
                    job.params | {"record": json.dumps(record), "finished": finished},
                ).rowcount
                == 1
            )

    def retry(self, job, *, busy=False):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """UPDATE agent_runs
                        SET job_lease_id=NULL,
                            job_lease_until=clock_timestamp() + interval '1 second',
                            job_attempts=job_attempts - :unstarted
                        """
                    + ACTIVE_LEASE
                ),
                job.params | {"unstarted": int(busy)},
            )


class ClaimedRunRepository(RunRepository):
    def __init__(self, queue, job):
        super().__init__(queue.engine)
        self.queue, self.job = queue, job

    def save(self, record, context):
        if not self.queue.save(self.job, record, finished=record["status"] != "running"):
            raise LeaseLost
