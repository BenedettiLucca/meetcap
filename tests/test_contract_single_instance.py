import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


class SingleInstanceContractTests(unittest.TestCase):
    """CT-2: acquire_single_instance must refuse when a live daemon owns the socket."""

    def test_acquire_single_instance_exists(self):
        self.assertTrue(
            hasattr(meetcap, "acquire_single_instance"),
            "contracts #28: meetcap.acquire_single_instance() does not exist yet — "
            "lane A must implement single-instance guard that refuses when socket is live",
        )

    @unittest.skipUnless(
        hasattr(meetcap, "acquire_single_instance"),
        "acquire_single_instance not yet implemented (lane A)",
    )
    def test_returns_false_when_socket_owned_by_live_daemon(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "meetcap.sock"

        # Simulate a live daemon: bind, listen, and accept once to prove responsiveness.
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(str(sock_path))
        server.listen(1)

        accepted = threading.Event()

        def accept_loop():
            try:
                conn, _ = server.accept()
                conn.sendall(b'{"ok": true}\n')
                conn.close()
            except OSError:
                pass
            accepted.set()

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        result = meetcap.acquire_single_instance(str(sock_path))
        self.assertFalse(
            result,
            "contracts #28: acquire_single_instance returned True while a live daemon "
            "already owns the socket; it must return False without unlinking the owner's socket",
        )
        self.assertTrue(
            accepted.wait(timeout=2),
            "contracts #28: original daemon socket should still accept connections",
        )

    @unittest.skipUnless(
        hasattr(meetcap, "acquire_single_instance"),
        "acquire_single_instance not yet implemented (lane A)",
    )
    def test_returns_true_when_no_socket_exists(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "meetcap.sock"
        self.assertTrue(
            meetcap.acquire_single_instance(str(sock_path)),
            "contracts #28: acquire_single_instance should return True when no socket exists",
        )


if __name__ == "__main__":
    unittest.main()
