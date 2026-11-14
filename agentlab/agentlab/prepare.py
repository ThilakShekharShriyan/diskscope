"""Build one incident's on-disk layout. Stdlib only; also runs inside an Archil exec.

<root>/files/<opaque>/   logs.jsonl spans.jsonl metrics.csv README.md   (files arm)
<root>/sql/<opaque>/     telemetry.db                                  (sql arm)
<root>/digest/<opaque>/  digest.txt index.json drill/<id>.txt          (digest arm)
<root>/grafana/<opaque>/ metrics.om loki.json                          (grafana arms)

<opaque> = salted hash of the incident id, so no path an agent can see names
the incident, and each arm's data is in its own tree.
"""
import hashlib
import json
import os
import shutil

DEFAULT_SALT = "agentlab-v1"


def opaque(incident_id, salt=DEFAULT_SALT):
    return "c" + hashlib.sha256((salt + ":" + incident_id).encode()).hexdigest()[:11]

try:  # package import locally, flat namespace when shipped to a microVM
    from . import bundle, digest, generate
except ImportError:  # pragma: no cover
    import bundle, digest, generate  # noqa: E401

ARM_FILES = {"files": ["logs.jsonl", "spans.jsonl", "metrics.csv", "README.md"], "sql": ["telemetry.db"],
             "grafana": ["metrics.om", "loki.json"]}


def prepare(incident, root, baselines=None, salt=DEFAULT_SALT):
    data = generate.generate(incident["effects"], seed=incident["seed"], baselines=baselines)
    oid = opaque(incident["id"], salt)
    tmp = os.path.join(root, "_tmp", oid)
    bundle.write_bundle(data, tmp)
    for arm, names in ARM_FILES.items():
        os.makedirs(os.path.join(root, arm, oid), exist_ok=True)
        for n in names:
            shutil.copyfile(os.path.join(tmp, n), os.path.join(root, arm, oid, n))
    text, index = digest.digest(data)
    os.makedirs(os.path.join(root, "digest", oid), exist_ok=True)
    with open(os.path.join(root, "digest", oid, "digest.txt"), "w") as f:
        f.write(text)
    with open(os.path.join(root, "digest", oid, "index.json"), "w") as f:
        json.dump(index, f)
    os.makedirs(os.path.join(root, "digest", oid, "drill"), exist_ok=True)
    for k, v in index.items():  # one small file per evidence id: drill() is a plain cat
        with open(os.path.join(root, "digest", oid, "drill", k + ".txt"), "w") as f:
            f.write(v)
    shutil.rmtree(tmp, ignore_errors=True)  # local only; on Archil the exec owns it, so this works there too
    return {"id": incident["id"], "dir": oid, "calls": data["calls"],
            "digest_sha": hashlib.sha256(text.encode()).hexdigest()[:12]}
