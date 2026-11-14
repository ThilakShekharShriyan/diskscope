"""Lessons feedback arms: "<base>+lessons" and "<base>+placebo" (base = sql | digest).

A lessons file is distilled by ONE fixed model from graded TRAINING-fold transcripts
and appended to the system prompt. Folds are leave-one-service-out over the 30-case
RE2-OB subset (5 root-cause services x 6 faults): each test case gets the file built
from the other 4 services' runs. The placebo is written by the same model from only
the fault vocabulary and the interface schema (no transcripts), capped to the same
fold's lessons word count.

Leak guards:
- prompts never name confusion pairs (research note 4.1.3);
- a deterministic post-filter drops every line naming any Online Boutique service
  (substring match, so `xservice_cpu` series names are caught) or a case id;
- check() greps every manifest file for service names / case ids and verifies its
  sha256; the harness re-runs both before using a file;
- live runs refuse a lessons dir with uncommitted changes (__main__).

  python3 -m agentlab lessons build --runs R1.json R2.partial.jsonl --out-dir lessons/ \
          [--model claude-opus-5-5] [--max-usd 2] [--variant rcaeval] [--dry-run]
  python3 -m agentlab lessons check --dir lessons/
"""
import hashlib
import json
import os
import re

from . import rcaeval

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "..", "lessons")
BASES = ("sql", "digest")
KINDS = ("lessons", "placebo")
HEADING = "Lessons from earlier graded investigations"
DISTILLER = "claude-opus-5-5"
MAX_WORDS = 400
MAX_PROMPT_CHARS = 60_000      # graded-runs section per lessons prompt (~20k tokens)
MAX_OUT_TOKENS = {"lessons": 8000, "placebo": 4000}
EFFORT = {"lessons": "medium", "placebo": "low"}
EXPECTED_OUT_TOKENS = 3000     # for the pre-call spend projection (thinking bills as output)

# Names that must never reach an agent through a lessons file. Stems catch "checkout", "productcatalog" ...
_STEMS = sorted({s.lower() for s in rcaeval.OB_SERVICES}
                | {"checkout", "currency", "productcatalog", "product catalog", "recommendation", "email",
                   "payment", "shipping", "cart", "redis", "frontend"}, key=len, reverse=True)
_NAME_RX = re.compile("|".join(re.escape(s) for s in _STEMS), re.I)
_CASE_RX = re.compile(r"re2[-_ ]?ob|re2[-_ ]?tt|rcaeval[:+]", re.I)


def base_of(arm):
    return arm.split("+", 1)[0]


def kind_of(arm):
    parts = arm.split("+", 1)
    return parts[1] if len(parts) == 2 else None


def is_lessons_arm(arm):
    return base_of(arm) in BASES and kind_of(arm) in KINDS


# ---------------------------------------------------------------- folds and inputs
def folds():
    """[(fold_id, held_out_service, [test case names])], fold ids f1..f5 in sorted service order."""
    subset = rcaeval.load_subset()
    svcs = sorted({c["service"] for c in subset})
    return [("f%d" % (k + 1), s, sorted(c["case"] for c in subset if c["service"] == s)) for k, s in enumerate(svcs)]


def load_runs(paths, variant="rcaeval"):
    """Observations from RunReport JSON ({"observations": [...]}) or .partial.jsonl files.
    Dedupe on (task, arm, model, trial); drop infra failures and other variants."""
    seen, out = set(), []
    for p in paths:
        with open(p) as f:
            text = f.read()
        if p.endswith(".jsonl"):
            obs = [json.loads(x) for x in text.splitlines() if x.strip()]
        else:
            obs = json.loads(text)["observations"]
        for o in obs:
            d = o.get("detail") or {}
            if d.get("infra_error") or not o.get("task", "").startswith(variant + ":"):
                continue
            key = (o["task"], d.get("arm"), d.get("model"), o.get("trial", 0))
            if key in seen:
                continue
            seen.add(key)
            out.append(o)
    out.sort(key=lambda o: (o["detail"]["arm"], o["task"], o["detail"]["model"], o.get("trial", 0)))
    return out


