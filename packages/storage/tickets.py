"""Tenant-scoped tickets and immutable action proposals."""

import hashlib
import hmac
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from packages.agent.persistence import RunRepository
from packages.tools.schemas import ProposedAction, TicketDraftArgs, TicketResult

from .schema import approvals, tickets


class ApprovalConflict(ValueError):
    pass


def payload_digest(*, run_id, tenant_id, requested_by, action, params):
    content = {
        "run_id": str(run_id),
        "tenant_id": tenant_id,
        "requested_by": requested_by,
        "action": action,
        "params": params,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


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
            payload_digest=row["payload_digest"],
            expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
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
        values["payload_digest"] = payload_digest(
            **{
                key: values[key]
                for key in ("run_id", "tenant_id", "requested_by", "action", "params")
            }
        )
        values["expires_at"] = sa.func.clock_timestamp() + sa.text("interval '30 minutes'")
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

    def get_approval(self, approval_id, context):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(approvals).where(approvals.c.id == UUID(str(approval_id)))
                )
                .mappings()
                .first()
            )
        self._owner(row, context)
        return self._proposal(row)

    def _owner(self, row, context):
        if row is None or row["requested_by"] != context.user_id:
            raise LookupError("approval_not_found")
        RunRepository(self.engine).load(row["run_id"], context)

    @staticmethod
    def _valid(row, now):
        digest = payload_digest(
            **{key: row[key] for key in ("run_id", "tenant_id", "requested_by", "action", "params")}
        )
        if not row["payload_digest"] or not hmac.compare_digest(digest, row["payload_digest"]):
            raise ApprovalConflict("approval_payload_changed")
        if row["expires_at"] is None or row["expires_at"] <= now:
            raise ApprovalConflict("approval_expired")

    def decide(self, approval_id, context, *, run_id, digest, approve):
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
            self._owner(row, context)
            if str(row["run_id"]) != str(run_id):
                raise LookupError("approval_not_found")
            self._valid(row, connection.scalar(sa.select(sa.func.clock_timestamp())))
            if not hmac.compare_digest(row["payload_digest"], digest):
                raise ApprovalConflict("approval_payload_changed")
            status = "approved" if approve else "rejected"
            if row["status"] == "pending":
                row = (
                    connection.execute(
                        approvals.update()
                        .where(approvals.c.id == row["id"])
                        .values(
                            status=status,
                            decided_by=context.user_id,
                            decided_at=sa.func.clock_timestamp(),
                        )
                        .returning(approvals)
                    )
                    .mappings()
                    .one()
                )
            elif row["status"] != status and not (approve and row["status"] == "executed"):
                raise ApprovalConflict("approval_already_decided")
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
            self._valid(row, connection.scalar(sa.select(sa.func.clock_timestamp())))
            if row["decided_by"] != context.user_id or row["decided_at"] is None:
                raise ApprovalConflict("approval_actor_missing")
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
