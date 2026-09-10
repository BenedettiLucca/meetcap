import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runtime_paths


class RuntimePathsResolutionTests(unittest.TestCase):
    def test_precedence_meetcap_runtime_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "MEETCAP_RUNTIME_DIR": tmp,
                "XDG_RUNTIME_DIR": "/xdg/dir",
            }):
                self.assertEqual(runtime_paths.runtime_dir(), Path(tmp))

    def test_precedence_xdg_runtime_dir(self):
        with patch.dict(os.environ, {
            "MEETCAP_RUNTIME_DIR": "",
            "XDG_RUNTIME_DIR": "/run/user/4242",
        }):
            self.assertEqual(runtime_paths.runtime_dir(), Path("/run/user/4242/meetcap"))

    def test_precedence_uid_fallback(self):
        env = {k: v for k, v in os.environ.items() if k not in ("MEETCAP_RUNTIME_DIR", "XDG_RUNTIME_DIR")}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(runtime_paths.runtime_dir(), Path(f"/run/user/{os.getuid()}/meetcap"))

    def test_derived_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": tmp}):
                self.assertEqual(runtime_paths.socket_path(), Path(tmp) / "meetcap.sock")
                self.assertEqual(runtime_paths.pid_path(), Path(tmp) / "meetcap.pid")
                self.assertEqual(runtime_paths.state_path(), Path(tmp) / "meetcap_state.json")
                self.assertEqual(runtime_paths.log_path(), Path(tmp) / "meetcap-daemon.log")

    def test_ensure_runtime_dir_sets_0700_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "isolated_dir"
            result = runtime_paths.ensure_runtime_dir(target)
            self.assertTrue(target.is_dir())
            self.assertEqual(result, target)
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o700)


class PidIsMeetcapTests(unittest.TestCase):
    def test_invalid_pids(self):
        self.assertFalse(runtime_paths.pid_is_meetcap(None))
        self.assertFalse(runtime_paths.pid_is_meetcap(0))
        self.assertFalse(runtime_paths.pid_is_meetcap(-1))
        self.assertFalse(runtime_paths.pid_is_meetcap("not-a-pid"))

    def test_nonexistent_pid(self):
        self.assertFalse(runtime_paths.pid_is_meetcap(4194303))

    def test_proc_cmdline_without_meetcap(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_proc = Path(tmp) / "12345"
            fake_proc.mkdir()
            (fake_proc / "cmdline").write_bytes(b"python3\x00some_script.py\x00")
            with patch("runtime_paths.Path") as mock_path:
                def fake_path(p):
                    if str(p) == "/proc/12345/cmdline":
                        return fake_proc / "cmdline"
                    return Path(p)
                mock_path.side_effect = fake_path
                self.assertFalse(runtime_paths.pid_is_meetcap(12345))

    def test_proc_cmdline_with_meetcap(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_proc = Path(tmp) / "12345"
            fake_proc.mkdir()
            (fake_proc / "cmdline").write_bytes(b"python3\x00src/meetcap.py\x00daemon\x00")
            with patch("runtime_paths.Path") as mock_path:
                def fake_path(p):
                    if str(p) == "/proc/12345/cmdline":
                        return fake_proc / "cmdline"
                    return Path(p)
                mock_path.side_effect = fake_path
                self.assertTrue(runtime_paths.pid_is_meetcap(12345))


if __name__ == "__main__":
    unittest.main()
