"""Agent loop, grading, and the agent's own trace.

Models:
  MockModel  - scripted, no network; default; used by tests and CI
  LiveModel  - Claude via the Anthropic SDK; requires --live AND credentials
Every run records tokens, dollars, tool calls, latency split (model vs tool),
whether the incident's evidence ever appeared in a tool result, and an
OTel-GenAI-shaped trace of the agent itself (invoke_agent / chat / execute_tool).
"""
import json
import os
import secrets
import time

from .incidents import ALL
from .tools import ARM_TOOLS, SUBMIT, SYSTEM

# $ per million tokens: (input, output, cache read). From the claude-api skill's
# model table (cached 2026-11-03); recheck before publishing dollar figures.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.10),
          "claude-haiku-5-5": (0.10, 0.50, None)}
MAX_TURNS = 20
MAX_INPUT_TOKENS = 600_000   # per run, summed over turns; same for every arm


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class MockModel:
    """Two scripted investigation steps per arm, then a fixed answer. Exercises the pipeline only."""
    name = "mock"
    SCRIPT = {"files": [("shell", {"command": "head -c 400 README.md; grep -c ERROR logs.jsonl"}),
                        ("shell", {"command": "grep ERROR logs.jsonl | head -5"})],
              "sql": [("query", {"sql": "SELECT component, level, count(*) n FROM logs GROUP BY 1,2 ORDER BY n DESC LIMIT 10"}),
                      ("query", {"sql": "SELECT msg FROM logs WHERE level='ERROR' LIMIT 5"})],
              "digest": [("get_digest", {}), ("drill", {"id": "M1"})]}

    def __init__(self, arm):
        self.steps = list(self.SCRIPT[arm])

    def turn(self, messages, tools, system):
        if self.steps:
            name, args = self.steps.pop(0)
            content = [Block(type="tool_use", id="mock_%s" % secrets.token_hex(4), name=name, input=args)]
        else:
            content = [Block(type="tool_use", id="mock_submit", name="submit_answer",
                             input={"component": "none", "cause": "no_fault", "evidence": [], "summary": "mock"})]
        usage = {"input_tokens": sum(len(json.dumps(m, default=str)) for m in messages) // 4, "output_tokens": 30,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        return content, "tool_use", usage


class LiveModel:
    def __init__(self, model, effort="medium"):
        import anthropic  # only needed for live runs
        self.client = anthropic.Anthropic()
        self.name, self.effort = model, effort

    def turn(self, messages, tools, system):
        kw = dict(model=self.name, max_tokens=16000, system=system, tools=tools, messages=messages,
                  thinking={"type": "adaptive"}, output_config={"effort": self.effort},
                  cache_control={"type": "ephemeral"})
        if self.name in ("claude-opus-5-5", "claude-sonnet-5-5"):
            r = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        else:
            r = self.client.beta.messages.create(**kw)
        u = r.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
        return r.content, r.stop_reason, usage


def cost_usd(model, usage):
    p = PRICES.get(model)
    if not p:
        return None
    inp, out, cr = p
    cw = inp * 1.25  # 5-minute cache write
    return (usage["input_tokens"] * inp + usage["output_tokens"] * out + usage["cache_read_input_tokens"] * (cr or inp)
            + usage["cache_creation_input_tokens"] * cw) / 1e6


def grade(incident, answer, tool_outputs):
    truth = incident["truth"]
    if answer is None:
        return {"correct": False, "component_ok": False, "cause_ok": False, "retrieved": None, "submitted": False}
    comp_ok = answer.get("component") in truth["components"]
    cause_ok = answer.get("cause") == truth["cause"]
    blob = "\n".join(tool_outputs)
    retrieved = any(e in blob for e in incident["evidence"]) if incident["evidence"] else None
    return {"correct": comp_ok and cause_ok, "component_ok": comp_ok, "cause_ok": cause_ok,
            "retrieved": retrieved, "submitted": True}


def run_one(incident_id, arm, model, backend, trial=0, trace_dir=None):
    inc = ALL[incident_id]
    tools = ARM_TOOLS[arm] + [SUBMIT]
    system = SYSTEM.format(turns=MAX_TURNS)
    messages = [{"role": "user", "content": "Investigate the last 60 minutes and submit your diagnosis."}]
    trace_id, root = secrets.token_hex(16), secrets.token_hex(8)
    spans, outputs, calls, texts = [], [], [], []
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    answer, error, stop = None, None, None
    t_start = time.time()
    model_ms = tool_ms = 0.0
    for turn in range(MAX_TURNS):
        if sum(totals[k] for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) > MAX_INPUT_TOKENS:
            error = "input token cap"
            break
        t0 = time.time()
        try:
            content, stop, usage = model.turn(messages, tools, system)
        except Exception as e:  # noqa: BLE001 - record and stop this run
            error = "model error: %s" % type(e).__name__
            break
        dt = (time.time() - t0) * 1000
        model_ms += dt
        for k in totals:
            totals[k] += usage.get(k, 0)
        spans.append(_span(trace_id, root, "chat %s" % model.name, t0, dt,
                           {"gen_ai.operation.name": "chat", "gen_ai.request.model": model.name,
                            "gen_ai.usage.input_tokens": usage["input_tokens"],
                            "gen_ai.usage.output_tokens": usage["output_tokens"]}))
        messages.append({"role": "assistant", "content": content})
        texts.extend(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")
        if stop == "refusal":
            error = "refusal"
            break
        uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            messages.append({"role": "user", "content": "Use the tools, then call submit_answer."})
            continue
        results = []
        for b in uses:
            if b.name == "submit_answer":
                answer = dict(b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": "recorded"})
                continue
            t1 = time.time()
            try:
                out, meta = backend.call(incident_id, arm, b.name, dict(b.input))
            except Exception as e:  # noqa: BLE001 - surface to the agent as a tool error
                out, meta = "tool error: %s" % e, {"tool_ms": (time.time() - t1) * 1000}
            tool_ms += meta.get("tool_ms", 0)
            outputs.append(out)
            calls.append({"tool": b.name, "input": dict(b.input), "chars": len(out), "output": out,
                          "wandered": _wandered(arm, dict(b.input)), **meta})
            spans.append(_span(trace_id, root, "execute_tool %s" % b.name, t1, meta.get("tool_ms", 0),
                               {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": b.name,
                                "agentlab.result_chars": len(out), "agentlab.backend": backend.name}))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        messages.append({"role": "user", "content": results})
        if answer is not None:
            break
    wall = (time.time() - t_start) * 1000
    g = grade(inc, answer, outputs)
    usd = cost_usd(model.name, totals)
    spans.insert(0, _span(trace_id, None, "invoke_agent agentlab", t_start, wall,
                          {"gen_ai.operation.name": "invoke_agent", "agentlab.incident": incident_id,
                           "agentlab.arm": arm, "agentlab.correct": g["correct"]}, span_id=root))
    if trace_dir:
        os.makedirs(trace_dir, exist_ok=True)
        with open(os.path.join(trace_dir, "%s-%s-%d.spans.jsonl" % (incident_id, arm, trial)), "w") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")
    return {
        "task": incident_id, "trial": trial, "fanout": 1, "ok": g["correct"], "client_wall_ms": wall,
        "remote_total_ms": None, "remote_queue_ms": None, "remote_execute_ms": None, "work_ms": tool_ms,
        "operations": len(calls), "bytes": sum(c["chars"] for c in calls), "error": error, "cleanup_error": None,
        "started_ms": t_start * 1000, "parallel": False,
        "detail": {"arm": arm, "model": model.name, "backend": backend.name, "answer": answer, "grade": g,
                   "truth": inc["truth"], "held_out": incident_id.startswith("ho-"), "turns": len(spans) - 1 - len(calls),
                   "tokens": totals, "usd": usd, "model_ms": model_ms, "tool_ms": tool_ms, "calls": calls,
                   "stop_reason": stop, "assistant_text": texts,
                   "wandered": any(c["wandered"] for c in calls)},
    }


def _wandered(arm, args):
    """Files-arm shell commands that leave the arm's own directory (could reach other arms' data)."""
    cmd = args.get("command", "")
    return arm == "files" and (".." in cmd or "/mnt" in cmd or "cd /" in cmd or " /" in cmd)


def _span(trace_id, parent, name, t0, dur_ms, attrs, span_id=None):
    return {"trace_id": trace_id, "span_id": span_id or secrets.token_hex(8), "parent_id": parent, "name": name,
            "start_ms": round(t0 * 1000, 1), "dur_ms": round(dur_ms, 2), "attrs": attrs}
