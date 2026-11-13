# Findings for the Archil team

Behaviours observed while benchmarking Archil serverless exec and sandboxes with [DiskScope](README.md) and
its state-correctness suite [BranchLab](branchlab/), on 2026-11-13 in `aws-us-east-1`, using
`POST /api/disks/{id}/exec` (default image unless noted), `POST /api/exec`, and the `disk@1.12.1` SDK for
sandboxes. The exec image's client reported `archil` v0.10.3. Each item says what Archil's docs say, what we
measured, and whether a conclusion is **verified by a repro** or **inferred**. Every code block below was
re-run verbatim on 2026-11-13 and produced the output shown.

All repros need only an API key and a disk ID:

```bash
export ARCHIL_API_KEY=key-...  ARCHIL_DISK_ID=dsk-...
HOST=https://control.green.us-east-1.aws.prod.archil.com
x() { jq -n --arg c "$1" '{command:$c}' | curl -sS -X POST "$HOST/api/disks/$ARCHIL_DISK_ID/exec" \
        -H "Authorization: Bearer $ARCHIL_API_KEY" -H 'Content-Type: application/json' -d @- \
      | jq -r '.data | "exit=\(.exitCode) executeMs=\(.timing.executeMs)\n\(.stdout)\(.stderr)"'; }
```

| # | Finding | Kind |
|---|---|---|
| 1 | Shared-mode execs can't rename, `chmod` or delete another exec's entries; `archil checkout` inside the command fixes it | Documented in part; gaps |
| 2 | SQLite fails in any directory an earlier exec created — even creating a new database | Consequence of #1, undocumented |
| 3 | The ~30 s delay for new files is anchored to the reader's failed lookup, and `--readdir-expiry 0` doesn't shorten it | ~30 s documented in a guide; mechanism and knob are new |
| 4 | A command that writes pays ~70 ms more `executeMs` | Measured |
| 5 | `sandbox.exec()` is slower per call than a serverless exec | Measured; cause inferred |
| 6 | Docs gaps: custom-image queue time, default image contents, exclusive sandbox mounts | Docs |

---

## 1. Shared-mode execs can't rename, `chmod` or delete another exec's entries

