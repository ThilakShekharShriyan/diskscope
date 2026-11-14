"""upload-archil: page-delta patches, part sizes, the finalize program, and a resumable run
against a local directory standing in for the disk (no network)."""
import hashlib
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from agentlab import prepare, rcaeval, upload
from agentlab.archil import MAX_COMMAND_BYTES


class FakeArchil:
    """Runs exec commands locally with /mnt/archil[/d] mapped to a temp dir."""
    def __init__(self, disk):
        self.disk, self.calls = disk, []

    def _run(self, cmd):
        self.calls.append(cmd)
        assert len(cmd.encode()) <= MAX_COMMAND_BYTES, len(cmd)
        if "find . -type f -printf" in cmd:  # GNU find: emulate
            base = cmd.split("cd ", 1)[1].split(" ", 1)[0]
            lines = []
            for dp, _, fns in os.walk(base):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    lines.append("%d %s" % (os.path.getsize(p), os.path.relpath(p, base)))
            out, code = "\n".join(lines), 0
        else:
            p = subprocess.run(["/bin/sh", "-c", cmd.replace("python3 -c", "%s -c" % sys.executable)],
                               capture_output=True, text=True)
            out, code = p.stdout, p.returncode
            if p.returncode:
                out += p.stderr
        return {"stdout": out, "stderr": "", "exit_code": code, "wall_ms": 1.0, "queue_ms": 0, "execute_ms": 1}

    def exec(self, cmd, timeout=120, retry=False):
        return self._run(cmd)

    def exec_conditional(self, cmd, timeout=300):
        return self._run(cmd)


class PatchTest(unittest.TestCase):
    def test_roundtrip_delta_and_full(self):
        rng = random.Random(1)
        local = bytes(rng.getrandbits(8) for _ in range(upload.PAGE * 20 + 123))
        cand = bytearray(local)
        for i in (0, 3, 4, 19, 20):  # pages 3,4 adjacent; 20 is the partial last page
            cand[i * upload.PAGE + 7] ^= 0xFF
        parts, n = upload.patch_parts(local, upload.page_hashes(bytes(cand)))
        self.assertEqual(n, 5)
        f = io.BytesIO(bytes(cand))
        upload.apply_records(__import__("lzma").decompress(b"".join(parts)), f)
        self.assertEqual(f.getvalue(), local)
        parts, n = upload.patch_parts(local, None)  # no candidate: whole file
        self.assertEqual(n, 21)
        f = io.BytesIO()
        upload.apply_records(__import__("lzma").decompress(b"".join(parts)), f)
        self.assertEqual(f.getvalue(), local)

    def test_part_commands_fit(self):
        part = os.urandom(upload.PART_RAW)  # incompressible worst case
        cmd = upload.part_command("/mnt/archil/d/" + "x" * 60, "f" * 64, 99999, part)
        self.assertLessEqual(len(cmd.encode()), MAX_COMMAND_BYTES)
        parts, _ = upload.patch_parts(os.urandom(300000))
        self.assertTrue(all(len(p) <= upload.PART_RAW for p in parts))
        self.assertGreater(len(parts), 1)

    def test_rebuild_command_fits_and_never_fetches_the_answer(self):
        c = rcaeval.load_subset()[0]["case"]
        cmd = upload.rebuild_command(c, {"files/x/logs.jsonl": ("0" * 64, True)}, "/mnt/archil/d/agentlab/t")
        self.assertLess(len(cmd.encode()), MAX_COMMAND_BYTES)
        self.assertNotIn("inject_time", repr(upload.PARQUETS))

    def test_prefix(self):
        self.assertEqual(upload.check_prefix("/agentlab/rc1/"), "agentlab/rc1")
        for bad in ("", "/", "agentlab/../x", "a b", "a;rm"):
            with self.assertRaises(ValueError):
                upload.check_prefix(bad)


class UploaderTest(unittest.TestCase):
    def setUp(self):
        self.root, self.disk = tempfile.mkdtemp(), tempfile.mkdtemp()
        rng = random.Random(2)
        self.files = {
            "files/c1/logs.jsonl": b"".join(b'{"msg":"line %d"}\n' % i for i in range(30000)),
            "sql/c2/telemetry.db": bytes(rng.getrandbits(8) for _ in range(200000)),
            "digest/c3/drill/M1.txt": b"m1\n",
            "digest/c3/digest.txt": b"",
        }
        for rel, b in self.files.items():
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "wb") as f:
                f.write(b)
        self.fake = FakeArchil(self.disk)

    def tearDown(self):
        shutil.rmtree(self.root)
        shutil.rmtree(self.disk)

    def _up(self):
        return upload.Uploader(self.root, "agentlab/t", parallel=2, client_factory=lambda: self.fake,
                               log=lambda *_: None, wmount=self.disk, rmount=self.disk)

    def test_upload_verify_resume(self):
        s, _ = self._up().run()
        self.assertEqual((s["errors"], s["mismatched"], s["verified"]), ([], [], True))
        base = os.path.join(self.disk, "agentlab/t")
        for rel, b in self.files.items():
            with open(os.path.join(base, rel), "rb") as f:
                self.assertEqual(f.read(), b, rel)
        self.assertEqual(os.listdir(os.path.join(base, "_upload")), [])  # staging cleaned
        n = len(self.fake.calls)
        s, _ = self._up().run()  # rerun: inventory + verify only
        self.assertEqual(s["part_execs"] + s.get("finalize_execs", 0), 0)
        self.assertTrue(s["verified"])
        self.assertLessEqual(len(self.fake.calls) - n, 3)

    def test_corrupt_file_is_reported_by_verify(self):
        self._up().run()
        with open(os.path.join(self.disk, "agentlab/t/digest/c3/drill/M1.txt"), "wb") as f:
            f.write(b"m2\n")
        s, _ = self._up().run()
        self.assertEqual(s["mismatched"], ["digest/c3/drill/M1.txt"])

    def test_bad_part_is_resent(self):
        up = self._up()
        rel, b = "sql/c2/telemetry.db", self.files["sql/c2/telemetry.db"]
        info = {"size": len(b), "sha": hashlib.sha256(b).hexdigest()}
        real = up._exec

        def flaky(kind, cmd, write=True, timeout=300):
            if kind == "part" and "/p00001" in cmd and not getattr(up, "_broke", False):
                up._broke = True
                return {"stdout": "ok", "stderr": "", "exit_code": 0, "wall_ms": 1, "queue_ms": 0, "execute_ms": 1}
            return real(kind, cmd, write, timeout)
        up._exec = flaky
        npages, nparts = up.send_file(rel, info)
        self.assertGreater(nparts, 1)
        with open(os.path.join(self.disk, "agentlab/t", rel), "rb") as f:
            self.assertEqual(f.read(), b)

    def test_case_groups(self):
        case = rcaeval.load_subset()[0]["case"]
        oid = prepare.opaque("rcaeval:" + case, arm="sql")
        g, other = upload.case_groups({"sql/%s/telemetry.db" % oid: {}, "files/zzz/a": {}})
        self.assertEqual(g, {case: ["sql/%s/telemetry.db" % oid]})
        self.assertEqual(other, ["files/zzz/a"])


if __name__ == "__main__":
    unittest.main()
