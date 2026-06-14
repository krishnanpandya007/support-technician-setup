---
inclusion: manual
---

# integrating-support-agent

> **When to use:** Use to integrate a built support kit into the operator's real end-user app — discovers the app's own serving, session, frontend, and deployment practice and generates, for that practice, the session bridge (verified user id → per-connection identity), the chat serving layer, the end-user entry point, real escalation wiring, deployment glue, and an operator-run smoke-test checklist, all for mandatory human review. Triggers: "integrate the support agent", "wire the support kit into my app", "add the support chat to <app>", "deploy the support agent".

# Integrating the Support Agent

## Overview

This is the last mile. The earlier stages produce a **support kit**; the operator then runs `support-binder`, applies the access migration, and fills `.secrets` — all locally, off-model. This skill takes that bound kit and the operator's **end-user app** and wires the two together, turning the kit's command-line runtime into a support endpoint real users reach from inside the app.

**Core principle: follow the operator's practice; enforce the invariants regardless of it.** Teams host things differently — a separate small service, a route inside the app, a serverless function, or an existing chat/helpdesk platform. This skill never prescribes a topology. It discovers the app's practice, confirms it, and generates the integration *for that practice* — while the five invariants below hold under every one of them. The repo-root `INTEGRATION.md` is the operator-facing companion: it explains the practices and their trade-offs in plain language; point the operator at it whenever a choice is theirs to make.

**Prerequisites — stop and say so if missing:** a complete support kit (`harness/`, `tools/`, `runbooks/`, `persona.md`, `support.config.yaml`); evidence the operator has done the local DB step (`tools/ACCESS_SETUP.md` and the `*.local.*` artifacts exist — you never read their *contents* beyond confirming presence); and the end-user app's source.

## Universal vs practice-specific

Some artifacts are identical for every practice; generate them always. The rest exist **only for the confirmed practice** — never generate all variants speculatively.

| Always (universal) | Per-practice only |
|---|---|
| Chat endpoint **contract** (request/response + fail-closed rules) | Serving-layer code (service / route / function / platform adapter) |
| Session-bridge **contract** (verified session → user id → per-connection identity) | Session-bridge **implementation** (reuses the app's own verification) |
| Escalation wiring **spec** + fire-test script | End-user entry point (widget snippet / component edit / platform binding) |
| `SMOKE.md` checklist + operator-run scripts | Deployment glue (compose service / route registration / function config / platform setup) |
| `CHANGES.md` review manifest | |

**The chat endpoint contract (fixed):** `POST /support/chat`. Request: `{message, conversation_id?}` plus the app's **own session credential** (cookie, bearer token, or platform identity — exactly the mechanism the app already uses). The request body **never** carries a user id. Response: `{reply, escalated, ticket_ref?}`. No valid session ⇒ `401` and no model call — fail closed, never an anonymous fallback.

**The session-bridge contract (fixed):** one small, reviewed function — `verify_session(request) → user_id` — that *reuses* the app's existing verification (its middleware, JWT library, or auth SDK; never a parallel auth path). Its return value is the **only** source of the identity the runtime sets per connection (`SET app.current_user_id`, or the platform's native equivalent such as `auth.uid()`). This replaces the kit runtime's session stub.

## Decisions to gather first (ask only what you cannot infer)

