"""Emit the *allowed read schema* as a machine-readable manifest.

This is the runtime-facing companion to the migration SQL. The migration tells the
database what the read-only role may SELECT; this manifest tells the tool server the same
facts in a form it can load to build correct, scoped queries (real table/column names,
the owner column each table is row-scoped by, and the session-identity expression).

LOCAL artifact: it contains REAL identifiers. In `blind` mode it must stay out of git and
out of any prompt sent to a hosted model - the model keeps calling named tools and never
sees real schema; only the local tool server reads this file.
"""
from __future__ import annotations

from .engines.base import ScopingPlan


def _yaml_str(value: str) -> str:
    """Double-quote a scalar and escape embedded quotes/backslashes - safe for identifiers
    and expressions like app.current_user_id()."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_allowed_schema(plan: ScopingPlan) -> str:
    """Render the allowed-schema manifest (YAML, hand-written - no yaml dependency)."""
    lines = [
        "# The real tables and columns the support tool is allowed to read.",
        "# Keep this file on your machine (it has real names). Use it to fill in the names",
        "# your tools query. The database still limits each read to the current user.",
        "",
        f"role: {_yaml_str(plan.role)}",
        f"schema: {_yaml_str(plan.schema)}",
        f"identity_expr: {_yaml_str(plan.identity_expr)}",
        "tables:",
    ]
    for t in plan.tables:
        owner = _yaml_str(t.owner_column) if t.owner_column else "null"
        cols = ", ".join(_yaml_str(c) for c in t.granted_columns)
        lines += [
            f"  - name: {_yaml_str(t.table)}",
            f"    kind: {t.kind}",
            f"    owner_column: {owner}   # RLS scopes rows by this column; null = public/reference",
            f"    columns: [{cols}]",
        ]
    return "\n".join(lines) + "\n"
