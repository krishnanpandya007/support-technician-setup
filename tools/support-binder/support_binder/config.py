"""Run configuration. Built from CLI args (and used to seed wizard defaults)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_OUT_NAME = "access.migration.local.sql"

# RLS session-identity expressions, chosen by platform. Supabase exposes auth.uid();
# a plain Postgres has no such function, so we default to a session-GUC-backed function
# (the convention this bundle's own runtime uses).
SUPABASE_IDENTITY_EXPR = "auth.uid()"
GENERIC_IDENTITY_EXPR = "app.current_user_id()"


def is_supabase_url(url: str) -> bool:
    """Heuristic: does this connection string point at a Supabase-hosted database?"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if host:
        return host.endswith((".supabase.co", ".supabase.com")) or "supabase." in host
    # key=value connstring or unparsable URL: fall back to a substring check.
    low = url.lower()
    return "supabase.co" in low or "supabase.com" in low


def resolve_identity_expr(explicit: str | None, url: str) -> tuple[str, bool | None]:
    """Pick the RLS identity expression. An explicit --identity-expr always wins; otherwise
    it's derived from whether the URL looks like Supabase. Returns (expr, detected_supabase),
    where detected_supabase is None when the value was explicit (no detection happened)."""
    if explicit:
        return explicit, None
    supabase = is_supabase_url(url)
    return (SUPABASE_IDENTITY_EXPR if supabase else GENERIC_IDENTITY_EXPR), supabase


@dataclass
class RunConfig:
    engine: str = "postgres"
    kit: str | None = None                 # path to a support-kit/ folder (for the default output location)
    role: str = "support_agent_ro"
    identity_expr: str | None = None       # None => auto-detect from the URL (Supabase vs generic)
    schema: str = "public"
    users_table: str | None = None
    tables: list[str] | None = None        # pre-selected relation names (skips the select prompt)
    out: str | None = None
    verify: bool = True
    non_interactive: bool = False

    def output_path(self) -> str:
        if self.out:
            return self.out
        if self.kit:
            return os.path.join(self.kit, "tools", DEFAULT_OUT_NAME)
        return DEFAULT_OUT_NAME


def from_args(args) -> RunConfig:
    return RunConfig(
        engine=args.engine,
        kit=args.kit,
        role=args.role,
        identity_expr=args.identity_expr,
        schema=args.schema,
        users_table=args.users_table,
        tables=[t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None,
        out=args.out,
        verify=not args.no_verify,
        non_interactive=args.non_interactive,
    )