def training_runs(runs, base, held_out):
    """Runs of this base arm whose root-cause service is one of the fold's training services."""
    train = {s for _, s, _ in folds()} - {held_out}
    return [o for o in runs if o["detail"]["arm"] == base and o["detail"]["truth"]["components"][0] in train]


# ---------------------------------------------------------------- prompts
def schema_block(base):
    """The interface as the agent sees it: identical in the lessons and placebo prompts."""
    from .tools import ARM_TOOLS, OUTPUT_CAP
    lines = []
    for t in ARM_TOOLS[base]:
        desc = rcaeval.TOOL_TEXT.get(t["name"], t["description"]).format(cap=OUTPUT_CAP)
        lines.append("- %s: %s" % (t["name"], desc))
    return "\n".join(lines)


def _fault_block():
    return rcaeval.SYSTEM.split("Fault types:", 1)[1].strip()


RULES = """Write rules for an on-call agent that will investigate NEW incidents through this same \
interface. Requirements:
- Group the rules under one markdown heading per fault type (## cpu, ## mem, ## disk, ## delay, \
## loss, ## socket); a short ## general section is allowed.
- Each rule is one bullet line that cites a concrete evidence pattern visible through this \
interface (which table/tool, which metric suffix, log or span pattern, what shape of change) and \
says how to tell this fault type apart from the others.
- NEVER name any service, host, container or case, not even the web entry service or a cache; say "the \
root-cause service", "its callers", "the entry service" instead. Lines naming a service are \
deleted automatically.
- At most {words} words in total. Output only the markdown rules, no preamble."""

LESSONS_INTRO = """You are distilling lessons from graded root-cause investigations of a \
microservice web shop. In each run an agent investigated one telemetry window in which one fault \
was injected into one service, and named the root-cause service and fault type. Below are the \
agent's interface, the fault vocabulary, and graded runs (the agent's answer, the correct answer, \
its tool calls and their outputs, possibly truncated). Find what evidence separated the correct \
fault type from the agent's answer, and which checks the agent should make."""

PLACEBO_INTRO = """You are writing guidance for an on-call agent that investigates a microservice \
web shop. In each incident one fault was injected into one service, and the agent must name the \
root-cause service and the fault type. Below are the agent's interface and the fault vocabulary. \
Write general advice on recognising each fault type through this interface."""


def _clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n] + " ...[%d more chars]" % (len(s) - n)


def _verdict(o):
    d = o["detail"]
    g = d.get("grade") or {}
    if not g.get("submitted", d.get("answer") is not None):
        return "NO ANSWER SUBMITTED"
    return "%s (service %s, fault %s)" % ("CORRECT" if g.get("correct") else "WRONG",
                                         "right" if g.get("component_ok") else "wrong",
                                         "right" if g.get("cause_ok") else "wrong")


def _correct(o):
    return bool((o["detail"].get("grade") or {}).get("correct"))


