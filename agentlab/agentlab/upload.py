"""Put a prepared layout (e.g. data-ext/al-rcaeval) on the Archil disk, byte for byte.

  python -m agentlab upload-archil --root ../data-ext/al-rcaeval --prefix agentlab/rc1 [--arms files,sql,digest]

Measured constraints (2026-11-14): an exec accepts at most 102,400 bytes of command and nothing
else (unknown body fields such as stdin or env are silently ignored), so pushing bytes costs one
exec per ~74 KB. Stdout of 300 KB comes back fine. The exec image has outbound HTTPS (Hugging Face,
PyPI) and pip3.

So for an RCAEval case the microVM rebuilds the layout itself: it fetches the case's Parquet files
from Hugging Face (never inject_time.txt), pip-installs the local converter's pandas/pyarrow
versions into its own /tmp, and runs this repo's conversion code (rcaeval.convert +
prepare.prepare_data). Every file whose sha256 equals the local copy's is written straight to its
final name. The SQLite files differ in a few hundred 4 KiB pages (the image's SQLite 3.37 leaves
different bytes in unused page space than the local 3.51), so the rebuilt file is kept as a
candidate and only the differing pages are sent, as an lzma-compressed patch in <=74 KB parts.
A file with no candidate (or a layout that is not an RCAEval case) is sent whole the same way.

A finalize exec applies the parts, checks the part hashes and the file's sha256, and only then
renames it to its final name: a final name on the disk is always a complete, verified file. Reruns
list the prefix and skip files already there (idempotent, resumable); staging lives in
<prefix>/_upload/<sha>/, never inside an arm's directory, and is removed after each file. A last
read-only pass re-hashes every file from fresh microVMs (verify).

Every write uses the conditional-mount endpoint (/mnt/archil/d); reads use the default mount.
"""
import base64
import concurrent.futures
import hashlib
import json
import lzma
import os
import shlex
import statistics
import struct
import sys
import threading
import time
import zlib

from . import prepare, rcaeval

HERE = os.path.dirname(os.path.abspath(__file__))
ARMS = ("files", "sql", "digest", "grafana")
PIP = "numpy==2.0.2 pandas==2.3.3 pyarrow==21.0.0"  # the local converter's versions (cp310 wheels exist)
PARQUETS = ("metrics.parquet", "logs.parquet", "traces.parquet")  # never inject_time.txt: it is the answer
PAGE = 4096                    # delta granularity (= SQLite page size)
PART_RAW = 74000               # bytes of compressed patch per exec; base64 -> 98,668 < 102,400
WMOUNT = "/mnt/archil/d"       # conditional-mount path for writes
RMOUNT = "/mnt/archil"         # default mount for read-only verification


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest(root, arms=ARMS):
    """{relpath: {"size", "sha"}} for every file under root/<arm>/."""
    out = {}
    for arm in arms:
        for dp, _, fns in os.walk(os.path.join(root, arm)):
            for fn in fns:
                if fn.startswith(".") or fn.endswith(".part"):
                    continue
                p = os.path.join(dp, fn)
                out[os.path.relpath(p, root)] = {"size": os.path.getsize(p), "sha": sha256_file(p)}
    return dict(sorted(out.items()))


def case_groups(man, salt=prepare.DEFAULT_SALT):
    """{case: [relpaths]} for RCAEval subset cases present in the manifest, plus the other files."""
    groups, seen = {}, set()
    for c in rcaeval.load_subset():
        iid = "rcaeval:" + c["case"]
        dirs = tuple("%s/%s/" % (a, prepare.opaque(iid, salt, a)) for a in ARMS)
        rels = [r for r in man if r.startswith(dirs)]
        if rels:
            groups[c["case"]] = rels
            seen.update(rels)
    return groups, [r for r in man if r not in seen]


def check_prefix(prefix):
    p = prefix.strip("/")
    if not p or ".." in p.split("/") or not all(c.isalnum() or c in "-_/." for c in p):
        raise ValueError("bad prefix %r" % prefix)
    return p


def page_hashes(data):
    return [hashlib.sha1(data[i:i + PAGE]).digest()[:4] for i in range(0, len(data), PAGE)]


def patch_parts(data, remote_hashes=None, part_raw=PART_RAW):
    """Records (offset, bytes) for the pages that differ from the remote candidate (all pages when
    there is none), lzma-compressed as one stream and cut into <= part_raw byte parts."""
    local = page_hashes(data)
    diff = [i for i, h in enumerate(local) if remote_hashes is None or i >= len(remote_hashes) or remote_hashes[i] != h]
    recs = bytearray()
    for i in diff:  # merge adjacent pages into one record
        if recs and last_end == i * PAGE:
            off, n = struct.unpack_from(">QI", recs, last_hdr)
            chunk = data[i * PAGE:(i + 1) * PAGE]
            struct.pack_into(">QI", recs, last_hdr, off, n + len(chunk))
            recs += chunk
        else:
            last_hdr = len(recs)
            chunk = data[i * PAGE:(i + 1) * PAGE]
            recs += struct.pack(">QI", i * PAGE, len(chunk)) + chunk
        last_end = i * PAGE + len(chunk)
    z = lzma.compress(bytes(recs), preset=6)
    return [z[k:k + part_raw] for k in range(0, len(z), part_raw)] or [z], len(diff)


