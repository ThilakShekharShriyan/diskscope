"""Build one incident's on-disk layout. Stdlib only; also runs inside an Archil exec.

<root>/<incident>/files/   logs.jsonl spans.jsonl metrics.csv README.md   (files arm)
<root>/<incident>/sql/     telemetry.db                                  (sql arm)
<root>/<incident>/digest/  digest.txt index.json                         (digest arm)
<root>/<incident>/grafana/ metrics.om loki.json                          (grafana arms)
"""
import hashlib
import json
import os
import shutil

try:  # package import locally, flat namespace when shipped to a microVM
    from . import bundle, digest, generate
except ImportError:  # pragma: no cover
    import bundle, digest, generate  # noqa: E401

ARM_FILES = {"files": ["logs.jsonl", "spans.jsonl", "metrics.csv", "README.md"], "sql": ["telemetry.db"],
             "grafana": ["metrics.om", "loki.json"]}


def prepare(incident, root, baselines=None):
    data = generate.generate(incident["effects"], seed=incident["seed"], baselines=baselines)
    base = os.path.join(root, incident["id"])
    tmp = os.path.join(base, "_all")
    bundle.write_bundle(data, tmp)
    for arm, names in ARM_FILES.items():
        os.makedirs(os.path.join(base, arm), exist_ok=True)
        for n in names:
            shutil.copyfile(os.path.join(tmp, n), os.path.join(base, arm, n))
    text, index = digest.digest(data)
    os.makedirs(os.path.join(base, "digest"), exist_ok=True)
    with open(os.path.join(base, "digest", "digest.txt"), "w") as f:
        f.write(text)
    with open(os.path.join(base, "digest", "index.json"), "w") as f:
        json.dump(index, f)
    shutil.rmtree(tmp, ignore_errors=True)  # local only; on Archil the exec owns it, so this works there too
    return {"id": incident["id"], "calls": data["calls"],
            "digest_sha": hashlib.sha256(text.encode()).hexdigest()[:12]}
