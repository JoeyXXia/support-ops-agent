"""Owner-scoped API access to the durable worker queue."""

from packages.agent.persistence import RunRepository
from packages.agent.run_queue import RunQueue

from .runs import RunStore


class PersistentRunStore(RunStore):
    supports_drafts = True

    def __init__(self, engine, **kwargs):
        super().__init__(**kwargs)
        self.repository = RunRepository(engine)
        self.queue = RunQueue(engine)

    def create(self, question, *, knowledge=False, product_version=None, ticket_draft=False):
        record = self._new_record(question, ticket_draft=ticket_draft)
        self.repository.create(
            record, self.context, knowledge=knowledge, product_version=product_version, enqueue=True
        )
        return record

    def get(self, run_id):
        try:
            return self.repository.load(run_id, self.context)["record"]
        except LookupError:
            return None

    def history(self, limit=20):
        return self.repository.history(self.context, limit)

    def resume(self, run_id):
        from packages.storage.tickets import TicketRepository

        record = self.repository.load(run_id, self.context)["record"]
        allow_paused = False
        if record["status"] == "awaiting_approval":
            proposal = TicketRepository(self.repository.engine).get_approval(
                record["proposed_action"]["approval_id"], self.context
            )
            allow_paused = proposal.status != "pending"
        return self.queue.enqueue(run_id, self.context, allow_paused=allow_paused)

    def resume_decision(self, run_id, connection):
        return self.queue.enqueue(run_id, self.context, allow_paused=True, connection=connection)
