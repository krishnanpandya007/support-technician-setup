"""Engine abstraction: the data shapes and the Protocol every database adapter implements.

Only Postgres is built in v1, but the wizard, SQL generation, and verification are all
written against these types so another engine can be added without touching them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    looks_secret: bool = False


@dataclass(frozen=True)
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Relation:
    name: str
    kind: str  # "table" | "view"
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)  # PK column name(s), if known

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass(frozen=True)
class Routine:
    name: str
    kind: str  # "function" | "procedure"


@dataclass
class SchemaSnapshot:
    relations: list[Relation] = field(default_factory=list)
    routines: list[Routine] = field(default_factory=list)

    def tables(self) -> list[Relation]:
        return [r for r in self.relations if r.kind == "table"]

    def views(self) -> list[Relation]:
        return [r for r in self.relations if r.kind == "view"]

    def names(self) -> list[str]:
        return [r.name for r in self.relations]

    def get(self, name: str) -> Relation | None:
        for r in self.relations:
            if r.name == name:
                return r
        return None


@dataclass
class TablePlan:
    """One selected relation and how it will be scoped."""
    table: str
    kind: str                      # "table" | "view"
    granted_columns: list[str]
    owner_column: str | None       # None => public/reference table (column grant, no RLS)


@dataclass
class ScopingPlan:
    role: str
    identity_expr: str             # e.g. "auth.uid()" for Supabase
    tables: list[TablePlan]
    schema: str = "public"
    identity_setup_sql: str | None = None   # optional bootstrap (e.g. the app.current_user_id() fn)


@runtime_checkable
class Engine(Protocol):
    """A database adapter. Built against an admin connection for introspection and a
    separate read-only connection for verification."""

    name: str

    def connect(self, url: str) -> None: ...

    def introspect(self) -> SchemaSnapshot: ...

    def emit_scoping_sql(self, plan: ScopingPlan) -> str: ...

    # --- verification (run while connected as the new read-only user) ---
    def list_visible_relations(self) -> list[str]: ...

    def write_blocked(self, relation: str) -> bool: ...

    def rows_without_identity(self, relation: str) -> int: ...
