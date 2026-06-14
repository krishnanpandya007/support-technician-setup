"""Command-line entry point and the interactive wizard.

Dual-mode, mirroring the archived harness CLI: a bare invocation runs the interactive
wizard; flags (notably --non-interactive with --tables) drive it headless for scripting.
Nothing is ever applied to the database - the tool emits reviewable SQL and can verify.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from .config import GENERIC_IDENTITY_EXPR, RunConfig, from_args, resolve_identity_expr
from .engines import get_engine
from .engines.base import ScopingPlan, TablePlan
from .introspect import (
    detect_owner_column,
    guess_users_table,
    identity_cast_type,
    owner_candidates,
)
from .manifest import build_allowed_schema
from .sqlgen import identity_bootstrap_sql
from .prompts import Prompter
from .secrets import Redactor, read_url
from .verify import CheckResult, verify

ADMIN_ENV = "SUPPORT_BINDER_ADMIN_URL"
READONLY_ENV = "SUPPORT_BINDER_READONLY_URL"
_NONE_OWNER = "(none - public/reference table)"


@dataclass
class WizardResult:
    plan: ScopingPlan
    sql: str
    out_path: str
    verify_results: list[CheckResult] | None = None


def _summary(plan: ScopingPlan) -> str:
    lines = [f"\nPlan: role '{plan.role}', identity {plan.identity_expr}, schema {plan.schema}"]
    for t in plan.tables:
        scope = f"row-scoped on '{t.owner_column}'" if t.owner_column else "public (no row scoping)"
        lines.append(f"  - {t.table} [{t.kind}]: {len(t.granted_columns)} column(s), {scope}")
    return "\n".join(lines)


def _format_results(results: list[CheckResult]) -> str:
    glyph = {True: "PASS", False: "FAIL", None: "SKIP"}
    return "\n".join(f"  [{glyph[r.passed]}] {r.name} - {r.detail}" for r in results)


def _write_sql(path: str, sql: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sql)


def _attach_identity_setup(plan: ScopingPlan, snapshot, users_table: str | None) -> None:
    """If the plan scopes via the generic app.current_user_id(), prepend the function that
    creates it so the emitted migration is self-contained (no 'schema app does not exist')."""
    if plan.identity_expr.strip() == GENERIC_IDENTITY_EXPR:
        users_rel = snapshot.get(users_table) if users_table else None
        plan.identity_setup_sql = identity_bootstrap_sql(plan.role, identity_cast_type(users_rel))


def _next_steps(plan: ScopingPlan, out_path: str) -> list[str]:
    """Ordered, plain-language steps for the operator (printed and written to disk)."""
    has_fn = bool(plan.identity_setup_sql)
    d = os.path.dirname(out_path) or "."
    schema_file = os.path.join(d, "access.allowed_schema.local.yaml")
    names_file = os.path.join(d, "bindings.template.yaml")

    steps = [
        f"1. Look over the file before applying it: {out_path}",
        "2. Apply it using an admin database connection (not the read-only one). "
        "It sets everything up in one pass:",
        f'     psql "<admin-connection-url>" -f "{out_path}"',
        "     # in Docker: copy the file into the container, then run psql -f against it "
        "(or pipe the file's contents into psql -U <admin> -d <db>).",
        "3. Give the new role a way to sign in (it is created without one, so reviewing "
        "the file commits you to nothing). Still on the admin connection:",
        f"     ALTER ROLE \"{plan.role}\" LOGIN PASSWORD '<choose-a-strong-password>';",
        "   The read-only connection string used in the later steps is this role plus "
        "that password.",
    ]
    if has_fn:
        steps.append(
            "4. On each connection, your app tells the database who the current user is "
            "(the file adds a small function for this; your app sets the value). "
            "If it isn't set, the role sees nothing.")
        nxt = 5
    else:
        nxt = 4
    steps += [
        f"{nxt}. Optional: re-run this tool with the read-only connection to check the role "
        "can only see what it should.",
        f"{nxt + 1}. Put the read-only connection string in your secrets file; keep the admin "
        "one out of the support tool.",
        f"{nxt + 2}. The real table and column names are listed in {schema_file}. Use them to "
        "fill in the names your tools query - by hand, or with help. Keep this file on your "
        "machine."
        + (f" (the names to fill in are in {names_file}.)" if os.path.exists(names_file) else ""),
        f"{nxt + 3}. Deploy alongside your app.",
    ]
    return steps


def _write_access_readme(out_path: str, plan: ScopingPlan) -> str:
    """Write a kit-local operator runbook next to the migration; return its path."""
    lines = [
        "# Applying the access migration - operator runbook",
        "",
        "Local, off-model steps to bring up the support agent's scoped read-only database",
        "access. The AI never performs these - they require a privileged connection.",
        "",
        f"- Generated SQL : `{out_path}`",
        f"- Read-only role: `{plan.role}`",
        f"- RLS identity  : `{plan.identity_expr}`",
        "",
        "## Steps (order matters)",
        "",
    ]
    lines += [f"{s}" for s in _next_steps(plan, out_path)]
    lines += [
        "",
        "## Why a separate read-only connection",
        "",
        "The admin connection is only used to set this up. The support tool should use the",
        "read-only connection instead, which can only read the listed columns and only the",
        "current user's own rows.",
        "",
    ]
    path = os.path.join(os.path.dirname(out_path) or ".", "ACCESS_SETUP.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return path


def _write_allowed_schema(out_path: str, plan: ScopingPlan) -> str:
    """Write the machine-readable allowed-schema manifest next to the migration; the tool
    server loads it to build correct, scoped SQL. Returns its path."""
    path = os.path.join(os.path.dirname(out_path) or ".", "access.allowed_schema.local.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_allowed_schema(plan))
    return path


def build_plan_auto(snapshot, config: RunConfig) -> ScopingPlan:
    """Non-interactive plan: granted columns default to all non-secret columns, owner
    columns are auto-detected. Used by --non-interactive."""
    users_table = config.users_table or guess_users_table(snapshot)
    names = config.tables or []
    tableplans: list[TablePlan] = []
    for name in names:
        rel = snapshot.get(name)
        if rel is None:
            raise SystemExit(f"selected table not found in schema: {name}")
        cols = [c.name for c in rel.columns if not c.looks_secret]
        owner = None if rel.kind == "view" else detect_owner_column(rel, users_table)
        tableplans.append(TablePlan(name, rel.kind, cols, owner))
    return ScopingPlan(role=config.role,
                       identity_expr=config.identity_expr or GENERIC_IDENTITY_EXPR,
                       tables=tableplans, schema=config.schema)


def run_wizard(prompter: Prompter, engine, config: RunConfig, *,
               url_reader, verify_engine=None, write: bool = True) -> WizardResult:
    prompter.info("support-binder - generate a scoped, read-only role. Nothing is applied; "
                  "SQL is emitted for your review.")

    admin_url = url_reader(ADMIN_ENV, "Admin database URL")
    engine.connect(admin_url)
    snapshot = engine.introspect()
    if not snapshot.relations:
        raise SystemExit("No tables or views found in the schema.")

    relation_names = [r.name for r in snapshot.relations]

    guessed = config.users_table or guess_users_table(snapshot)
    users_table = prompter.choice("Which table holds user identities?", relation_names, default=guessed)

    chosen = config.tables or prompter.select_many(
        "Select the tables/views the support agent may read", relation_names)
    if not chosen:
        raise SystemExit("Nothing selected - aborting.")

    tableplans: list[TablePlan] = []
    for name in chosen:
        rel = snapshot.get(name)
        if rel is None:
            prompter.warn(f"skipping unknown relation: {name}")
            continue
        non_secret = [c.name for c in rel.columns if not c.looks_secret]
        secret = [c.name for c in rel.columns if c.looks_secret]
        if secret:
            prompter.warn(f"'{name}': excluding secret-looking columns by default: {', '.join(secret)}")
        cols = prompter.select_many(f"Columns to expose for '{name}'", rel.column_names(),
                                    default=non_secret) or non_secret

        if rel.kind == "view":
            owner = None
            prompter.info(f"'{name}' is a view - no row-level-security policy will be emitted.")
        else:
            candidates = owner_candidates(rel, users_table)
            options = candidates + [_NONE_OWNER]
            default = candidates[0] if candidates else _NONE_OWNER
            picked = prompter.choice(
                f"Owner column for '{name}' (scopes rows to the requesting user)", options, default=default)
            owner = None if picked == _NONE_OWNER else picked
        tableplans.append(TablePlan(name, rel.kind, cols, owner))

    role = prompter.ask("Read-only role name", default=config.role)
    identity_default, detected_supabase = resolve_identity_expr(config.identity_expr, admin_url)
    if detected_supabase is not None:  # i.e. auto-detected, not explicitly passed
        kind = "Supabase" if detected_supabase else "non-Supabase"
        prompter.info(f"Detected a {kind} database -> default RLS identity expression: {identity_default}")
    identity = prompter.ask("Row-level-security session-identity expression", default=identity_default)
    plan = ScopingPlan(role=role, identity_expr=identity, tables=tableplans, schema=config.schema)
    _attach_identity_setup(plan, snapshot, users_table)

    prompter.info(_summary(plan))
    if plan.identity_setup_sql:
        prompter.info("Note: the migration will also create app.current_user_id() (typed to your "
                      "user id) so it applies in one pass - no separate bootstrap needed.")
    if not prompter.confirm("Emit the migration SQL now?", default=True):
        raise SystemExit("Aborted before emitting.")

    sql = engine.emit_scoping_sql(plan)
    out_path = config.output_path()
    if write:
        _write_sql(out_path, sql)
        manifest = _write_allowed_schema(out_path, plan)
        readme = _write_access_readme(out_path, plan)
        prompter.info(f"\nWrote {out_path}")
        prompter.info(f"Wrote {manifest}  (allowed-schema manifest for the tool server)")
        prompter.info(f"Wrote {readme}  (the operator runbook below)\n")
        prompter.info("Next steps:")
        for step in _next_steps(plan, out_path):
            prompter.info(f"  {step}")

    results = None
    if config.verify and verify_engine is not None and prompter.confirm(
            "Verify now by connecting as the new read-only user?", default=False):
        verify_engine.connect(url_reader(READONLY_ENV, "Read-only database URL"))
        results = verify(verify_engine, plan)
        prompter.info("\nVerification:\n" + _format_results(results))

    return WizardResult(plan=plan, sql=sql, out_path=out_path, verify_results=results)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="support-binder",
        description="Introspect a Postgres/Supabase database and emit a scoped, read-only "
                    "role (grants + row-level security) for a support agent. No LLM; nothing "
                    "is applied - SQL is emitted for review.")
    p.add_argument("--engine", default="postgres", help="database engine (only 'postgres' in v1)")
    p.add_argument("--kit", default=None, help="path to a support-kit/ folder (sets default output location)")
    p.add_argument("--role", default="support_agent_ro", help="read-only role name to create")
    p.add_argument("--identity-expr", default=None,
                   help="session-identity expression used in RLS policies. Default: "
                        "auto-detected from the URL - auth.uid() for Supabase, "
                        "app.current_user_id() for a generic Postgres.")
    p.add_argument("--schema", default="public", help="schema to introspect")
    p.add_argument("--users-table", default=None, help="name of the users/accounts table")
    p.add_argument("--tables", default=None, help="comma-separated relations to authorize (skips selection)")
    p.add_argument("--out", default=None, help="output SQL path (default: <kit>/tools/access.migration.local.sql)")
    p.add_argument("--no-verify", action="store_true", help="do not offer the read-only verification step")
    p.add_argument("--non-interactive", action="store_true",
                   help="headless: requires --tables; columns default to non-secret, owners auto-detected")
    return p


def main(argv=None) -> None:
    # Force UTF-8 so selection glyphs (the green ●) don't crash on a cp1252 Windows
    # console; harmless elsewhere. Mirrors exposer.py.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    config = from_args(args)
    redactor = Redactor()

    def url_reader(env_var: str, label: str) -> str:
        return read_url(env_var, label, redactor)

    try:
        engine = get_engine(config.engine)
        if config.non_interactive:
            if not config.tables:
                raise SystemExit("--non-interactive requires --tables")
            admin_url = url_reader(ADMIN_ENV, "Admin database URL")
            engine.connect(admin_url)
            snapshot = engine.introspect()
            config.identity_expr, _ = resolve_identity_expr(config.identity_expr, admin_url)
            plan = build_plan_auto(snapshot, config)
            _attach_identity_setup(plan, snapshot, config.users_table or guess_users_table(snapshot))
            sql = engine.emit_scoping_sql(plan)
            out_path = config.output_path()
            _write_sql(out_path, sql)
            manifest = _write_allowed_schema(out_path, plan)
            readme = _write_access_readme(out_path, plan)
            print(f"Wrote {out_path}")
            print(f"Wrote {manifest}")
            print(_summary(plan))
            print(f"\nWrote {readme}\nNext steps:")
            for step in _next_steps(plan, out_path):
                print(f"  {step}")
        else:
            from .prompts import RichPrompter
            verify_engine = get_engine(config.engine) if config.verify else None
            run_wizard(RichPrompter(), engine, config,
                       url_reader=url_reader, verify_engine=verify_engine)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - mask any secret before surfacing
        print(redactor.mask(f"error: {e}"), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
