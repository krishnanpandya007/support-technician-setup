"""In-memory engine for tests — no database. Analogous to the legacy FakeLLMClient:
it returns a fixture snapshot and records the SQL it was asked to emit.
"""
from __future__ import annotations

from .base import SchemaSnapshot, ScopingPlan


class MockEngine:
    name = "mock"

    def __init__(
        self,
        snapshot: SchemaSnapshot,
        *,
        visible: list[str] | None = None,
        writes: dict[str, bool] | None = None,
        rows: dict[str, int] | None = None,
    ):
        self._snapshot = snapshot
        self._visible = visible if visible is not None else []
        self._writes = writes or {}
        self._rows = rows or {}
        self.connected_url: str | None = None
        self.emitted_sql: str | None = None

    def connect(self, url: str) -> None:
        self.connected_url = url

    def introspect(self) -> SchemaSnapshot:
        return self._snapshot

    def emit_scoping_sql(self, plan: ScopingPlan) -> str:
        from ..sqlgen import build_sql
        self.emitted_sql = build_sql(plan)
        return self.emitted_sql

    def list_visible_relations(self) -> list[str]:
        return list(self._visible)

    def write_blocked(self, relation: str) -> bool:
        return self._writes.get(relation, True)

    def rows_without_identity(self, relation: str) -> int:
        return self._rows.get(relation, 0)
