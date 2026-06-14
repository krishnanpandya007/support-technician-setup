# Support Agent Setup Bundle

> Status: **Living design doc** for the current bundle (skills + agents + `support-binder`
> + runtime). The previous code-pipeline design is archived under `legacy/DESIGN.md`.

This bundle turns a codebase into a deployable **customer-support agent**: an agent that
diagnoses a user's real, live situation from **read-only** state, answers from a behavioral
knowledge base, and **escalates a proposed fix to a human** when a change is needed — it
never mutates anything itself.

---

## 1. The one hard principle

> The agent diagnoses from read-only live state, resolves what it can from a knowledge
> base, and escalates a proposed fix to a human when a change is needed. It never mutates
> anything, and it can only read **what it is allowed to** — scoped to the acting user.

Everything else is a consequence of taking that sentence literally. The crucial design
stance: **safety is structural, not behavioral.** It does not depend on the model choosing
to behave, nor on branching logic catching every bad case. It depends on the box the model
runs in — a box enforced in **code and in the database**, not in prompts.

The build-time methodology (the skills) shapes *how the building model thinks*; it is **not**
what keeps the shipped agent safe. The prohibitions that matter — read-only, no write tool,
scope-from-session, the allowlists — are enforced programmatically in the runtime and the
database. Markdown shapes the build; code enforces the cage.

---

## 2. Two phases, one hard wall

- **Build-time** (this bundle, run in an agentic CLI): a model reads a project and *writes
  files* — the support kit. It never connects to a database, never holds a connection
  string, never runs a migration.
- **Local hand-off** (the operator, via `support-binder`): an **off-model**, no-AI CLI run
  on a trusted machine creates the scoped read-only database role from the real schema.
- **Run-time** (deployed alongside the app): the agent loads the kit and serves users,
  reading live state only through generated, named, read-only tools; its only
  side-effecting action is escalating to a human channel.

The model that *builds* the kit and the model that *runs* it are different processes; the
real schema and credentials live only on the trusted side of the wall.

---

## 3. Build-time architecture (skills + agents)

The orchestrator skill `setting-up-support-agent` runs seven stages, with a mandatory
human-review gate after each:

1. **Knowledge base** (`generating-codebase-harness`) → `harness/` — behavior-only,
   sanitized, plain-language articles built from the **end-user app**. No framework names,
   field names, file paths, or security mechanisms.
2. **Read-only tools + access artifacts** (`discovering-support-tools`) → `tools/` — the
   tool catalog (checkers preferred over fetchers), per-connection access artifacts, and,
   for SQL, the placeholder query files and a bindings template. Read from the **backend**.
3. **Runbooks + evals** (`authoring-support-runbooks`) → `runbooks/` — advisory
   symptom→resolution guidance and synthetic eval tickets mined from the failure surface.
4. **Persona** (`generating-support-persona`) → `persona.md` — tunable voice; the safety
   blocks are copied in verbatim and never softened.
5. **Config + secrets scaffolding** → `support.config.yaml` (committed, no secrets) and
   `.secrets.template` (blanks only).
6. **Runtime** → `runtime/` — the tool-agent runtime and, for SQL, the DB bring-up scripts.
7. **Operator runbook** → `OPERATOR.md` — the ordered, plain-language local hand-off,
   referencing `tools/ACCESS_SETUP.md` (which `support-binder` writes for the DB step).

Agent: `support-architect` runs the whole pipeline and,
because it cannot pause, gathers every review gate and flag into one final report.

---

## 4. The data-access model (the seven locks)

A request to read live state must survive all seven of these. SQL is the worked example;
the same invariant holds for every connection type.

1. **Named tools only.** No free-form query surface; the model selects from a fixed menu.
2. **The server holds the credential.** The connection string/token lives only in the tool
   server's environment — never in a prompt, an argument, or a returned value.
3. **Read-only at the source.** The SQL role can only `SELECT`; it is granted no
   `INSERT`/`UPDATE`/`DELETE` and no `EXECUTE` beyond the identity function.
4. **An allowlist.** Only the specific columns each tool needs are granted; secret-looking
   columns are excluded by default.