def render_run(o, label, per_call, seen):
    """One graded run: answer, truth, verdict, tool calls (outputs clipped to per_call chars)."""
    d = o["detail"]
    a = d.get("answer") or {}
    small = max(80, per_call // 2)
    lines = ["#### %s (model %s): %s" % (label, d.get("model"), _verdict(o)),
             "Agent answer: services=%s fault=%s" % (json.dumps(a.get("services")), a.get("fault")),
             "Agent summary: %s" % _clip(a.get("summary"), small)]
    for j, c in enumerate(d.get("calls") or [], 1):
        out = c.get("output") or ""
        h = hashlib.sha256(out.encode()).hexdigest()
        if h in seen:
            shown = "(same output as %s)" % seen[h]
        else:
            seen[h] = "%s call %d" % (label, j)
            shown = _clip(out, per_call)
        lines.append("call %d: %s %s\n%s" % (j, c.get("tool"), _clip(json.dumps(c.get("input"), sort_keys=True), small),
                                             shown))
    texts = [x for x in (d.get("assistant_text") or []) if x and x.strip()]
    if texts:
        lines.append("Agent's last note: %s" % _clip(texts[-1], small))
    return "\n".join(lines)


def exemplars(runs):
    """Per case: every run feeds the outcome tally; one wrong and one correct run (first by model,
    trial) are shown in full. Returns [(task, tally_line, [runs to show])] in sorted task order."""
    by = {}
    for o in runs:
        by.setdefault(o["task"], []).append(o)
    out = []
    for task in sorted(by):
        rs = sorted(by[task], key=lambda o: (o["detail"]["model"], o.get("trial", 0)))
        t = rs[0]["detail"]["truth"]
        answers = {}
        for o in rs:
            a = o["detail"].get("answer") or {}
            key = "%s/%s" % ((a.get("services") or ["-"])[0], a.get("fault", "-")) if a else "no answer"
            answers[key] = answers.get(key, 0) + 1
        tally = "Correct answer: service=%s fault=%s. %d of %d runs correct; answers given (top service/fault): %s" % (
            t["components"][0], t["cause"], sum(map(_correct, rs)), len(rs),
            ", ".join("%s x%d" % kv for kv in sorted(answers.items())))
        show = [o for o in rs if not _correct(o)][:1] + [o for o in rs if _correct(o)][:1]
        out.append((task, tally, show))
    return out


def _render_cases(cases, per_call):
    seen, parts = {}, []
    for k, (task, tally, show) in enumerate(cases, 1):
        body = ["### Case %d" % k, "Question: %s" % rcaeval.USER, tally]
        body += [render_run(o, "case %d %s run" % (k, "wrong" if not _correct(o) else "correct"), per_call, seen)
                 for o in show]
        parts.append("\n".join(body))
    return "\n\n".join(parts)


def lessons_prompt(base, runs, max_chars=MAX_PROMPT_CHARS, words=MAX_WORDS):
    """Deterministic: same runs and budget -> byte-identical prompt. The per-call output clip is the
    largest (from a fixed ladder) that keeps the graded-runs section within max_chars."""
    cases = exemplars(runs)
    for per_call in (4000, 3000, 2000, 1500, 1000, 700, 500, 350, 250, 150, 80):
        body = _render_cases(cases, per_call)
        if len(body) <= max_chars:
            break
    return "\n\n".join([LESSONS_INTRO, "## Interface (%s)\n%s" % (base, schema_block(base)),
                        "## Fault types\n%s" % _fault_block(), "## Graded runs\n" + body,
                        "## Task\n" + RULES.format(words=words)])


def placebo_prompt(base, words):
    return "\n\n".join([PLACEBO_INTRO, "## Interface (%s)\n%s" % (base, schema_block(base)),
                        "## Fault types\n%s" % _fault_block(), "## Task\n" + RULES.format(words=words)])


# ---------------------------------------------------------------- filter and leak check
def words(text):
    return len(text.split())


def leaks(text, extra=()):
    """Lines of text naming a service, a case id, or any of `extra` (e.g. test-fold case names)."""
    bad = []
    for line in text.splitlines():
        low = line.lower()
        if _NAME_RX.search(line) or _CASE_RX.search(line) or any(x.lower() in low for x in extra):
            bad.append(line)
    return bad


def post_filter(text, max_words=MAX_WORDS):
    """Drop leaking lines; then drop trailing bullets until <= max_words. Returns (text, dropped lines)."""
    bad = set(leaks(text))
    kept = [ln.rstrip() for ln in text.strip().splitlines() if ln not in bad]
    dropped = [ln for ln in text.splitlines() if ln in bad]
    while kept and words("\n".join(kept)) > max_words:
        dropped.append(kept.pop())
    kept = [ln for i, ln in enumerate(kept)  # drop headings left with no rules under them
            if not ln.lstrip().startswith("#")
            or any(x.strip() and not x.lstrip().startswith("#") for x in
                   kept[i + 1:next((j for j in range(i + 1, len(kept)) if kept[j].lstrip().startswith("#")), len(kept))])]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out + "\n", dropped


def _read(path):
    with open(path) as f:
        return f.read()


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def check(dir_):
    """Verify every manifest file: sha256 matches and no service name / case id appears.
    Returns a list of problems (empty = ok)."""
    if not os.path.exists(os.path.join(dir_, "manifest.json")):
        return ["%s: no manifest.json (run `lessons build` first)" % dir_]
    with open(os.path.join(dir_, "manifest.json")) as f:
        man = json.load(f)
    probs = []
    for e in man["files"]:
        p = os.path.join(dir_, e["file"])
        if not os.path.exists(p):
            probs.append("%s: missing" % e["file"])
            continue
        text = _read(p)
        if sha256(text) != e["sha256"]:
            probs.append("%s: sha256 differs from manifest" % e["file"])
        bad = leaks(text, extra=[e["held_out"]] + e.get("test_cases", []))
        if bad:
            probs.append("%s: leaks %r" % (e["file"], bad[:3]))
    return probs


# ---------------------------------------------------------------- distiller and build
def make_distiller(model):
    """Live distiller (Anthropic). Returns fn(prompt, kind) -> (text, usage)."""
    import anthropic
    client = anthropic.Anthropic(max_retries=8)

    def distill(prompt, kind):
        r = client.messages.create(model=model, max_tokens=MAX_OUT_TOKENS[kind],
                                   thinking={"type": "adaptive"}, output_config={"effort": EFFORT[kind]},
                                   messages=[{"role": "user", "content": prompt}])
        if r.stop_reason != "end_turn":
            raise RuntimeError("distiller stopped with %s; nothing written" % r.stop_reason)
        u = r.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                 "served_by": getattr(r, "model", model)}
        return "".join(b.text for b in r.content if getattr(b, "type", None) == "text"), usage

    return distill


