"""Shared test doubles: a fixture schema snapshot and a scripted prompter."""
from __future__ import annotations

from typing import Sequence

from support_binder.engines.base import (
    Column,
    ForeignKey,
    Relation,
    Routine,
    SchemaSnapshot,
)


def make_snapshot() -> SchemaSnapshot:
    """A small schema exercising every interesting path: an owned table (FK to users),
    a public reference table, a secret column, a view, and a routine."""
    users = Relation("users", "table", [
        Column("id", "uuid", False),
        Column("email", "text", False),
        Column("password_hash", "text", False, looks_secret=True),
        Column("created_at", "timestamptz", True),
    ])
    bookings = Relation("bookings", "table", [
        Column("id", "uuid", False),
        Column("user_id", "uuid", False),
        Column("cafe_id", "uuid", True),
        Column("status", "text", True),
        Column("amount", "integer", True),
    ], [
        ForeignKey("user_id", "users", "id"),
        ForeignKey("cafe_id", "cafes", "id"),
    ])
    cafes = Relation("cafes", "table", [
        Column("id", "uuid", False),
        Column("name", "text", False),
        Column("city", "text", True),
    ])
    active_bookings = Relation("active_bookings", "view", [
        Column("id", "uuid", True),
        Column("status", "text", True),
    ])
    return SchemaSnapshot(
        relations=[users, bookings, cafes, active_bookings],
        routines=[Routine("cleanup_old_sessions", "procedure")],
    )


# Sentinel: tells FakePrompter.select_many to return the wizard's computed default.
USE_DEFAULT = "__default__"


class FakePrompter:
    """Scripted prompter. Each method type consumes from its own queue in order, so
    tests don't have to reason about cross-type call interleaving."""

    def __init__(self, *, asks: Sequence[str] = (), confirms: Sequence[bool] = (),
                 choices: Sequence[str] = (), selects: Sequence = ()):
        self.asks = list(asks)
        self.confirms = list(confirms)
        self.choices = list(choices)
        self.selects = list(selects)
        self.log: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.log.append(("info", message))

    def warn(self, message: str) -> None:
        self.log.append(("warn", message))

    def ask(self, label: str, default: str | None = None) -> str:
        return self.asks.pop(0) if self.asks else (default or "")

    def confirm(self, label: str, default: bool = True) -> bool:
        return self.confirms.pop(0) if self.confirms else default

    def choice(self, label, options, default=None) -> str:
        return self.choices.pop(0) if self.choices else default

    def select_many(self, label, options, default=None) -> list:
        if not self.selects:
            return list(default or [])
        item = self.selects.pop(0)
        if item == USE_DEFAULT:
            return list(default or [])
        return list(item)
