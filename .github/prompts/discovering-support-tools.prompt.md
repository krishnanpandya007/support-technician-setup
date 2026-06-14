---
mode: agent
description: Use when generating the read-only data-access tools a customer-support agent uses to diagnose an end user's live state — reading a project's backend to propose a tool catalog plus the database access SQL (restricted read-only role, column grants, row-level security), for mandatory human review. Triggers: "discover support tools", "generate the read-only tools / DB access for the support agent", part of setting up a support agent.
---

# Discovering Support Tools

## Overview

The runtime support agent must diagnose an end user's *actual* situation, not guess. It does that by calling **read-only tools** that fetch and check the user's live state. The user-owned state may live behind more than one read surface — SQL tables, a read API, a non-SQL store, or a pure read-only accessor — so this skill works for whatever **connection types** the operator enabled (default: SQL only; see "Connection types" below). It reads a product's **backend** and proposes, for human review:

1. a **tool catalog** — named, fixed, parameterized read tools scoped to the authenticated end user, each tagged with its `connection_type`;
2. per-type **read-only access artifacts** — for SQL, the migration SQL (a restricted read-only role, a table/column allowlist, and row-level-security policies); for any other enabled type, that type's equivalent read-only access spec (endpoint allowlist + read-scoped token for an API, read-only rules/credential for a non-SQL store, an allowlist of pure read accessors for custom);
3. one **per-tool source spec** (the SQL query, HTTP request, non-SQL query, or named accessor the tool runs).

**Core principle:** the agent reads the world *only* through these named tools. It never gets a raw connection, never composes its own query/request/execution, never sees another user's data, and never performs a change.

## Connection types — confirm what the agent reads through

