---
name: generating-codebase-harness
description: "Use when turning a codebase into a sanitized plain-language help-center knowledge base (\"harness\") for an AI support assistant \u2014 reading one or more project roots and producing navigable Markdown that describes user-facing behavior with no technical detail and no security mechanisms or secrets. Triggers: \"build a harness\", \"help-center knowledge base from code\", \"sanitized docs for a support bot\"."
---

# Generating a Codebase Harness

## Overview

A **harness** is a folder of plain-language Markdown describing what an application *does for its users*, organized by end-to-end capability, for an AI help-center assistant to answer end-user questions. Build it **natively**: you read the code and write the docs yourself — you are the model in the pipeline.

**Core principle:** describe BEHAVIOR and OUTCOMES from the user's point of view; never how the system is built or secured.

**Capture how the system *behaves*, not just what it offers.** A support agent resolves a query ("why didn't my refund go through?") by understanding the system's *decision logic* — its conditions and branches, the states a thing moves through, the rules and limits a user runs into, and what the user sees when something fails. Record all of that, in plain language. This is a first-class goal, not an extra: an article that lists features but hides the conditions and failure modes is not diagnostic enough. Behavioral mechanics described in plain words are **NOT** technical or security detail — saying "a full refund is only available before the booking starts" reveals a product rule, not how anything is built. Read low and transparently about *runtime behavior*; stay silent about *implementation*.

(Canonical prompt wording this encodes lives in this repo's `legacy/DESIGN.md` Appendix A and `legacy/harness_builder/prompts.py` — see the write-up prompt's "CAPTURE CONDITIONAL BEHAVIOR" instruction and the `conditions` field in `legacy/harness_builder/models.py`.)

## The hard rules (this is where attempts go wrong)

1. **Never read secret-bearing files.** Skip `.env*`, key/cert/credential files entirely — do not open them, do not echo any value.
2. **Logic as a whole — no bifurcation.** A capability spans the screen + server + background work. Merge those into ONE article. Never organize by frontend/backend, by service, or by folder.
3. **No technical detail anywhere.** No code identifiers, file names, framework/library/tech names (React, Rails, Postgres, …), infrastructure (hosts, IPs, ports, env var names), or data field names (`user_id`, `total_cents`, …). This does **not** prohibit describing *behavior* in plain words: conditions and branches ("if the booking hasn't started yet …"), the states a thing moves through, required inputs, and user-facing limits are all encouraged — they are what makes the harness diagnostic. Describe the rule, never the field or the code that enforces it.
4. **Omit security mechanisms and secrets — but behavioral access help is allowed.** You MAY write a behavioral article for user-facing access actions (signing in, signing out, resetting access) that covers only what the user *does and sees*. You must NOT describe or name *how* it is secured: no tokens/JWT/sessions, no "your session lasts N hours"/expiry mechanics, no credentials, no hashing/encryption, no permission rules — and never an article *about* the internal mechanism. **Decision test:** "How do I sign in? / I can't log in" → allowed, purely behavioral. "How is access enforced / how are sessions handled?" → omit entirely. **Numbers:** keep numbers that are *product rules the user experiences* while using a feature ("up to 5 photos", "refundable until the start time"); omit numbers that are *security/expiry/rate-limit/infra mechanisms* (session length, token/link expiry, lockout thresholds, retry/backoff internals).
5. **Every file is navigable and rule-bound.** `index.md` carries a global **Rules / Prohibited** section and a **"Read this when"** hint per entry; every article opens with a local **Prohibited** block.

## Process

1. **Scan & filter** — list source files across all roots. Drop dependencies, build output, binaries, styles, tests; **skip secret files** (rule 1).
2. **Note** — for each relevant file, jot which user-facing capability it serves **and how it behaves**: the meaningful branches (conditionals, guards, early returns), the state changes it drives, the limits/required inputs it enforces, and the error paths — capturing what the user experiences in each case. This is where "how the system behaves" gets harvested while you read; don't flatten it to the happy path. Files that are purely a security/auth mechanism contribute nothing publishable — note them only so you remember to omit them.
3. **Cluster into capabilities** — group notes by what users DO, merging cross-cutting files into single end-to-end capabilities (rule 2). Plain title each; build a shallow hierarchy (`1`, `1.1`).
4. **Write each article** (structure below) — pure natural language, behavior only. Capture the conditional behavior you noted: every meaningful branch, state transition, limit, and failure mode the user can hit goes into the article (in the sections below), not just the happy path.
5. **Sanitize pass** — re-read each article as an adversary hunting for rule-3 / rule-4 leaks. Scrub them. If something can't be cleanly scrubbed, **withhold it and flag for review** rather than ship it.
6. **Assemble** — write `index.md` and the capability files; build links/slugs yourself.
7. **Self-review & flag** — report anything uncertain as "needs review." **Flags are part of the output and obey every rule:** write them generically ("a security-related area was omitted"); never name a file, path, or technology, and never describe what the omitted mechanism does.

