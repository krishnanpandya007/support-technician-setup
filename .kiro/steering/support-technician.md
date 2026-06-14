# Support Technician

This project ships Support Technician: a staged methodology for turning a codebase
into a read-only customer-support agent (knowledge base, read-only tools + access SQL,
diagnostic runbooks, persona, config scaffolding). The full per-stage instructions live in
AGENTS.md at the repo root. When asked to build or set up a support agent - or any of its
stages - open AGENTS.md and follow the matching section. The safety contract is
non-negotiable: the generated support agent only reads data, and the only action it may
take is escalating a proposed change to a human.

The per-stage procedures are also available as manual steering files in this folder - include the one matching the stage you are running:

- `authoring-support-runbooks.md` - Use when authoring the diagnostic runbooks a customer-support agent follows at runtime — turning a product's failure surface, its harness, and its read-only tool catalog into symptom-to-resolution decision trees plus synthetic evaluation tickets, for mandatory human review.
- `discovering-support-tools.md` - Use when generating the read-only data-access tools a customer-support agent uses to diagnose an end user's live state — reading a project's backend to propose a tool catalog plus the database access SQL (restricted read-only role, column grants, row-level security), for mandatory human review.
- `generating-codebase-harness.md` - Use when turning a codebase into a sanitized plain-language help-center knowledge base ("harness") for an AI support assistant — reading one or more project roots and producing navigable Markdown that describes user-facing behavior with no technical detail and no security mechanisms or secrets.
- `generating-support-persona.md` - Use when generating a project-specific customer-support assistant persona from the reference template — turning a harnessed product's knowledge base and tool catalog into a finished persona.
- `integrating-support-agent.md` - Use to integrate a built support kit into the operator's real end-user app — discovers the app's own serving, session, frontend, and deployment practice and generates, for that practice, the session bridge (verified user id → per-connection identity), the chat serving layer, the end-user entry point, real escalation wiring, deployment glue, and an operator-run smoke-test checklist, all for mandatory human review.
- `setting-up-support-agent.md` - Use to set up a full customer-support agent for a web app — orchestrates the whole build pipeline (knowledge base, read-only tools + access SQL, diagnostic runbooks, persona, config/secrets scaffolding) by running the four stage skills in order, with a human-review gate after each, and hands off the local database step to the support-binder CLI.
- `support-architect.md` - Use to set up a full customer-support agent for a web app — runs the whole build pipeline (knowledge base, read-only tools + access SQL, diagnostic runbooks, persona, config/secrets scaffolding) and reports everything needing review.
- `support-integrator.md` - Use to integrate a built support kit into the operator's end-user app — generates the session bridge, chat serving layer, end-user entry point, escalation wiring, deploy glue, and an operator-run smoke checklist, following the operator's own hosting practice.