def apply_records(stream, f):
    """Inverse of patch_parts (used by the finalize program and tests)."""
    k = 0
    while k < len(stream):
        off, n = struct.unpack_from(">QI", stream, k)
        k += 12
        f.seek(off)
        f.write(stream[k:k + n])
        k += n


# ---- programs that run inside the microVM (stdlib only, plus pandas/pyarrow from /tmp/py) ----

def _py(src):
    blob = base64.b64encode(zlib.compress(src.encode(), 9)).decode()
    return "python3 -c %s" % shlex.quote("import base64,zlib;exec(zlib.decompress(base64.b64decode('%s')))" % blob)


_REBUILD = r'''
import sys, types, json, os, time, hashlib, shutil, base64, urllib.request
T = {}; t0 = time.time()
SRC, CASE, IID, EXPECT, BASE, PAGE, HF, PARQ = %r
for name in ("generate", "bundle", "digest", "prepare", "rcaeval"):
    m = types.ModuleType(name); m.__file__ = "/tmp/" + name + ".py"; sys.modules[name] = m; exec(SRC[name], m.__dict__)
P, R = sys.modules["prepare"], sys.modules["rcaeval"]
raw = "/tmp/raw/" + CASE; os.makedirs(raw, exist_ok=True)
for n in PARQ:
    try:
        urllib.request.urlretrieve(HF + CASE + "/" + n, raw + "/" + n)
    except Exception as e:
        print("no %%s: %%s" %% (n, e), file=sys.stderr)
T["fetch"] = time.time() - t0
data = R.convert(raw); T["convert"] = time.time() - t0
P.prepare_data(IID, data, "/tmp/out", readme=R.README); del data
T["prepare"] = time.time() - t0
res, wrote = {}, 0
for rel, (exp, write) in EXPECT.items():
    p = "/tmp/out/" + rel
    if not os.path.exists(p):
        res[rel] = "missing"; continue
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    if h.hexdigest() == exp:
        res[rel] = "ok"
        if write:
            d = BASE + "/" + rel
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(p, d + ".part"); os.replace(d + ".part", d); wrote += os.path.getsize(d)
        continue
    if not write:
        res[rel] = "differs"; continue
    st = BASE + "/_upload/" + exp
    shutil.rmtree(st, ignore_errors=True); os.makedirs(st)
    shutil.copyfile(p, st + "/cand"); wrote += os.path.getsize(p)
    with open(p, "rb") as f:
        hs = b"".join(hashlib.sha1(b).digest()[:4] for b in iter(lambda: f.read(PAGE), b""))
    res[rel] = {"cand": os.path.getsize(p), "pages": base64.b64encode(hs).decode()}
T["write"] = time.time() - t0
extra = sorted(os.path.relpath(os.path.join(dp, f), "/tmp/out") for dp, _, fs in os.walk("/tmp/out") for f in fs)
extra = [r for r in extra if r not in EXPECT and not r.startswith("_tmp/")]
print("AGENTLAB_JSON=" + json.dumps({"case": CASE, "files": res, "extra": extra, "wrote": wrote,
                                     "secs": {k: round(v, 1) for k, v in T.items()}}), flush=True)
'''


def rebuild_command(case, expect, base=None):
    """One exec: fetch + convert one RCAEval case; expect = {rel: (sha256, write?)}; base = prefix dir."""
    src = {m: open(os.path.join(HERE, m + ".py")).read() for m in ("generate", "bundle", "digest", "prepare", "rcaeval")}
    prog = _REBUILD % ((src, case, "rcaeval:" + case, expect, base, PAGE, rcaeval.HF, list(PARQUETS)),)
    return ("pip3 install -q --disable-pip-version-check --no-warn-script-location --target /tmp/py %s "
            ">/tmp/pip.log 2>&1 || { tail -3 /tmp/pip.log; exit 3; }; PYTHONPATH=/tmp/py %s" % (PIP, _py(prog)))


def part_command(base, sha, k, part):
    d = "%s/_upload/%s" % (base, sha)
    return "mkdir -p %s && echo %s | base64 -d > %s/p%05d && echo ok" % (
        shlex.quote(d), base64.b64encode(part).decode(), shlex.quote(d), k)


