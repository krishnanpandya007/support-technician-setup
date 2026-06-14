---
name: setting-up-support-agent
type: knowledge
agent: CodeAct
triggers:
- support agent
- support kit
- support pipeline
---

# Setting Up a Support Agent

## Overview

This is the orchestrator. It turns a web app into a deployable **support kit** by running the stage skills in order, assembling their outputs, shipping a runtime, then telling the operator the local, off-model steps only a human can do. The end goal is a support agent that diagnoses an end user's real situation from read-only live state, resolves what it can from the knowledge base, and **escalates a proposed fix to a human** when a change is needed — it never mutates anything.

**Runtime brain (default `agent`).** At runtime the kit is driven by an **LLM tool-agent**: the model diagnoses by freely calling the kit's read-only tools and composes the answer. There is no decision tree to author for control flow — the runbooks are consumed as **advisory guidance** ("symptom → when to escalate"), not walked. Safety does **not** rest on branching; it rests on two hard, structural facts:
1. **A scoped read-only DB role** (column-grants + row-level security) — there is *no write tool*, so the model physically cannot change data, and RLS confines every read to the acting user.
2. **A single explicit `escalate_to_human` tool** — the only way the agent can "act", logged prominently; the agent is instructed never to claim it made a change.
A deterministic `walker` mode (the runbooks as an actual decision tree) remains available for auditable/offline runs, but `agent` is the shipped default.

**Core principle:** this skill *coordinates*; it does not re-implement. Each stage is delegated to its own skill, which owns the rules for that artifact. Between stages there is a mandatory human-review gate.

## Decisions to gather first (ask only what you cannot infer)

1. **End-user app path** — the source of the knowledge base. This is the app real users use, NOT an admin/back-office tool. If only an admin codebase is offered, stop and say the end-user app is required for the knowledge base.
2. **Backend source path** — where the read-only tools and runbooks are derived from (often the admin/back-office code). May differ from the end-user app.
3. **Schema exposure mode** — `grounded` | `aliased` | `blind` (default to `blind`: the model never sees real schema; names are bound locally by support-binder). Applies to schema-backed connections (SQL, and non-SQL where it has a schema); it does not apply to API/custom connections.
4. **Escalation channel** — Telegram, email, or both.
5. **User follow-up channel** — how a user is told their issue was resolved (e.g. email).
6. **Connection types (read-only).** Ask the operator: *"Besides tool→SQL connections, does the agent need other read-only connection types to diagnose a user's live state — external API calls (HTTP), a non-SQL data store, or custom read-only executions? Select all that apply."* Multi-select; **default = SQL only**. Every selected type MUST be read-only — non-negotiable; `discovering-support-tools` enforces it per type. The chosen set becomes the `connections` list in the config.
7. **SQL engine** *(only if SQL is enabled)* — Postgres/Supabase is the supported target today.
8. **Brain model** — which AI model powers the agent brain at runtime. **Ask the operator** (it must support tool/function calling). This becomes `runtime.model` in the config and drives the runtime by default. (e.g. `nvidia_nim/qwen/qwen3-coder-480b-a35b-instruct`, a `llama-3.x-70b-instruct`, or a Claude model.)

## Stages (run in order; review gate after each)

1. **Knowledge base** — run `generating-codebase-harness` on the **end-user app** → `support-kit/harness/`.
2. **Read-only tools + access artifacts** — run `discovering-support-tools` on the **backend source** for the enabled `connections` (at the chosen `schema_exposure` for schema-backed ones) → `support-kit/tools/`. For SQL this is the access SQL; for any enabled API/non-SQL/custom connection it additionally emits that type's per-source specs and its read-only access artifact (all human-review).
3. **Runbooks + evals** — run `authoring-support-runbooks` (uses the harness + tool catalog + failure surface) → `support-kit/runbooks/`. These are the agent brain's **advisory guidance** and the eval scenarios; they are not a control-flow tree at runtime. Include read-only **listing/query** intents ("show my bookings", "my refunds") alongside the problem-diagnosis ones, so common "show me my X" questions are first-class. If it flags tool-catalog gaps, loop back into stage 2 to add those tools, then continue.
4. **Persona** — run `generating-support-persona` (uses the end-user harness + tool catalog + config) → `support-kit/persona.md`.
5. **Config + secrets scaffolding** — write `support.config.yaml` (committed, includes the `runtime` block below) and `.secrets.template` (committed template only). Fill config from the gathered decisions; never put a real secret in any file.
6. **Runtime** — ship `support-kit/runtime/`: the agent runtime (`exposer.py`-style tool-agent), and for the **sql** connection the DB bring-up scripts (`db/01_schema.sql`, `02_seed.sql`, `03_readonly_role.sql` — the scoped read-only role + RLS, using a session GUC like `app.current_user_id()` where the platform has no `auth.uid()`), plus a `RUNTIME.md`. Default `runtime.brain: agent`; `walker` remains available. *Runtime executors for any enabled `http_api`/`nosql`/`custom` connection (behind the StateAdapter boundary) are a documented follow-up — today's runtime ships only the SQL executor; `RUNTIME.md` should note which enabled connections still need an executor wired.*
7. **Operator runbook** — write `support-kit/OPERATOR.md`: a single, ordered, concrete checklist of the **local, off-model hand-off** below, so it persists in the kit instead of living only in this chat. It MUST spell out, in order: run `support-binder`; that the emitted SQL migration is **self-contained** (it creates the read-only role, the `app.current_user_id()` identity function where the platform is not Supabase, the column grants, and the RLS policies — in dependency order); apply it with a **privileged** connection; that the **runtime must `SET app.current_user_id` per connection from the verified session** (never the model); verify; put the **read-only** credential in `.secrets`; then integrate. Reference `support-kit/tools/ACCESS_SETUP.md` (which `support-binder` itself writes for the DB step) rather than duplicating it. For any enabled `http_api`/`nosql`/`custom` connection, include its provisioning steps too. `OPERATOR.md` MUST end by pointing at the integration step: run `integrating-support-agent` (or the `support-integrator` agent) to wire the bound kit into the app following the operator's own practice — see the repo's `INTEGRATION.md`.

