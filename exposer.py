"""Expose a generated support kit as a fully-traced, mocked support agent (litellm/NVIDIA-backed).

Where legacy/exposer.py was a single-shot Q&A over a harness folder, this drives the whole
*orchestration* a support kit produces — persona, the tool catalog, the intent taxonomy, and the
verdict-branching runbooks — against a MOCKED user and MOCKED tools, printing a detailed trace of
every step: which LLM call was made, which reference file was sent into context, which tool was
called (and the SQL template behind it), and how the runbook branched to a resolution or escalation.

There is no live database (schema_exposure is 'blind' and the SQL is placeholder {{...}}), so tools
are mocked: in a ticket-seeded run a checker returns the ticket's ground-truth verdict; in free chat
it returns the runbook's happy-path verdict; fetchers return a synthesized record. The point is to
exercise and visualise the decision flow, not to read real data.

Two brains (--brain):
  agent  (default) — the LLM diagnoses by freely calling the read-only tools and composes the
          answer. It cannot change anything (no write tool exists) and the scoped read-only RLS
          DB role is the hard backstop; the only way to "act" is the explicit, loudly-logged
          escalate_to_human tool. Handles open-ended queries ("show me my recent activity").
  walker  — the original DETERMINISTIC runbook tree: runbooks decide which tool runs and which
          verdict maps to which outcome. Auditable and replayable; the LLM is used only for intent
          routing, persona-voice phrasing, and the simulated user. (Forced when --no-llm.)

Model: discovered live from your NVIDIA key's /v1/models endpoint (or pass --model). Works with any
litellm provider, but defaults to nvidia_nim/<model> + $env:NVIDIA_NIM_API_KEY (legacy convention).

Tools are MOCKED by default. With --live they instead run real read-only SQL against your
own Postgres via a scoped read-only role, pinned to an acting user by an RLS GUC — so the
agent only ever sees that user's rows. Live mode needs a fully bound kit (real names in
bindings.local.yaml plus the query files) and your own read-only connection.

Usage (PowerShell):
    $env:NVIDIA_NIM_API_KEY = "nvapi-..."
    py -3 exposer.py --list-models
    py -3 exposer.py <support-kit> --mode sim --scenario <scenario-id>
    py -3 exposer.py <support-kit> --mode sim --intent <intent-key>
    py -3 exposer.py <support-kit> --mode chat
    py -3 exposer.py <support-kit> --mode sim --scenario <scenario-id> --no-llm   # fully offline
    py -3 exposer.py <support-kit> --live --mode sim --intent <intent-key> --user-id <uuid>
    py -3 exposer.py <support-kit> --live --mode chat --user-id <uuid>            # real DB
"""
import argparse, glob, json, os, random, re, sys, time
import urllib.request
from dataclasses import dataclass, field

# ── stdout: force UTF-8 so the box-drawing/arrow glyphs in personas & runbooks don't
#    crash a redirected run on Windows (cp1252). Harmless elsewhere. ────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Silence litellm's noisy import-time warnings (botocore/sagemaker preload) before it loads.
import logging
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
os.environ.setdefault("LITELLM_LOG", "ERROR")

DEFAULT_KIT = os.environ.get("SUPPORT_KIT_DIR", "support-kit")
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
FALLBACK_MODELS = ["meta/llama-3.1-70b-instruct", "meta/llama-3.3-70b-instruct",
                   "meta/llama-3.1-8b-instruct"]

# Provider prefix -> env var litellm reads for that provider's key. Fail fast with a
# clear message instead of a cryptic auth error deep in litellm (same as legacy).
PROVIDER_KEYS = {"groq/": "GROQ_API_KEY", "nvidia_nim/": "NVIDIA_NIM_API_KEY"}

DECISIVE = {"self_serve", "escalate_with_proposal", "escalate_unknown"}


# ══════════════════════════════════════════════════════════════════════════════════
# Vendored retry  (copied verbatim from legacy/harness_builder/llm.py — that package is
# no longer importable from the repo root, and this script must stay self-contained).
# ══════════════════════════════════════════════════════════════════════════════════
_TRANSIENT = ("rate limit", "ratelimit", "429", "overloaded", "503", "502", "504",
              "timed out", "timeout", "temporarily", "connection reset",
              "connection aborted", "too many requests")
_RETRY_AFTER = re.compile(r"try again in ([0-9.]+)\s*s|retry[- ]after[\":\s]+([0-9.]+)", re.I)


def with_retry(call, *, retries: int = 8, base_delay: float = 1.0, max_delay: float = 30.0,
               sleep=time.sleep):
    """Call ``call()``, retrying transient errors with backoff; re-raise real bugs fast."""
    for attempt in range(retries + 1):
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - we re-raise unless transient
            msg = str(e).lower()
            if attempt >= retries or not any(t in msg for t in _TRANSIENT):
                raise
            m = _RETRY_AFTER.search(msg)
            if m:
                delay = float(m.group(1) or m.group(2))
            else:
                delay = base_delay * (2 ** attempt)
                if any(t in msg for t in ("429", "too many requests", "rate limit", "ratelimit")):
                    delay = max(delay, 5.0 * (attempt + 1))
            sleep(min(delay, max_delay) + random.uniform(0, 0.75))


# ══════════════════════════════════════════════════════════════════════════════════
# Tracer — the core deliverable: every line answers one of "which LLM call / which
# reference file / which tool / which branch".
# ══════════════════════════════════════════════════════════════════════════════════
_C = {"route": "\033[36m", "llm": "\033[35m", "tool": "\033[33m", "ref": "\033[32m",
      "esc": "\033[31m", "user": "\033[34m", "agent": "\033[1m", "info": "\033[2m",
      "out": "\033[1;32m", "bad": "\033[1;31m", "reset": "\033[0m", "bar": "\033[1;36m"}


class Tracer:
    def __init__(self, *, color=True, verbose=False):
        self.verbose = verbose
        self.color = color and sys.stdout.isatty()
        if self.color and os.name == "nt":
            os.system("")  # enable ANSI VT processing on Windows 10+

    def _c(self, key, s):
        return f"{_C[key]}{s}{_C['reset']}" if self.color else s

    def _line(self, key, tag, body):
        print(f"{self._c(key, tag)} {body}")

    @staticmethod
    def _clip(s, n=320):
        s = " ".join(str(s).split())
        return s if len(s) <= n else s[: n - 1] + "…"

    def section(self, title):
        print()
        print(self._c("bar", "═" * 78))
        print(self._c("bar", f"  {title}"))
        print(self._c("bar", "═" * 78))

    def route(self, model, intent, runbook, *, how):
        self._line("route", "[ROUTE]", f"{how} ({model or 'keyword'}) -> intent={intent} -> {runbook}")

    def llm(self, model, n_msgs, ref_files, usage, content):
        refs = ",".join(ref_files) if ref_files else "-"
        self._line("llm", "[LLM]  ",
                   f"{model}  msgs={n_msgs}  refs=[{refs}]  tokens={usage}")
        if content:
            shown = content if self.verbose else self._clip(content)
            print(f"        {self._c('info', '↳ ' + shown)}")

    def tool(self, name, args, query_ref, result, source):
        self._line("tool", "[TOOL] ", f"{name}  args={json.dumps(args, ensure_ascii=False)}")
        print(f"        query_ref={query_ref}")
        print(f"        result={self._clip(result, 160)}  source={source}")

    def ref(self, filename, present):
        if present:
            self._line("ref", "[REF]  ", f"{filename}")
        else:
            self._line("bad", "[REF]  ", f"{filename}  (MISSING — returning stub)")

    def escalate(self, proposal):
        ent = proposal.get("entity", "?")
        chg = proposal.get("change", "?")
        self._line("esc", "[ESCAL]", f"entity={ent}  change={self._clip(chg, 140)}")
        if proposal.get("reason"):
            print(f"        reason={self._clip(proposal['reason'], 200)}")

    def user(self, text, *, sim=False):
        self._line("user", "[USER] ", ("(sim) " if sim else "") + self._clip(text))

    def agent(self, text):
        self._line("agent", "[AGENT]", self._clip(text))

    def info(self, text):
        self._line("info", "[INFO] ", text)

    def out(self, outcome, result):
        self._line("out", "[OUT]  ",
                   f"{outcome}  tools={result.tool_calls}  kb_refs={result.kb_refs}"
                   + (f"  proposal={result.proposal_entity}" if result.proposal_entity else ""))


