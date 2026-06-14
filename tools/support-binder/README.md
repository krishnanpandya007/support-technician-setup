# support-binder

A standalone, **operator-run** CLI that turns the support agent's declarative data-access
design into a real, scoped, **read-only** database user — entirely **locally, with no LLM
involvement**. It is the off-model counterpart to the `discovering-support-tools` skill:
the skill designs *what* the agent should be able to read (in business terms, schema kept
private); this tool binds that to your real database on your machine.

It introspects your schema with an admin URL, lets you choose which tables/views the
support agent may read and which column scopes each table to a user, then **emits**
reviewable SQL that creates a least-privilege role with column-level `GRANT`s and
row-level-security policies. It does **not** apply anything; you review and apply it
yourself. It can then **verify** by connecting as the new role.

## Why two database URLs

- **Admin / all-access URL** — needed to *list* the full schema and (later) to apply the
  generated SQL. A freshly created read-only user with no grants can see nothing, so
  introspection must use an admin connection. Used transiently; never stored or logged.
- **Read-only URL** — the new `support_agent_ro` credential. Used only by the optional
  verify step, and is what you later hand to the support tool server's read path. The
  admin URL is never handed to the tool server. The migration creates the role *without*
  sign-in access; when you apply it, give it one yourself
  (`ALTER ROLE support_agent_ro LOGIN PASSWORD '…';`) — that role + password is this URL.

## Run (one command, any platform)

You only need Python 3.11+. The launcher provisions a private virtual environment beside
itself (installing `psycopg[binary]` and `rich` on first use) and then runs the CLI — no
global install, no manual venv, identical on Windows, macOS, and Linux:

```bash
# macOS / Linux
python3 tools/support-binder/run.py --kit ../your-app-harness/support-kit
```

```powershell
# Windows (PowerShell)
python tools\support-binder\run.py --kit ..\your-app-harness\support-kit
```

The admin database URL is read from `SUPPORT_BINDER_ADMIN_URL` if set, otherwise you're
prompted for it (input hidden, never stored):

```bash
export SUPPORT_BINDER_ADMIN_URL="postgresql://admin:...@host:5432/db"   # macOS/Linux
```
```powershell
$env:SUPPORT_BINDER_ADMIN_URL = "postgresql://admin:...@host:5432/db"   # Windows
```

The wizard walks you through: confirm the users table → select tables/views → pick the
exposed columns (secret-looking columns are excluded by default) → confirm the owner
column per table → name the role and the identity expression → emit the SQL → optionally
verify.

Headless (scripting/CI) — same launcher, just add flags:

```bash
python3 tools/support-binder/run.py --non-interactive --tables orders,refunds,accounts --role support_agent_ro
```

### Alternatives

- **Already manage your own environment?** Install once and use the console script (or the
  module form) directly — both are cross-platform:
  ```bash
  pip install -e tools/support-binder
  support-binder --kit ...           # console script
  python -m support_binder --kit ... # module form (no PATH dependency)
  ```
- **Have [uv](https://docs.astral.sh/uv/)?** `uv run --project tools/support-binder support-binder --kit ...`

## Output

The generated SQL is written to `--out` (default `<kit>/tools/access.migration.local.sql`).
It contains a review header, the role creation, column-level `GRANT SELECT`s, and
`ENABLE ROW LEVEL SECURITY` + policies on owned tables. **It contains real schema names**,
so it is kept local and git-ignored — review it and apply it with a privileged connection
yourself.

## Safety

- Database URLs are read from the environment or a no-echo prompt, held in memory for the
  run only, and masked everywhere they might otherwise be printed.
- The tool never writes a credential into its output and never applies DDL itself.
- Generated SQL grants only `SELECT`, never on secret-looking columns, and never `EXECUTE`.

## Scope (v1)

Postgres/Supabase only. MySQL, NoSQL, an `--apply` mode, and auto-binding the skill's
placeholder artifacts are deliberately deferred.

## Test

```bash
pip install -e "tools/support-binder[dev]"
pytest tools/support-binder/tests
```

Tests run fully offline via an in-memory mock engine and a scripted prompter — no database
needed.
