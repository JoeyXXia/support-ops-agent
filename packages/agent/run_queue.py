"""Short PostgreSQL transactions for the durable worker queue."""

import json
import os
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text

from packages.tools.schemas import AuthContext

from .persistence import TERMINAL, RunRepository
from .trace import run_scope, timed

MAX_ATTEMPTS = 3
STOP_REASON = """CASE WHEN cancel_requested THEN 'cancelled_by_owner'
                     WHEN job_deadline <= clock_timestamp() THEN 'run_timeout' END"""
ACTIVE_LEASE = """
    WHERE id=:id AND job_pending AND job_lease_id=:lease
      AND job_lease_until > clock_timestamp()
"""


class RunStop(Exception):
    pass


class QueueFull(OverflowError):
    def __init__(self, *, owner_limit):
        self.owner_limit = owner_limit


def setting(name, default, maximum):
    value = int(os.getenv(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


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
        self.queue_limit = setting("SUPPORTOPS_QUEUE_LIMIT", 64, 10000)
        self.user_limit = setting("SUPPORTOPS_USER_RUN_LIMIT", 4, 1000)
        self.timeout = setting("SUPPORTOPS_RUN_TIMEOUT_SECONDS", 120, 3600)

    def _lock_admission(self, conn):
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('supportops:admission', 0))")
        )

    def _check_capacity(self, conn, context):
        counts = conn.execute(
            text("""SELECT count(*) AS total,
                       count(*) FILTER (WHERE tenant_id=:tenant AND user_id=:user) AS own
                    FROM agent_runs WHERE job_pending"""),
            {"tenant": context.tenant_id, "user": context.user_id},
        ).one()
        if counts.own >= self.user_limit:
            raise QueueFull(owner_limit=True)
        if counts.total >= self.queue_limit:
            raise QueueFull(owner_limit=False)

    def create(self, repository, record, context, **settings):
        with run_scope(record), timed("database.run_enqueue"), self.engine.begin() as conn:
            self._lock_admission(conn)
            self._check_capacity(conn, context)
            repository.create(record, context, **settings, enqueue=True, connection=conn)

    def _settle(self, conn, run_id, record, reason):
        if reason in {"cancelled_by_owner", "run_timeout"}:
            ticket = conn.execute(
                text("""SELECT e.result, a.status FROM action_executions e
                        JOIN approvals a ON a.id=e.approval_id WHERE a.run_id=:id"""),
                {"id": run_id},
            ).one_or_none()
            record.update(
                status="cancelled" if reason == "cancelled_by_owner" else "failed",
                reason=reason,
                answer="已提交的工单已保留。" if ticket else "",
            )
            if ticket:
                record["ticket"] = ticket.result
                if record.get("proposed_action"):
                    record["proposed_action"]["status"] = ticket.status
        else:
            record.update(status="failed", reason=reason)
        conn.execute(
            text("""UPDATE agent_runs SET record=CAST(:record AS jsonb), job_pending=false,
                       job_lease_id=NULL, job_lease_until=NULL WHERE id=:id"""),
            {"id": run_id, "record": json.dumps(record)},
        )
        return record

    def cancel(self, run_id, context):
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    text("""SELECT record, job_pending AND job_lease_id IS NOT NULL
                           AND job_lease_until > clock_timestamp() AS active
                        FROM agent_runs WHERE id=:id AND owner_context=CAST(:owner AS jsonb)
                        FOR UPDATE"""),
                    {"id": str(run_id), "owner": context.model_dump_json()},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("run_not_found")
            record = row["record"]
            if record["status"] in TERMINAL:
                return record
            conn.execute(
                text("UPDATE agent_runs SET cancel_requested=true WHERE id=:id"),
                {"id": str(run_id)},
            )
            if not row["active"]:
                return self._settle(conn, str(run_id), record, "cancelled_by_owner")
            return record | {"cancel_requested": True}

    def check(self, job):
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + STOP_REASON + " AS reason FROM agent_runs " + ACTIVE_LEASE),
                job.params,
            ).one_or_none()
        if row is None:
            raise LeaseLost
        if row.reason:
            raise RunStop

    def stop(self, job):
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT record, "
                    + STOP_REASON
                    + " AS reason FROM agent_runs "
                    + ACTIVE_LEASE
                    + " FOR UPDATE"
                ),
                job.params,
            ).one_or_none()
            if row is not None and row.reason:
                self._settle(conn, job.run_id, row.record, row.reason)

    def enqueue(self, run_id, context, *, allow_paused=False, connection=None):
        transaction = self.engine.begin() if connection is None else nullcontext(connection)
        with transaction as conn:
            self._lock_admission(conn)
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
                if connection is not None and (
                    record["status"] == "cancelled" or record.get("reason") == "run_timeout"
                ):
                    raise OverflowError("run_stopped")
                return record
            if owned["job_pending"]:
                if connection is not None:
                    return record
                raise OverflowError("busy")
            if record["status"] == "awaiting_approval" and not allow_paused:
                return record
            self._check_capacity(conn, context)
            record.update(status="queued", reason="")
            conn.execute(
                text("""UPDATE agent_runs
                        SET record=CAST(:record AS jsonb), job_pending=true, job_attempts=0,
                            job_lease_id=NULL, job_lease_until=NULL, job_deadline=NULL
                        WHERE id=:id"""),
                {"id": str(run_id), "record": json.dumps(record)},
            )
            return record

    def claim(self, lease_seconds):
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    text(
                        """SELECT id, owner_context, record, job_attempts, """
                        + STOP_REASON
                        + """ AS reason FROM agent_runs
                        WHERE job_pending AND
                            (job_lease_until IS NULL OR job_lease_until <= clock_timestamp())
                        ORDER BY job_lease_until NULLS LAST, record->>'created_at', id
                        FOR UPDATE SKIP LOCKED LIMIT 1"""
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            reason = row["reason"]
            if not reason and row["job_attempts"] >= MAX_ATTEMPTS:
                reason = "worker_retry_exhausted"
            if reason:
                self._settle(conn, row["id"], row["record"], reason)
                return None
            context = AuthContext.model_validate_json(json.dumps(row["owner_context"]))
            job = Job(row["id"], uuid4(), context)
            conn.execute(
                text("""UPDATE agent_runs SET job_lease_id=:lease,
                        job_lease_until=clock_timestamp() + :seconds * interval '1 second',
                        job_deadline=COALESCE(job_deadline,
                            clock_timestamp() + :timeout * interval '1 second'),
                        job_attempts=job_attempts+1 WHERE id=:id"""),
                job.params | {"seconds": lease_seconds, "timeout": self.timeout},
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
            if finished:
                row = conn.execute(
                    text(
                        "SELECT "
                        + STOP_REASON
                        + " AS reason FROM agent_runs "
                        + ACTIVE_LEASE
                        + " FOR UPDATE"
                    ),
                    job.params,
                ).one_or_none()
                if row is None:
                    return False
                if row.reason:
                    self._settle(conn, job.run_id, record, row.reason)
                    return True
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
        with timed("database.run_save"):
            if not self.queue.save(self.job, record, finished=record["status"] != "running"):
                raise LeaseLost
