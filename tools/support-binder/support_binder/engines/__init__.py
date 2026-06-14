"""Engine registry. Adapters are imported lazily so optional drivers (and, later,
other engines) are only required when actually selected."""
from __future__ import annotations

from .base import (
    Column,
    Engine,
    ForeignKey,
    Relation,
    Routine,
    SchemaSnapshot,
    ScopingPlan,
    TablePlan,
)

ENGINE_NAMES = ["postgres"]


def get_engine(name: str) -> Engine:
    if name == "postgres":
        from .postgres import PostgresEngine
        return PostgresEngine()
    raise ValueError(f"unsupported engine: {name!r} (supported: {', '.join(ENGINE_NAMES)})")


__all__ = [
    "Column", "Engine", "ForeignKey", "Relation", "Routine",
    "SchemaSnapshot", "ScopingPlan", "TablePlan", "ENGINE_NAMES", "get_engine",
]