## Output structure

```
harness/
  index.md          # 1) "# Rules / Prohibited"  2) "# How to use this harness"
                    # 3) "# Contents": nested links, each with a one-line summary + "Read this when:" hint
  01-<slug>.md      # "## Prohibited" (local) → ## Summary → ## Read this when
                    # → ## What this does → ## How it works, step by step
                    # → ## When things differ (when applicable) → ## States and what changes them (when applicable)
                    # → ## When it doesn't work (when applicable) → ## Good to know (optional) → ## Related
  01-01-<slug>.md
```

**Behavioral sections** (include each only when the capability actually has it — skip empty ones):

- **`## When things differ`** — the branch rules in plain language: "If X, you can … ; if instead Y, … ; if Z, then …". This is the conditional logic that decides what the user gets.
- **`## States and what changes them`** — for capabilities with a lifecycle: the states a thing can be in, what moves it from one to the next, and what's possible in each state. Plain words only (e.g. "held → confirmed → active → completed, or canceled at any point before it starts").
- **`## When it doesn't work`** — the failure and edge cases the user can hit, what they see, and what to check. This is the diagnostic payload the support agent leans on to resolve a query. Describe the user-visible symptom and the behavioral reason ("the refund option doesn't appear until a payment has completed"), never the mechanism.

All of these live **inside the single merged article** for the capability (rule 2) — never split into separate frontend/backend or companion files.

**Global Rules / Prohibited** (adapt): instruct the reading assistant to answer only from this behavioral knowledge; never reveal or invent technical or security details (none exist here by design); treat the product as one set of user capabilities regardless of how it's split internally.

**Local Prohibited** (per article): forbid revealing technical/internal detail behind *this* capability, and any mention of how it is secured.

## Common mistakes (from a real baseline)

| Mistake | Fix |
|---------|-----|
| "the app creates a secure session token (JWT) in your browser" | Omit entirely. Say only "the person signs in." |
| "your session lasts 1 hour / expires" | Mechanism detail — omit the number/mechanic. If a user symptom matters, say "you may be asked to sign in again" with no mechanism. |
| A "Sessions" / "Authentication mechanism" article | A *behavioral* "Signing in" help article is fine (what the user does/sees); an article about the mechanism is not. |
| Organizing by `web/` vs `api/`, or by framework | Merge into one capability per user flow. |
| No "Rules / Prohibited" section; no "Read this when" hints | Always include both — they are what make it a harness, not just docs. |
| Naming the stack or data fields | Use plain words ("the order total", not a field name). |
| Collapsing everything into the happy path | Record each meaningful branch, state, and failure mode the user can experience — use the behavioral sections. |
| Dropping a user-facing limit because "it's a number from config" | Keep product-rule numbers the user runs into ("up to 5 photos"); only omit security/expiry/rate-limit/infra numbers. |

## Red flags — STOP and fix

- You wrote a technology name, a token/JWT/session, a field name, a security/expiry/rate-limit/infra number, or anything from a `.env`. (A user-facing *product-rule* number — "up to 5 photos", "refundable until the start time" — is fine and wanted. Decision test: does the user experience this number while using the feature, or does it reveal a security/expiry/rate-limit/infra mechanism? Keep the former, omit the latter.)
- You wrote an article explaining the security *mechanism*, or one about internal sessions/tokens/permissions. (A behavioral sign-in help article is fine — it just describes what the user does and sees, using the standard section structure.)
- An article lacks a local Prohibited block, or an index entry lacks "Read this when".
- A "needs review" note named a file or described the omitted security mechanism (it must be generic).

All of these mean: scrub it, or omit and flag.