**What the docs say.** Serverless exec mounts disks in shared mode, so you need to run `archil checkout`
before changing files ([Serverless execution](https://docs.archil.com/compute/serverless-execution)). In shared
mode, creating a file or directory takes a delegation automatically, but deleting needs a delegation on the
parent, so removal fails with `EROFS` ([Sharing disks](https://docs.archil.com/concepts/sharing-disks)).

**What we measured** (BranchLab `semantics`, n = 8 trials, identical every time). One exec creates entries;
a later exec tries ten operations on them:

| Operation on another exec's entries | Default exec | After `archil checkout <dir>` | Docs |
|---|---|---|---|
| overwrite, append, create file, `mkdir` | ✓ 8/8 | ✓ 8/8 | ✓ covered |
| `unlink` (also in a subdirectory) | ✕ `EROFS` 8/8 | ✓ 8/8 | ✓ covered |
| `rename`, and **write-temp-then-rename** | ✕ `EROFS` 8/8 | ✓ 8/8 | not mentioned |
| `chmod` | ✕ `EROFS` 8/8 | ✓ 8/8 | not mentioned |
| SQLite create + commit (see #2) | ✕ `disk I/O error` 8/8 | ✓ 8/8 | not mentioned |

The `archil` CLI is installed in the exec image, so the documented fix works on the single-disk endpoint:
`archil checkout` inside the command took a median **13.5 ms** (12.8–14.4 ms, n = 8).

```bash
x 'mkdir -p /mnt/archil/archil-repro/1 && echo v1 > /mnt/archil/archil-repro/1/a && echo created'   # exec 1
x 'echo v2 > /mnt/archil/archil-repro/1/a.tmp && mv /mnt/archil/archil-repro/1/a.tmp /mnt/archil/archil-repro/1/a'   # exec 2
#  -> mv: cannot move '.../1/a.tmp' to '.../1/a': Read-only file system
x 'archil checkout /mnt/archil/archil-repro/1 && mv /mnt/archil/archil-repro/1/a.tmp /mnt/archil/archil-repro/1/a && cat /mnt/archil/archil-repro/1/a'
#  -> v2
```

**What would help:**
- Show `archil checkout <dir> && …` in a command on the serverless-execution page, and say that renames —
  including the write-temp-then-rename pattern most tools use to save files safely — and `chmod` need it,
  not only deletes. Our first reading of `EROFS` on a read-write mount was "something is broken".
- `POST /api/disks/{id}/exec` accepts a `checkoutPaths` field with HTTP 200 and silently ignores it (it is a
  `POST /api/exec` mount option). Rejecting unknown fields would make the mistake visible.

## 2. SQLite fails in any directory an earlier exec created

**Verified.** A consequence of #1 worth its own note, because the
[performance tips](https://docs.archil.com/details/performance-tips) give SQLite tuning advice, including for
WAL mode (which fails across execs), and the failure looks like data loss. SQLite's default journal mode deletes its rollback journal on every commit, which needs
a delegation on the directory. So in a directory another exec created, even **creating** a database fails,
and a database committed earlier becomes unreadable from a default exec once a later commit fails:

```bash
x 'mkdir -p /mnt/archil/archil-repro/2 && echo created'                                                          # exec 1
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/new.db\");d.execute(\"create table t(v)\");d.commit()"'
#  -> sqlite3.OperationalError: disk I/O error     (creating a database, exec 2)
x 'archil checkout /mnt/archil/archil-repro/2 && python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"create table t(v)\");d.execute(\"insert into t values (1)\");d.commit();print(\"ok\")"'
#  -> ok                                           (with a checkout, exec 3)
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"insert into t values (2)\");d.commit()"'
#  -> sqlite3.OperationalError: disk I/O error     (no checkout; app.db-journal is left behind, exec 4)
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"file:/mnt/archil/archil-repro/2/app.db?mode=ro\",uri=True);print(d.execute(\"select count(*) from t\").fetchone())"'
#  -> sqlite3.OperationalError: attempt to write a readonly database   (even read-only, exec 5)
x 'archil checkout /mnt/archil/archil-repro/2 && python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"insert into t values (3)\");d.commit();print(\"rows\",d.execute(\"select count(*) from t\").fetchone()[0])"'
#  -> rows 2                                       (a checkout rolls back the hot journal and recovers, exec 6)
```

Journal modes in a directory from an earlier exec, without a checkout: `DELETE` ✗ · `WAL` ✗ · `TRUNCATE` ✓ ·
`PERSIST` ✓ · `MEMORY` ✓. A short "SQLite across execs" note — run `archil checkout` on the database's
directory, or use `journal_mode=TRUNCATE` — would prevent data that looks lost.

## 3. The ~30 s new-file delay comes from the reader's failed lookup, and `--readdir-expiry 0` doesn't shorten it

**What the docs say.** The guide [What Archil does](https://archil.com/guides/storage-for-agents/what-archil-does)
says "A new file takes about 30 s to appear on another machine by default" and recommends running
`archil invalidate-cache` before reading. The reference pages say "opening a file rechecks it with the disk",
attributes are cached up to 30 s and listings up to five minutes, adjustable with `archil set-cache-expiry`
([Consistency](https://docs.archil.com/details/consistency), [Caching](https://docs.archil.com/details/caching)),
but don't mention failed lookups. The CLI's help says `--readdir-expiry 0` "makes every listing and every lookup
of an absent name resolve against the server".

**What we measured** (BranchLab `visibility`, n = 6 trials). One long-lived reader exec primes its caches,
then polls; a separate exec creates new files and overwrites existing ones 3, 12 or 20 s later. Each access
path has its own directory, so probes can't refresh each other's caches.

| Access path in the reader | Change first seen, counted from when the reader **looked** | Stale after the write |
|---|---|---|
| `open()` / `stat()` of a new file, after looking it up once | **30.00–30.02 s, every trial** | 9.5–26.7 s |
| …with `archil set-cache-expiry --readdir-expiry 0` on that directory first | 30.00–30.02 s — **no effect** | 9.5–26.7 s |
| `listdir()`, non-empty directory | not within 90 s (6/6) — consistent with the 5-minute listing cache | > 70 s |
| `listdir()`, empty directory never looked up in | immediately | ≈ 0 s |
| re-`open()` of an overwritten file | immediately — "open rechecks" ✓ | ≈ 0 s |
| `stat()` size / already-open fd of an overwritten file | 30.30–30.37 s — the documented 30 s attribute cache | 9.8–27.0 s |

The new-file window ends 30 s after the **failed lookup**, regardless of when the file was written (writer at
+3, +12 and +20 s; seen at +30.0 s every time). So `open()` rechecks files that exist, but a name that was
missing stays missing for 30 s; the directory's listing is stale too once a lookup in it has failed.
`archil invalidate-cache /mnt/archil` fixes it in about 7 ms. **Inferred:** a constant 30.00 s that
`--readdir-expiry 0` doesn't change looks like a kernel FUSE negative-entry timeout rather than Archil's own
cache.

```bash
x 'mkdir -p /mnt/archil/archil-repro/3 && echo created'                                      # exec 1
x 'python3 -c "import os,time,subprocess as s;f=\"/mnt/archil/archil-repro/3/x\";print(\"t+0 \",os.path.exists(f));time.sleep(12);print(\"t+12\",os.path.exists(f),os.listdir(\"/mnt/archil/archil-repro/3\"));s.run([\"archil\",\"invalidate-cache\",\"-q\",\"/mnt/archil\"]);print(\"after invalidate-cache\",os.path.exists(f))"' &   # exec 2: long-lived reader
sleep 5; x 'echo hi > /mnt/archil/archil-repro/3/x && echo written'                          # exec 3, while exec 2 runs
wait
#  exec 2 -> t+0  False
#            t+12 False []                  (7 s after exec 3 wrote x and returned)
#            after invalidate-cache True
```

**What this adds to the guide's "about 30 s":** the window is exactly 30 s from the reader's *failed lookup*, not
from the write — a reader that never looked sees the file at once, and one that looked just before the write
waits the full 30 s; the directory's listing goes stale too; and the documented per-directory knob doesn't
shorten it.

**What would help:** make `--readdir-expiry 0` cover absent-name lookups as its help text says (or correct the
help text), and note failed-lookup caching on the Consistency and Caching reference pages next to "opening a
file rechecks it" — that's where we looked first, and it is what "poll until the file appears" handoffs between
agents run into.

## 4. Performance — a command that writes pays ~70 ms more in `executeMs`

**Verified twice** with shell-only commands (default image, n = 6 each, two sessions): `true` has
`executeMs` of **3–4 ms**; `echo x > /mnt/archil/<file>` has **56–92 ms** (median ≈ 77 ms). That matches the
documented commit at exit ("When a command finishes, its writes to the mounted disk are committed before the
response returns"). Inside a persistent sandbox, a 4 KiB write + fsync completes end-to-end in **86 ms**
including the network. Documenting this cost — and whether it scales with the amount written — would help
users choose between exec and sandboxes.

## 5. Performance — `sandbox.exec()` is slower per call than a serverless exec

**Measured; cause inferred.** With one warm sandbox (1 vCPU / 2 GiB), `sandbox.exec("python3 …")` took a
median **434 ms** per no-op (n = 10), versus **77 ms** for the same work sent over stdin to one long-lived
process (`sandbox.run()` + `sendInput`), and **157 ms** for a serverless exec (default 2 vCPU / 4 GiB). The
gap is likely process-connection setup (connection URL, WebSocket and TLS) on every `exec()`. Reusing a
connection across `exec()` calls in the SDK, or documenting the long-lived-process pattern, would make warm
sandboxes the fastest option rather than the slowest.

## 6. Docs — gaps that cost us time

- **Custom image cost.** The docs say a non-default image "boots on demand" with higher `queueMs`, without a
  number. Default image: **30–33 ms**, every time (n = 10+). `python:3.13-slim`: **134–161 ms** across 46
  back-to-back calls in one run, but an hour later it was **bimodal** — about 140 ms or **1.3–3.2 s**
  (8 of 13 calls in the slow mode). Worth documenting both the typical cost and the slow mode.
- **Default image contents** aren't listed. It is Ubuntu 22.04 with **Python 3.10.12** and the `archil` CLI,
  so many Python workloads don't need `baseImage` at all.
- **Exclusive sandbox mounts.** A plain sandbox mount takes the disk's exclusive root delegation (from the
  SDK typings); worth stating on the sandbox page, since it blocks other writers while the sandbox runs.

---

Raw data for every number above: [`data/`](data/) and [`data/branchlab/`](data/branchlab/). Measurement code:
[`src/`](src/), [`bench/sandbox/`](bench/sandbox/), [`branchlab/`](branchlab/). Anything the repros create
under `/mnt/archil/archil-repro/` can be removed afterwards with
`x 'archil checkout /mnt/archil && rm -rf /mnt/archil/archil-repro && echo removed'`.