After the stages, hand off the **local, off-model** steps (below). Do not perform them — but DO capture them in `OPERATOR.md` (step 7) so the operator has a concrete runbook in the kit.

## The review gate

After each stage, surface what was produced and its self-review flags, and do not silently proceed past anything marked needs-review. When this skill is run interactively (by the main assistant), pause for the operator at each gate. When it is run as the `support-architect` subagent (which cannot pause), complete all stages and surface every gate and flag together in the final report for the operator to review on the artifacts.

## Hard rules

- **Delegate, don't reinvent.** Each stage's rules live in its skill; follow that skill, don't paraphrase it loosely.
- **End-user app is the knowledge base.** Never build the KB from the admin/back-office app.
- **Never touch the database.** This skill (and the agent running it) never connects to a database, never runs or applies the access migration, and never holds a connection string. That is the operator's job via support-binder.
- **Never open or write secrets.** Skip `.env*`, keys, certs. `support.config.yaml` carries non-secret choices; `.secrets.template` carries blanks only. Add the real `.secrets` to `.gitignore`.
- **Inherit the no-leak contract.** Everything user-facing (harness, persona) stays behavioral and free of technical/security detail, per the stage skills.

## Output structure

```
support-kit/
  harness/                 # stage 1 — end-user knowledge base (index.md + articles)
  tools/                   # stage 2 — catalog.yaml (per-tool connection_type), sources/<tool>.*,
                           #           per-type access artifacts (access.migration.sql / access.api.md /
                           #           access.nosql.md / access.custom.md), bindings.template.yaml, schema.snapshot.md
  runbooks/                # stage 3 — taxonomy.yaml (incl. listing intents), <symptom>.runbook.yaml, evals/
  persona.md               # stage 4 — runtime persona (locked safety sections verbatim)
  support.config.yaml      # stage 5 — committed, no secrets (includes the runtime block)
  .secrets.template        # stage 5 — committed template; the real .secrets is git-ignored
  runtime/                 # stage 6 — exposer.py (tool-agent), db/*.sql (role+RLS+seed), RUNTIME.md
  OPERATOR.md              # stage 7 — ordered local hand-off runbook (points to tools/ACCESS_SETUP.md)
  tools/ACCESS_SETUP.md    # written by support-binder — the concrete DB bring-up sequence
```

**`support.config.yaml`** (filled from the decisions; no secrets):
```yaml
product: <name>
enduser_app: <path>            # source of the knowledge base
backend_source: <path>         # source of tools + runbooks
connections:                   # the enabled read-only connection types (decision 6). DEFAULT = a single sql entry.
  - type: sql                  # sql | http_api | nosql | custom — every entry is read-only
    engine: postgres           # sql/nosql only
    schema_exposure: blind     # grounded | aliased | blind (schema-backed types only)
    read_only_role: support_agent_ro
    credential_env: SUPPORT_READONLY_DB_URL   # read-only credential, by env var (never the value)
    scope_source: session_jwt  # how the acting user is established server-side (never from the model)
  # Add one entry per additional enabled type, e.g.:
  # - type: http_api
  #     base_url_env: SUPPORT_API_BASE_URL
  #     credential_env: SUPPORT_API_READONLY_TOKEN   # read-scoped token
  #     scope_source: user_token                     # GET/HEAD only; endpoints allowlisted in access.api.md
  # - type: nosql
  #     store: firestore
  #     credential_env: SUPPORT_NOSQL_READONLY_KEY
  #     scope_source: session_uid                    # mandatory owner filter / store rules
  # - type: custom
  #     module_env: SUPPORT_ACCESSORS_MODULE         # pure read-only accessors allowlisted in access.custom.md
  #     scope_source: session_jwt
escalation:
  transport: telegram          # telegram | email | both
  confidence_floor: 0.7        # below this, ask the user instead of escalating
  dedup_window_minutes: 60     # thread a re-asking user into one ticket
  escalate_on_leaves: [needs_change, unknown]
followup:
  notify_user_on_resolution: true
  user_channel: email
runtime:
  provider: litellm
  brain: agent                   # agent = LLM tool-agent (default) | walker = deterministic runbook tree
  model: <tool-calling model id> # ASK the operator; must support function calling
  key_env: NVIDIA_NIM_API_KEY    # env var holding the brain model's key
  credential_envs: [SUPPORT_READONLY_DB_URL]  # one read-only credential env per enabled connection
  # db_url_env: SUPPORT_READONLY_DB_URL       # DEPRECATED single-SQL alias; still accepted for existing kits
  runtime_dir: runtime/
```

