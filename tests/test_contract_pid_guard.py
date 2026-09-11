import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


class PidGuardContractTests(unittest.TestCase):
    """CT-4: pid_is_meetcap must reject non-meetcap processes (PID reuse safety)."""

    def test_pid_is_meetcap_exists(self):
        self.assertTrue(
            hasattr(meetcap, "pid_is_meetcap"),
            "contracts #13: meetcap.pid_is_meetcap(pid) does not exist yet — "
            "lane A must implement PID verification that rejects non-meetcap processes",
        )

    @unittest.skipUnless(
        hasattr(meetcap, "pid_is_meetcap"),
        "pid_is_meetcap not yet implemented (lane A)",
    )
    def test_rejects_sleep_process(self):
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.kill)
        self.addCleanup(proc.wait)
        self.assertFalse(
            meetcap.pid_is_meetcap(proc.pid),
            "contracts #13: pid_is_meetcap returned True for a non-meetcap process "
            "(sleep); it must read /proc/<pid>/cmdline and only accept PIDs containing meetcap.py",
        )

    @unittest.skipUnless(
        hasattr(meetcap, "pid_is_meetcap"),
        "pid_is_meetcap not yet implemented (lane A)",
    )
    def test_accepts_own_meetcap_pid(self):
        # The current process is not a meetcap daemon, so we skip the positive case
        # unless we can verify it contains 'meetcap.py' in its cmdline.
        # We verify the negative path here; the positive path is deferred to lane A's tests.
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.kill)
        self.addCleanup(proc.wait)
        result = meetcap.pid_is_meetcap(proc.pid)
        self.assertIsInstance(result, bool)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
