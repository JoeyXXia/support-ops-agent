"""Small owner-scoped context store for the single-process demo."""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from packages.tools.schemas import AuthContext

Version = Literal["1.0", "2.0"]
RECENT_TURNS = 4
SUMMARY_BUDGET = 96


@dataclass
class Conversation:
    owner: AuthContext
    expires_at: float
    version: Version | None = None
    source: str | None = None
    revision: int = 0
    turn_count: int = 0
    turns: deque = field(default_factory=lambda: deque(maxlen=RECENT_TURNS))

    def set_version(self, version, *, source="explicit_user_update"):
        self.version = version
        self.source = source if version is not None else None
        self.revision += 1

    def append(self, question, run_id):
        self.turn_count += 1
        self.turns.append({"turn": self.turn_count, "question": question, "run_id": run_id})

    def snapshot(self):
        summary = "" if self.version is None else f"product_version={self.version}"
        size = len(summary.encode("utf-8"))
        if size > SUMMARY_BUDGET:
            raise ValueError("conversation_summary_budget")
        return {
            "recent_turns": list(self.turns),
            "turn_count": self.turn_count,
            "facts": {}
            if self.version is None
            else {
                "product_version": {
                    "value": self.version,
                    "source": self.source,
                    "revision": self.revision,
                },
            },
            "revision": self.revision,
            "summary": summary,
            "summary_utf8_bytes": size,
            "summary_budget_bytes": SUMMARY_BUDGET,
        }


class ConversationStore:
    def __init__(self, *, clock=time.monotonic, ttl=3600, capacity=128):
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.entries = {}

    def create(self, owner):
        now = self.clock()
        self.entries = {key: value for key, value in self.entries.items() if value.expires_at > now}
        if len(self.entries) >= self.capacity:
            raise OverflowError("conversation_capacity")
        identifier = uuid4()
        record = Conversation(owner=owner, expires_at=now + self.ttl)
        self.entries[identifier] = record
        return identifier, record

    def get(self, identifier, owner):
        record = self.entries.get(identifier)
        if record is None or record.owner != owner:
            raise LookupError("conversation_not_found")
        if record.expires_at <= self.clock():
            del self.entries[identifier]
            raise LookupError("conversation_not_found")
        return record

    def clear_preference(self, owner):
        for record in self.entries.values():
            if record.owner == owner and record.source == "confirmed_preference":
                record.set_version(None)