The agent brain's safety is structural, not authored: only read-only tools exist (no write tool), every enabled connection's credential is read-only + session-scoped to the acting user, and the lone `escalate_to_human` tool is the only way to act. Two properties become behavioral (verify in evals): the agent must escalate rather than fake a change, and must never claim it changed anything.

**`.secrets.template`** (blanks only; segregated by trust level — the read path and write path must not share secrets):
```bash
# --- READ PATH (tool server only): read-only, session-scoped. One credential per enabled connection. ---
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPPORT_SESSION_JWT_SECRET=
SUPPORT_READONLY_DB_URL=        # sql: the support_agent_ro credential produced by support-binder
# Add only for the connection types you enabled (all read-only / read-scoped):
# SUPPORT_API_BASE_URL=         # http_api: base URL of the read API
# SUPPORT_API_READONLY_TOKEN=   # http_api: read-scoped token (GET/HEAD only)
# SUPPORT_NOSQL_READONLY_KEY=   # nosql: read-only store credential
# SUPPORT_ACCESSORS_MODULE=     # custom: module path of the allowlisted pure read accessors
# --- WRITE PATH (privileged executor ONLY, separate process) ---
SUPABASE_SERVICE_ROLE_KEY=      # never in the read path / tool server
# --- CHANNELS ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
SMTP_URL=
# --- RUNTIME LLM ---
LLM_API_KEY=
```

## Local, off-model hand-off (the operator does these — you do not)

1. **sql connection:** run the **support-binder** CLI against the database with an admin URL to create the scoped read-only role and generate the real access SQL (in `blind`/`aliased`, this also binds the placeholder names to real ones — locally, off-model). Review and apply that migration with a privileged connection.
2. **Any other enabled connection** (`http_api`/`nosql`/`custom`): provision its read-only access per that type's access artifact yourself — `support-binder` is SQL-only (it has no schema to introspect for these). That means: mint a read-scoped API token and confirm the endpoint allowlist (`access.api.md`); create the read-only store credential and apply the rules/owner filter (`access.nosql.md`); register and review the pure read-only accessors (`access.custom.md`).
3. Put each resulting read-only credential into `.secrets` under the read path (one per connection); keep the service/admin key only in the write-path executor.
4. Fill the remaining `.secrets` blanks.
5. **Integrate into the app:** run `integrating-support-agent` (or the `support-integrator` agent) on the bound kit + the end-user app — it follows the operator's own hosting practice and generates the session bridge, serving layer, entry point, escalation wiring, deploy glue, and the go-live smoke checklist. The operator-facing guide to the practices is the repo's `INTEGRATION.md`.

## Self-review — STOP and fix if any is true

- The knowledge base was built from the admin app instead of the end-user app.
- A stage was skipped, or its needs-review flags were not surfaced.
- Tool-catalog gaps flagged by the runbooks stage were left unreconciled with the tools stage.
- A real secret value was written into any file, or `.secrets` was not git-ignored.
- A connection type was enabled but its read-only enforcement / per-type access artifact was not produced or surfaced, or any enabled connection's credential is not read-only.
- This skill connected to a database/API/store, ran a migration, or held a connection string/token.

## Report back

- The support-kit path and a one-line status for each stage (what it produced).
- All needs-review flags from every stage, gathered in one place.
- Any tool-catalog gaps and whether they were reconciled.
- The `schema_exposure` mode used, and the **brain model** configured (`runtime.brain`/`runtime.model`).
- The explicit operator checklist for the local hand-off (support-binder, apply migration, bring up the read-only role + RLS, fill secrets), ending with the integration step: `integrating-support-agent` / `support-integrator` wires the bound kit into the app (see `INTEGRATION.md`).
