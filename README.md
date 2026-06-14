# Support Technician — a CLI-invokable skill bundle

A portable bundle of **skills, agents, and local tools** that an agentic CLI (Claude Code,
or any CLI that reads skills) uses to turn a codebase into a deployable, **read-only
customer-support agent** — one that diagnoses a user's real, live problem but is
structurally incapable of changing anything.

There is no build pipeline to run — the CLI's model *is* the engine; the skills are the
methodology it follows, with a **mandatory human-review gate after every stage**.

The end goal is a support agent that behaves like a **technician, not a chatbot**: it
diagnoses a user's actual situation from live (read-only) state, resolves what it can from
a knowledge base, and **escalates a proposed fix to a human** when a change is needed — it
never mutates anything itself.

## How it works: build-time → run-time

- **Build-time (this bundle, run in a CLI):** point the skills at a target project to
  generate a **support kit** — a behavioral knowledge base, a read-only tool catalog +
  database access SQL, diagnostic runbooks, a persona, and config scaffolding. Every
  generated artifact passes a **mandatory human-review** step before it's trusted.
- **Run-time (deployed in the customer's webapp):** the agent loads the kit and serves end
  users. It reads live state only through the generated tools (never a raw DB connection),
  and its sole side-effecting action is escalating to an admin channel.

## Skills

| Skill | Produces |
|-------|----------|
| `generating-codebase-harness` | Behavioral **knowledge base** (sanitized, plain-language) |
| `generating-support-persona` | Project **persona.md** from the reference template |
| `discovering-support-tools` | Read-only **tool catalog** + DB access **migration SQL** (restricted role, grants, RLS) |
| `authoring-support-runbooks` | Diagnostic **runbooks** + synthetic **eval tickets** (from the code's failure surface) |
| `setting-up-support-agent` | Orchestrator: runs the four stages in order, assembles the kit + config/secrets scaffolding |
| `integrating-support-agent` | Wires the bound kit into the operator's app — session bridge, serving layer, entry point, escalation wiring, deploy glue, smoke checklist — following the operator's own practice (see [`INTEGRATION.md`](INTEGRATION.md)) |

Agents: `support-architect` runs the build pipeline end to end; `support-integrator` wires
the finished, operator-bound kit into the app.

## Repository layout

- `.claude/`, `.opencode/`, `.cursor/`, `.github/`, `.windsurf/`, `.clinerules/`, `.roo/`, `.kilocode/`, `.amazonq/`, `.kiro/`, `.continue/`, `.openhands/`, `.gemini/`+`GEMINI.md`, `QWEN.md`, `.trae/`, `.junie/`, `.goosehints`, `AGENTS.md` — the bundle, pre-rendered for each CLI (just Markdown; copy your tool's folder, per the table below)
- `bundle/` — the tool-neutral master the packs above were rendered from
- `tools/support-binder/` — off-model local CLI that creates the scoped read-only DB role and
  access SQL on your machine (the model never sees your real schema)
- `exposer.py` — a runnable demo: drives a generated kit on a mocked *or* local database,
  tracing every tool call and decision in your terminal
- `runtime_exec.py` — the generic, kit-driven read-only SQL executor the demo/agent uses
- `INTEGRATION.md` — the operator's guide to wiring the agent into their app (the practices and their trade-offs, the invariants, the go-live smoke tests)
- `DESIGN.md` — the data-access and security model, written up in full

## Quick start

### 1. Use the bundle in your CLI

Copy `.claude/skills/` and `.claude/agents/` into your CLI's skills/agents location. In
Claude Code they're discovered automatically — invoke a skill by name or describe the task.
Point it at a target repo and it builds the kit stage by stage, pausing for review each time.

### 2. Bind a read-only DB role — one command, any OS

The tools stage designs the catalog against placeholders; you bind it to your real schema
**locally, off-model**. No separate install step:

```
py tools/support-binder/run.py --kit path/to/support-kit
```

It introspects your schema, lets you choose the tables, columns, and per-row owner scope,
then emits a reviewable migration. You apply it; it never does. See
[`tools/support-binder/README.md`](tools/support-binder/README.md).

### 3. Watch it run (demo)

Install the runtime dependencies and drive a generated kit:

```
pip install -r requirements.txt
py exposer.py path/to/support-kit --mode sim --no-llm     # fully offline, traced
```

`--no-llm` runs offline with mocked tools; drop it (and set your provider key) to use a real
model. Add `--live` to read a real database — that needs a fully bound kit (real names in
`bindings.local.yaml` plus the query files) and your own scoped read-only connection
(`--db-url`/`SUPPORT_READONLY_DB_URL`) and `--user-id`.

## Security model

Read-only DB role, table/column allowlist, row-level security scoped from the **verified
session** (never the model), tool-mediated access (the connection never reaches the agent),
**escalate-only** actions, and **schema-blind** tool design. The full writeup — including the
runtime execution contract, the config/secrets layout, and DPDP considerations — is in
[`DESIGN.md`](DESIGN.md).

## Use in any CLI

The bundle is just Markdown, so it isn't tied to Claude Code. Every major CLI's pack ships
ready to copy at the repo root — pick your tool's folder and drop it into your project:

| Your CLI | Copy into your project |
|----------|------------------------|
| Claude Code | `.claude/` |
| opencode | `.opencode/` (native subagents + skills; also reads `.claude/skills/` and `AGENTS.md`) |
| Cursor | `.cursor/` |
| GitHub Copilot | `.github/` |
| Windsurf | `.windsurf/` |
| Cline | `.clinerules/` |
| Roo Code | `.roo/` |
| Kilo Code | `.kilocode/` |
| Amazon Q Developer CLI | `.amazonq/` |
| Kiro | `.kiro/` (always-on overview + manual steering file per stage) |
| Continue | `.continue/` |
| OpenHands | `.openhands/` (keyword-triggered microagents) |
| Gemini CLI | `GEMINI.md` + `.gemini/` (slash command per stage) — copy `AGENTS.md` too (imported) |
| Qwen Code | `QWEN.md` — copy `AGENTS.md` too (imported) |
| Trae · JetBrains Junie · Goose | `.trae/` · `.junie/` · `.goosehints` — copy `AGENTS.md` too (pointed to) |
| Codex · Antigravity · Crush · Warp · Zed · Factory · 20+ others | `AGENTS.md` (read natively) |
| Aider | `AGENTS.md`, loaded via `aider --read AGENTS.md` |

`AGENTS.md` is the open cross-tool standard that 20+ CLIs read natively, so that one file
covers the long tail — with two honest caveats: it delivers the five stages as **staged
instructions** (always in context, not lazily-loaded skills), and the `support-architect`
agent as an **inline role** rather than a spawnable subagent (a preamble in the file says so, and the
persona reference template is appended verbatim so a single-file install is
self-contained). `bundle/` is the tool-neutral master these packs were rendered from —
when changing a skill or agent, edit it there first, then mirror the change into the packs
you ship.

## License

[MIT](LICENSE).
