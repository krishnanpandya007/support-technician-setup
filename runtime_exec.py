"""Generic, kit-driven SQL execution for the support runtime.

Lets the runtime run *any* kit's tools against a real database, instead of hand-written
per-tool SQL. It takes a tool's query file, fills in the real table/column names from a
local names file, checks the query only touches tables the support tool is allowed to
read, runs it on the read-only connection (already limited to the current user), and turns
the result into the short answer the runbooks expect.

Two kinds of value go into a query, kept strictly apart:

  - **Names** (tables/columns) that differ per deployment are written in the query file as
    ``{{name}}`` and filled in from a local *names file* the operator prepared. These are
    the operator's own identifiers - never anything the model typed.
  - **Values** (the current user, tool arguments) are passed as bound parameters
    (``%(name)s``) and never pasted into the query text. ``%(current_user)s`` is always
    available.

A "checker" query selects a single value (the short answer). A "fetcher" query selects a
row or rows (turned into a small summary record).
"""
from __future__ import annotations

import re

_NAME_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
# tables referenced after FROM / JOIN, for the allowed-tables check (best-effort guard;
# the database grants + row rules are the real enforcement). The trailing lookahead skips
# function calls (``FROM now()``, ``FROM generate_series(...)``) — those aren't table reads.
_TABLE_RE = re.compile(r'\b(?:from|join)\s+"?([A-Za-z0-9_]+)\b"?(?!\s*\()', re.IGNORECASE)
# Comments and string literals are prose, not SQL surface: a '{{placeholders}}' mention in
# a header comment must not be treated as a binding, and a literal like 'days from approval'
# must not be treated as a table read. One alternation so a '--' inside a literal survives.
_COMMENT_OR_STRING_RE = re.compile(r"('(?:[^']|'')*')|(--[^\r\n]*|/\*.*?\*/)", re.DOTALL)
# ``EXTRACT(DAY FROM expr)`` — that FROM is part of the function, not a table read.
_EXTRACT_FROM_RE = re.compile(r"\bextract\s*\(\s*\w+\s+from\b", re.IGNORECASE)
# CTE names (``WITH x AS (...)``) — referenced like tables, but they aren't grants targets.
_CTE_AS_RE = re.compile(r"\b([A-Za-z0-9_]+)\s+as\s*\(", re.IGNORECASE)
# A substituted identifier must be a plain (optionally schema-qualified) SQL name. Names are
# pasted into the query text — unlike values, they can't be bound as parameters — so this
# rejects anything that isn't an identifier (spaces, quotes, ';', comments) before it lands
# in SQL. Defense-in-depth on top of the read-only role; the names file is operator-authored.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")


class BindingError(Exception):
    """A query referenced a {{name}} with no entry in the names file, or a table the
    support tool is not allowed to read."""


def fill_names(query_template: str, names: dict) -> str:
    """Replace every ``{{name}}`` with its real value from the names map. Raises if any
    placeholder is left unfilled (so a half-configured kit fails loudly, not silently)."""
    missing: list[str] = []
    unsafe: list[str] = []

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in names:
            missing.append(key)
            return m.group(0)
        real = str(names[key])
        if not _SAFE_NAME_RE.match(real):
            unsafe.append(f"{key}={real!r}")
            return m.group(0)
        return real

    filled = _NAME_RE.sub(repl, query_template)
    if missing:
        raise BindingError(f"unfilled name(s) in query: {', '.join(sorted(set(missing)))}")
    if unsafe:
        raise BindingError("names file maps to non-identifier value(s) (must be a plain, "
                           f"optionally schema-qualified SQL name): {', '.join(sorted(set(unsafe)))}")
    return filled


def strip_comments(sql: str) -> str:
    """Remove SQL comments, leaving string literals intact. Run before filling names so
    documentation in a query file (which may mention ``{{placeholders}}`` in prose) can't
    break execution."""
    return _COMMENT_OR_STRING_RE.sub(lambda m: m.group(1) or " ", sql)


def _scan_text(sql: str) -> str:
    """The query as the table scanner should see it: comments and string literals blanked,
    and EXTRACT's keyword FROM removed — none of those are table reads."""
    s = _COMMENT_OR_STRING_RE.sub(lambda m: "''" if m.group(1) else " ", sql)
    return _EXTRACT_FROM_RE.sub("extract(", s)


