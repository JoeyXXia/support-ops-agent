"""Tenant-scoped tickets and immutable action proposals."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from packages.agent.persistence import RunRepository
from packages.tools.schemas import ProposedAction, TicketDraftArgs, TicketResult

from .schema import approvals, tickets


class TicketRepository:
    def __init__(self, engine):
        self.engine = engine

    def get_ticket(self, ticket_id, allowed_tenants):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(tickets).where(
                        tickets.c.id == UUID(str(ticket_id)),
                        tickets.c.tenant_id.in_(allowed_tenants),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LookupError("ticket_not_found")
        return self._ticket(row)

    @staticmethod
    def _ticket(row):
        return TicketResult(
            ticket_id=str(row["id"]),
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
        )

    @staticmethod
    def _proposal(row):
        return ProposedAction(
            approval_id=str(row["id"]),
            run_id=str(row["run_id"]),
            tenant_id=row["tenant_id"],
            action=row["action"],
            params=TicketDraftArgs.model_validate(row["params"]),
            status=row["status"],
        )

    def propose(self, run_id, args, context, *, target_tenant):
        RunRepository(self.engine).load(run_id, context)
        values = dict(
            id=uuid4(),
            run_id=UUID(str(run_id)),
            tenant_id=target_tenant,
            requested_by=context.user_id,
            action="create_ticket",
            params=args.model_dump(mode="json"),
            status="pending",
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(approvals)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[approvals.c.run_id, approvals.c.action])
            )
            row = (
                connection.execute(
                    sa.select(approvals).where(
                        approvals.c.run_id == values["run_id"],
                        approvals.c.action == "create_ticket",
                    )
                )
                .mappings()
                .one()
            )
            if any(row[key] != values[key] for key in ("tenant_id", "requested_by", "params")):
                raise ValueError("proposal_changed")
        return self._proposal(row)

    def create_ticket(self, approval_id, context):
        """Server execution boundary. The model never receives this capability."""
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(approvals)
                    .where(approvals.c.id == UUID(str(approval_id)))
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise LookupError("approval_not_found")
            RunRepository(self.engine).load(row["run_id"], context)
            if row["requested_by"] != context.user_id:
                raise LookupError("approval_not_found")
            if row["status"] == "executed":
                return self.get_ticket(row["ticket_id"], {row["tenant_id"]})
            if row["status"] != "approved":
                raise PermissionError("approval_required")
            args = TicketDraftArgs.model_validate(row["params"])
            created = (
                connection.execute(
                    tickets.insert()
                    .values(
                        id=uuid4(),
                        tenant_id=row["tenant_id"],
                        workspace_id=args.workspace_id,
                        title=args.title,
                        description=args.description,
                        status="open",
                        created_by=context.user_id,
                    )
                    .returning(tickets)
                )
                .mappings()
                .one()
            )
            connection.execute(
                approvals.update()
                .where(approvals.c.id == row["id"])
                .values(status="executed", ticket_id=created["id"])
            )
            return self._ticket(created)
