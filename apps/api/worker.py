"""Durable worker with bounded concurrency. Start with python -m apps.api.worker."""

import asyncio
import logging
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError

from packages.agent.persistence import RunBusy, checkpoint_saver, execute_run
from packages.agent.run_queue import ClaimedRunRepository, LeaseLost, RunQueue, RunStop, setting
from packages.storage.database import engine_from_environment

from .runs import DemoProvider

LOGGER = logging.getLogger(__name__)
LEASE_SECONDS = 15
POLL_SECONDS = 0.15


async def heartbeat(queue, job, lease_seconds):
    while True:
        await asyncio.sleep(lease_seconds / 3)
        if not queue.heartbeat(job, lease_seconds):
            raise LeaseLost


async def process(queue, saver, job, lease_seconds, provider_factory):
    repo = ClaimedRunRepository(queue, job)
    execution = asyncio.create_task(
        execute_run(
            repo,
            saver,
            job.run_id,
            job.context,
            provider_factory,
            persist_progress=True,
            operation_guard=lambda: queue.check(job),
        )
    )
    renew = asyncio.create_task(heartbeat(queue, job, lease_seconds))
    try:
        await asyncio.wait((execution, renew), return_when=asyncio.FIRST_COMPLETED)
        if execution.done():
            record = execution.result()
            # Handles an already settled record left by a trusted operator.
            # A normal graph has already atomically published and acknowledged it.
            queue.save(job, record, finished=True)
        else:
            renew.result()
    except RunStop:
        queue.stop(job)
    except LeaseLost:
        LOGGER.warning("Job lease lost: %s", job.run_id)
    except RunBusy:
        queue.retry(job, busy=True)
    except Exception as error:
        LOGGER.warning("Job interrupted: %s (%s)", job.run_id, type(error).__name__)
        queue.retry(job)
    finally:
        for task in (renew, execution):
            task.cancel()
        await asyncio.gather(renew, execution, return_exceptions=True)


async def run_worker(queue, saver, *, lease_seconds=LEASE_SECONDS, provider_factory=DemoProvider):
    concurrency = setting("SUPPORTOPS_WORKER_CONCURRENCY", 2, 4)
    async with asyncio.TaskGroup() as group:
        for _ in range(concurrency):
            group.create_task(consume(queue, saver, lease_seconds, provider_factory))


async def consume(queue, saver, lease_seconds, provider_factory):
    while True:
        try:
            job = queue.claim(lease_seconds)
            if job is not None:
                await process(queue, saver, job, lease_seconds, provider_factory)
                continue
        except SQLAlchemyError:
            LOGGER.warning("Worker database temporarily unavailable")
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def running_worker(engine):
    """Start a worker under the calling harness; the API process never calls this."""
    async with checkpoint_saver(engine) as saver:
        task = asyncio.create_task(run_worker(RunQueue(engine), saver))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def main():
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env.local", override=False)
    engine = engine_from_environment()
    try:
        async with checkpoint_saver(engine) as saver:
            await run_worker(RunQueue(engine), saver)
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
