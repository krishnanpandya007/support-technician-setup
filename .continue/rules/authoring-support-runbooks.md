---
name: authoring-support-runbooks
description: "Use when authoring the diagnostic runbooks a customer-support agent follows at runtime \u2014 turning a product's failure surface, its harness, and its read-only tool catalog into symptom-to-resolution decision trees plus synthetic evaluation tickets, for mandatory human review. Triggers: \"author the support runbooks\", \"build the diagnostic decision trees\", part of setting up a support agent."
alwaysApply: false
---

# Authoring Support Runbooks

## Overview

A runbook is the pre-authored decision tree the runtime support agent follows for one user symptom: it says which checks to run (via the read-only tools), how to branch on the results, and where each branch ends. Runbooks are what make the runtime agent behave like a technician instead of a chatbot, and they are what let a weaker runtime model stay reliable — the judgement is authored here, in advance, so at runtime the model mostly selects among pre-authored branches rather than reasoning open-endedly.

This skill produces, for mandatory human review:

1. an **intent taxonomy / router** that maps an incoming user message to the right runbook;
2. one **runbook** (decision tree) per symptom;
3. a set of **synthetic evaluation tickets** that stand in for the real ticket history that does not exist yet.

**Core principle:** a runbook never invents a resolution. Every branch ends in one of exactly four outcomes, and every check it performs maps to a tool that already exists in the catalog.

## The four terminal outcomes (every branch ends in one)

1. **Self-serve** — answer or instruct the user, grounded in specific knowledge-base articles.
2. **Ask** — request one clarifying detail, then re-enter the tree.
3. **Escalate as unknown** — out of scope or undiagnosable; hand to a human without guessing.
4. **Escalate with a proposed fix** — a change is warranted; emit a structured proposal (what change, on what entity, and why) for a human to approve and the privileged executor to perform. The agent proposes; it never performs the change and must never imply that it did.

## Where runbooks come from (you have no real ticket history)

Because there is no historical ticket data, derive everything from the product itself:

- **The failure surface** — every error return, rejected input, and "entity in a bad state" branch in the backend is a symptom a user can report. Inventory these; each becomes a candidate runbook. (In a schema-blind setup, take these from the harness's plain-language conditions rather than from raw code.)
- **The harness** — supplies the plain-language description of each capability and its conditional behavior, and the articles that self-serve branches cite.
- **The tool catalog** — supplies the checks. Prefer the verdict-returning **checkers**; a runbook step should read a diagnosis, not raw data.

The synthetic evaluation tickets are generated from these same failure modes. Because each ticket is generated from a known failure path, its ground-truth cause and its expected terminal outcome are known by construction — which gives a graded evaluation set with no labelling effort, and bootstraps the dataset you will later replace with real, admin-labelled escalations.

## Design rules for a weaker runtime model

- **Bounded, not open-ended.** Each runbook declares a maximum depth, and every step offers a small, explicit set of next checks drawn from the catalog. The agent chooses among authored options; it does not improvise queries.
- **Every check maps to a real tool.** If a runbook needs a check that no catalog tool provides, do not invent it — record a **tool-catalog gap** for `discovering-support-tools` to fill, and mark the branch as needing that tool.
- **Self-serve branches must be grounded.** Each cites the knowledge-base article(s) it answers from, so the runtime verifier can confirm the answer is supported before it is sent.
- **Conservative escalation.** Escalate only at an unknown or needs-a-change leaf. Below the configured confidence floor, prefer an *ask* over an escalation, so admins are not flooded with low-value tickets.
- **No leaked detail in user-facing text.** Questions and self-serve answers inherit the harness rule: plain language, no technical, internal, or security detail. Never open secret files.

## Schema-exposure mode and connection types carry over

A proposed fix at an *escalate-with-a-proposal* leaf names the change and the entity using the same naming the tool catalog uses, by the tool's `connection_type`:
- **Schema-backed tools (`sql`, schema-backed `nosql`):** use the same `schema_exposure` mode as the catalog; in `aliased`/`blind` mode use the catalog's placeholders (for example `{{bookings}}.{{status_col}}`) and never a real identifier. Final binding happens locally, off-model.
- **`http_api` / `custom` tools:** there are no physical identifiers to expose — reference the tool name and the **logical entity** (e.g. "the user's shipment", "the quota record"), never an endpoint, URL, accessor internals, or a raw identifier.

Either way the runbook only ever *names* checks by catalog tool name; it never embeds a query, request, or accessor — and it never assumes a tool's backend.

## Process

1. **Pick the exposure mode** (inherit from the tool catalog) and confirm the tool catalog and harness are available.
2. **Inventory symptoms** from the failure surface (or, schema-blind, from the harness's conditions). Phrase each as a user would report it.
3. **Build the intent taxonomy / router** — map likely user phrasings for each symptom to its runbook, and record the escalation policy (confidence floor, dedup window, which leaves may escalate). Without ticket history, derive phrasings from the symptom in natural user language.
4. **Author each runbook** — entry checks (mapped to catalog tools), branches on their verdicts, and a terminal outcome for every branch including the "everything is fine" and "cannot tell" cases. Cap the depth. Cite knowledge-base articles on self-serve leaves; attach a structured proposed fix on needs-a-change leaves.
5. **Run the tool-gap check** — every check references an existing tool, or a gap is flagged.
6. **Generate synthetic evaluation tickets** — per symptom, with the ground-truth cause and expected terminal outcome.
7. **Self-review** against the checklist, then **report** for human review.

## Output structure

Into the project's support kit, next to its harness and tools:

```
support-kit/
  runbooks/
    taxonomy.yaml              # intent -> runbook routing + escalation policy
    <symptom>.runbook.yaml     # one decision tree per symptom
    evals/
      <symptom>.tickets.yaml   # synthetic tickets: prompt + ground-truth cause + expected outcome
```

**`taxonomy.yaml`** lists each intent with its trigger phrasings and target runbook, plus an `escalation_policy` block: the `confidence_floor` below which the agent asks rather than escalates, the `dedup_window` that threads a re-asking user into one ticket instead of many, and the set of leaf types permitted to escalate.

**`<symptom>.runbook.yaml`** declares the symptom, its `intent_keys`, a `max_depth`, the entry checks (each naming a catalog tool and the filters it needs — never the scoping identity), and the branches. Each branch states the verdict it matches and its terminal outcome: a self-serve answer with `kb_refs`; an `ask` with a single question; an `escalate_unknown`; or an `escalate_with_proposal` carrying the proposed change (entity, change, reason) using mode-appropriate names. A check's params reference details collected from the user as `{{user_supplied.<slot>}}` (e.g. `{{user_supplied.booking_id}}`, matching the `ask_for_if_missing` slot name) — that exact form is the runtime's substitution contract; a bare `{{booking_id}}` is not guaranteed to be filled.

**`<symptom>.tickets.yaml`** lists synthetic tickets: the user-style prompt, the failure path it was derived from, the ground-truth cause, and the expected terminal outcome (and expected proposal where applicable). **`ground_truth_cause` must begin with the exact verdict token of the branch the ticket exercises** (e.g. `APPROVED (approved 2 days ago, payout still pending)`), because offline evaluation feeds it back as the entry check's verdict and matches branches on that leading token — a prose-only cause matches no branch and the eval silently tests the fallback instead of the intended path. Tickets for `ask`-on-missing-detail paths (no check runs) are the exception.

## Self-review — STOP and fix if any is true

- A branch has no terminal outcome, the tree has no "everything is fine" or "cannot tell" branch, or it exceeds its declared `max_depth`.
- A check step references a tool that is not in the catalog, and the gap was not flagged.
- A self-serve leaf has no knowledge-base grounding.
- The escalation policy escalates on low confidence instead of asking, or any leaf other than unknown / needs-a-change escalates.
- A needs-a-change leaf is phrased as though the agent performs the change itself, rather than proposing it for approval.
- A ticket's `ground_truth_cause` does not start with the verdict token of the branch it exercises, or a check param uses a placeholder form other than `{{user_supplied.<slot>}}`.
- In `aliased`/`blind` mode, a real identifier appears in a proposed fix, or you opened a schema/dump/migration file.
- Any user-facing question or answer leaks technical, internal, or security detail.

## Report back

- The `schema_exposure` mode used.
- Symptoms covered (count) and the intents in the taxonomy.
- Any **tool-catalog gaps** flagged for `discovering-support-tools` to fill.
- Count of synthetic evaluation tickets generated.
- The escalation policy values chosen.
- Any symptom you could not safely cover, as a generic needs-review flag.