1. **Topology practice** — `sidecar` (separate small service) | `in_app` (route inside the app) | `serverless` (function) | `chat_infra` (existing chat/helpdesk platform). Infer candidates from the app's repo — a Dockerfile/compose file suggests sidecar fits naturally; a monolith framework suggests an in-app route; a `serverless.yml`/functions directory suggests a function — then **confirm with the operator** (see `INTEGRATION.md` for the trade-offs). Default when unanswerable: **sidecar**, the least invasive (no app code beyond the entry point and bridge).
2. **Session mechanism** — how the app already verifies a signed-in user: cookie session, JWT, an auth provider SDK, framework middleware. Read the app's auth code to find the verification call to reuse. Never invent a parallel auth path, and never accept a client-supplied user id as the identity.
3. **End-user entry point** — an embeddable chat widget (default: a dependency-free JS snippet), an edit to an existing frontend component, or none (`chat_infra` practice, where the platform owns the UI).
4. **Escalation channel readiness** — does a Telegram bot / SMTP relay already exist (env-var names only — never values), or must provisioning steps be added to the operator checklist?
5. **Deployment practice** — how the operator deploys today (compose, Kubernetes, a PaaS, the app's own pipeline). The glue follows *their* practice; never introduce a new platform.

## Process

1. **Discover the app stack** — framework, auth middleware, route layout, frontend, deploy artifacts. Skip `.env*`, keys, certs entirely.
2. **Confirm the practice** — state what you inferred and which of the five decisions need the operator. Run interactively: pause for answers. Run as the `support-integrator` subagent (which cannot pause): take the decisions as inputs; where one is missing, use the least-invasive default and **flag the assumption**.
3. **Session bridge** — generate `verify_session` for the app's real mechanism, wired so its output is the only value that ever reaches the per-connection identity. Unset/invalid ⇒ refuse the request. This supersedes the kit runtime's session stub.
4. **Serving layer** — the chat endpoint for the confirmed practice, wrapping the kit's runtime (persona + read-only tools + advisory runbooks + `escalate_to_human`). Its process reads **only** the read-path env names from `.secrets` (the read-only DB URL, the LLM key, the channel tokens).
5. **End-user entry point** — generate the widget/snippet, or the minimal component edit, or the platform binding. All user-visible text inherits the kit's no-leak contract: plain language, no technical, internal, or security detail.
6. **Escalation wiring** — replace the kit's channel stubs with real transports (Telegram send, SMTP send) reading env names only, plus a small fire-test script the operator runs to confirm a test ticket arrives.
7. **Deployment glue** — for the operator's practice: a compose service entry, a route registration, a function config, or platform setup steps. Serverless/pooled connections **must** set the per-request identity with `SET LOCAL` inside a transaction (or use a per-request connection) — a pooled connection that keeps a previous user's identity is a cross-user leak; the smoke test below is the backstop.
8. **Smoke checklist** — write `SMOKE.md` and runnable scripts for the five go-live checks: cross-user isolation, write rejection, escalation fire, no-session refusal, secret sweep. **You never run them** — they touch the live database and channels; only the operator does.
9. **Review gate** — write `CHANGES.md`: every file in the operator's app you touched, with a one-line rationale and a summary of the edit. Nothing ships without the operator reviewing it.

## Hard rules — the invariants (every practice, no exceptions)

1. **The acting user id comes only from the app's verified session.** Never from the model, a request body, a query param, or any client-supplied field. The only path is `verify_session(request) → user_id → per-connection identity`.
2. **The serving process holds only the read-only credential.** Never an admin, service, or write-capable key. If the practice co-locates the agent with the app (`in_app`), the read-only URL still lives in its own env var and the privileged write path stays a separate process.
3. **Escalate-to-human remains the only side effect.** The serving layer adds no new action surface — no write endpoint, no admin call, no data mutation.
4. **No secrets in files.** Env-var names only, in code and docs alike. Never open `.env*`, keys, or certs.
5. **Every change to the operator's app is minimal and listed.** The smallest diff that wires the integration, each file recorded in `CHANGES.md` for review.

And the house rules: **never connect to anything** — no database, no API, no channel; the smoke tests are generated for the operator, never executed by you. **No leaked detail** in anything an end user can see.

## Output structure

```
support-kit/
  integration/
    INTEGRATION_PLAN.md          # discovered stack, confirmed practice, decisions (incl. defaults assumed)
    contracts/
      chat.contract.md           # the universal endpoint contract + fail-closed rules
      session-bridge.md          # the universal verified-session → identity contract
    serving/                     # practice-specific: the chat endpoint wrapping the kit runtime
    entrypoint/                  # widget.js + snippet, or the component-edit spec, or platform binding
    escalation/                  # real channel transports + fire-test script
    deploy/                      # glue for the operator's own deploy practice
    SMOKE.md                     # the five go-live checks — OPERATOR-RUN ONLY
    smoke/                       # the runnable check scripts
    CHANGES.md                   # every app file edited — HUMAN REVIEW REQUIRED
  support.config.yaml            # gains an additive, no-secrets `integration:` block
<operator's app>                 # minimal wiring edits only, each listed in CHANGES.md
```

**`integration:` block in `support.config.yaml`** (additive, no secrets):
```yaml
integration:
  practice: sidecar              # sidecar | in_app | serverless | chat_infra
  endpoint: /support/chat
  entrypoint: widget             # widget | component | none
  deploy: compose                # whatever the operator's practice is
```

## Self-review — STOP and fix if any is true

- A user/owner id can reach the runtime from anywhere other than `verify_session` (a request field, a widget parameter, a default).
- The serving process's configuration names an admin/service/write credential, or a secret **value** appears in any generated file or app edit.
- Any side-effecting tool, endpoint, or call exists beyond `escalate_to_human`.
- An app file was changed but is missing from `CHANGES.md`, or an edit is larger than the wiring strictly requires.
- More than one practice's serving/entry-point/deploy artifacts were generated.
- A pooled/serverless connection sets the identity without `SET LOCAL` (or a per-request connection).
- Any end-user-visible text leaks technical, internal, or security detail.
- You connected to a database, API, or channel; ran a migration or a smoke test; or opened a secret file.
- A practice decision was assumed without being flagged.

## Report back

- The **practice** integrated for, and which of the five decisions were operator-confirmed vs defaulted (flag every default).
- Paths written under `integration/`, one line each.
- The complete `CHANGES.md` list of app edits, restated for review.
- The smoke checklist location, with the explicit line: **the operator must run all five checks before go-live; this skill has run none of them.**
- One line per invariant confirming how the generated integration upholds it.
- Anything left unwired (e.g. an escalation channel not yet provisioned) as a needs-review flag.