5. **Scope comes from the session, never the model.** The acting-user identity is injected
   server-side (a session GUC / `auth.uid()`), and row-level security filters every read to
   that user. The model can filter *within* the user's own data but can never choose *whose*
   data — defeating the classic prompt-injection "now show me another user's rows."
6. **Summarized output.** Tools return short verdicts or small records, not raw row dumps —
   minimizing PII reaching the model.
7. **Audit log.** Every tool call is recorded. Read-only is not the same as unwatched.

This is testable: signed in as one seeded user, a request for another user's row returns
"not found," because the row policy filters on the session identity.

---

## 5. Schema-blind design and `support-binder`

The hosted build model never sees real table/column names. It designs tools against
`{{placeholders}}` in business terms (`schema_exposure: blind`, the default; `aliased` and
`grounded` also exist). The real schema is bound **locally, off-model**, by `support-binder`.

`support-binder` (`tools/support-binder/`) is a no-AI CLI:

- **Two database URLs.** An **admin** URL is used transiently to introspect the schema (a
  fresh read-only role can see nothing). A separate **read-only** URL is used only by the
  optional verify step and is what the tool server later holds. The admin URL never reaches
  the tool server. URLs are read from env or a no-echo prompt, masked everywhere, and held
  in memory for the run only.
- **Operator-confirmed scoping.** The wizard guesses the users table, lets the operator pick
  tables/views, exposed columns (secret-looking columns excluded by default), and the owner
  column that scopes each table.
- **Owner-column heuristics.** Foreign keys pointing at the users table rank first for child
  tables. For the **users table itself** the owner is its **primary key** (`id`), and
  **self-referential FKs** (`managerId`, `createdBy`) are excluded — a self-FK points at
  *another* user, never the row's own identity.
- **Self-contained migration.** The emitted `access.migration.local.sql` creates, in
  dependency order: the read-only role, the **session-identity function** (when the platform
  is not Supabase — see §6), the column grants, and the row-level-security policies. It
  applies in one pass; it is never applied by the tool itself.
- **Platform detection.** The RLS identity expression is auto-chosen from the URL:
  `auth.uid()` for Supabase, `app.current_user_id()` for a generic Postgres (a session-GUC
  function). An explicit `--identity-expr` always wins.
- **Allowed-schema manifest.** Alongside the SQL it writes `access.allowed_schema.local.yaml`
  — the machine-readable record of exactly what the role may read (tables, columns, owner
  column, identity expression), for the runtime to consume.
- **Verification.** Connecting as the new role, it proves: visible relations equal the
  selection, no write privilege on any of them, and zero rows returned with no identity set.

Local artifacts carry **real identifiers** and are kept out of git (`*.local.*`) and out of
any prompt sent to a hosted model.

---

## 6. The session-identity contract

Row-level security needs the acting user's id, sourced from the session. On a plain Postgres
this is a session GUC the tool server sets per connection and a small function reads:

```sql
CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$;
```

