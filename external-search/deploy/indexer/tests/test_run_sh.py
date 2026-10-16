"""run.sh: syntax, configuration checks, and a failing indexer does not stop the loop (fake indexer command)."""
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import ROOT

RUN_SH = os.path.join(ROOT, "run.sh")


class TestRunSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = os.path.join(self.tmp.name, "calls")
        self.fake = os.path.join(self.tmp.name, "fake_indexer.sh")
        with open(self.fake, "w") as f:
            f.write('#!/bin/sh\necho "$*" >> "%s"\nexit "${FAKE_RC:-0}"\n' % self.calls)
        os.chmod(self.fake, 0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, **env):
        full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "STATE_DIR": os.path.join(self.tmp.name, "state"),
                "INDEXER_CMD": self.fake, "INTERVAL_SECONDS": "1", "RUN_LOOPS": "2"}
        full.update(env)
        return subprocess.run(["sh", RUN_SH], env=full, capture_output=True, text=True, timeout=30)

    def calls_made(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return f.read().split("\n")[:-1]

    def test_syntax(self):
        for shell in ("sh", "dash"):
            if shutil.which(shell):
                subprocess.run([shell, "-n", RUN_SH], check=True)

    def test_failing_indexer_keeps_looping(self):
        p = self.run_sh(FAKE_RC="1", RECONCILE_AT="23:59")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.calls_made()[:2], ["--incremental", "--incremental"])
        self.assertIn("exited with code 1, continuing", p.stdout)

    def test_reconcile_once_a_day(self):
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state)
        with open(os.path.join(state, "last_reconcile"), "w") as f:
            f.write("2000-01-01\n")
        p = self.run_sh(RECONCILE_AT="00:00", RUN_LOOPS="3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.calls_made(), ["--incremental", "--reconcile", "--incremental", "--incremental"])

    def test_first_start_after_reconcile_time_skips_today(self):
        p = self.run_sh(RECONCILE_AT="00:00")
        self.assertEqual(p.returncode, 0)
        self.assertNotIn("--reconcile", self.calls_made())

    def test_extra_arguments_forwarded(self):
        full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "STATE_DIR": os.path.join(self.tmp.name, "state"),
                "INDEXER_CMD": self.fake, "INTERVAL_SECONDS": "1", "RUN_LOOPS": "1", "RECONCILE_AT": "23:59"}
        subprocess.run(["sh", RUN_SH, "--env-file", "x.env"], env=full, capture_output=True, timeout=30)
        self.assertEqual(self.calls_made(), ["--incremental --env-file x.env"])

    def test_invalid_settings_exit_2(self):
        for env in ({"INTERVAL_SECONDS": "5m"}, {"INTERVAL_SECONDS": "0"}, {"RECONCILE_AT": "25:00"}):
            with self.subTest(env=env):
                self.assertEqual(self.run_sh(**env).returncode, 2)
        self.assertEqual(self.calls_made(), [])


if __name__ == "__main__":
    unittest.main()