# ══════════════════════════════════════════════════════════════════════════════════
# Kit loading
# ══════════════════════════════════════════════════════════════════════════════════
@dataclass
class Kit:
    kit_dir: str
    harness_dir: str
    persona_text: str
    tools_by_name: dict
    intent_to_runbook: dict
    runbooks: dict          # filename -> parsed runbook
    scenarios: list         # flattened eval tickets, each tagged with intent_key
    config: dict


def _load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_kit(kit_dir, harness_dir=None) -> Kit:
    if not os.path.isdir(kit_dir):
        raise SystemExit(f"not a support-kit folder: {kit_dir}")

    with open(os.path.join(kit_dir, "persona.md"), encoding="utf-8") as f:
        persona_text = f.read()

    catalog = _load_yaml(os.path.join(kit_dir, "tools", "catalog.yaml")) or {}
    tools_by_name = {t["name"]: t for t in catalog.get("tools", [])}

    taxonomy = _load_yaml(os.path.join(kit_dir, "runbooks", "taxonomy.yaml")) or {}
    intent_to_runbook = {i["intent_key"]: i["target_runbook"] for i in taxonomy.get("intents", [])}

    runbooks = {}
    for path in glob.glob(os.path.join(kit_dir, "runbooks", "*.runbook.yaml")):
        runbooks[os.path.basename(path)] = _load_yaml(path)

    scenarios = []
    for path in glob.glob(os.path.join(kit_dir, "runbooks", "evals", "*.tickets.yaml")):
        intent_key = os.path.basename(path).replace(".tickets.yaml", "")
        data = _load_yaml(path) or {}
        for t in data.get("tickets", []):
            t["intent_key"] = intent_key
            scenarios.append(t)

    config = _load_yaml(os.path.join(kit_dir, "support.config.yaml")) or {}
    harness_dir = harness_dir or config.get("harness") or ""
    # A relative harness path historically resolved from the cwd; generated configs write
    # it relative to the kit's parent (the app root). Try the cwd reading first, then the
    # kit's parent, then inside the kit itself — so the kit works wherever it's run from.
    if harness_dir and not os.path.isabs(harness_dir) and not os.path.isdir(harness_dir):
        for base in (os.path.dirname(os.path.abspath(kit_dir)), os.path.abspath(kit_dir)):
            candidate = os.path.join(base, harness_dir)
            if os.path.isdir(candidate):
                harness_dir = candidate
                break

    return Kit(kit_dir, harness_dir, persona_text, tools_by_name,
               intent_to_runbook, runbooks, scenarios, config)


def query_ref_for(kit, tool_name):
    return (kit.tools_by_name.get(tool_name) or {}).get("query_ref", "?")


def tier_for(kit, tool_name):
    return (kit.tools_by_name.get(tool_name) or {}).get("tier", "fetcher")


def load_harness_article(kit, filename, tracer):
    path = os.path.join(kit.harness_dir, filename)
    if os.path.isfile(path):
        tracer.ref(filename, True)
        with open(path, encoding="utf-8") as f:
            return f.read()
    tracer.ref(filename, False)
    return f"(article {filename} not found in harness — no content available)"


