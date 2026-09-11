import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class RuntimePathsContractTests(unittest.TestCase):
    """CT-1: runtime_paths module must provide isolated runtime paths per user."""

    def test_runtime_paths_module_exists(self):
        spec = importlib.util.find_spec("runtime_paths")
        self.assertIsNotNone(
            spec,
            "contracts #18/#13: runtime_paths module not found — "
            "lane A must expose runtime_dir/socket_path/pid_path/state_path/log_path",
        )

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_runtime_dir_uses_meetcap_runtime_dir_when_set(self):
        import runtime_paths

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "/custom/runtime"}):
            # Force re-import to pick up new env var.
            import importlib
            importlib.reload(runtime_paths)
            self.assertEqual(str(runtime_paths.runtime_dir()), "/custom/runtime")

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_runtime_dir_falls_back_to_xdg(self):
        import runtime_paths
        import importlib

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "", "XDG_RUNTIME_DIR": "/run/user/1000"}):
            importlib.reload(runtime_paths)
            path = runtime_paths.runtime_dir()
        self.assertIn("meetcap", str(path))
        self.assertTrue(Path(path).is_absolute())

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_runtime_dir_falls_back_to_uid_path(self):
        import runtime_paths
        import importlib

        env = {k: v for k, v in os.environ.items() if k not in ("MEETCAP_RUNTIME_DIR", "XDG_RUNTIME_DIR")}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(runtime_paths)
            path = runtime_paths.runtime_dir()
        self.assertIn(str(os.getuid()), str(path))
        self.assertTrue(Path(path).is_absolute())

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_socket_path_derived_from_runtime_dir(self):
        import runtime_paths
        import importlib

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "/tmp/rt"}):
            importlib.reload(runtime_paths)
            self.assertEqual(runtime_paths.socket_path(), Path("/tmp/rt/meetcap.sock"))

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_pid_path_derived_from_runtime_dir(self):
        import runtime_paths
        import importlib

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "/tmp/rt"}):
            importlib.reload(runtime_paths)
            self.assertEqual(runtime_paths.pid_path(), Path("/tmp/rt/meetcap.pid"))

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_state_path_derived_from_runtime_dir(self):
        import runtime_paths
        import importlib

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "/tmp/rt"}):
            importlib.reload(runtime_paths)
            self.assertEqual(runtime_paths.state_path(), Path("/tmp/rt/meetcap_state.json"))

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_log_path_derived_from_runtime_dir(self):
        import runtime_paths
        import importlib

        with patch.dict(os.environ, {"MEETCAP_RUNTIME_DIR": "/tmp/rt"}):
            importlib.reload(runtime_paths)
            self.assertEqual(runtime_paths.log_path(), Path("/tmp/rt/meetcap-daemon.log"))

    @unittest.skipUnless(
        importlib.util.find_spec("runtime_paths") is not None,
        "runtime_paths module not yet implemented (lane A)",
    )
    def test_paths_are_isolated_by_user(self):
        import runtime_paths
        import importlib

        # Two different XDG dirs must produce different socket paths.
        # MEETCAP_RUNTIME_DIR is neutralized: it outranks XDG and would mask
        # the isolation being tested when exported in the ambient environment.
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000", "MEETCAP_RUNTIME_DIR": ""}):
            importlib.reload(runtime_paths)
            path_a = runtime_paths.socket_path()
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1001", "MEETCAP_RUNTIME_DIR": ""}):
            importlib.reload(runtime_paths)
            path_b = runtime_paths.socket_path()
        self.assertNotEqual(path_a, path_b)


if __name__ == "__main__":
    unittest.main()
