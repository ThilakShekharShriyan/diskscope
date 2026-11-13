# Findings for the Archil team

Reproducible behaviours observed while benchmarking Archil serverless exec and sandboxes with
[DiskScope](README.md). All were observed on 2026-11-13 in `aws-us-east-1`, on an Archil-managed disk,
using `POST /api/disks/{id}/exec` (default image unless noted) and the `disk@1.12.1` SDK for sandboxes.

Every repro below needs only an API key and a disk ID. Because of #1, anything the repros create under
`/mnt/archil/archil-repro/` cannot be deleted afterwards via exec.

```bash
export ARCHIL_API_KEY=key-...  ARCHIL_DISK_ID=dsk-...
HOST=https://control.green.us-east-1.aws.prod.archil.com
x() { jq -n --arg c "$1" '{command:$c}' | curl -sS -X POST "$HOST/api/disks/$ARCHIL_DISK_ID/exec" \
        -H "Authorization: Bearer $ARCHIL_API_KEY" -H 'Content-Type: application/json' -d @- \
      | jq -r '.data | "exit=\(.exitCode)\n\(.stdout)\(.stderr)"'; }
```

---

## 1. Bug — entries in a directory created by an earlier exec can never be removed (`EROFS`)

**Severity: high.** A later exec cannot `unlink`, `rmdir` or `rm` *anything* inside a directory that
was created by a different (earlier) exec — not even a file it created itself a moment ago. The call
fails with `EROFS (Read-only file system)` although the mount is read-write and creating/writing files
in the same directory works.

```bash
x 'mkdir -p /mnt/archil/archil-repro/1 && echo hi > /mnt/archil/archil-repro/1/a && echo created'   # exec 1
x 'rm /mnt/archil/archil-repro/1/a'                                                         # exec 2
#  -> rm: cannot remove '/mnt/archil/archil-repro/1/a': Read-only file system
x 'touch /mnt/archil/archil-repro/1/b && rm /mnt/archil/archil-repro/1/b'                           # exec 3
#  -> rm: cannot remove '/mnt/archil/archil-repro/1/b': Read-only file system   (file created in the SAME exec)
x 'rmdir /mnt/archil/archil-repro/1 || true; ls /mnt/archil/archil-repro/1'                          # the directory can't go either
```

Controlled matrix (Python `os.unlink` / `os.rmdir`, one fresh exec per column):

| Operation | Same exec that created the directory | A later exec |
|---|---|---|
| unlink a file (fsync'ed or not) | ok | **EROFS** |
| unlink a file this exec just created | ok | **EROFS** |
| rmdir an empty directory | ok | **EROFS** |
| unlink inside a directory the later exec created itself | — | ok |
| rmdir that new directory (parent from an earlier exec) | — | **EROFS** |

`fsync` is irrelevant (we initially suspected it). Inside one persistent **sandbox** the same workload
removes everything without error (0 failures in 168 task runs), so this is specific to serverless exec.

**Expected:** directory-entry removal works across execs like any other metadata change, or fails with a
documented, specific error rather than `EROFS`.

**Impact:** any exec that cleans up after a previous exec fails; scratch directories leak forever
(DiskScope has left 150+ undeletable empty directories on its test disk); and see #2.

## 2. Bug — a SQLite database created in one exec is bricked by the next exec that writes to it

**Severity: high** (direct consequence of #1, but worth tracking on its own: SQLite's default rollback
journal deletes its `-journal` file on commit).

```bash
x 'mkdir -p /mnt/archil/archil-repro/2 && python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"create table t(v)\");d.execute(\"insert into t values (1)\");d.commit();print(\"ok\")"'
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"/mnt/archil/archil-repro/2/app.db\");d.execute(\"insert into t values (2)\");d.commit()"'
#  -> sqlite3.OperationalError: disk I/O error      (and app.db-journal is left behind as a "hot" journal)
x 'python3 -c "import sqlite3;d=sqlite3.connect(\"file:/mnt/archil/archil-repro/2/app.db?mode=ro\",uri=True);print(d.execute(\"select count(*) from t\").fetchone())"'
#  -> sqlite3.OperationalError: attempt to write a readonly database   (the DB is now unreadable too)
```

Journal modes in a directory created by an earlier exec: `DELETE` ✗ disk I/O error · `WAL` ✗ disk I/O
error · `TRUNCATE` ✓ · `PERSIST` ✓ · `MEMORY` ✓. `PRAGMA journal_mode=TRUNCATE` (or `PERSIST`) is a
workaround until #1 is fixed. The Archil performance-tips page recommends SQLite on Archil, so a note
there would help users avoid data loss.

## 3. Performance — every exec that writes pays ~120 ms at exit

Median Archil-reported `executeMs` with the default image (n = 10 each): a no-op `python3 -S` guest
takes **38 ms**; adding a 4 KiB write + fsync (7 ms of measured work) takes **155 ms**; 64 tiny files
(11 ms of work) **171 ms**. The ~120 ms difference appears whenever the command wrote anything, which
matches the documented commit at exit ("command output may not have been durably committed"). Inside a
persistent sandbox the same fsync task completes end-to-end in **86 ms**. Documenting this cost (and
whether it scales with dirty data) would help users decide between exec and sandboxes.

## 4. Performance — `sandbox.exec()` costs ~430 ms per call from a client

With one warm sandbox, `sandbox.exec("python3 …")` round-trips took a median **434 ms** for a no-op
(n = 10), versus **77 ms** for the same work sent over stdin to one long-lived process
(`sandbox.run()` + `sendInput`), and **157 ms** for a serverless exec. The per-call cost seems to be
process-connection setup (connection URL + WebSocket + TLS) on every `exec`. Reusing a connection for
successive `exec()` calls in the SDK, or documenting the long-lived-process pattern, would make warm
sandboxes the fastest option instead of the slowest.

## 5. Docs — small gaps that cost us time

- The serverless docs say non-default images "boot on demand" with higher `queueMs` but give no number.
  We measured **~145 ms** for `python:3.13-slim` vs **~32 ms** for the default image — a 4.5× queue
  difference that made our first 300 calls slower than necessary.
- The default exec image's contents aren't listed. It is Ubuntu 22.04 with **Python 3.10.12**, which
  makes `baseImage` unnecessary for many Python workloads.
- A plain sandbox mount takes the disk's **exclusive** root delegation (from SDK typings). Worth stating
  on the sandbox page, since it blocks other writers to the disk while the sandbox runs.

---

Raw data for every number above: [`data/`](data/). Measurement code: [`src/`](src/), [`bench/sandbox/`](bench/sandbox/).