# ══════════════════════════════════════════════════════════════════════════════════
# NVIDIA model discovery
# ══════════════════════════════════════════════════════════════════════════════════
def list_nvidia_models(api_key):
    """GET /v1/models from the NVIDIA API (OpenAI-compatible). Returns sorted ids, or []."""
    req = urllib.request.Request(f"{NVIDIA_BASE}/models",
                                 headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        return sorted(m["id"] for m in data.get("data", []) if m.get("id"))
    except Exception as e:
        print(f"(could not list models from NVIDIA API: {e})", file=sys.stderr)
        return []


def normalize_model(model_id):
    """Map a bare NVIDIA model id to a litellm route; leave provider-prefixed ids alone."""
    if "/" in model_id and model_id.split("/", 1)[0] in ("nvidia_nim", "groq", "openai", "anthropic"):
        return model_id
    return "nvidia_nim/" + model_id.removeprefix("nvidia_nim/")


def resolve_model(args):
    if args.model:
        return normalize_model(args.model)
    key = os.environ.get("NVIDIA_NIM_API_KEY", "")
    ids = list_nvidia_models(key) if key else []
    if ids and sys.stdin.isatty():
        instruct = [m for m in ids if "instruct" in m.lower()] or ids
        print("\nAvailable NVIDIA models (instruct):")
        for i, m in enumerate(instruct[:40], 1):
            print(f"  {i:>2}. {m}")
        raw = input("Pick a model number (Enter for #1): ").strip()
        chosen = instruct[(int(raw) - 1) if raw.isdigit() and int(raw) >= 1 else 0]
        return normalize_model(chosen)
    chosen = (ids[0] if ids else FALLBACK_MODELS[0])
    print(f"(using model: {chosen})")
    return normalize_model(chosen)


# ══════════════════════════════════════════════════════════════════════════════════
# LLM edge calls
# ══════════════════════════════════════════════════════════════════════════════════
def _usage_str(usage):
    if usage is None:
        return "?"
    g = (lambda k: getattr(usage, k, None) if not isinstance(usage, dict) else usage.get(k))
    return f"{g('prompt_tokens')}+{g('completion_tokens')}={g('total_tokens')}"


def llm_call(messages, *, model, tracer, ref_files=None, max_tokens=512):
    import litellm
    import logging
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    litellm.suppress_debug_info = True

    def _call():
        resp = litellm.completion(model=model, messages=messages, timeout=120, max_tokens=max_tokens)
        content = (resp["choices"][0]["message"]["content"] or "").strip()
        tracer.llm(model, len(messages), ref_files, _usage_str(getattr(resp, "usage", None)), content)
        return content

    return with_retry(_call)


NO_PHRASE = False  # set by --no-phrase; routing/user-sim still use the model, only phrasing is skipped

# Internal doc references ("(article 21)", "article 06") are meaningless to end users —
# the persona forbids that kind of jargon. Strip them from anything shown to the user.
_ARTICLE_RE = re.compile(r"\s*\(?\barticles?\s+\d+[a-z]?(?:\s*(?:,|and|&)\s*\d+[a-z]?)*\)?", re.I)
_RESOLVED = ("showing now", "now showing", "shows now", "works now", "working now", "it works",
             "resolved", "fixed", "sorted", "all good", "it's there", "its there", "appeared",
             "is showing", "good now", "no longer an issue", "that worked", "it did")
_UNRESOLVED = ("still", "not working", "doesn't", "does not", "didn't", "did not", "nope",
               "same problem", "full price", "not showing", "won't", "can't", "cannot", "isn't")


def strip_refs(text):
    """Remove internal 'article NN' references and collapse whitespace for user-facing text."""
    return " ".join(_ARTICLE_RE.sub("", text or "").split())


def render_result(text, result):
    """Substitute {{result}} / {{result.field}} in a runbook message with the live tool result,
    so listing/fetch answers can put real data in the reply (diagnosis messages have no such tokens)."""
    if not text or "{{" not in text:
        return text

    def repl(m):
        key = m.group(1)
        if isinstance(result, dict):
            return str(result.get(key, "")) if key else str(result.get("summary", result))
        return "" if key else str(result or "")

    return re.sub(r"\{\{\s*result(?:\.([a-zA-Z_]+))?\s*\}\}", repl, text)


def looks_resolved(text):
    """True only when the user clearly signals the issue is fixed (drives dynamic follow-ups)."""
    t = (text or "").lower()
    return any(p in t for p in _RESOLVED) and not any(u in t for u in _UNRESOLVED)


def phrase_reply(kit, canned, *, conv, model, tracer, mode, ref_texts=None, ref_files=None):
    """Rewrite the runbook's canned message in the persona voice. Pass-through when offline."""
    canned = strip_refs(canned)
    if mode == "offline" or not model or NO_PHRASE:
        return canned
    context = ""
    if ref_texts:
        joined = "\n\n".join(t[:4000] for t in ref_texts)
        context = f"\n\nRelevant knowledge-base article(s) you may quote from:\n{joined}"
    system = (kit.persona_text
              + "\n\n---\nYou are replying to the user right now. Rewrite the DRAFT reply below in "
                "your own voice. Preserve every fact exactly; do not add facts, dates, or amounts that "
                "are not in the draft. Never mention internal article numbers or document references — "
                "if the draft points to one, describe the relevant app screen in plain words instead. "
                "Keep it concise and warm." + context)
    transcript = "\n".join(f"{r}: {t}" for r, t in conv[-4:])
    user = f"Conversation so far:\n{transcript}\n\nDRAFT reply to rewrite:\n{canned}"
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        return llm_call(msgs, model=model, tracer=tracer,
                        ref_files=(["persona.md"] + (ref_files or [])), max_tokens=512)
    except Exception as e:
        tracer.info(f"phrasing failed ({e}); using canned message")
        return canned


def ask_user_sim(scenario, agent_question, *, conv, model, tracer):
    """An LLM role-plays the user, answering the agent's follow-up question."""
    bid = scenario.get("booking_id") or scenario.get("id", "")
    system = ("You are role-playing a customer of the product who has contacted support. "
              f"Your situation: {scenario.get('prompt', '')} "
              f"Background: {scenario.get('failure_path', '')}. "
              f"If asked for a reference/booking ID, yours is {bid}. "
              "Answer the support agent's question briefly and naturally, in first person, as the "
              "customer only. One or two sentences. Do not break character or mention you are an AI.")
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": f"The support agent says: {agent_question}"}]
    try:
        reply = llm_call(msgs, model=model, tracer=tracer, ref_files=None, max_tokens=256)
    except Exception:
        reply = str(bid)
    tracer.user(reply, sim=True)
    return reply


# ══════════════════════════════════════════════════════════════════════════════════
# Routing
# ══════════════════════════════════════════════════════════════════════════════════
def keyword_route(kit, msg):
    """Substring/word-overlap fallback over taxonomy trigger phrases. Ties on matched-word
    count are broken by how distinctive the matched words are: a word used by only one
    intent's phrasings ("rejected") outweighs one shared by many ("refund"), so the winner
    isn't just whichever intent comes first in the taxonomy."""
    import yaml  # taxonomy already parsed into intent_to_runbook; re-read phrases lazily
    tax_path = os.path.join(kit.kit_dir, "runbooks", "taxonomy.yaml")
    with open(tax_path, encoding="utf-8") as f:
        tax = yaml.safe_load(f)
    intents = tax.get("intents", [])

    def words_of(phrase):
        return [w for w in re.findall(r"[a-z']+", phrase.lower()) if len(w) > 3]

    intents_using = {}  # word -> how many intents' phrasings use it (1 = distinctive)
    for intent in intents:
        for w in {w for ph in intent.get("trigger_phrases", []) for w in words_of(ph)}:
            intents_using[w] = intents_using.get(w, 0) + 1

    low = msg.lower()
    best, best_score = "unknown", (0, 0.0)
    for intent in intents:
        for phrase in intent.get("trigger_phrases", []):
            p = phrase.lower()
            if p.startswith("("):  # the catch-all placeholder
                continue
            if p in low or low in p:
                score = (10, 0.0)
            else:
                hits = [w for w in words_of(p) if w in low]
                score = (len(hits), sum(1.0 / intents_using[w] for w in hits))
            if score > best_score:
                best, best_score = intent["intent_key"], score
    return best if best_score[0] >= 2 else "unknown"


def route_intent(kit, msg, *, model, tracer, mode):
    if mode == "offline" or not model:
        intent = keyword_route(kit, msg)
        runbook = kit.intent_to_runbook.get(intent, "escalate_unknown.runbook.yaml")
        tracer.route(None, intent, runbook, how="keyword")
        return intent, runbook

    keys = list(kit.intent_to_runbook.keys())
    system = ("You are an intent router for the product's support agent. Classify the user's "
              "message into exactly one of these intent keys:\n- " + "\n- ".join(keys)
              + "\nIf nothing fits, use 'unknown'. Respond with ONLY a JSON object: "
                '{"intent_key": "<one of the keys>"}.')
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": msg}]
    try:
        raw = llm_call(msgs, model=model, tracer=tracer, ref_files=["taxonomy.yaml"], max_tokens=64)
        m = re.search(r'"intent_key"\s*:\s*"([a-z_]+)"', raw)
        intent = m.group(1) if m else "unknown"
    except Exception as e:
        tracer.info(f"routing LLM failed ({e}); falling back to keyword match")
        intent = keyword_route(kit, msg)
    if intent not in kit.intent_to_runbook:
        intent = "unknown"
    runbook = kit.intent_to_runbook.get(intent, "escalate_unknown.runbook.yaml")
    tracer.route(model, intent, runbook, how="llm")
    return intent, runbook


# ══════════════════════════════════════════════════════════════════════════════════
# Mocked tool layer  (no DB exists; the SQL is never executed)
# ══════════════════════════════════════════════════════════════════════════════════
def clean_verdict(s):
    """'NO_REFUND (booking is cancelled)' -> 'NO_REFUND'."""
    return re.sub(r"\s*\(.*\)\s*$", "", str(s)).strip()


def synth_fetcher_record(tool_name, scenario):
    """Synthesize only the fields a downstream verdict_match / {{result}} reads."""
    # offline stand-in for a listing fetch — kits name these list_* or get_my_*
    if tool_name.startswith(("list_", "get_my_")):
        return {"count": 2, "status": "confirmed",
                "summary": "#1001 [confirmed]; #1002 [pending]  (sample offline data)"}
    hint = ""
    if scenario:
        hint = (scenario.get("failure_path", "") + " " + scenario.get("ground_truth_cause", "")).lower()
    status = "cancelled" if "cancel" in hint else "confirmed"
    return {"status": status,
            "summary": f"sample record #1001 — status {status} (offline mock)"}


def mock_tool(kit, tool_name, args, *, scenario, mode, tracer, default_verdict=None):
    """Returns (result, is_record). Checker -> verdict string; fetcher -> record dict."""
    query_ref = query_ref_for(kit, tool_name)
    tier = tier_for(kit, tool_name)
    if tier == "checker":
        if scenario and scenario.get("ground_truth_cause"):
            verdict, source = clean_verdict(scenario["ground_truth_cause"]), "ground_truth"
        elif default_verdict:
            verdict, source = default_verdict, "static"
        else:
            verdict, source = "UNKNOWN", "static"
        tracer.tool(tool_name, args, query_ref, verdict, source)
        return verdict, False
    # fetcher
    record = synth_fetcher_record(tool_name, scenario)
    source = "synth" if scenario else "static"
    tracer.tool(tool_name, args, query_ref, record, source)
    return record, True


# ══════════════════════════════════════════════════════════════════════════════════
# Live tool layer — real read-only SQL via the kit's scoped read-only role. Tools run
# through the generic, kit-driven executor (runtime_exec): the kit's query files, with
# names bound from bindings.local.yaml. RLS + the app.current_user_id GUC keep every read
# to the acting user. There is no built-in example schema — bring your own bound kit + DB.
# ══════════════════════════════════════════════════════════════════════════════════


class LiveDB:
    """A single read-only connection, pinned to the acting user via the RLS GUC."""
    def __init__(self, url, user_id, tracer):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        self.user_id = user_id
        with self.conn.cursor() as cur:
            cur.execute("select set_config('app.current_user_id', %s, false)", (user_id,))
        tracer.info(f"live DB connected as support_agent_ro; acting user_id={user_id}")

    def one(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            if cur.description is None:
                return None
            rows = cur.fetchall()
            return rows[0] if rows else None

    def all(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass



def _redact(url):
    return re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", url or "")


def load_secret(kit_dir, key):
    path = os.path.join(kit_dir, ".secrets")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and line.split("=", 1)[0].strip() == key:
                val = line.split("=", 1)[1].strip()
                return val or None
    return None


def resolve_db_url(args, kit):
    url = (args.db_url or os.environ.get("SUPPORT_READONLY_DB_URL")
           or load_secret(kit.kit_dir, "SUPPORT_READONLY_DB_URL"))
    if not url:
        raise SystemExit("--live needs a read-only DB URL: pass --db-url, set "
                         "SUPPORT_READONLY_DB_URL, or put it in <kit>/.secrets.")
    return url


def resolve_user_id(args):
    if not args.user_id:
        raise SystemExit("--live needs --user-id <uuid>: the user to act as (row-level "
                         "security scopes every read to that user).")
    return args.user_id


# ══════════════════════════════════════════════════════════════════════════════════
# Deterministic runbook-walker
# ══════════════════════════════════════════════════════════════════════════════════
@dataclass
class RunResult:
    tool_calls: list = field(default_factory=list)
    kb_refs: list = field(default_factory=list)
    terminal_outcome: str = None
    proposal_entity: str = None
    proposal_change: str = None
    final_message: str = None


@dataclass
class Ctx:
    kit: Kit
    runbook: dict
    scenario: dict
    conv: list
    model: str
    tracer: Tracer
    mode: str
    result: RunResult
    slots: dict
    max_depth: int
    live_db: object = None     # LiveDB when --live, else None (mocked tools)
    last_result: object = None # most recent tool result, for {{result.*}} message templating


def resolve_params(params, slots):
    """Fill a runbook step's params from the collected slots. Accepts the documented
    ``{{user_supplied.x}}`` form and the bare ``{{x}}`` shorthand kits often write; a bare
    token with no matching slot is left as-is (it may be someone else's placeholder)."""
    def fill(m):
        prefixed, key = m.group(1), m.group(2)
        if key in slots:
            return str(slots[key])
        return "" if prefixed else m.group(0)

    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, str):
            v = re.sub(r"\{\{\s*(user_supplied\.)?([a-zA-Z_]+)\s*\}\}", fill, v)
        out[k] = v
    return out


def eval_match(expr, record):
    """Evaluate a runbook verdict_match against a fetched record (no Python eval()).

    Handles: 'any', 'not_found', 'field == value', 'field != value', and
    'field == a OR b'. Freeform/descriptive expressions return False (they fall
    through to an 'any' catch-all or the escalate fallback)."""
    e = expr.strip().lower()
    if e.startswith("any"):
        return True
    if e == "not_found":
        return (not record) or str(record.get("status", "")).lower() in ("", "not_found", "none")
    for op in ("==", "!="):
        if op in expr:
            left, right = (s.strip() for s in expr.split(op, 1))
            val = record.get(left)
            if val is None:
                return op == "!="
            options = [v.strip().lower() for v in re.split(r"\bor\b", right, flags=re.I)]
            hit = str(val).lower() in options
            return hit if op == "==" else not hit
    return False


def select_branch(branches, result, is_record):
    """Pick the branch matching a tool result, honoring both `verdict:` (checker
    verdict string) and `verdict_match:` (expression over a fetched record)."""
    if not is_record:                                  # checker verdict string
        b = match_branch(branches, result)             # exact then prefix on 'verdict'
        if b:
            return b
    else:                                              # fetcher record
        for b in branches:
            if "verdict_match" in b and eval_match(b["verdict_match"], result):
                return b
    for b in branches:                                 # 'any' catch-all, either style
        if str(b.get("verdict_match", "")).strip().lower().startswith("any"):
            return b
    return None


def llm_pick_branch(branches, result, conv, *, model, tracer):
    """When branches carry descriptive (non-evaluable) verdict_match conditions, let the model
    pick the runbook author's intended branch given the real tool result + the conversation."""
    cands = [(i, b) for i, b in enumerate(branches or []) if b.get("verdict") or b.get("verdict_match")]
    if not cands:
        return None
    lines = [f"{i}: when [{b.get('verdict') or b.get('verdict_match')}] -> {b.get('outcome', '(continue)')}"
             for i, b in cands]
    transcript = "\n".join(f"{r}: {txt}" for r, txt in conv[-4:])
    system = ("You map a diagnostic tool's RESULT to exactly one runbook branch. Pick the branch whose "
              "condition best fits the result and the user's request. Respond with ONLY JSON: "
              '{"branch": <index>}.')
    user = f"User conversation:\n{transcript}\n\nTool result:\n{result}\n\nBranches:\n" + "\n".join(lines)
    try:
        raw = llm_call([{"role": "system", "content": system}, {"role": "user", "content": user}],
                       model=model, tracer=tracer, ref_files=None, max_tokens=64)
        m = re.search(r'"branch"\s*:\s*(\d+)', raw)
        if m and 0 <= int(m.group(1)) < len(branches):
            return branches[int(m.group(1))]
    except Exception as e:
        tracer.info(f"LLM branch pick failed ({e})")
    return None


def resolve_branch(ctx, branches, result, is_record):
    """Deterministic match first; then LLM judgment for descriptive conditions; then a
    non-escalating fallback when the tool actually returned usable data."""
    b = select_branch(branches, result, is_record)
    if b is not None:
        return b
    if ctx.model and ctx.mode != "offline":
        b = llm_pick_branch(branches, result, ctx.conv, model=ctx.model, tracer=ctx.tracer)
        if b is not None:
            ctx.tracer.info("branch chosen by LLM judgment (descriptive condition)")
            return b
    for cand in (branches or []):     # tool succeeded but no rule fit — present, don't escalate
        if cand.get("outcome") in ("self_serve", "ask"):
            ctx.tracer.info("no rule matched; using first self-serve/ask branch instead of escalating")
            return cand
    return None


_KIT_RUNTIME_CFG = {}


def _kit_runtime_cfg(kit):
    """(names, allowed_tables) for generic SQL execution: the local names file the operator
    filled and the allowed-schema the binder wrote. (None, set()) if the kit has no names
    file, in which case the runtime uses its built-in demo tools. Cached per kit."""
    if kit.kit_dir not in _KIT_RUNTIME_CFG:
        import runtime_exec
        tools_dir = os.path.join(kit.kit_dir, "tools")
        names_path = os.path.join(tools_dir, "bindings.local.yaml")
        schema_path = os.path.join(tools_dir, "access.allowed_schema.local.yaml")
        names = runtime_exec.load_names(names_path) if os.path.isfile(names_path) else None
        allowed = runtime_exec.load_allowed_tables(schema_path) if os.path.isfile(schema_path) else set()
        _KIT_RUNTIME_CFG[kit.kit_dir] = (names, allowed)
    return _KIT_RUNTIME_CFG[kit.kit_dir]


def generic_live_tool(kit, tool_name, args, *, db, tracer):
    """Run a tool from its kit query file via the generic executor. Returns (result, is_record),
    or None if the kit isn't wired for it (no names file, or no/stub query) so the caller can
    fall back to the built-in demo tools."""
    import runtime_exec
    names, allowed = _kit_runtime_cfg(kit)
    if not names:
        return None
    qref = (kit.tools_by_name.get(tool_name) or {}).get("query_ref")
    path = os.path.join(kit.kit_dir, "tools", qref) if qref else None
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        template = f.read()
    if not runtime_exec.is_runnable_query(template):
        return None
    try:
        result, is_record = runtime_exec.execute_tool(
            template, args, db=db, names=names, allowed_tables=allowed, current_user=db.user_id)
    except runtime_exec.BindingError as e:
        tracer.info(f"generic SQL for {tool_name!r} skipped ({e}); falling back")
        return None
    tracer.tool(tool_name, args, qref, result, "live-db (generic)")
    return result, is_record


def dispatch_tool(ctx, tool_name, args, node):
    """Run a tool either live (real read-only SQL) or mocked, returning (result, is_record)."""
    if ctx.live_db is not None:
        generic = generic_live_tool(ctx.kit, tool_name, args, db=ctx.live_db, tracer=ctx.tracer)
        if generic is not None:
            return generic
        ctx.tracer.info(f"no runnable query bound for {tool_name!r}; live mode needs a fully "
                        f"bound kit (bindings.local.yaml + query files)")
        return {"status": "unavailable", "summary": "(no live query bound for this tool)"}, True
    default_verdict = (node.get("branches") or [{}])[0].get("verdict")
    return mock_tool(ctx.kit, tool_name, args, scenario=ctx.scenario,
                     mode=ctx.mode, tracer=ctx.tracer, default_verdict=default_verdict)


def extract_id(text):
    m = re.search(r"\d{3,}", text or "")
    return m.group(0) if m else None


def get_user_turn(ctx, question):
    """Obtain the user's next reply, honoring the interaction mode."""
    if ctx.mode == "chat":
        try:
            reply = input("  you> ").strip()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        ctx.tracer.user(reply)
        return reply
    if ctx.mode == "offline":
        reply = str(ctx.slots.get("booking_id") or "yes")
        ctx.tracer.user(reply, sim=True)
        return reply
    return ask_user_sim(ctx.scenario, question, conv=ctx.conv, model=ctx.model, tracer=ctx.tracer)


def obtain_slot(ctx, name):
    """Fill a slot named by ask_for_if_missing (e.g. booking_id)."""
    if ctx.scenario and ctx.scenario.get(name):
        return str(ctx.scenario[name])
    for role, text in ctx.conv:               # maybe the user already mentioned it
        if role == "user":
            got = extract_id(text)
            if got:
                return got
    q = f"Could you share your {name.replace('_', ' ')}? You'll find it in your account."
    ctx.tracer.agent(q)
    ctx.conv.append(("agent", q))
    reply = get_user_turn(ctx, q)
    ctx.conv.append(("user", reply))
    return extract_id(reply) or "000000"


def first_step(entry):
    for key in sorted(k for k in entry if k.startswith("step")):
        return entry[key]
    return None


def match_branch(branches, verdict):
    for b in branches or []:
        if b.get("verdict") == verdict:
            return b
    for b in branches or []:                  # tolerant: prefix match (MISMATCH: ...)
        v = b.get("verdict")
        if v and (str(verdict).startswith(v) or v.startswith(str(verdict))):
            return b
    return None


def process_step(ctx, step, depth):
    r, t = ctx.result, ctx.tracer
    if depth > ctx.max_depth:
        t.info(f"max_depth {ctx.max_depth} exceeded — forcing escalate_unknown")
        r.terminal_outcome = r.terminal_outcome or "escalate_unknown"
        return
    if "tool" in step:
        tool_name = step["tool"]
        args = resolve_params(step.get("params"), ctx.slots)
        result, is_record = dispatch_tool(ctx, tool_name, args, step)
        ctx.last_result = result
        r.tool_calls.append(tool_name)
        branch = resolve_branch(ctx, step.get("branches"), result, is_record)
        if branch is None:
            t.info(f"result {result!r} matched no branch — escalating")
            r.terminal_outcome = r.terminal_outcome or "escalate_unknown"
            return
        process_branch(ctx, branch, depth)
    else:
        process_branch(ctx, step, depth)     # a tool-less node (direct outcome)


def run_followup(ctx, followup, depth):
    r, t = ctx.result, ctx.tracer
    # Shape A — a follow-up tool whose branches (verdict: or verdict_match:) decide the rest.
    if "tool" in followup:
        tool_name = followup["tool"]
        args = resolve_params(followup.get("params"), ctx.slots)
        result, is_record = dispatch_tool(ctx, tool_name, args, followup)
        ctx.last_result = result
        r.tool_calls.append(tool_name)
        branch = resolve_branch(ctx, followup.get("branches"), result, is_record)
        if branch is not None:
            process_branch(ctx, branch, depth + 1)
            return
        t.info("follow-up matched no branch — escalating")
        r.terminal_outcome = r.terminal_outcome or "escalate_unknown"
        return
    # Shape B — a conditional continuation keyed by a condition (e.g. 'if_still_not_working').
    # These conditions mean "the problem persists" — so honor what the user actually said:
    # if their last reply signals it's resolved, close positively instead of escalating.
    last_reply = next((txt for role, txt in reversed(ctx.conv) if role == "user"), "")
    if looks_resolved(last_reply):
        msg = "Great — glad that's sorted! If anything else comes up, just let me know."
        t.agent(msg)
        ctx.conv.append(("agent", msg))
        r.final_message = msg
        r.terminal_outcome = "self_serve"
        t.info("user indicated the issue is resolved — closing without escalation")
        return
    for cond_key, branch in followup.items():
        if isinstance(branch, dict):
            t.info(f"follow-up condition '{cond_key}' holds (user still affected) — proceeding")
            process_branch(ctx, branch, depth + 1)
            return
    t.info("follow-up had no actionable branch — escalating")
    r.terminal_outcome = r.terminal_outcome or "escalate_unknown"


def process_branch(ctx, branch, depth):
    r, t = ctx.result, ctx.tracer
    oc = branch.get("outcome")

    # 1. reference articles
    ref_texts, ref_files = [], []
    for fn in branch.get("kb_refs", []):
        ref_texts.append(load_harness_article(ctx.kit, fn, t))
        ref_files.append(fn)
        if fn not in r.kb_refs:
            r.kb_refs.append(fn)

    # 2. escalation proposal
    if "proposal" in branch:
        p = branch["proposal"]
        r.proposal_entity = p.get("entity")
        r.proposal_change = (p.get("change") or "").strip()
        t.escalate(p)

    # 3. declarative message (self_serve / escalate_*); {{result.*}} is filled from the live
    #    tool result so listing/fetch answers carry real data into the reply.
    if branch.get("message"):
        msg = render_result(branch["message"], ctx.last_result)
        final = phrase_reply(ctx.kit, msg, conv=ctx.conv, model=ctx.model,
                             tracer=t, mode=ctx.mode, ref_texts=ref_texts, ref_files=ref_files)
        r.final_message = final
        t.agent(final)
        ctx.conv.append(("agent", final))

    # 4. record decisive outcome — last one wins, so a self_serve that chains into a
    #    later escalation correctly reports the escalation as the terminal outcome.
    if oc in DECISIVE:
        r.terminal_outcome = oc

    # 5. continuation
    if "follow_up" in branch:                 # ask, then a deterministic fetch decides the rest
        if branch.get("question"):
            q = strip_refs(branch["question"])
            t.agent(q)
            ctx.conv.append(("agent", q))
        reply = get_user_turn(ctx, branch.get("question", ""))
        ctx.conv.append(("user", reply))
        got = extract_id(reply)
        if got:
            ctx.slots["booking_id"] = got
        run_followup(ctx, branch["follow_up"], depth)
    elif any(k.startswith("step") and isinstance(v, dict) for k, v in branch.items()):
        # Inline nested step (e.g. a branch carrying its own step_2 tool+branches).
        nested = next(v for k, v in branch.items() if k.startswith("step") and isinstance(v, dict))
        process_step(ctx, nested, depth + 1)
    elif "follow_up_step" in branch:          # e.g. self_serve -> ask_confirm_resolved
        sib = ctx.runbook.get(branch["follow_up_step"])
        if sib:
            process_step(ctx, sib, depth + 1)
    elif oc == "ask":                         # leaf ask: pose the question and stop
        if branch.get("question"):
            q = strip_refs(branch["question"])
            t.agent(q)
            ctx.conv.append(("agent", q))
        if r.terminal_outcome is None:
            r.terminal_outcome = "ask"


def run_runbook(kit, runbook, scenario, conv, *, model, tracer, mode, live_db=None):
    result = RunResult()
    max_depth = runbook.get("max_depth", 4)
    ctx = Ctx(kit, runbook, scenario, conv, model, tracer, mode, result, {}, max_depth, live_db)
    entry = runbook.get("entry", {})
    missing = entry.get("ask_for_if_missing")
    if missing:
        ctx.slots[missing] = obtain_slot(ctx, missing)
    step = first_step(entry) or entry
    process_step(ctx, step, depth=1)
    if result.terminal_outcome is None:
        result.terminal_outcome = "ask"
    return result


# ══════════════════════════════════════════════════════════════════════════════════
# Modes
# ══════════════════════════════════════════════════════════════════════════════════
def pick_scenario(kit, args):
    if args.scenario:
        for s in kit.scenarios:
            if s.get("id") == args.scenario:
                return s
        raise SystemExit(f"no scenario with id {args.scenario!r} (try --mode sim with no --scenario "
                          f"to list them)")
    if args.intent:
        for s in kit.scenarios:
            if s.get("intent_key") == args.intent:
                return s
        raise SystemExit(f"no eval scenario for intent {args.intent!r}")
    if sys.stdin.isatty():
        print("\nAvailable scenarios:")
        for i, s in enumerate(kit.scenarios, 1):
            print(f"  {i:>2}. [{s['intent_key']}] {s['id']}: {Tracer._clip(s.get('prompt',''), 70)}")
        raw = input("Pick a scenario number (Enter for #1): ").strip()
        idx = (int(raw) - 1) if raw.isdigit() and int(raw) >= 1 else 0
        return kit.scenarios[idx]
    return kit.scenarios[0]


def run_sim(kit, scenario, *, model, tracer, mode, live_db=None, brain="agent"):
    tracer.section(f"SIM  {scenario['id']}  [intent: {scenario['intent_key']}]  brain={brain}")
    if live_db is None:
        tracer.info(f"ground-truth cause: {scenario.get('ground_truth_cause')}  "
                    f"| expected outcome: {scenario.get('expected_terminal_outcome')}  "
                    f"| expected tools: {scenario.get('expected_tool_calls')}")

    if brain == "agent":
        user_msg = scenario["prompt"]
        if live_db is not None and scenario.get("booking_id"):
            user_msg += f" (my booking id is {scenario['booking_id']})"
        tracer.user(user_msg)
        run_agent(kit, [("user", user_msg)], model=model, tracer=tracer, live_db=live_db, mode=mode)
        return

    conv = []
    user_msg = scenario["prompt"]
    tracer.user(user_msg)
    conv.append(("user", user_msg))

    intent, runbook_file = route_intent(kit, user_msg, model=model, tracer=tracer, mode=mode)
    if intent == "unknown" and scenario.get("intent_key") in kit.intent_to_runbook:
        runbook_file = kit.intent_to_runbook[scenario["intent_key"]]
        tracer.info(f"router said 'unknown'; using ticket's own intent -> {runbook_file}")

    runbook = kit.runbooks.get(runbook_file)
    if not runbook:
        tracer.info(f"runbook {runbook_file} not found; using escalate_unknown")
        runbook = kit.runbooks.get("escalate_unknown.runbook.yaml", {"entry": {"step_1": {
            "outcome": "escalate_unknown", "message": "I've flagged this to our team."}}})

    result = run_runbook(kit, runbook, scenario, conv, model=model, tracer=tracer,
                         mode=mode, live_db=live_db)
    tracer.out(result.terminal_outcome, result)


def run_chat(kit, *, model, tracer, mode, live_db=None, brain="agent"):
    tracer.section(f"CHAT  (you are the user; type 'quit' to exit)  brain={brain}")
    if live_db is not None:
        tracer.info(f"Tools run LIVE read-only SQL as the acting user ({live_db.user_id}).")
    else:
        tracer.info("Tools are mocked with happy-path verdicts (no live data).")

    conversation = []      # full running history for the agent brain
    while True:
        try:
            msg = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg.lower() in ("quit", "exit"):
            break
        tracer.user(msg)
        if brain == "agent":
            conversation.append(("user", msg))
            result = run_agent(kit, conversation, model=model, tracer=tracer,
                               live_db=live_db, mode=mode)
            if result.final_message:
                conversation.append(("agent", result.final_message))
            continue
        intent, runbook_file = route_intent(kit, msg, model=model, tracer=tracer, mode=mode)
        runbook = kit.runbooks.get(runbook_file) or kit.runbooks.get("escalate_unknown.runbook.yaml")
        if not runbook:
            tracer.info("no runbook available; escalating")
            continue
        result = run_runbook(kit, runbook, None, [("user", msg)], model=model, tracer=tracer,
                             mode=mode, live_db=live_db)
        tracer.out(result.terminal_outcome, result)


# ══════════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════
# LLM tool-agent brain  (--brain agent): no decision tree. The model diagnoses by calling
# read-only tools and composes the answer. Safety rests on (1) the scoped read-only RLS DB
# role — there is NO write tool, so it physically cannot change anything — and (2) a single
# explicit escalate_to_human tool that is the ONLY way to "act", loudly logged.
# ══════════════════════════════════════════════════════════════════════════════════
def _param_schema(params):
    props, req = {}, []
    for p in params or []:
        typ = "integer" if p.get("type") == "integer" else "string"
        props[p["name"]] = {"type": typ}
        if p.get("description"):
            props[p["name"]]["description"] = p["description"]
        if p.get("required"):
            req.append(p["name"])
    return {"type": "object", "properties": props, "required": req}


def build_agent_tools(kit):
    """Catalog read-only tools as OpenAI function schemas + escalate_to_human + lookup_kb_article."""
    tools = []
    for t in kit.tools_by_name.values():
        desc = (t.get("summary", "") + "  Returns: " + t.get("returns", "")).strip()
        tools.append({"type": "function", "function": {
            "name": t["name"], "description": desc[:1024],
            "parameters": _param_schema(t.get("params"))}})
    tools.append({"type": "function", "function": {
        "name": "escalate_to_human",
        "description": ("Raise a change request to a human operator. This is the ONLY way you can "
                        "cause any change. Call it whenever fixing the issue needs data to change "
                        "(confirm a booking, retry a refund, correct a status, etc.). You cannot make "
                        "changes yourself and must never claim you did."),
        "parameters": {"type": "object", "properties": {
            "entity": {"type": "string", "description": "what needs changing, e.g. booking/refund/subscription"},
            "change": {"type": "string", "description": "the concrete change requested"},
            "reason": {"type": "string", "description": "why, citing what you observed in the data"}},
            "required": ["entity", "change", "reason"]}}})
    tools.append({"type": "function", "function": {
        "name": "lookup_kb_article",
        "description": "Read a help-center article for guidance (filenames like 10-refunds.md, 21-my-bookings.md).",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string"}}, "required": ["filename"]}}})
    return tools


def agent_guidance(kit):
    """Condense the runbooks into plain guidance (symptom -> when to escalate) — the authored
    expertise, offered as advice rather than enforced as a tree."""
    lines = []
    for rb in kit.runbooks.values():
        sym = " ".join((rb.get("symptom") or "").split())
        if not sym:
            continue
        changes = []

        def scan(n):
            if isinstance(n, dict):
                if str(n.get("outcome", "")).startswith("escalate") and n.get("proposal"):
                    changes.append(" ".join((n["proposal"].get("change") or "").split()))
                for v in n.values():
                    scan(v)
            elif isinstance(n, list):
                for x in n:
                    scan(x)
        scan(rb)
        line = f"- {sym}"
        if changes:
            line += f"  → if so, escalate: {changes[0]}"
        lines.append(line)
    return "\n".join(lines)


def _tc_to_dict(tc):
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}


def _agent_completion(messages, tools, model, tracer):
    import litellm
    litellm.suppress_debug_info = True

    def _call():
        resp = litellm.completion(model=model, messages=messages, tools=tools,
                                  tool_choice="auto", timeout=120, max_tokens=1024)
        m = resp["choices"][0]["message"]
        tcs = getattr(m, "tool_calls", None)
        summary = ("wants tools: " + ", ".join(t.function.name for t in tcs)) if tcs \
            else ("↳ " + ((m.content or "")[:200]))
        tracer.llm(model, len(messages), ["persona.md"], _usage_str(getattr(resp, "usage", None)), summary)
        return resp

    return with_retry(_call)


def dispatch_agent_tool(kit, name, args, *, live_db, mode, tracer, result):
    result.tool_calls.append(name)
    if name == "escalate_to_human":
        proposal = {"entity": args.get("entity"), "change": args.get("change"), "reason": args.get("reason")}
        result.proposal_entity = proposal["entity"]
        result.proposal_change = (proposal["change"] or "").strip()
        result.terminal_outcome = "escalate_with_proposal"
        tracer.section("⚠  ESCALATION TO A HUMAN OPERATOR")
        tracer.escalate(proposal)
        return ("Escalation recorded. Tell the user plainly that you've passed this to the team "
                "with the details and they'll get an update by email — do NOT claim it's fixed.")
    if name == "lookup_kb_article":
        fn = args.get("filename", "")
        if fn not in result.kb_refs:
            result.kb_refs.append(fn)
        return load_harness_article(kit, fn, tracer)[:4000]
    if name not in kit.tools_by_name:
        tracer.info(f"agent called unknown tool {name!r}")
        return f"ERROR: no such tool {name!r}"
    if live_db is not None:
        generic = generic_live_tool(kit, name, args, db=live_db, tracer=tracer)
        if generic is not None:
            res = generic[0]
        else:
            tracer.info(f"no runnable query bound for {name!r}; live mode needs a fully bound kit")
            res = {"status": "unavailable", "summary": "(no live query bound for this tool)"}
    else:
        res, _ = mock_tool(kit, name, args, scenario=None, mode=mode, tracer=tracer, default_verdict=None)
    return json.dumps(res, default=str)


def run_agent(kit, conversation, *, model, tracer, live_db, mode, max_iters=8):
    """Single agent turn over the running conversation. Returns a RunResult."""
    tools = build_agent_tools(kit)
    system = (kit.persona_text + "\n\n---\nOPERATING RULES (read carefully):\n"
              "- You are STRICTLY READ-ONLY. Use tools to look up the user's real data, but you "
              "cannot change anything.\n"
              "- Whenever resolving the issue would require changing data, you MUST call "
              "escalate_to_human with a concrete proposal. NEVER state or imply you performed or "
              "will perform a change yourself.\n"
              "- Answer only from tool results and the knowledge base. If you don't have the "
              "information, say so honestly — never guess or invent data.\n"
              "- Plain language only: never mention internal article numbers, table/field names, or "
              "any system internals.\n"
              "- Diagnose before answering; if you need an id (e.g. a booking id) and don't have it, ask.\n"
              "\nDIAGNOSTIC GUIDANCE (advisory, from the support playbooks):\n" + agent_guidance(kit))
    messages = [{"role": "system", "content": system}]
    for role, content in conversation:
        messages.append({"role": "assistant" if role == "agent" else "user", "content": content})

    result = RunResult()
    for _ in range(max_iters):
        resp = _agent_completion(messages, tools, model, tracer)
        m = resp["choices"][0]["message"]
        tool_calls = getattr(m, "tool_calls", None)
        if not tool_calls:
            content = (m.content or "").strip()
            result.final_message = content
            result.terminal_outcome = result.terminal_outcome or "answered"
            tracer.agent(content)
            break
        messages.append({"role": "assistant", "content": m.content or "",
                         "tool_calls": [_tc_to_dict(tc) for tc in tool_calls]})
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out = dispatch_agent_tool(kit, tc.function.name, args,
                                      live_db=live_db, mode=mode, tracer=tracer, result=result)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.function.name, "content": out})
    else:
        tracer.info("agent reached the tool-iteration cap; stopping")
    tracer.out(result.terminal_outcome or "answered", result)
    return result


def build_parser():
    p = argparse.ArgumentParser(
        description="Drive a generated support kit (persona + tools + runbooks) with a mocked, "
                    "fully-traced conversation. NVIDIA/litellm-backed.")
    p.add_argument("kit", nargs="?", default=DEFAULT_KIT,
                   help=f"path to the support-kit folder (default: {DEFAULT_KIT})")
    p.add_argument("--harness", help="path to the harness folder (default: from support.config.yaml)")
    p.add_argument("--mode", choices=["chat", "sim"], default="chat",
                   help="chat = you type; sim = an LLM role-plays the user (default: chat)")
    p.add_argument("--brain", choices=["agent", "walker"], default=None,
                   help="agent = LLM decides tools and composes answers; walker = deterministic "
                        "runbook tree. Default comes from support.config.yaml runtime.brain (else agent).")
    p.add_argument("--model", help="NVIDIA model id; omit to pick from the API's /v1/models list")
    p.add_argument("--list-models", action="store_true",
                   help="list models available to your NVIDIA key and exit")
    p.add_argument("--scenario", help="sim: eval ticket id to replay (e.g. eval_bsoh_001)")
    p.add_argument("--intent", help="sim: pick the first eval ticket for this intent key")
    p.add_argument("--no-phrase", action="store_true",
                   help="skip LLM phrasing; print the runbook's canned message verbatim")
    p.add_argument("--no-llm", action="store_true",
                   help="fully offline: keyword routing, static mocks, no API calls")
    p.add_argument("--live", action="store_true",
                   help="execute real read-only SQL against the live DB instead of mocking tools")
    p.add_argument("--db-url",
                   help="read-only DB URL (default: $SUPPORT_READONLY_DB_URL, kit/.secrets, or local default)")
    p.add_argument("--user-id",
                   help="acting user id (uuid) for --live: scopes every read to that user via RLS")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    p.add_argument("--verbose", action="store_true", help="dump full LLM replies, not summaries")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list_models:
        key = os.environ.get("NVIDIA_NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        if not key:
            raise SystemExit("set NVIDIA_NIM_API_KEY (or NVIDIA_API_KEY) to list models")
        models = list_nvidia_models(key)
        print("\n".join(models) if models else "(no models returned)")
        return

    # Accept NVIDIA_API_KEY as an alias for the litellm-expected NVIDIA_NIM_API_KEY.
    if not os.environ.get("NVIDIA_NIM_API_KEY") and os.environ.get("NVIDIA_API_KEY"):
        os.environ["NVIDIA_NIM_API_KEY"] = os.environ["NVIDIA_API_KEY"]

    kit = load_kit(args.kit, args.harness)
    if not kit.harness_dir or not os.path.isdir(kit.harness_dir):
        print(f"(warning: harness folder not found at {kit.harness_dir!r}; "
              f"kb_refs will show as MISSING)", file=sys.stderr)

    runtime_cfg = kit.config.get("runtime", {}) or {}
    interaction = "offline" if args.no_llm else args.mode
    model = None
    if not args.no_llm:
        chosen = args.model or runtime_cfg.get("model")     # CLI > config > live /v1/models pick
        if chosen:
            model = normalize_model(chosen)
            if not args.model:
                print(f"(using configured model: {model})")
        else:
            model = resolve_model(args)
        env_var = PROVIDER_KEYS.get(model.split("/", 1)[0] + "/")
        if env_var and not os.environ.get(env_var):
            raise SystemExit(f"set {env_var} before running (model {model!r} needs it)")
    if args.no_phrase:
        global NO_PHRASE
        NO_PHRASE = True

    tracer = Tracer(color=not args.no_color, verbose=args.verbose)
    tracer.info(f"kit={kit.kit_dir}")
    tracer.info(f"harness={kit.harness_dir}")
    tracer.info(f"model={model or 'OFFLINE (--no-llm)'}  mode={interaction}  "
                f"tools={len(kit.tools_by_name)}  runbooks={len(kit.runbooks)}  "
                f"scenarios={len(kit.scenarios)}")

    live_db = None
    if args.live:
        url = resolve_db_url(args, kit)
        uid = resolve_user_id(args)
        tracer.info(f"LIVE: db={_redact(url)}  acting_user={uid}")
        try:
            live_db = LiveDB(url, uid, tracer)
        except Exception as e:
            raise SystemExit(f"could not connect to read-only DB ({_redact(url)}): {e}")

    brain = args.brain or runtime_cfg.get("brain") or "agent"   # CLI > config > agent
    if interaction == "offline" and brain == "agent":
        brain = "walker"
        tracer.info("agent brain needs the LLM; --no-llm forces --brain walker")
    tracer.info(f"brain={brain}")

    try:
        if args.mode == "sim":
            scenario = pick_scenario(kit, args)
            run_sim(kit, scenario, model=model, tracer=tracer, mode=interaction,
                    live_db=live_db, brain=brain)
        else:
            run_chat(kit, model=model, tracer=tracer, mode=interaction, live_db=live_db, brain=brain)
    finally:
        if live_db is not None:
            live_db.close()


if __name__ == "__main__":
    main()
