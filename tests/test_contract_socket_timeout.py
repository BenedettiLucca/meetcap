import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import meetcap


class SocketTimeoutContractTests(unittest.TestCase):
    """CT-3: client_handler and send_command must respect I/O timeouts, not hang."""

    def test_client_handler_terminates_with_mute_peer(self):
        """Handler must exit within timeout when peer never sends data."""
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)

        handler_started = threading.Event()
        handler_done = threading.Event()

        def run_handler():
            handler_started.set()
            meetcap.client_handler(server)
            handler_done.set()

        thread = threading.Thread(target=run_handler)
        thread.start()
        self.assertTrue(handler_started.wait(timeout=2), "handler thread did not start")

        # Peer stays mute — handler must not block forever.
        alive_after = thread.is_alive()
        thread.join(timeout=5)
        self.assertFalse(
            thread.is_alive(),
            "contracts #19: client_handler did not terminate within 5s with a mute peer; "
            "handler must use settimeout to avoid blocking indefinitely",
        )

    def test_send_command_raises_when_socket_never_responds(self):
        """send_command must not hang when daemon accepts but never replies."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "meetcap.sock"

        # Set up a listener that accepts but never reads or writes.
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(str(sock_path))
        server.listen(1)
        server.settimeout(2.0)

        def accept_and_ignore():
            try:
                conn, _ = server.accept()
                # Intentionally do nothing — simulates a hung daemon.
                time.sleep(5)
                conn.close()
            except OSError:
                pass

        accept_thread = threading.Thread(target=accept_and_ignore, daemon=True)
        accept_thread.start()

        # Give the accept time to happen.
        time.sleep(0.3)

        with patch.object(meetcap, "SOCKET_PATH", sock_path):
            with self.assertRaises((OSError, TimeoutError, SystemExit)):
                meetcap.send_command("status")

    def test_send_command_thread_joins_within_timeout_on_hung_daemon(self):
        """Even if send_command doesn't raise, the call must return within timeout."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "meetcap.sock"

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(str(sock_path))
        server.listen(1)
        server.settimeout(2.0)

        def accept_and_ignore():
            try:
                conn, _ = server.accept()
                time.sleep(10)
                conn.close()
            except OSError:
                pass

        accept_thread = threading.Thread(target=accept_and_ignore, daemon=True)
        accept_thread.start()
        time.sleep(0.3)

        called = threading.Event()
        result = [None]
        error = [None]

        def call_send():
            try:
                result[0] = meetcap.send_command("status")
            except Exception as exc:
                error[0] = exc
            called.set()

        # Point send_command at the hung listener explicitly (contract fix: the
        # removed /tmp glob discovery was a cross-user isolation hole, issue #18).
        with patch.object(meetcap, "SOCKET_PATH", sock_path):
            thread = threading.Thread(target=call_send)
            thread.start()
            finished = thread.join(timeout=5) is None

        self.assertTrue(
            called.wait(timeout=6),
            "contracts #19: send_command did not return within 5s against a hung daemon; "
            "it must use a timeout and raise instead of blocking forever",
        )


if __name__ == "__main__":
    unittest.main()