_FINALIZE = r'''
import hashlib, lzma, os, shutil, struct, json
BASE, REL, SHA, SIZE, PARTS = %r
st = BASE + "/_upload/" + SHA
bad = []
z = b""
for k, ph in enumerate(PARTS):
    try:
        b = open("%%s/p%%05d" %% (st, k), "rb").read()
    except OSError:
        b = b""
    if hashlib.sha256(b).hexdigest()[:16] != ph:
        bad.append(k)
    z += b
if bad:
    print("AGENTLAB_JSON=" + json.dumps({"rel": REL, "bad_parts": bad})); raise SystemExit(0)
stream = lzma.decompress(z)
cand = st + "/cand"
with open(cand, "r+b" if os.path.exists(cand) else "w+b") as f:
    f.truncate(SIZE)
    k = 0
    while k < len(stream):
        off, n = struct.unpack_from(">QI", stream, k); k += 12
        f.seek(off); f.write(stream[k:k + n]); k += n
h = hashlib.sha256()
with open(cand, "rb") as f:
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
ok = h.hexdigest() == SHA
if ok:
    d = BASE + "/" + REL
    os.makedirs(os.path.dirname(d), exist_ok=True)
    os.replace(cand, d)
    shutil.rmtree(st, ignore_errors=True)
print("AGENTLAB_JSON=" + json.dumps({"rel": REL, "ok": ok}))
'''


def finalize_command(base, rel, sha, size, parts):
    return _py(_FINALIZE % ((base, rel, sha, size, [hashlib.sha256(p).hexdigest()[:16] for p in parts]),))


def parse_json_line(stdout):
    for x in (stdout or "").splitlines():
        if x.startswith("AGENTLAB_JSON="):
            return json.loads(x.split("=", 1)[1])
    return None


# ---- driver ----