The tool server runs `SET app.current_user_id = '<verified-user-id>'` on each connection,
from the verified session — never from the model. Unset ⇒ NULL ⇒ zero rows (fail-closed).
`support-binder` emits this function (typed to the users table's id column) inside the
migration so it exists before the policies that reference it. On Supabase the same role is
filled by `auth.uid()`.

---

## 7. Run-time architecture

The runtime (`exposer.py` today — a traced demo bench) loads the kit and serves a turn.

- **Two brains.** `agent` (default) — an LLM tool-agent that diagnoses by freely calling the
  read-only tools and composes the reply; it cannot change anything because no write tool
  exists. `walker` — the deterministic runbook tree, for auditable/offline runs. The choice
  is `runtime.brain` in the config.
- **Generic SQL execution** (`runtime_exec.py`). Tool calls run against the real database via
  a contract that keeps names and values strictly apart:
  - **Names** (tables/columns) are written in query files as `{{name}}` and filled from a
    local, operator-prepared names file (`tools/bindings.local.yaml`) — never model input.
  - **Values** (the current user, tool arguments) are bound as `%(name)s` parameters, never
    pasted into SQL; `%(current_user)s` is always available.
  - A **checker** query selects one value (a short verdict); a **fetcher** selects a row/rows
    (a small record).
  - A guard rejects any query that reads a table outside the allowed-schema manifest —
    belt-and-suspenders over the role grants.
  - If a kit has no names file (or only stub queries), the runtime falls back to its built-in
    tools, so the bundled demo keeps working.
- **Scoping at the connection.** The runtime opens the read-only connection and sets the
  identity GUC for the acting user before any tool runs; RLS does the rest.

---

## 8. The escalate-only action model

The agent has exactly one way to affect the world: a single `escalate_to_human` tool, loudly
logged. It hands a human a structured proposal — the **entity**, the **change**, and the
**reason** (the verdicts it found) — and tells the user plainly that the team has the details,
never claiming the change is done. A separate, privileged executor (a different process with
the write-path credential) is where an approved change is actually applied; it is not part of
the read path and the agent never holds its credential.

The boundary is enforced in three independent places: the tool catalog has no write tool, the
database role cannot write, and the persona's locked rules forbid claiming otherwise.

---

## 9. Config and secrets layout

`support.config.yaml` (committed, no secret values) carries the gathered decisions: the
end-user app and backend paths, the enabled read-only `connections`, `schema_exposure`,
escalation/follow-up channels, and the `runtime` block (`brain`, `model`, credential env
names).

`.secrets.template` (committed; blanks only — the real `.secrets` is git-ignored) is
segregated by trust level so the read and write paths never share a secret:

- **Read path** (tool server only): one read-only, session-scoped credential per connection.
- **Write path** (privileged executor only, separate process): the service/admin key.
- **Channels / runtime LLM**: escalation transport and the brain model's key.

No real secret is ever written into any file by the build or the binder.

---

## 10. Privacy / DPDP considerations

- **Data minimization.** Checkers return verdicts, not rows; fetchers return small summaries.
  The model sees as little user data as the answer requires.
- **Purpose limitation & scope.** RLS pins every read to the acting user; cross-user reads
  are structurally impossible, not policy-dependent.
- **No schema/PII to third parties at build time.** Blind design means the hosted build model
  never learns the data model; real names are bound locally.
- **Auditability.** Every tool call and every escalation is logged, supporting access records
  and the right to explanation of an automated decision.
- **Right to rectification via a human.** Changes are proposed to and executed by a human, not
  the automated agent.

---

## 11. Invariants / guardrails

- The knowledge base is built from the **end-user app**, never the admin/back-office app.
- Nothing in the build path connects to a database, runs a migration, or holds a connection
  string. That is the operator's job via `support-binder`.
- No write tool is ever generated; the runtime has no code path that mutates data.
- Secrets are never opened or written; `.secrets` is git-ignored; local real-name artifacts
  (`*.local.*`) stay out of git and out of any hosted-model prompt.
- Persona safety blocks are copied verbatim into every project and never softened.
- Operator-facing generated text stays plain and general (no internal jargon).

---

## 12. Open questions / follow-ups

The current frontier — known gaps, tracked honestly:

- **Non-SQL runtime executors.** `http_api` / `nosql` / `custom` connections can be designed
  and have access artifacts, but the runtime executor (`runtime_exec.py`) is **SQL-only**.
  Extending the executor to those types behind a common boundary is the largest functional
  gap.
- **Generator ↔ executor contract.** `discovering-support-tools` must emit `queries/*.sql`
  that follow the runtime contract in §7 (`{{names}}` + `%(param)s`, checker/fetcher shape).
  Today some kits emit stub queries; the generator and executor need to be reconciled.
- **Bindings worksheet.** The runtime reads `tools/bindings.local.yaml` (operator-filled). A
  generated worksheet + validator (without exposing real schema to the build model) would
  make wiring less manual; a `RUNTIME.md` should document the file and the §7 contract.
- **Production tool server.** `exposer.py` is a traced demo bench, not a deployable,
  standalone read-only tool server.
- **`support-binder` scope.** Postgres/Supabase only; MySQL, NoSQL, an `--apply` mode, and
  automatic placeholder reconciliation are deferred.
