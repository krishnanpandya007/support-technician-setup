---
inclusion: manual
---

# generating-support-persona

> **When to use:** Use when generating a project-specific customer-support assistant persona from the reference template — turning a harnessed product's knowledge base and tool catalog into a finished persona.md for the runtime support agent. Triggers: "generate the support persona", "make the assistant persona for <project>", part of setting up a support agent.

# Generating a Support Persona

## Overview

The runtime customer-support agent loads a **persona** that fixes its identity, voice, and — most importantly — its **safety boundaries**. You produce a project-specific `persona.md` by filling the reference template in `templates/persona.reference.md` from the harnessed product. You are the generator described in that template's GENERATION CONTRACT.

**Core principle:** tone and scope are tailored per project; the safety invariants are copied verbatim and never softened.

## Inputs

- The **end-user** KB harness for the product (`index.md` + articles). *(The end-user app — not an internal/admin tool. If only an admin harness exists, stop and say the end-user harness is required first.)*
- The generated **tool catalog** (for `{{support_scope}}` / `{{key_entities}}`), if available.
- The product's `support.config.yaml` (for `{{user_followup_channel}}`), if available.
- `templates/persona.reference.md` (in this skill folder).

## The hard rules

1. **`[LOCKED]` blocks are copied verbatim.** Privacy/security boundaries, the read-only/escalate-only Actions block, refusals/honesty, and the Never list must appear unchanged in every persona, for every project. Do not reword, soften, merge, or drop them. These are the safety contract.
2. **`{{slots}}` are filled only from real project facts.** Derive each from the inputs per its `<!-- GEN ... -->` note. If a slot can't be grounded, use the conservative fallback in the note — **never invent product facts, amounts, dates, or policies.**
3. **`[TUNABLE]` blocks may be rephrased**, not re-scoped. Adjust register to the domain (casual for consumer apps, formal for finance/health); keep the intent and every bullet.
4. **Strip all `<!-- GEN ... -->` and contract comments** from the generated file. The output is clean Markdown the agent reads.
5. **Scope is end-user only.** `{{support_scope}}` lists user-facing capabilities from the end-user harness — never admin-only or internal capabilities, never how anything is built or secured (inherit the harness's no-technical-detail rule).
6. **Examples read natively.** Regenerate the example phrasings using the product's real domain nouns (from the tool catalog / harness), one per category (resolved, need-info, escalating, don't-know). Use placeholders for specific values, not invented ones.

## Process

1. **Read** the reference template and the GENERATION CONTRACT at its top.
2. **Read** the end-user harness `index.md` (+ a few articles) to extract `{{product_name}}`, `{{user_noun}}`, `{{domain_one_liner}}`, and the capability list for `{{support_scope}}`.
3. **Read** the tool catalog (if present) for `{{key_entities}}`; read `support.config.yaml` for `{{user_followup_channel}}`.
4. **Fill** every `{{slot}}`; **tune** `[TUNABLE]` blocks to the domain register; **copy** `[LOCKED]` blocks verbatim.
5. **Strip** all generation comments.
6. **Self-review** (below), then write `persona.md` and report.

## Output

Write `persona.md` (into the project's support kit, next to its harness). It must contain, in order: Identity → Voice & tone → What you help with → Core behaviors → **Boundaries (LOCKED)** → **Actions (LOCKED)** → **Refusals & honesty (LOCKED)** → Example phrasings → **Never (LOCKED)**.

## Self-review — STOP and fix if any is true

- A `[LOCKED]` block is missing, reworded, softened, or merged.
- A `{{slot}}` survived unfilled, or was filled with an invented fact (a specific amount/date/policy not in the inputs).
- The persona implies the agent can make changes itself (it escalates; the team's system acts).
- `{{support_scope}}` names an admin/internal capability, or any technical/security detail leaked in.
- A `<!-- GEN ... -->` comment or the contract header is still in the file.

## Report back

- Path to the written `persona.md`.
- Which slots were filled and from which input (one line each); any that fell back to defaults.
- Confirmation that every `[LOCKED]` block is present and verbatim.
- A one-line **needs-review** flag for anything you couldn't ground (generic — no invented facts).
