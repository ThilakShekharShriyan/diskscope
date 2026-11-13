# Findings for the Archil team

Behaviours observed while benchmarking Archil serverless exec and sandboxes with [DiskScope](README.md),
on 2026-11-13 in `aws-us-east-1`, using `POST /api/disks/{id}/exec` (default image unless noted),
`POST /api/exec`, and the `disk@1.12.1` SDK for sandboxes. Each item says whether it is **verified by a
repro** or **inferred**.

All repros need only an API key and a disk ID:

```bash
export ARCHIL_API_KEY=key-...  ARCHIL_DISK_ID=dsk-...
HOST=https://control.green.us-east-1.aws.prod.archil.com
x() { jq -n --arg c "$1" '{command:$c}' | curl -sS -X POST "$HOST/api/disks/$ARCHIL_DISK_ID/exec" \
        -H "Authorization: Bearer $ARCHIL_API_KEY" -H 'Content-Type: application/json' -d @- \
      | jq -r '.data | "exit=\(.exitCode) executeMs=\(.timing.executeMs)\n\(.stdout)\(.stderr)"'; }
```

---

## 1. Usability — the single-disk exec can't delete what an earlier exec created, and says `EROFS`

**Verified.** With `POST /api/disks/{id}/exec`, a command cannot `unlink`, `rmdir` or `rm` anything in a
directory created by a *different* exec — not even a file it created a moment earlier. It fails with
`EROFS (Read-only file system)` although the mount is read-write and creating files works.

```bash
x 'mkdir -p /mnt/archil/archil-repro/1 && echo hi > /mnt/archil/archil-repro/1/a && echo created'   # exec 1
x 'rm /mnt/archil/archil-repro/1/a'                                                                 # exec 2
#  -> rm: cannot remove '/mnt/archil/archil-repro/1/a': Read-only file system
x 'touch /mnt/archil/archil-repro/1/b && rm /mnt/archil/archil-repro/1/b'                           # exec 3
#  -> rm: cannot remove '/mnt/archil/archil-repro/1/b': Read-only file system
```

**Cause (verified):** the delegation model. The default exec mount takes no delegation checkout, so an
exec can only remove entries under directories it created itself. Through `POST /api/exec` the same delete
succeeds with either `"checkoutPaths": ["archil-repro/1"]` or `"conditional": true` on the mount.
`listDelegations()` showed no orphaned delegations, so nothing was stuck.

**What would help:**
- A clearer error than `EROFS` for "no delegation on this directory" — `EROFS` sent us looking at fsync
  and read-only mounts first.
- Saying in the serverless-exec docs that deletes (and SQLite, see #2) need `checkoutPaths` or
  `conditional`, and that the single-disk `/api/disks/{id}/exec` body has no mount options at all.
- Our earlier runs left 188 directories that default execs could not remove; one `conditional` exec
  through `/api/exec` removed them.

## 2. Usability — SQLite's default journal breaks across execs, and the database becomes unreadable

**Verified.** A consequence of #1 worth its own note: the failure is silent until the second exec, and the
performance-tips page recommends SQLite on Archil.

```bash
x 'mkdir -p /mnt/archil/archil-repro/2 && python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"create table t(v)\");d.execute(\"insert into t values (1)\");d.commit();print(\"ok\")"'
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"insert into t values (2)\");d.commit()"'
#  -> sqlite3.OperationalError: disk I/O error      (app.db-journal is left behind as a hot journal)
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"file:/mnt/archil/archil-repro/2/app.db?mode=ro\",uri=True);print(d.execute(\"select count(*) from t\").fetchone())"'
#  -> sqlite3.OperationalError: attempt to write a readonly database   (now unreadable from a default exec)
```

The rollback journal is deleted on every commit, which needs a delegation on the directory. Journal modes
in a directory from an earlier exec: `DELETE` ✗ · `WAL` ✗ · `TRUNCATE` ✓ · `PERSIST` ✓ · `MEMORY` ✓. With
`"checkoutPaths": ["<db dir>"]` on `/api/exec`, a second exec commits normally (verified). A short "SQLite
across execs" note — check out the directory, or use `journal_mode=TRUNCATE` — would prevent data that
looks lost.

## 3. Performance — a command that writes pays ~70 ms more in `executeMs`

**Verified** with shell-only commands (default image, n = 6 each): `true` has `executeMs` of **3–4 ms**;
`echo x > /mnt/archil/<file>` has **60–92 ms** (median ≈ 76 ms). That matches the documented commit at exit
("command output may not have been durably committed"). Inside a persistent sandbox, a 4 KiB write + fsync
completes end-to-end in **86 ms** including the network. Documenting this cost — and whether it scales with
the amount written — would help users choose between exec and sandboxes.

## 4. Performance — `sandbox.exec()` is slower per call than a serverless exec

**Measured; cause inferred.** With one warm sandbox (1 vCPU / 2 GiB), `sandbox.exec("python3 …")` took a
median **434 ms** per no-op (n = 10), versus **77 ms** for the same work sent over stdin to one long-lived
process (`sandbox.run()` + `sendInput`), and **157 ms** for a serverless exec (default 2 vCPU / 4 GiB). The
gap is likely process-connection setup (connection URL, WebSocket and TLS) on every `exec()`. Reusing a
connection across `exec()` calls in the SDK, or documenting the long-lived-process pattern, would make warm
sandboxes the fastest option rather than the slowest.

## 5. Docs — gaps that cost us time

- **Custom image cost.** The docs say a non-default image "boots on demand" with higher `queueMs`, without a
  number. Measured: **~143 ms** for `python:3.13-slim` vs **~33 ms** for the default image.
- **Default image contents** aren't listed. It is Ubuntu 22.04 with **Python 3.10.12**, so many Python
  workloads don't need `baseImage` at all.
- **Exclusive sandbox mounts.** A plain sandbox mount takes the disk's exclusive root delegation (from the
  SDK typings); worth stating on the sandbox page, since it blocks other writers while the sandbox runs.

---

Raw data for every number above: [`data/`](data/). Measurement code: [`src/`](src/), [`bench/sandbox/`](bench/sandbox/).
Anything the repros create under `/mnt/archil/archil-repro/` can be removed afterwards with a `conditional`
mount via `POST /api/exec`.
