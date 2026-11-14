"""Agent loop, grading, and the agent's own trace.

Models:
  MockModel   - scripted, no network; default; used by tests and CI
  LiveModel   - Claude via the Anthropic SDK; requires --live AND ANTHROPIC_API_KEY
  OpenAIModel - OpenAI Responses API; requires --live AND OPENAI_API_KEY
No server-side model fallback: a run must be served by the model it names (each
turn records the model that actually answered; a mismatch is flagged).
Every run records tokens, dollars, tool calls, latency split (model vs tool),
whether the incident's evidence ever appeared in a tool result, and an
OTel-GenAI-shaped trace of the agent itself (invoke_agent / chat / execute_tool).
"""
import json
import os
import secrets
import time

from .incidents import ALL
from .tools import ARM_TOOLS, OUTPUT_CAP, SUBMIT, SYSTEM, leaves_dir

# $ per million tokens: (input, output, cache read). From the claude-api skill's
# model table (cached 2026-11-03); recheck before publishing dollar figures.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.10),
          "claude-haiku-5-5": (0.10, 0.50, None),
          # OpenAI standard tier, <=272K-token requests; developers.openai.com/api/docs/pricing, 2026-11-14
          "gpt-5.6-sol": (4.0, 20.0, 0.40), "gpt-5.6-terra": (2.0, 12.0, 0.20), "gpt-5.6-luna": (0.20, 1.20, 0.02),
          "gpt-5.5": (5.0, 30.0, 0.50), "gpt-5.4": (2.5, 15.0, 0.25), "gpt-5.4-mini": (0.75, 4.50, 0.075)}
MAX_TURNS = 20               # model calls per run
MAX_TOOL_CALLS = 40          # tool calls per run (a model may call several tools per turn)
MAX_INPUT_TOKENS = 600_000   # per run, summed over turns; same for every arm and provider


class InfraError(RuntimeError):
    """Failure of the benchmark's own machinery (Archil budget/connection, spend cap): the run
    is stopped and marked infra, never shown to the agent as a tool result."""