class Uploader:
    def __init__(self, root, prefix, arms=ARMS, parallel=8, client_factory=None, log=print,
                 wmount=WMOUNT, rmount=RMOUNT):
        from .archil import Archil
        self.root, self.prefix, self.arms, self.parallel = root, check_prefix(prefix), arms, parallel
        self.wbase, self.rbase = "%s/%s" % (wmount, self.prefix), "%s/%s" % (rmount, self.prefix)
        self.factory = client_factory or (lambda: Archil(max_calls=100000))
        self.log, self.lock, self.local = log, threading.Lock(), threading.local()
        self.stats = {"execs": 0, "rebuild_execs": 0, "part_execs": 0, "finalize_execs": 0, "verify_execs": 0,
                      "bytes_rebuilt": 0, "bytes_sent": 0, "pages_patched": 0, "timings": []}

    def _client(self):
        if not hasattr(self.local, "c"):
            self.local.c = self.factory()
        return self.local.c

    def _exec(self, kind, cmd, write=True, timeout=300):
        c = self._client()
        r = c.exec_conditional(cmd, timeout=timeout) if write else c.exec(cmd, timeout=timeout, retry=True)
        with self.lock:
            self.stats["execs"] += 1
            self.stats[kind + "_execs"] = self.stats.get(kind + "_execs", 0) + 1
            self.stats["timings"].append({"kind": kind, "wall_ms": round(r["wall_ms"]), "queue_ms": r["queue_ms"],
                                          "execute_ms": r["execute_ms"], "cmd_bytes": len(cmd)})
        return r

    def inventory(self):
        """{relpath: size} of final files already under the prefix (staging and .part excluded)."""
        r = self._exec("inventory", "cd %s 2>/dev/null && find . -type f -printf '%%s %%P\\n' || true"
                       % shlex.quote(self.rbase), write=False)
        out = {}
        for line in r["stdout"].splitlines():
            size, _, rel = line.partition(" ")
            if rel and not rel.startswith("_upload/") and not rel.endswith(".part"):
                out[rel] = int(size)
        return out

    def send_file(self, rel, info, remote_hashes=None):
        """Send pages that differ from the remote candidate (or the whole file), then finalize."""
        data = open(os.path.join(self.root, rel), "rb").read()
        parts, npages = patch_parts(data, remote_hashes)
        with self.lock:
            self.stats["pages_patched"] += npages
            self.stats["bytes_sent"] += sum(len(p) for p in parts)
        todo = list(range(len(parts)))
        for _ in range(3):
            for k in todo:
                r = self._exec("part", part_command(self.wbase, info["sha"], k, parts[k]))
                if r["exit_code"] != 0:
                    raise RuntimeError("part %d of %s failed: %s" % (k, rel, (r["stderr"] or r["stdout"])[-200:]))
            r = self._exec("finalize", finalize_command(self.wbase, rel, info["sha"], info["size"], parts))
            j = parse_json_line(r["stdout"]) or {}
            if j.get("ok"):
                return npages, len(parts)
            if not j.get("bad_parts"):
                raise RuntimeError("finalize %s failed: %s" % (rel, (r["stderr"] or r["stdout"])[-300:]))
            todo = j["bad_parts"]
        raise RuntimeError("finalize %s: parts kept failing" % rel)

    def do_case(self, case, rels, have, man):
        need = [r for r in rels if have.get(r) != man[r]["size"]]
        if not need:
            return {"case": case, "skipped": True}
        t0 = time.time()
        expect = {r: (man[r]["sha"], r in need) for r in rels}
        r = self._exec("rebuild", rebuild_command(case, expect, self.wbase))
        j = parse_json_line(r["stdout"])
        if j is None:
            raise RuntimeError("rebuild %s failed (exit %s): %s" % (case, r["exit_code"], (r["stderr"] or r["stdout"])[-400:]))
        with self.lock:
            self.stats["bytes_rebuilt"] += j.get("wrote", 0)
        sent = {}
        for rel in need:
            st = j["files"].get(rel)
            if st == "ok":
                continue
            hashes = None
            if isinstance(st, dict):
                raw = base64.b64decode(st["pages"])
                hashes = [raw[i:i + 4] for i in range(0, len(raw), 4)]
            sent[rel] = self.send_file(rel, man[rel], hashes)
        return {"case": case, "rebuild_s": j["secs"], "patched": {k: {"pages": v[0], "parts": v[1]} for k, v in sent.items()},
                "extra": j["extra"], "secs": round(time.time() - t0, 1)}

    def verify(self, man):
        """Re-hash every file from fresh read-only microVMs, ~1 GB per exec; returns mismatches."""
        groups, cur, size = [], [], 0
        for rel in man:
            cur.append(rel)
            size += man[rel]["size"]
            if size > 1 << 30 or len(cur) >= 400:
                groups.append(cur)
                cur, size = [], 0
        if cur:
            groups.append(cur)

        def one(g):
            cmd = "cd %s && sha256sum %s" % (shlex.quote(self.rbase), " ".join(shlex.quote(r) for r in g))
            r = self._exec("verify", cmd, write=False)
            got = {}
            for line in r["stdout"].splitlines():
                h, _, rel = line.partition("  ")
                got[rel] = h
            return [rel for rel in g if got.get(rel) != man[rel]["sha"]]

        with concurrent.futures.ThreadPoolExecutor(self.parallel) as pool:
            return sorted(x for bad in pool.map(one, groups) for x in bad)

    def run(self, verify=True):
        t0 = time.time()
        man = manifest(self.root, self.arms)
        total = sum(v["size"] for v in man.values())
        self.log("local: %d files, %.2f GB (manifest %.1f s)" % (len(man), total / 1e9, time.time() - t0))
        have = self.inventory()
        groups, other = case_groups(man)
        self.log("remote already has %d files; %d RCAEval cases, %d other files" % (
            sum(1 for r in man if have.get(r) == man[r]["size"]), len(groups), len(other)))
        new = sum(man[r]["size"] for r in man if have.get(r) != man[r]["size"])
        if new:
            dirs = sorted({"%s/%s" % (self.wbase, a) for a in self.arms} | {self.wbase + "/_upload"})
            self._exec("setup", "mkdir -p %s" % " ".join(shlex.quote(d) for d in dirs))
        results, errors = [], []

        def case_job(item):
            try:
                res = self.do_case(item[0], item[1], have, man)
            except Exception as e:  # noqa: BLE001 - report, keep the other cases going
                errors.append("%s: %s" % (item[0], e))
                return
            results.append(res)
            self.log(json.dumps(res))

        def file_job(rel):
            try:
                if have.get(rel) != man[rel]["size"]:
                    self.send_file(rel, man[rel])
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %s" % (rel, e))

        with concurrent.futures.ThreadPoolExecutor(self.parallel) as pool:
            list(pool.map(case_job, sorted(groups.items())))
            list(pool.map(file_job, other))
        t_up = time.time() - t0
        bad = self.verify(man) if verify else None
        wall = time.time() - t0
        tim = self.stats["timings"]

        def med(kind, key):
            xs = [t[key] for t in tim if t["kind"] == kind and t[key] is not None]
            return round(statistics.median(xs)) if xs else None
        summary = {"files": len(man), "bytes": total, "bytes_new": new, "upload_s": round(t_up, 1),
                   "total_s": round(wall, 1), "throughput_MBps": round(new / 1e6 / max(t_up, 1e-9), 1),
                   "verified": None if bad is None else not bad, "mismatched": bad, "errors": errors,
                   **{k: v for k, v in self.stats.items() if k != "timings"},
                   "median_ms": {k: {"wall": med(k, "wall_ms"), "queue": med(k, "queue_ms"), "execute": med(k, "execute_ms")}
                                 for k in sorted({t["kind"] for t in tim})}}
        return summary, results
