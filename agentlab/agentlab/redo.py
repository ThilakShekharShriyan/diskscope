"""Re-run the infra-failed runs of a finished RunReport and merge them back.

  python -m agentlab.redo RUN.json --backend local --root DIR [--prefix P] [--parallel 3] [--max-usd 5]

Infra failures (rate limits, provider errors, Archil budget) are excluded from every
statistic, which leaves holes in the paired design. This re-runs exactly those
(task, arm, trial) jobs with the same model and effort, a few attempts each with a
pause, replaces them in RUN.json, and notes it in environment.notes.
"""
import argparse
import concurrent.futures
import json
import threading
import time

from . import harness, tools


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("run")
    p.add_argument("--backend", choices=["local", "archil"], default="local")
    p.add_argument("--root")
    p.add_argument("--prefix")
    p.add_argument("--lessons-dir")
    p.add_argument("--parallel", type=int, default=3)
    p.add_argument("--max-usd", type=float, default=5.0)
    p.add_argument("--attempts", type=int, default=3)
    a = p.parse_args(argv)
    rep = json.load(open(a.run))
    bad = [o for o in rep["observations"] if o["detail"].get("infra_error")]
    if not bad:
        print("nothing to redo")
        return
    model_name = bad[0]["detail"]["model"]
    effort = rep["environment"]["notes"].split("effort=")[-1].split()[0] if "effort=" in rep["environment"]["notes"] else "medium"
    if a.lessons_dir:
        import os
        os.environ["AGENTLAB_LESSONS_DIR"] = a.lessons_dir
    meter = harness.SpendMeter(a.max_usd)
    local = threading.local()

    def backend():
        if not hasattr(local, "b"):
            if a.backend == "local":
                local.b = tools.LocalBackend(a.root)
            else:
                from .archil import Archil
                local.b = tools.ArchilBackend(Archil(max_calls=len(bad) * (harness.MAX_TOOL_CALLS + 2) * a.attempts), a.prefix)
        return local.b

    def go(o):
        for k in range(a.attempts):
            new = harness.run_one(o["task"], o["detail"]["arm"], harness.make_model(model_name, effort), backend(),
                                  trial=o["trial"], meter=meter)
            if not new["detail"].get("infra_error") or meter.over():
                return new
            time.sleep(20 * (k + 1))
        return new

    with concurrent.futures.ThreadPoolExecutor(a.parallel) as pool:
        fixed = list(pool.map(go, bad))
    keep = [o for o in rep["observations"] if not o["detail"].get("infra_error")]
    rep["observations"] = keep + fixed
    still = sum(1 for o in fixed if o["detail"].get("infra_error"))
    rep["environment"]["notes"] += " | redo: %d infra runs re-run, %d still infra, $%.2f" % (len(bad), still, meter.spent)
    json.dump(rep, open(a.run, "w"), indent=1, default=str)
    print("%s: re-ran %d, still infra %d, spent $%.2f" % (a.run, len(bad), still, meter.spent))


if __name__ == "__main__":
    main()