Before designing anything, **confirm the enabled connection types** with the operator (this is decision 6 in `setting-up-support-agent`; the set lives in the config's `connections` list). State back which are enabled. **Default = SQL only**, in which case every output below is identical to the SQL-only behavior. The four supported types:

- **`sql`** (default) — a relational database. Tools are parameterized SQL; enforcement is a read-only role + column grants + RLS / mandatory owner filter.
- **`http_api`** — a read API the product already exposes. Tools are single, enumerated `GET`/`HEAD` requests against allowlisted endpoints.
- **`nosql`** — a non-relational store (document/key-value/etc.). Tools are scoped reads with a mandatory owner filter, over a read-only credential.
- **`custom`** — a pure, side-effect-free read accessor the product provides (a function that computes a verdict from data it reads). Tools name an allowlisted accessor; the model never supplies code.

Every enabled type is **read-only, non-negotiable** — the per-type enforcement is spelled out in "Per-type enforcement" below, and each gets its own human-review access artifact. A tool's type is recorded as `connection_type` in the catalog (schema below). You may mix types in one catalog (e.g. most checks in SQL, one against a payments API).

## Schema exposure — decide how much the model sees

The schema reveals your whole data model, so you may not want to hand real table/column names to a hosted CLI model. Split the work in two and pick a mode:

- **Logical design** (the model's job): which entities, fields, relationships, and scope each tool needs — in **business terms from the harness**.
- **Physical binding** (real names): done **locally, off-model**.

Set by `schema_exposure`:

1. **`grounded`** — the model sees the verified schema and emits real SQL. Highest fidelity, full exposure.
2. **`aliased`** — the operator first replaces real names with neutral tokens locally (`table_1.col_a`), keeping business labels, types, and relationships; the model designs against *that*. A local, deterministic substitution (operator-held alias→real map) restores real names. The model learns shape, never identifiers.
3. **`blind`** — the model sees **no schema**. It designs purely from the harness and emits **logical** tool specs plus a **binding worksheet** of placeholders; the operator fills real names and produces the SQL locally. Maximum protection.

In `aliased`/`blind` you (the agent) **must not** open schema files, migrations, ORM field definitions, or a dump, and you emit **placeholder** SQL only — e.g. `GRANT SELECT ON {{bookings}} ({{owner_col}}, {{status_col}})` — never real identifiers. Final binding and SQL generation happen locally without a model; an optional read-only local introspection helper can auto-fill and validate the worksheet against the live database.

## Schema grounding — verified names, never guesses

*(Applies in `grounded` and `aliased` modes, where the model is given real or locally-aliased structural truth. In `blind` mode the model receives no schema — it designs from the harness and defers all binding to the worksheet.)*

You cannot write correct `GRANT`s or RLS policies from guessed names. Before designing anything, obtain **ground-truth schema** (real in `grounded`, locally-aliased in `aliased`) and build a verified map from it. Acquiring schema at *setup* time is read-only and human-run — it does **not** violate the runtime rule that the agent never touches the database; that rule governs the deployed agent, not this authoring step.

Schema sources, in order of preference:

1. **A read-only schema dump or introspection** the operator provides — e.g. `pg_dump --schema-only`, a query of `information_schema` / `pg_catalog`, `supabase db dump`, or the project's generated DB types. This is *live truth*.
2. **Schema definition files in the repo** — migrations, ORM models, a `schema.sql` / Prisma / Supabase schema. Truth *as written*, which may drift from the live database.

Rules:

- If **neither** is available, **STOP** and request a schema dump or introspection output. Do not invent table or column names.
- If **both** exist, reconcile them and **flag any drift** (a column in code but not in the dump, or vice-versa) for review — trust the live dump for the migration.
- Build a **verified map**: each table, its real column names, the **owner column** that ties a row to a user (the foreign key to the users/accounts table), foreign keys, and any **existing RLS policies / authorization rules** — these define which related rows a user may already see, so mirror them rather than inventing scope.
- Every `reads:` entry, every `scope` owner column, and every name in the migration SQL must cite a name present in this verified map. Anything you cannot verify is a **needs-review flag, never a guess**.

## The non-negotiable security model (encode all seven layers)

The invariant is **identical for every connection type**; only how each layer is *enforced* differs (see "Per-type enforcement" below). The SQL phrasing is the worked example.

1. **Named tools only** — fixed, parameterized tools. No free-form query/request/execution surface, no query builder, no raw passthrough, no model-supplied URL or code.
2. **The tool server holds the credential** — the connection string, API token, store key, or accessor handle lives only in the tool server's environment, never in a prompt, a tool argument, or a return value.
3. **Read-only at the source** — the credential physically cannot mutate. For SQL that is a role that cannot `INSERT/UPDATE/DELETE`; for an API, read-only verbs (`GET`/`HEAD`) against a read-scoped token; for a non-SQL store, a read-only credential; for custom, a pure accessor with no write capability. Writes are impossible at the source level, not merely discouraged.
4. **Allowlist** — exactly the read surface each tool needs, nothing more: `GRANT SELECT` on specific columns of specific tables (SQL), enumerated endpoints (API), specific collections/paths (non-SQL), or named accessor functions (custom). Secrets, password hashes, and other users' internal fields are never granted/exposed.
5. **Scope from the verified session — never from the model.** The identity that scopes data to "this user" comes from the authenticated session, injected server-side — on *every* connection type. The model may pass *filters within* its own already-scoped data (a date range, a booking id it was shown); it may **never** pass the user/owner id (or token, or path segment) that defines the scope.
6. **Summarized output** — tools return short, labeled prose (a verdict or a small summary), not raw rows/records/payloads. Minimizes PII reaching the model and suits a weaker runtime model.
7. **Audit log** — every tool call (name, args, result digest) is logged, regardless of source. Read-only is not unaudited.

If any tool you propose would break one of these, redesign it.

## Two tiers of tools — prefer checkers

- **Fetcher** — returns state: `get_booking(id) → {status, amount, when, ...}`.
- **Checker** — returns a *diagnosis verdict*: `check_payment_booking_consistency(id) → "MISMATCH: payment captured, booking cancelled, no refund issued"`.

Push diagnostic logic into **checkers** (server-side, deterministic, testable) so the runtime model reads a conclusion instead of deriving one. For every fetcher, ask whether the real question is a checker.

## Scope: end users only

Tools read the **asking user's own data** and the **related rows that user can already see** (e.g. the store a user ordered from, public catalog data) — rely on the existing per-user access; do **not** invent an elevated read path. Never expose admin-only or cross-user data. Derive tools from the backend's data models, its read endpoints, and especially its **failure/error surface** (every bad state the code can produce is a symptom a user will report → a checker).

## Per-type enforcement (how each connection type honors the seven layers)

The invariant above is constant: **scope comes from the verified session; credentials are read-only and least-privilege.** Only the mechanism differs per `connection_type`.

### `sql` — relational database (Postgres/Supabase primary; MySQL compatible)

- **Postgres / Supabase (primary):** RLS-native.
  - Policies reference the session identity (e.g. `using (auth.uid() = <owner_col>)`).
  - The tool server authenticates **as the end user** (the user's JWT), never with a service/admin key — an admin key bypasses RLS and breaks the whole model. Prefer a short-lived, read-scoped session JWT carrying the user's id + a `scope` claim that policies restrict to SELECT on allowlisted tables.
  - The service/admin key belongs ONLY to the separate privileged executor (the write path), never here.
- **MySQL:** no native RLS. Enforce in the access layer — a mandatory `WHERE <owner_col> = :session_id` injected from the verified session (never the model), over a read-only user with column grants; optionally `SQL SECURITY`-defined views.

### `http_api` — a read API the product already exposes

- **Read-only verbs only** — `GET`/`HEAD`. Never `POST`/`PUT`/`PATCH`/`DELETE`, and never an endpoint with a side effect (an action endpoint that happens to use GET is still forbidden).
- **Endpoint allowlist** — each tool names exactly one enumerated endpoint + method. No templated/arbitrary/model-supplied URL, no proxy/passthrough.
- **Read-scoped credential** — the tool server holds a read-scoped token; the privileged/write key is never here.
- **Scope server-side** — the user's identity is attached server-side (the user's own access token, or a server-derived id placed into the path/header). The model never supplies the id, token, or path segment that defines whose data is read; it may pass only filters within the user's own data.
- **Summarized output** — return a verdict/summary, never the raw API payload.

### `nosql` — a non-relational store (document / key-value / etc.)

- **Store-native read-only rules** keyed on the authenticated uid (e.g. Firestore security rules), OR an adapter that injects a **mandatory owner filter** from the verified session (never the model), over a **read-only credential** (no write/delete capability).
- **Collection/path allowlist** — only the specific collections/paths each tool needs.
- Same scope-from-session rule and summarized output as every other type.

### `custom` — a pure, read-only accessor the product provides (the riskiest type)

A `custom` tool calls a **named accessor function** (a piece of product code that reads some state and returns a verdict/summary). Because an accessor is arbitrary code rather than a constrained verb or role, keep it read-only with stacked constraints:

- **Name, not code.** The model supplies only the accessor **name** (from a human-reviewed allowlist in `access.custom.md`) plus filters within the user's own data. It never supplies code, a query, a URL, or the scoping id.
- **Pure / side-effect-free.** Each accessor only *reads*: no write, no shell, no filesystem mutation, no non-`GET` network call, no mutation of a store or global, no `exec`/`eval`. It is sandboxed with no write capability.
- **Scope from session.** The acting user's id is injected from the verified session, never received as a parameter.
- **Custom red-flag list — STOP and redesign if an accessor:** writes a file, spawns a process, makes a mutating/non-`GET` network call, mutates a store or global, or takes raw code / SQL / a URL / the scoping id as a parameter. Any of these means it is **not** a read-only accessor.

## The StateAdapter boundary (where read-only + scope are enforced once)

Every tool, regardless of `connection_type`, sits behind a single **StateAdapter** boundary — `fetch(tool, params, session) → summary`. This is the one reviewed place where read-only enforcement and **session-scoping** live: the adapter looks up the tool's `connection_type` and its per-type source spec, **injects the scope id from `session` (never from `params`)**, executes against the held read-only credential via that type's executor, and returns a summarized result. Keeping the catalog connection-type-agnostic and the enforcement centralized means a reviewer audits scoping and read-only-ness once, not per tool.

*Runtime note:* the shipped demo runtime (`exposer.py`) and `support-binder` implement only the **SQL** executor today. The `http_api`/`nosql`/`custom` executors behind this boundary are a runtime follow-up — this skill's job is to *specify* them (catalog + per-type access artifacts) so they can be reviewed and built; it does not require them to exist to produce the kit.

## Mandatory human review — you propose, a human applies

You **never** connect to a real database/API/store, run a migration, or execute any query, request, or accessor. Each per-type access artifact (the SQL migration, the API endpoint allowlist + token requirement, the non-SQL credential + rules, the custom accessor allowlist) grants a read-only, session-scoped capability — it MUST be read and applied/provisioned by a human. Generate each as a reviewable artifact with a clear review header. Inherit the harness rule: **never open secret files** (`.env*`, keys, certs) and never echo any secret value.

## Process

0. **Confirm enabled connection types** (`connections`; default SQL only — see "Connection types" above) and, for schema-backed types, **pick the exposure mode** (`grounded` / `aliased` / `blind`). In `aliased`/`blind`, do not open schema/dump/migration files; design from the harness and emit placeholders + a binding worksheet.
1. **Ground the schema** (schema-backed types in `grounded`/`aliased` only) — acquire a dump/introspection or aliased schema, build the verified map, reconcile and flag drift. If a needed schema source is missing, **STOP** and request it. (`http_api`/`custom` have no schema to ground — design their tools from the harness + the backend's read endpoints/accessors.)
2. **Locate the backend's** read surfaces and error/failure paths (the bad states become checkers): SQL read paths, read endpoints/accessors per enabled type. Skip secret files.
3. **Inventory entities & symptoms** — the user-owned entities (by their verified owner column / scope source), the related rows the user can already see (per existing RLS/authorization), and the bad states the code produces.
4. **Design the catalog** — for each tool, decide its `connection_type`, fetcher vs checker, the **verified** scope basis it uses (owner column / token / owner filter), the model-suppliable filters (never the scope id), and the summarized return shape. Favor checkers.
5. **Write the per-tool source spec** under `sources/` for the tool's type (SQL query, HTTP request, non-SQL query, or accessor reference); the scope is bound server-side from the session, never a model param.
6. **Write the per-type access artifact(s)** for each enabled type — the SQL migration (read-only role, column/table grants, RLS, referencing verified names and mirroring existing scope), and/or the API endpoint allowlist + read-scoped token, the non-SQL read-only credential + rules/owner filter, the custom accessor allowlist — each with comments explaining what it allows and why it's safe.
7. **Self-review** against the seven layers and the checklist below.
8. **Report** — for human review and application.

## Output structure

Into the project's support kit (next to its harness):

```
support-kit/
  tools/
    schema.snapshot.md      # logical entity / (where applicable) table·column·owner map + source + drift flags
    catalog.yaml            # the tool catalog (schema below)
    sources/<tool>.<ext>    # one source spec per tool, by connection_type:
                            #   <tool>.sql        — parameterized query (sql; placeholder in aliased/blind)
                            #   <tool>.http.yaml  — GET/HEAD endpoint + param→path/query map + server-side scope (http_api)
                            #   <tool>.nosql.yaml — collection/path + mandatory owner filter (nosql)
                            #   <tool>.accessor.md— accessor name/signature/reads + no-side-effect proof (custom)
    access.migration.sql    # sql: restricted role + grants + RLS — HUMAN REVIEW REQUIRED
    access.api.md           # http_api (if enabled): endpoint allowlist + read-scoped token — HUMAN REVIEW REQUIRED
    access.nosql.md         # nosql (if enabled): read-only credential + rules/owner filter — HUMAN REVIEW REQUIRED
    access.custom.md        # custom (if enabled): allowlisted pure accessors + sign-off — HUMAN REVIEW REQUIRED
    bindings.template.yaml  # (aliased/blind, schema-backed types) logical→real name worksheet filled LOCALLY
```

Emit only the access artifact(s) for the **enabled** connection types — SQL-only ⇒ exactly `schema.snapshot.md`, `catalog.yaml`, `sources/<tool>.sql`, `access.migration.sql`, and (in `aliased`/`blind`) `bindings.template.yaml`, identical to before.

`schema.snapshot.md` records the map you designed against: the **source** (live dump/introspection, locally-aliased, or — in `blind` — the harness logical entities) and each covered entity with its scope/owner basis. For schema-backed types (`sql`, and `nosql` where it has a schema) it lists tables/collections, columns/fields, the owner column, the existing RLS/authorization mirrored, and any **drift** flagged. For `http_api`/`custom` it records the *logical* entities and the scope source only — there are no physical identifiers to map (the read surface is the endpoint allowlist in `access.api.md` / the accessor allowlist in `access.custom.md`).

**Schema-exposure modes apply only to schema-backed types.** In `aliased`/`blind`, the `sources/*.sql` (and any schema-backed `*.nosql.yaml`) plus `access.migration.sql` are emitted with `{{placeholders}}`, and `bindings.template.yaml` lists every placeholder for the operator to bind **locally** — the model never sees the substitutions. The worksheet may be a flat `placeholder: value` mapping or group placeholders under section headings (`tables:`, `columns:`, …); either way every leaf key must be a placeholder name exactly as it appears in the queries, because the runtime reads the filled file with the section headings ignored. `http_api` and `custom` have no schema to expose, so they carry no placeholders and need no binding worksheet; their surface is the human-reviewed allowlist in their access artifact.

**`catalog.yaml` entry.** Every entry carries the same universal fields — `name`, `tier` (`fetcher` | `checker`), `summary`, `reads`, `scope: current_user`, `params` (model-suppliable filters only), `returns`, `risk: read-only` — plus a `connection_type` (`sql` default | `http_api` | `nosql` | `custom`) and **exactly one** per-type source reference: `query_ref` for `sql`/`nosql`, `request_ref` for `http_api`, `accessor_ref` for `custom`.

```yaml
# sql (default) — parameterized query; scope enforced by the read-only role + RLS
- name: check_refund_status          # verb_noun, plain
  tier: checker                      # fetcher | checker
  connection_type: sql               # sql (default) | http_api | nosql | custom
  summary: Whether a refund is owed/pending/issued for the user's booking.
  reads: [bookings, refunds]         # plain entity names (for review)
  scope: current_user                # enforced server-side from session; NOT a model param
  params:                            # model-suppliable filters ONLY
    - { name: booking_id, type: string, required: true }
  returns: "labeled verdict, e.g. 'PENDING: refund of <amount> approved, not yet sent'"
  query_ref: sources/check_refund_status.sql
  risk: read-only

# http_api — one allowlisted GET endpoint; user scope attached server-side
- name: check_shipment_status
  tier: checker
  connection_type: http_api
  summary: Whether the user's order has shipped, is in transit, or was delivered.
  reads: [shipments]
  scope: current_user                # server attaches the user's token / derived id; NOT a model param
  params:
    - { name: order_id, type: string, required: true }
  returns: "labeled verdict, e.g. 'IN_TRANSIT: shipped 2 days ago, ETA tomorrow'"
  request_ref: sources/check_shipment_status.http.yaml
  risk: read-only

# nosql — scoped read with a mandatory owner filter over a read-only credential
- name: get_recent_events
  tier: fetcher
  connection_type: nosql
  summary: The user's most recent activity events.
  reads: [events]
  scope: current_user                # mandatory owner filter injected from session; NOT a model param
  params:
    - { name: since, type: string, required: false }
  returns: "short summary of the latest events"
  query_ref: sources/get_recent_events.nosql.yaml
  risk: read-only

# custom — a human-reviewed, pure read-only accessor; model passes the NAME + filters, never code
- name: check_quota_consistency
  tier: checker
  connection_type: custom
  summary: Whether the user's used quota matches their plan allowance.
  reads: [subscription, usage]
  scope: current_user                # accessor receives the user id from session; NOT a model param
  params:
    - { name: period, type: string, required: false }
  returns: "labeled verdict, e.g. 'MISMATCH: usage exceeds plan by 12 units'"
  accessor_ref: sources/check_quota_consistency.accessor.md
  risk: read-only
```

**Per-type access artifacts** — each opens with a review header (what it grants, why it is safe, that it is read-only and session-scoped) and is **human-review-required**:

- **`access.migration.sql`** (`sql`): create the read-only role → `GRANT SELECT` on allowlisted columns → `ENABLE ROW LEVEL SECURITY` + policies scoped to the session identity for the user's tables and the related-but-visible tables.
- **`access.api.md`** (`http_api`): the endpoint allowlist (one method + path per tool, all `GET`/`HEAD`), the read-scoped token the tool server must hold (named by env var, never the value), and exactly how the user's scope is attached server-side. States that no mutating/off-allowlist endpoint is reachable.
- **`access.nosql.md`** (`nosql`): the read-only credential (named by env var), the store-native rules or the mandatory owner filter per collection/path, and the collection/path allowlist.
- **`access.custom.md`** (`custom`): the allowlisted accessor functions, each with its signature, what it reads, a read-only/no-side-effect attestation, and a human sign-off line. States that the model passes only the accessor name + filters, never code.

## Self-review — STOP and fix if any is true

- A tool's `connection_type` is missing, or it lacks the matching single source ref (`query_ref` for sql/nosql, `request_ref` for http_api, `accessor_ref` for custom), or an enabled connection type has no per-type access artifact.
- A table, column, or owner name in the catalog or migration is **not present in the verified schema map** (guessed, not grounded) — or code-vs-live drift was found but not flagged. *(schema-backed types)*
- In `aliased`/`blind` mode: a **real identifier leaked** (a non-placeholder/non-alias name) into the catalog, sources, or access artifacts — or you opened a schema/dump/migration file. *(schema-backed types)*
- A tool takes the **scoping user/owner id (or token, or path segment) as a parameter** on **any** connection type — it must come from the session, not the model.
- Any tool exposes free SQL / a query builder / a raw query passthrough, a **model-supplied or templated URL**, **raw code** for a custom accessor, raw rows/records/payloads of sensitive fields, or another user's / admin-only data.
- An `http_api` tool uses a mutating verb (anything but `GET`/`HEAD`) or an off-allowlist/side-effecting endpoint.
- A `nosql` tool lacks a mandatory owner filter (or store rule) or uses a write-capable credential.
- A `custom` accessor writes/ spawns/ mutates/ makes a non-`GET` network call, or is not on the human-reviewed `access.custom.md` allowlist (see the custom red-flag list above).
- A connection string, an API token, a service/admin key, a store key, or any secret appears in the catalog, a source spec, or a return value.
- The SQL migration grants more than SELECT, grants secret/credential columns, or omits RLS (or the engine-appropriate scoping) on any user table; or any enabled connection's credential is not read-only.
- A return shape dumps raw PII instead of a summarized verdict.
- You connected to a real database/API/store, ran a migration, or opened a secret file. (You must not.)

## Report back

- The **enabled connection types** (default SQL only), and for schema-backed types the **`schema_exposure` mode** used — and (in `aliased`/`blind`) that no real identifier was exposed and `bindings.template.yaml` is the operator's local binding step.
- Paths written (`schema.snapshot.md`, `catalog.yaml`, `sources/`, the per-type access artifact(s) — `access.migration.sql` and/or `access.api.md`/`access.nosql.md`/`access.custom.md` — and in `aliased`/`blind` `bindings.template.yaml`).
- The **source** used per type (live dump/introspection, locally-aliased, or harness-only for schema-backed types; read endpoints/accessors for API/custom) and any drift flagged.
- Tool count split by tier (fetchers vs checkers) **and by `connection_type`**, and the entities covered.
- For SQL, the database engine assumed and how scoping is enforced; for each other enabled type, how read-only + session-scope is enforced.
- An explicit **HUMAN REVIEW REQUIRED** note on each per-type access artifact, listing what it grants.
- Any symptom/entity you could not safely cover, as a generic needs-review flag.