def referenced_tables(sql: str) -> set[str]:
    """Best-effort set of table names the SQL reads from. Ignores comments, string
    literals, EXTRACT expressions, and the query's own CTE names."""
    s = _scan_text(sql)
    ctes = {c.lower() for c in _CTE_AS_RE.findall(s)}
    return {t.lower() for t in _TABLE_RE.findall(s)} - ctes


def is_runnable_query(sql: str) -> bool:
    """True if the query actually reads a table or has names to fill - i.e. a real query,
    not a no-op stub like ``SELECT 1`` (used in kits whose tools are HTTP calls, not SQL).
    Prose in comments ("copied from the api") doesn't count."""
    s = _scan_text(sql)
    return bool(_NAME_RE.search(s) or _TABLE_RE.search(s))


def check_tables_allowed(sql: str, allowed_tables: set[str]) -> None:
    """Guard: every table the query reads must be in the allowed set. Belt-and-suspenders
    on top of the database grants - catches a query that was edited to read elsewhere."""
    if not allowed_tables:
        return  # no manifest provided; rely on the database grants alone
    used = referenced_tables(sql)
    extra = {t for t in used if t not in {a.lower() for a in allowed_tables}}
    if extra:
        raise BindingError(f"query reads table(s) outside the allowed set: {', '.join(sorted(extra))}")


def shape_result(rows: list[dict]):
    """Turn fetched rows into the runbook-facing result.

    Returns ``(result, is_record)``:
      - no rows                  -> ({"status": "not_found"}, True)
      - one row, one column      -> (that value as text, False)     # a checker's short answer
      - one row, many columns    -> (that row as a dict, True)      # a fetcher's record
      - many rows                -> ({"count", "rows", "summary"}, True)
    """
    if not rows:
        return {"status": "not_found"}, True
    if len(rows) == 1:
        row = dict(rows[0])
        if len(row) == 1:
            return str(next(iter(row.values()))), False
        return row, True
    summary = "; ".join(
        str(r.get("summary") or next(iter(r.values()), "")) for r in rows
    )
    return {"count": len(rows), "rows": [dict(r) for r in rows], "summary": summary}, True


def execute_tool(query_template: str, args: dict, *, db, names: dict,
                 allowed_tables: set[str], current_user) -> tuple[object, bool]:
    """Fill names into the query, check the tables, run it via ``db`` (anything with an
    ``all(sql, params)`` method), and shape the result. ``db`` is the read-only connection,
    already pinned to ``current_user`` - which is also bound as ``%(current_user)s``."""
    sql = fill_names(strip_comments(query_template), names)
    check_tables_allowed(sql, allowed_tables)
    params = dict(args or {})
    params["current_user"] = current_user
    rows = db.all(sql, params)
    return shape_result(rows)


# ── file loaders (thin; the pure functions above take plain dicts so they test without IO) ──

def load_names(path: str) -> dict:
    """Load the names file: a YAML mapping of ``{{name}}`` (or ``name``) -> real identifier.
    Accepts a flat mapping, a sectioned worksheet (placeholders grouped under headings like
    ``tables:`` / ``columns:`` — the shape ``bindings.template.yaml`` is generated in), or a
    list of ``{placeholder, real}`` entries. Unfilled (empty) values are skipped so a
    half-filled worksheet fails as 'unfilled name', not as a broken identifier."""
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out: dict = {}

    def put(key, real):
        key = str(key).strip().strip("{}").strip()
        if key and real:
            out[key] = real

    if isinstance(data, dict) and "bindings" in data and isinstance(data["bindings"], list):
        for entry in data["bindings"]:
            put(entry.get("placeholder", ""), entry.get("real"))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):       # a worksheet section: every leaf is a placeholder
                for k2, v2 in v.items():
                    put(k2, v2)
            else:
                put(k, v)
    return out


def load_allowed_tables(path: str) -> set[str]:
    """Load the allowed-schema file and return the set of table names it lists."""
    import yaml
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {t["name"] for t in data.get("tables", []) if t.get("name")}
