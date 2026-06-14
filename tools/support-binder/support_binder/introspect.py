"""Heuristics over a SchemaSnapshot: secret-column detection, locating the users table,
and ranking owner-column candidates (the FK that ties a row to a user).

These only ever *propose*; the wizard makes the operator confirm. Nothing here guesses
silently when a choice is ambiguous — it returns candidates and lets the caller decide.
"""
from __future__ import annotations

import re

from .engines.base import Relation, SchemaSnapshot

# Columns whose names suggest a credential/secret — never auto-granted.
_SECRET_RE = re.compile(
    r"(password|passwd|secret|token|hash|api[_-]?key|private[_-]?key|credential|salt|otp|mfa)",
    re.IGNORECASE,
)

# Table names that commonly hold user/account identities.
_USERS_TABLE_NAMES = (
    "users", "user", "accounts", "account", "auth_users", "profiles", "profile",
    "members", "member", "customers", "customer",
)

# Column names that commonly tie a row to its owning user, in rough preference order.
_OWNER_COLUMN_NAMES = (
    "user_id", "owner_id", "account_id", "uid", "customer_id", "profile_id",
    "member_id", "created_by", "author_id",
)


def looks_secret(column_name: str) -> bool:
    return bool(_SECRET_RE.search(column_name))


def guess_users_table(snapshot: SchemaSnapshot) -> str | None:
    """Best guess at the table holding user identities, or None if unclear."""
    names = {r.name.lower(): r.name for r in snapshot.tables()}
    for candidate in _USERS_TABLE_NAMES:
        if candidate in names:
            return names[candidate]
    # Fall back to anything containing "user".
    for lower, original in names.items():
        if "user" in lower:
            return original
    return None


def owner_candidates(relation: Relation, users_table: str | None) -> list[str]:
    """Ranked owner-column candidates for a relation.

    For the users table itself, the owner is its own identity - the primary key
    (``id``); a foreign key *from* users *to* users (managerId, createdBy, ...) is a
    self-reference to another user, never the row owner, so such self-FKs are excluded.
    For every other table, foreign keys pointing at the users table rank first (strongest
    signal), then columns whose names match the common owner-column conventions.
    """
    candidates: list[str] = []
    existing = {c.name.lower(): c.name for c in relation.columns}
    is_users_table = bool(users_table) and relation.name.lower() == users_table.lower()

    if is_users_table:
        # Scope by the row's own identity: the primary key, or a column named 'id' if the
        # engine didn't introspect PKs. Do NOT fall through to self-referential FKs.
        for pk in relation.primary_key:
            if pk not in candidates:
                candidates.append(pk)
        if not candidates and "id" in existing:
            candidates.append(existing["id"])
        return candidates  # users table is scoped only by its own identity

    if users_table:
        ut = users_table.lower()
        for fk in relation.foreign_keys:
            if fk.ref_table.lower() == ut and fk.column not in candidates:
                candidates.append(fk.column)

    for name in _OWNER_COLUMN_NAMES:
        if name in existing and existing[name] not in candidates:
            candidates.append(existing[name])

    return candidates


def detect_owner_column(relation: Relation, users_table: str | None) -> str | None:
    """The single most likely owner column, or None if there is no clear candidate."""
    candidates = owner_candidates(relation, users_table)
    return candidates[0] if candidates else None


def identity_cast_type(users_relation: Relation | None) -> str:
    """SQL type the session-identity function should return / cast to - matched to the
    users table's identity column (its PK, or a column named 'id'). Defaults to 'uuid'
    (the common case) when the type can't be determined."""
    if users_relation is None:
        return "uuid"
    cols = {c.name: c for c in users_relation.columns}
    id_col = None
    if users_relation.primary_key and users_relation.primary_key[0] in cols:
        id_col = cols[users_relation.primary_key[0]]
    else:
        for c in users_relation.columns:
            if c.name.lower() == "id":
                id_col = c
                break
    if id_col is None:
        return "uuid"
    t = (id_col.type or "").lower()
    if "uuid" in t:
        return "uuid"
    if t in ("bigint", "int8", "bigserial"):
        return "bigint"
    if t in ("integer", "int", "int4", "serial", "smallint", "int2"):
        return "integer"
    if "char" in t or "text" in t:
        return "text"
    return "uuid"
