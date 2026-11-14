"""Minimal Archil exec client (stdlib). Same endpoints as src/archil.rs.

Never puts the URL or disk id in errors; scrubs disk ids from all output.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

ENDPOINTS = {
    "aws-us-east-1": "https://control.green.us-east-1.aws.prod.archil.com",
    "aws-us-west-2": "https://control.green.us-west-2.aws.prod.archil.com",
    "aws-eu-west-1": "https://control.green.eu-west-1.aws.prod.archil.com",
}
_DISK_RX = re.compile(r"dsk-[0-9a-f]{16}")
MOUNT = "/mnt/archil"


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
        self.calls, self.max_calls = 0, max_calls
        self.disk_suffix = disk[-4:]

    def exec(self, command, timeout=120):
        """Run a shell command in a fresh microVM. Returns dict with stdout, stderr, exit_code, timing."""
        if self.calls >= self.max_calls:
            raise ArchilError("call budget exhausted (%d)" % self.max_calls)
        self.calls += 1
        body = json.dumps({"command": command}).encode()
        req = urllib.request.Request("%s/api/disks/%s/exec" % (self._base, self._disk), data=body, method="POST",
                                     headers={"Authorization": "Bearer " + self._key,
                                              "Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ArchilError("Archil exec failed: HTTP %d" % e.code) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise ArchilError("Archil exec failed: %s" % type(e).__name__) from None
        wall = (time.perf_counter() - t0) * 1000
        d = payload.get("data") or {}
        t = d.get("timing") or {}
        return {"stdout": scrub(d.get("stdout")), "stderr": scrub(d.get("stderr")),
                "exit_code": d.get("exitCode"), "wall_ms": wall, "queue_ms": t.get("queueMs"),
                "execute_ms": t.get("executeMs"), "total_ms": t.get("totalMs")}
