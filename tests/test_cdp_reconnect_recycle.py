"""Tests for CDP connection recovery and browser process recycling.

Issue #186: after ~20 navigations Chrome's renderer grows to ~3.5 GB and the
OS kills it, dropping the CDP socket. The plugin then treats every subsequent
document as a per-item failure (680 cascading failures in 11 minutes).
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from wandao_core.browser import (
    CDPClient,
    CDPConnectionError,
    ExportError,
    register_browser_launch,
)


class FakeSocket:
    def __init__(self):
        self.closed = False
    def settimeout(self, t): pass
    def sendall(self, data):
        if self.closed:
            raise OSError("[WinError 10054]")
    def recv(self, size): return b""
    def close(self): self.closed = True


class ReconnectTests(unittest.TestCase):
    def _client(self) -> CDPClient:
        c = CDPClient("ws://127.0.0.1:9222/devtools/page/TEST")
        c.sock = FakeSocket()
        return c

    def test_connection_error_is_a_subclass_of_export_error(self):
        self.assertTrue(issubclass(CDPConnectionError, ExportError))

    def test_send_retries_once_on_connection_error(self):
        c = self._client()
        calls: list[str] = []

        def flaky_recv(label, timeout):
            calls.append(label)
            if len(calls) == 1:
                raise CDPConnectionError("simulated drop")
            return {"id": c.next_id, "result": {"ok": True}}
        c._recv_checked = flaky_recv  # type: ignore[assignment]

        reconnect_called = []
        c.reconnect = lambda: reconnect_called.append(True)  # type: ignore[assignment]

        result = c.send("Runtime.evaluate", {"expression": "1"})
        self.assertEqual(result, {"id": c.next_id, "result": {"ok": True}})
        self.assertEqual(len(reconnect_called), 1)
        self.assertEqual(len(calls), 2)

    def test_send_does_not_retry_twice(self):
        c = self._client()
        def always_drop(label, timeout):
            raise CDPConnectionError("still dead")
        c._recv_checked = always_drop  # type: ignore[assignment]
        c.reconnect = lambda: None  # type: ignore[assignment]

        with self.assertRaises(CDPConnectionError):
            c.send("Runtime.evaluate", {"expression": "1"})

    def test_reconnect_closes_socket_and_resets_state(self):
        c = self._client()
        c.pending_events.append({"method": "Foo"})
        c._heartbeat_binding_installed = True

        with patch.object(c, "close") as mock_close, \
             patch.object(c, "_find_page_websocket", return_value=None), \
             patch("wandao_core.browser.chrome_debug_available", return_value=True), \
             patch("wandao_core.browser.wait_for_debug_port"):
            with self.assertRaises(CDPConnectionError):
                c.reconnect()
        mock_close.assert_called_once()
        self.assertEqual(c.pending_events, [])
        self.assertFalse(c._heartbeat_binding_installed)


class RecycleTests(unittest.TestCase):
    def _client(self) -> CDPClient:
        c = CDPClient("ws://127.0.0.1:9222/devtools/page/TEST")
        c.sock = FakeSocket()
        return c

    def test_navigate_count_increments(self):
        c = self._client()
        c._recycle_every = 3
        c.send = lambda method, params=None, **kw: {"result": {}}  # type: ignore[assignment]
        c._restart_browser = lambda: None  # type: ignore[assignment]
        c.reconnect = lambda: None  # type: ignore[assignment]

        c.navigate("https://a.example.com")
        c.navigate("https://b.example.com")
        self.assertEqual(c._navigate_count, 2)

    def test_recycle_triggers_at_threshold(self):
        c = self._client()
        c._recycle_every = 2
        restarted: list[bool] = []
        reconnected: list[bool] = []
        c._restart_browser = lambda: restarted.append(True)  # type: ignore[assignment]
        c.reconnect = lambda: reconnected.append(True)  # type: ignore[assignment]
        c.send = lambda method, params=None, **kw: {"result": {}}  # type: ignore[assignment]

        c.navigate("https://a.example.com")
        self.assertEqual(len(restarted), 0)
        c.navigate("https://b.example.com")
        self.assertEqual(len(restarted), 1)
        self.assertEqual(len(reconnected), 1)
        self.assertEqual(c._navigate_count, 0)

    def test_recycle_disabled_when_env_zero(self):
        old = os.environ.get("WANDAO_CDP_RECYCLE_NAVIGATIONS")
        os.environ["WANDAO_CDP_RECYCLE_NAVIGATIONS"] = "0"
        try:
            c = CDPClient("ws://127.0.0.1:9222/devtools/page/TEST")
            self.assertEqual(c._recycle_every, 0)
            c.sock = FakeSocket()
            restarted: list[bool] = []
            c._restart_browser = lambda: restarted.append(True)  # type: ignore[assignment]
            c.send = lambda method, params=None, **kw: {"result": {}}  # type: ignore[assignment]
            for i in range(20):
                c.navigate(f"https://{i}.example.com")
            self.assertEqual(len(restarted), 0)
        finally:
            if old is None:
                os.environ.pop("WANDAO_CDP_RECYCLE_NAVIGATIONS", None)
            else:
                os.environ["WANDAO_CDP_RECYCLE_NAVIGATIONS"] = old


class LaunchInfoTests(unittest.TestCase):
    def test_register_and_find_launch_info(self):
        register_browser_launch(19999, exe="/usr/bin/chrome", profile_dir=Path("/tmp/test-profile"))
        c = CDPClient("ws://127.0.0.1:19999/devtools/page/X")
        info = c._find_launch_info()
        self.assertIsNotNone(info)
        self.assertEqual(info["exe"], "/usr/bin/chrome")
        self.assertIn("test-profile", info["profile_dir"])

    def test_guess_launch_info_finds_chrome_profile_dir(self):
        c = CDPClient("ws://127.0.0.1:19998/devtools/page/X")
        with patch("wandao_core.browser.default_data_dir") as mock_dir, \
             patch("wandao_core.browser.find_chrome", return_value="/usr/bin/chrome"), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True):
            profile = Path("/tmp/wandao/feishu-chrome-profile")
            mock_dir.return_value = Path("/tmp/wandao")
            with patch.object(Path, "iterdir", return_value=[profile]):
                info = c._guess_launch_info()
        self.assertIsNotNone(info)
        self.assertIn("chrome-profile", info["profile_dir"])


if __name__ == "__main__":
    unittest.main()
