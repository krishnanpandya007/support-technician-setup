# Integrating the support agent into your app

You've built the support kit and set up the read-only database access. This page is about
the last step: putting the assistant **inside your product**, so a signed-in user can open
a chat, get their actual problem diagnosed, and — when something needs changing — have a
ticket land with your team.

The `integrating-support-agent` skill (or the `support-integrator` agent) does the wiring
for you. This page explains what that wiring is, the choices that are yours to make, and
the rules that hold no matter what you choose.

## What integrating means

Your app already knows who the user is — that's the whole trick. Integration adds a small
support endpoint that receives the user's message **together with the proof of identity
your app already produces** (its login session). The assistant then reads only that user's
data through its read-only database login, answers what it can, and raises a ticket to
your team when a change is needed. Nothing about how your app works today changes.

## The non-negotiables

These hold under every setup below. The generated integration is built to keep them true,
and the smoke tests at the end let you verify each one yourself.

1. **The assistant only ever knows who's asking because *your* login system said so.**
   The user's identity comes from your app's verified session — never from anything the
   user, the page, or the model typed.
2. **It holds a read-only database login and nothing stronger.** The admin or service key
   never enters the assistant's process.
3. **The only thing it can *do* is open a ticket.** It has no way to change data, send
   anything on a user's behalf, or call anything with side effects.
4. **No passwords or keys end up in files.** Generated code and docs name the environment
   variables to fill; the values stay in your secrets store.
5. **Every line changed in your app is listed for you to review** — in
   `support-kit/integration/CHANGES.md` — before anything ships.

## Pick the setup that matches how you already work

There is no prescribed topology. Tell the skill which of these matches your practice (or
let it infer and confirm), and it generates the integration for that one.

### A separate small service (the default)

The assistant runs as its own little service next to your app; your app gets one snippet
for the chat box and a few lines that pass the user's session along.

- Almost nothing is added to your app's code.
- Clean separation: the read-only login lives in its own process, deployed and scaled on
  its own.
- One more thing to run and monitor.
- The default when you don't have a strong preference — it's the least invasive.

### A route inside your app

The chat endpoint becomes a route in your existing backend.

- Reuses your login check directly — the simplest identity story.
- No new process, no new network hop; ships with your normal deploys.
- The assistant now runs inside a process that also holds your app's full database
  credentials — the generated code keeps its read-only login in a separate setting, but
  the separation is by discipline, not by process boundary.
- Best when you run a monolith and adding services is a burden.

### A serverless function

The chat endpoint is a function on your existing serverless platform.

- Nothing to keep running; scales to zero.
- Cold starts add latency to the first message.
- Needs care with shared database connections: the generated code applies the user's
  identity per request (not per connection) so one user's identity can never linger into
  another's request — and the cross-user smoke test verifies exactly that.
- Best when the rest of your stack is already serverless.

### Your existing chat or helpdesk system

The assistant plugs into the chat platform your users already write to (the platform owns
the UI; the integration binds the assistant behind it).

- Users stay where they already are; your team's existing routing and escalation apply.
- No widget to add — the platform is the entry point.
- The identity hand-off depends on what that platform can verify about the user; the
  integration refuses to answer when the platform can't establish who's asking.
- Best when support already lives in one tool and you don't want a second inbox.

## The pieces that are the same for everyone

- **The endpoint.** One route (by default `POST /support/chat`) that takes the user's
  message and their session. No valid login, no answer — there is no anonymous mode.
- **The identity hand-off.** One small, reviewable function takes your app's verified
  session and produces the user id; that id is applied to every database read, and a blank
  id reads zero rows.
- **The ticket channel.** When a fix needs a human, the assistant sends a structured
  ticket (what, for which record, why) to the channel you chose — Telegram, email, or
  both — and tells the user plainly that it's been passed to the team. It never claims to
  have fixed anything itself.

## Before going live: run all five smoke tests

The integration ships a checklist (`support-kit/integration/SMOKE.md`) and the scripts to
run it. The skill never runs them — they touch your real database and channels, so you do.

1. **Cross-user check** — signed in as user A, the assistant must see nothing of user B's.
2. **Write rejection** — the assistant's database login must be unable to change anything,
   even when asked to directly.
3. **Ticket fire** — a test escalation must actually arrive in your channel.
4. **No-login check** — a request without a valid session must be refused.
5. **Secret sweep** — no key, password, or connection string ended up in any generated
   file or app change.

## What changes in your app

As little as possible: typically the chat entry point (one include) and the few lines that
pass the session to the endpoint. Every touched file is listed in
`support-kit/integration/CHANGES.md` with the reason for the change — review it like any
other pull request before merging.