class SpendMeter:
    """Live dollars across all concurrent runs, updated after every model turn."""

    def __init__(self, limit=None):
        import threading
        self.limit, self.spent, self._lock = limit, 0.0, threading.Lock()

    def add(self, usd):
        with self._lock:
            self.spent += usd or 0.0

    def over(self):
        return self.limit is not None and self.spent >= self.limit


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class MockModel:
    """Two scripted investigation steps per arm, then a fixed answer. Exercises the pipeline only."""
    name = "mock"
    SCRIPT = {"files": [("shell", {"command": "head -c 400 README.md; grep -c ERROR logs.jsonl"}),
                        ("shell", {"command": "grep ERROR logs.jsonl | head -5"})],
              "prior": [],
              "sql": [("query", {"sql": "SELECT component, level, count(*) n FROM logs GROUP BY 1,2 ORDER BY n DESC LIMIT 10"}),
                      ("query", {"sql": "SELECT msg FROM logs WHERE level='ERROR' LIMIT 5"})],
              "digest": [("get_digest", {}), ("drill", {"id": "M1"})]}

    def __init__(self, arm, answer=None):
        self.steps = list(self.SCRIPT[arm])
        self.answer = answer or {"component": "none", "cause": "no_fault", "evidence": [], "summary": "mock"}

    def turn(self, messages, tools, system):
        if self.steps:
            name, args = self.steps.pop(0)
            content = [Block(type="tool_use", id="mock_%s" % secrets.token_hex(4), name=name, input=args)]
        else:
            content = [Block(type="tool_use", id="mock_submit", name="submit_answer", input=dict(self.answer))]
        usage = {"input_tokens": sum(len(json.dumps(m, default=str)) for m in messages) // 4, "output_tokens": 30,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        return content, "tool_use", usage


class LiveModel:
    def __init__(self, model, effort="medium"):
        import anthropic  # only needed for live runs
        self.client = anthropic.Anthropic(max_retries=8)  # backoff on 429/529 under high --parallel
        self.name, self.effort = model, effort

    def turn(self, messages, tools, system):
        r = self.client.messages.create(
            model=self.name, max_tokens=16000, system=system, tools=tools, messages=messages,
            thinking={"type": "adaptive"}, output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"})
        u = r.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                 "served_by": getattr(r, "model", self.name)}
        return r.content, r.stop_reason, usage


class OpenAIModel:
    """OpenAI Responses API. The conversation lives server-side (previous_response_id), so the
    model keeps its reasoning between turns, as Claude does with its thinking blocks; each turn
    sends only the harness messages added since the last call (tool results, nudges)."""

    def __init__(self, model, effort="medium"):
        import openai  # only needed for live runs
        self.client = openai.OpenAI(max_retries=8)  # backoff on 429 under high --parallel
        self.name, self.effort = model, effort
        self.prev, self.sent = None, 0

    @staticmethod
    def _tools(tools):
        return [{"type": "function", "name": t["name"], "description": t["description"],
                 "parameters": t["input_schema"], "strict": True} for t in tools]

    def _input(self, messages):
        items = []
        for m in messages[self.sent:]:
            if m["role"] == "assistant":
                continue  # already held server-side
            if isinstance(m["content"], str):
                items.append({"role": "user", "content": m["content"]})
            else:
                for r in m["content"]:
                    items.append({"type": "function_call_output", "call_id": r["tool_use_id"],
                                  "output": r["content"] if isinstance(r["content"], str) else json.dumps(r["content"])})
        return items

    def turn(self, messages, tools, system):
        r = self.client.responses.create(
            model=self.name, instructions=system, input=self._input(messages), tools=self._tools(tools),
            reasoning={"effort": self.effort}, max_output_tokens=16000, previous_response_id=self.prev,
            parallel_tool_calls=True, store=True)
        self.prev, self.sent = r.id, len(messages) + 1  # +1: the assistant turn the harness appends
        content, refused = [], False
        for it in r.output:
            if it.type == "function_call":
                try:
                    args = json.loads(it.arguments or "{}")
                except ValueError:
                    args = {}
                content.append(Block(type="tool_use", id=it.call_id, name=it.name, input=args))
            elif it.type == "message":
                for c in it.content:
                    if c.type == "output_text":
                        content.append(Block(type="text", text=c.text))
                    elif c.type == "refusal":
                        refused = True
        u = r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        usage = {"input_tokens": u.input_tokens - cached, "output_tokens": u.output_tokens,
                 "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0,
                 "reasoning_tokens": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0,
                 "served_by": getattr(r, "model", self.name)}
        if refused:
            stop = "refusal"
        elif r.status == "incomplete":
            reason = getattr(getattr(r, "incomplete_details", None), "reason", "") or ""
            stop = "content_filter" if "filter" in reason else "max_tokens"
        else:
            stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return content, stop, usage


def make_model(name, effort="medium"):
    if name.startswith(("gpt-", "o3", "o4")):
        return OpenAIModel(name, effort)
    return LiveModel(name, effort)


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


def task_spec(incident_id):
    """Everything suite-specific about one task: truth, prompt, submit tool, grader."""
    if incident_id.startswith(("rcaeval:", "rcaeval+t:")):
        from . import rcaeval
        return rcaeval.task(incident_id)
    inc = ALL[incident_id]
    return {"truth": inc["truth"], "evidence": inc["evidence"], "system": SYSTEM, "submit": SUBMIT,
            "user": "Investigate the last 60 minutes and submit your diagnosis.", "tool_text": {},
            "grade": lambda t, a, o: grade(inc, a, o), "held_out": incident_id.startswith("ho-"),
            "mock_answer": None}


def arm_tools(arm, tool_text):
    """The arm's tools, with descriptions replaced per suite (tool_text is keyed by tool name)."""
    out = []
    for t in ARM_TOOLS[arm]:
        t = dict(t)
        if t["name"] in tool_text:
            t["description"] = tool_text[t["name"]].format(cap=OUTPUT_CAP)
        out.append(t)
    return out


def run_one(incident_id, arm, model, backend, trial=0, trace_dir=None, meter=None):
    spec = task_spec(incident_id)
    tools = arm_tools(arm, spec["tool_text"]) + [spec["submit"]]
    system = spec["system"].replace("{turns}", str(MAX_TURNS))
    if arm == "prior":  # no-telemetry control: what the model answers from the prompt alone (memorisation check)
        system += ("\n\nIn this run you have no telemetry tools. Call submit_answer right away with your "
                   "best guess from the information above.")
    messages = [{"role": "user", "content": spec["user"]}]
    trace_id, root = secrets.token_hex(16), secrets.token_hex(8)
    spans, outputs, calls, texts, turns_log = [], [], [], [], []
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    answer, error, stop, infra = None, None, None, False
    t_start = time.time()
    model_ms = tool_ms = 0.0
    for turn in range(MAX_TURNS):
        if sum(totals[k] for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) > MAX_INPUT_TOKENS:
            error = "input token cap"
            break
        if meter is not None and meter.over():
            error, infra = "spend cap reached", True
            break
        t0 = time.time()
        try:
            content, stop, usage = model.turn(messages, tools, system)
        except Exception as e:  # noqa: BLE001 - provider/network failure: infra, not a model mistake
            error, infra = "model error: %s" % type(e).__name__, True
            break
        dt = (time.time() - t0) * 1000
        model_ms += dt
        for k in totals:
            totals[k] += usage.get(k, 0)
        turn_usd = cost_usd(model.name, usage)
        if meter is not None:
            meter.add(turn_usd)
        uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
        turns_log.append({"turn": turn, "t_ms": round((t0 - t_start) * 1000), "model_ms": round(dt),
                          "stop": stop, "served_by": usage.get("served_by", model.name), "n_tool_calls": len(uses),
                          "usd": turn_usd, **{k: usage.get(k, 0) for k in list(totals) + ["reasoning_tokens"]}})
        spans.append(_span(trace_id, root, "chat %s" % model.name, t0, dt,
                           {"gen_ai.operation.name": "chat", "gen_ai.request.model": model.name,
                            "gen_ai.response.model": usage.get("served_by", model.name),
                            "gen_ai.usage.input_tokens": usage["input_tokens"],
                            "gen_ai.usage.output_tokens": usage["output_tokens"]}))
        messages.append({"role": "assistant", "content": content})
        texts.extend(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")
        if stop in ("refusal", "content_filter"):
            error = stop
            break
        if not uses:
            messages.append({"role": "user", "content": "Use the tools, then call submit_answer."})
            continue
        results = []
        for b in uses:
            if stop == "max_tokens":  # reply was cut off: tool input may be partial, do not run it
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": "error: your reply was cut off; send the call again"})
                continue
            if b.name == "submit_answer":
                answer = dict(b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": "recorded"})
                continue
            if len(calls) >= MAX_TOOL_CALLS:
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": "error: tool-call budget used up; call submit_answer now"})
                continue
            t1 = time.time()
            try:
                out, meta = backend.call(incident_id, arm, b.name, dict(b.input))
            except InfraError as e:
                error, infra = "infra: %s" % e, True
                break
            except Exception as e:  # noqa: BLE001 - surface to the agent as a tool error
                out, meta = "tool error: %s" % e, {"tool_ms": (time.time() - t1) * 1000}
            tool_ms += meta.get("tool_ms", 0)
            outputs.append(out)
            calls.append({"turn": turn, "t_ms": round((t1 - t_start) * 1000), "tool": b.name, "input": dict(b.input),
                          "chars": len(out), "output": out, "wandered": _wandered(arm, dict(b.input)), **meta})
            spans.append(_span(trace_id, root, "execute_tool %s" % b.name, t1, meta.get("tool_ms", 0),
                               {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": b.name,
                                "agentlab.result_chars": len(out), "agentlab.backend": backend.name}))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        if infra:
            break
        messages.append({"role": "user", "content": results})
        if answer is not None:
            break
    wall = (time.time() - t_start) * 1000
    g = spec["grade"](spec, answer, outputs)
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
                   "truth": spec["truth"], "held_out": spec["held_out"], "variant": spec.get("variant", ""), "turns": len(turns_log),
                   "tokens": totals, "usd": usd, "model_ms": model_ms, "tool_ms": tool_ms, "calls": calls,
                   "turns_log": turns_log, "infra_error": infra,
                   "served_by": sorted({t["served_by"] for t in turns_log}),
                   "model_mismatch": any(not str(t["served_by"]).startswith(model.name) for t in turns_log),
                   "stop_reason": stop, "assistant_text": texts,
                   "wandered": any(c["wandered"] for c in calls)},
    }


def _wandered(arm, args):
    """Files-arm shell commands that tried to leave the arm's directory (refused by the backend; recorded)."""
    return arm == "files" and leaves_dir(args.get("command", ""))


def _span(trace_id, parent, name, t0, dur_ms, attrs, span_id=None):
    return {"trace_id": trace_id, "span_id": span_id or secrets.token_hex(8), "parent_id": parent, "name": name,
            "start_ms": round(t0 * 1000, 1), "dur_ms": round(dur_ms, 2), "attrs": attrs}
