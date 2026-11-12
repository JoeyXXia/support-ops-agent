"""Opt-in durable mode for the local demo's trusted server identity."""

import asyncio

from packages.agent.persistence import RunBusy, RunRepository, execute_run

from .runs import RunStore


class PersistentRunStore(RunStore):
    supports_drafts = True

    def __init__(self, engine, saver, **kwargs):
        super().__init__(**kwargs)
        self.repository = RunRepository(engine)
        self.saver = saver
        self.active_ids = set()

    def _save_new(self, record, *, knowledge, product_version):
        self.repository.create(
            record, self.context, knowledge=knowledge, product_version=product_version
        )
        self.active_ids.add(record["id"])

    async def _execute(self, record, *, knowledge, product_version):
        run_id = record["id"]
        self.active_ids.add(run_id)
        record.update(status="running", reason="")
        try:
            result = await execute_run(
                self.repository,
                self.saver,
                run_id,
                self.context,
                self.provider_factory,
                on_event=lambda event: record["events"].append(event),
            )
            record.update(result)
        except RunBusy:
            pass
        except asyncio.CancelledError:
            # The durable snapshot remains resumable. Do not replace it with a terminal failure.
            record.update(status="interrupted", reason="recovery_required")
            raise
        except Exception:
            record.update(status="interrupted", reason="recovery_required")
        finally:
            self.active_ids.discard(run_id)
            self.records.pop(run_id, None)

    def get(self, run_id):
        try:
            saved = self.repository.load(run_id, self.context)["record"]
            if str(run_id) in self.active_ids:
                return self.records.get(str(run_id), saved)
            return saved
        except LookupError:
            return None

    def resume(self, run_id):
        owned = self.repository.load(run_id, self.context)
        record = owned["record"]
        if record["status"] in {"completed", "stopped", "failed"}:
            return record
        if str(run_id) in self.active_ids or len(self.tasks) >= self.max_active:
            raise OverflowError("busy")
        # Reserve synchronously so two requests in one loop cannot schedule the same run.
        self.active_ids.add(str(run_id))
        self.records[str(run_id)] = record
        task = asyncio.create_task(self._execute(record, **owned["input"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return record.copy()
