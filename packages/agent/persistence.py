"""Trusted local run registry and read-only checkpoint recovery."""

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import text

from packages.tools.routing import build_executor

from .costs import record_progress
from .graph import GraphAgent
from .knowledge_mock import KnowledgeScenarioMock
from .ticket_mock import TicketDraftMock
from .trace import run_scope, timed

TERMINAL = {"completed", "stopped", "failed", "cancelled"}


class RunBusy(Exception):
    pass


class RunRepository:
    def __init__(self, engine):
        self.engine = engine

    def create(
        self, record, context, *, knowledge, product_version, enqueue=False, connection=None
    ):
        transaction = self.engine.begin() if connection is None else nullcontext(connection)
        with transaction as connection:
            connection.execute(
                text("""INSERT INTO agent_runs
                    (id, tenant_id, user_id, owner_context, input, record, job_pending)
                    VALUES (:id, :tenant, :user, CAST(:owner AS jsonb),
                            CAST(:input AS jsonb), CAST(:record AS jsonb), :enqueue)"""),
                {
                    "id": record["id"],
                    "tenant": context.tenant_id,
                    "user": context.user_id,
                    "owner": context.model_dump_json(),
                    "enqueue": enqueue,
                    "input": json.dumps(
                        {"knowledge": knowledge, "product_version": product_version}
                    ),
                    "record": json.dumps(record),
                },
            )

    def load(self, run_id, context):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text("""SELECT owner_context, input, record FROM agent_runs
                    WHERE id=:id AND tenant_id=:tenant AND user_id=:user"""),
                    {"id": str(run_id), "tenant": context.tenant_id, "user": context.user_id},
                )
                .mappings()
                .first()
            )
        if row is None or row["owner_context"] != context.model_dump(mode="json"):
            raise LookupError("run_not_found")
        return row

    def history(self, context, limit=20):
        if not 1 <= limit <= 50:
            raise ValueError("invalid_history_limit")
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT id::text AS id, record->>'created_at' AS created_at,
                       record->>'question' AS question, record->>'status' AS status,
                       record->>'reason' AS reason
                FROM agent_runs
                WHERE tenant_id=:tenant AND user_id=:user AND owner_context=CAST(:owner AS jsonb)
                ORDER BY record->>'created_at' DESC NULLS LAST, id DESC
                LIMIT :limit
            """),
                    {
                        "tenant": context.tenant_id,
                        "user": context.user_id,
                        "owner": context.model_dump_json(),
                        "limit": limit,
                    },
                ).mappings()
            ]

    def save(self, record, context):
        with self.engine.begin() as connection:
            changed = connection.execute(
                text("""UPDATE agent_runs SET record=CAST(:record AS jsonb)
                    WHERE id=:id AND tenant_id=:tenant AND user_id=:user
                    AND owner_context=CAST(:owner AS jsonb)"""),
                {
                    "id": record["id"],
                    "record": json.dumps(record),
                    "tenant": context.tenant_id,
                    "user": context.user_id,
                    "owner": context.model_dump_json(),
                },
            ).rowcount
        if changed != 1:
            raise LookupError("run_not_found")


class ThreadedPostgresSaver(PostgresSaver):
    """Bridge the official sync saver to async graphs on Windows and Unix."""

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)


@asynccontextmanager
async def checkpoint_saver(engine):
    options = engine.dialect.create_connect_args(engine.url)[1]
    with psycopg.connect(
        **options, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as connection:
        saver = ThreadedPostgresSaver(connection)
        await asyncio.to_thread(saver.setup)
        yield saver


async def execute_run(
    repository,
    saver,
    run_id,
    context,
    provider_factory,
    *,
    on_event=None,
    persist_progress=False,
    operation_guard=None,
):
    owned = repository.load(run_id, context)
    # Transaction-scoped lock is released by PostgreSQL when the process dies.
    with run_scope(owned["record"]), repository.engine.begin() as lock:
        acquired = lock.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:id, 0))"),
            {"id": str(run_id)},
        )
        if not acquired:
            raise RunBusy("run_busy")
        owned = repository.load(run_id, context)
        record = owned["record"]
        if record["status"] in TERMINAL:
            return record
        settings = owned["input"]
        provider = (
            KnowledgeScenarioMock(record["question"], settings["product_version"])
            if settings["knowledge"]
            else provider_factory()
        )
        if record.get("ticket_draft", False):
            provider = TicketDraftMock(record["question"])
        agent = GraphAgent(
            provider,
            build_executor(
                engine=repository.engine,
                run_id=str(run_id),
                transport=record.get("read_tool_transport", "direct"),
            ),
            checkpointer=saver,
            operation_guard=operation_guard,
        )
        config = {"configurable": {"thread_id": str(run_id)}}
        with timed("database.checkpoint_read"):
            snapshot = await agent.graph.aget_state(config)
        approval_ready = False
        if snapshot.values and any(task.interrupts for task in snapshot.tasks):
            from packages.storage.tickets import TicketRepository

            proposal = snapshot.values["turn"]["result"].get("proposed_action")
            if proposal:
                status = (
                    TicketRepository(repository.engine)
                    .get_approval(proposal["approval_id"], context)
                    .status
                )
                approval_ready = status in {"approved", "rejected", "executed"}
        record.update(status="running", reason="")
        if persist_progress:
            # Discard speculative events emitted after the last durable graph checkpoint.
            turn = snapshot.values.get("turn", {})
            record["events"] = list(turn.get("result", {}).get("events", []))
        repository.save(record, context)

        def publish(event):
            record_progress(record, event)
            repository.save(record, context)

        result = await agent.run(
            record["question"],
            context=context,
            grounded=settings["knowledge"],
            on_event=publish if persist_progress else on_event,
            thread_id=str(run_id),
            resume=bool(snapshot.values),
            approval_ready=approval_ready,
        )
        record.update(result.model_dump())
        repository.save(record, context)
        return record
