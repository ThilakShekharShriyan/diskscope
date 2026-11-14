"""Minimal Archil exec client (stdlib). Same endpoints as src/archil.rs.

Never puts the URL or disk id in errors; scrubs disk ids from all output.
"""
import http.client
import json
import os
import re
import time
import urllib.parse

ENDPOINTS = {
    "aws-us-east-1": "https://control.green.us-east-1.aws.prod.archil.com",
    "aws-us-west-2": "https://control.green.us-west-2.aws.prod.archil.com",
    "aws-eu-west-1": "https://control.green.eu-west-1.aws.prod.archil.com",
}
_DISK_RX = re.compile(r"dsk-[0-9a-f]{16}")
MOUNT = "/mnt/archil"
MAX_COMMAND_BYTES = 102400  # server-side limit on the command field (HTTP 400 above it; measured 2026-11-14)


def scrub(s):
    return _DISK_RX.sub("dsk-XXXX", s or "")


class ArchilError(RuntimeError):
    pass


class Archil:
    def __init__(self, max_calls=400):
        key, disk, region = (os.environ.get(k) for k in ("ARCHIL_API_KEY", "ARCHIL_DISK_ID", "ARCHIL_REGION"))
        if not (key and disk and region):
            raise ArchilError("set ARCHIL_API_KEY, ARCHIL_DISK_ID, ARCHIL_REGION (source .env.local)")
        if region not in ENDPOINTS:
            raise ArchilError("unsupported region")
        if not _DISK_RX.fullmatch(disk):
            raise ArchilError("ARCHIL_DISK_ID must be dsk- plus 16 hex characters")
        self._key, self._disk, self._base = key, disk, ENDPOINTS[region]
        self._host = urllib.parse.urlparse(self._base).netloc
        self._conn = None  # one keep-alive HTTPS connection: no TCP+TLS handshake per tool call
        self.calls, self.max_calls = 0, max_calls
        self.disk_suffix = disk[-4:]

    def _post(self, path, body, timeout, retry):
        headers = {"Authorization": "Bearer " + self._key, "Content-Type": "application/json",
                   "Connection": "keep-alive"}
        for attempt in (0, 1):  # retry once on a dropped idle connection
            if self._conn is None:
                self._conn = http.client.HTTPSConnection(self._host, timeout=timeout)
            self._conn.timeout = timeout
            try:
                self._conn.request("POST", path, body=body, headers=headers)
                r = self._conn.getresponse()
                data = r.read()
                return r.status, data
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.CannotSendRequest, http.client.ResponseNotReady):
                self._conn.close()
                self._conn = None
                if attempt or not retry:
                    raise ArchilError("Archil exec failed: connection dropped") from None
            except (OSError, http.client.HTTPException) as e:
                self._conn.close()
                self._conn = None
                raise ArchilError("Archil exec failed: %s" % type(e).__name__) from None

    def exec_conditional(self, command, timeout=300, name="d"):
        """Run a command via the multi-disk endpoint with a conditional mount at /mnt/archil/<name>:
        writes and deletes need no `archil checkout` (shared mode would give EROFS on other execs'
        entries). Never retried: the first attempt may already have written."""
        if self.calls >= self.max_calls:
            raise ArchilError("call budget exhausted (%d)" % self.max_calls)
        if len(command.encode()) > MAX_COMMAND_BYTES:
            raise ArchilError("command is %d bytes; Archil accepts at most %d" % (len(command.encode()), MAX_COMMAND_BYTES))
        self.calls += 1
        body = json.dumps({"disks": {name: {"disk": self._disk, "conditional": True}}, "command": command}).encode()
        return self._result(body, "/api/exec", timeout, False)

    def _result(self, body, path, timeout, retry):
        t0 = time.perf_counter()
        status, raw = self._post(path, body, timeout, retry)
        if status != 200:
            try:
                why = (json.loads(raw).get("error") or "")[:200]
            except (ValueError, AttributeError):
                why = ""
            raise ArchilError(scrub("Archil exec failed: HTTP %d %s" % (status, why)).strip())
        payload = json.loads(raw)
        wall = (time.perf_counter() - t0) * 1000
        d = payload.get("data") or {}
        t = d.get("timing") or {}
        return {"stdout": scrub(d.get("stdout")), "stderr": scrub(d.get("stderr")),
                "exit_code": d.get("exitCode"), "wall_ms": wall, "queue_ms": t.get("queueMs"),
                "execute_ms": t.get("executeMs"), "total_ms": t.get("totalMs")}

    def exec(self, command, timeout=120, retry=False):
        """Run a shell command in a fresh microVM. Returns dict with stdout, stderr, exit_code, timing.

        retry=True resends once if a kept-alive connection was dropped; only for read-only commands,
        since the first attempt may already have run."""
        if self.calls >= self.max_calls:
            raise ArchilError("call budget exhausted (%d)" % self.max_calls)
        self.calls += 1
        body = json.dumps({"command": command}).encode()
        t0 = time.perf_counter()
        status, raw = self._post("/api/disks/%s/exec" % self._disk, body, timeout, retry)
        if status != 200:
            raise ArchilError("Archil exec failed: HTTP %d" % status)
        payload = json.loads(raw)
        wall = (time.perf_counter() - t0) * 1000
        d = payload.get("data") or {}
        t = d.get("timing") or {}
        return {"stdout": scrub(d.get("stdout")), "stderr": scrub(d.get("stderr")),
                "exit_code": d.get("exitCode"), "wall_ms": wall, "queue_ms": t.get("queueMs"),
                "execute_ms": t.get("executeMs"), "total_ms": t.get("totalMs")}