def _projected(model, prompt, spent):
    from .harness import cost_usd
    return spent + (cost_usd(model, {"input_tokens": len(prompt) // 3, "output_tokens": EXPECTED_OUT_TOKENS,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}) or 0.0)


def build(run_paths, out_dir, model=DISTILLER, max_usd=2.0, variant="rcaeval", dry_run=False,
          distill=None, max_chars=MAX_PROMPT_CHARS, bases=BASES, log=print):
    """Write <base>-<fold>-lessons.md and -placebo.md for every (base, fold) plus manifest.json.
    dry_run prints the prompts and the projected cost and never creates a client."""
    from .harness import cost_usd
    runs = load_runs(run_paths, variant)
    plan = []
    for base in bases:
        for fid, held, tests in folds():
            tr = training_runs(runs, base, held)
            if not tr:
                raise SystemExit("no %s training runs for fold %s (variant %s)" % (base, fid, variant))
            plan.append((base, fid, held, tests, tr, lessons_prompt(base, tr, max_chars)))
    if dry_run:
        total = 0.0
        for base, fid, held, tests, tr, prompt in plan:
            log("=" * 30 + " %s %s (holds out %s; %d training runs) %d chars ~%d tokens" % (
                base, fid, held, len(tr), len(prompt), len(prompt) // 3))
            log(prompt)
            total = _projected(model, prompt, total)
        for base in bases:
            pp = placebo_prompt(base, MAX_WORDS)
            log("=" * 30 + " placebo %s (one per fold; target = that fold's lessons word count)" % base)
            log(pp)
            total += sum(1 for x in plan if x[0] == base) * _projected(model, pp, 0.0)
        log("dry run: %d lessons + %d placebo calls to %s; projected ~$%.2f vs --max-usd %.2f" % (
            len(plan), len(plan), model, total, max_usd))
        return None
    if os.path.exists(os.path.join(out_dir, "manifest.json")):
        raise SystemExit("%s already has a manifest; lessons are frozen once written (use a new dir)" % out_dir)
    distill = distill or make_distiller(model)
    os.makedirs(out_dir, exist_ok=True)
    spent, files = 0.0, []

    def call(prompt, kind):
        nonlocal spent
        if _projected(model, prompt, spent) > max_usd:
            raise SystemExit("stopping: next %s call would pass --max-usd %.2f (spent $%.2f)" % (kind, max_usd, spent))
        text, usage = distill(prompt, kind)
        usd = cost_usd(model, usage) or 0.0
        spent += usd
        return text, usage, usd

    for base, fid, held, tests, tr, prompt in plan:
        raw, usage, usd = call(prompt, "lessons")
        text, dropped = post_filter(raw, MAX_WORDS)
        n = words(text)
        entries = [("lessons", text, dropped, prompt, usage, usd, n, None)]
        pprompt = placebo_prompt(base, n)
        praw, pusage, pusd = call(pprompt, "placebo")
        ptext, pdropped = post_filter(praw, n)
        entries.append(("placebo", ptext, pdropped, pprompt, pusage, pusd, words(ptext), n))
        for kind, body, drop, pr, us, cost, nw, target in entries:
            name = "%s-%s-%s.md" % (base, fid, kind)
            with open(os.path.join(out_dir, name), "w") as f:
                f.write(body)
            files.append({"file": name, "base": base, "kind": kind, "fold": fid, "held_out": held,
                          "test_cases": tests, "sha256": sha256(body), "words": nw, "target_words": target,
                          "dropped_lines": len(drop), "dropped": drop, "prompt_sha256": sha256(pr),
                          "prompt_chars": len(pr), "usage": us, "usd": round(cost, 4),
                          "training_runs": [[o["task"], o["detail"]["model"], o.get("trial", 0)] for o in tr]
                          if kind == "lessons" else []})
            log("%s: %d words, %d lines dropped, $%.3f" % (name, nw, len(drop), cost))
    man = {"schema_version": 1, "distiller": model, "variant": variant, "max_words": MAX_WORDS,
           "max_prompt_chars": max_chars, "spent_usd": round(spent, 4),
           "inputs": [{"path": os.path.basename(p), "sha256": sha256(_read(p))}
                      for p in run_paths],
           "files": files}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    probs = check(out_dir)
    if probs:
        raise SystemExit("leak check failed:\n" + "\n".join(probs))
    log("wrote %d files + manifest.json to %s; spent $%.2f; leak check ok" % (len(files), out_dir, spent))
    return man


# ---------------------------------------------------------------- harness hook
def section(arm, incident_id, dir_=None):
    """System-prompt suffix for a lessons/placebo arm, and the detail fields to record."""
    if not incident_id.startswith(("rcaeval:", "rcaeval+t:")):
        raise ValueError("arm %s is defined only for rcaeval incidents (got %s)" % (arm, incident_id))
    dir_ = dir_ or os.environ.get("AGENTLAB_LESSONS_DIR") or DEFAULT_DIR
    case = incident_id.split(":", 1)[1]
    svc = {c["case"]: c["service"] for c in rcaeval.load_subset()}[case]
    with open(os.path.join(dir_, "manifest.json")) as f:
        man = json.load(f)
    e = [x for x in man["files"] if x["base"] == base_of(arm) and x["kind"] == kind_of(arm) and x["held_out"] == svc]
    if len(e) != 1:
        raise ValueError("no unique %s file for held-out %s in %s" % (arm, svc, dir_))
    e = e[0]
    text = _read(os.path.join(dir_, e["file"]))
    if sha256(text) != e["sha256"]:
        raise ValueError("%s changed since the manifest was written" % e["file"])
    if case not in e["test_cases"] or leaks(text, extra=[svc, case]):
        raise ValueError("%s fails the leak check for %s" % (e["file"], case))
    return ("\n\n## %s\n\n%s" % (HEADING, text.strip()),
            {"lessons_sha": e["sha256"], "lessons_file": e["file"], "lessons_fold": e["fold"]})
