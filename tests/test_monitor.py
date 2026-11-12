import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from scripts.ai_export_monitor import ExportMonitor


class MonitorTests(unittest.TestCase):
    def test_state_events_and_stage_progress(self):
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="off")
            monitor.stage("filesystem", "running", 40, "Scanning")
            monitor.update("Found files", filesystem_entries=123)
            monitor.stage("filesystem", "completed", 100, "Done")
            monitor.finish("completed", "Finished")
            state = json.loads((Path(td) / ".monitor/state.json").read_text())
            events = (Path(td) / ".monitor/events.jsonl").read_text().splitlines()
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["stages"]["filesystem"]["status"], "completed")
            self.assertEqual(state["counters"]["filesystem_entries"], 123)
            self.assertGreaterEqual(len(events), 4)
            self.assertEqual([json.loads(x)["id"] for x in events], list(range(1, len(events) + 1)))
            monitor.close()

    def test_remote_bind_requires_token_file(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            ExportMonitor(Path(td), mode="web", host="0.0.0.0")

    def test_web_health_and_state_endpoints(self):
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="web", host="127.0.0.1", port=0)
            try:
                base = monitor.dashboard_url
                with urllib.request.urlopen(base + "/health", timeout=3) as response:
                    self.assertEqual(json.loads(response.read())["ok"], True)
                with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
                    state = json.loads(response.read())
                    self.assertEqual(state["status"], "starting")
                    self.assertIn("filesystem", state["stages"])
            finally:
                monitor.close()

    def test_remote_dashboard_requires_token_and_sets_session_cookie(self):
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "token"
            token_path.write_text("test-token-value-with-at-least-32-characters", encoding="utf-8")
            token_path.chmod(0o600)
            monitor = ExportMonitor(Path(td) / "out", mode="web", host="0.0.0.0", port=0,
                                    token_file=str(token_path))
            try:
                base = monitor.dashboard_url
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + "/api/state", timeout=3)
                self.assertEqual(denied.exception.code, 401)
                request = urllib.request.Request(base + "/api/auth", data=json.dumps({"token": "test-token-value-with-at-least-32-characters"}).encode(),
                                                 headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=3) as response:
                    cookie = response.headers.get("Set-Cookie")
                    self.assertIn("HttpOnly", cookie)
                    self.assertIn("Secure", cookie)
                authorized = urllib.request.Request(base + "/api/state", headers={"Cookie": cookie.split(";", 1)[0]})
                with urllib.request.urlopen(authorized, timeout=3) as response:
                    self.assertEqual(json.loads(response.read())["status"], "starting")
            finally:
                monitor.close()

    def test_loopback_host_header_rejects_dns_rebinding(self):
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="web", host="127.0.0.1", port=0)
            try:
                request = urllib.request.Request(monitor.dashboard_url + "/api/state", headers={"Host": "attacker.example"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(denied.exception.code, 421)
            finally:
                monitor.close()

    def test_terminal_renderer_handles_live_state(self):
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="off")
            monitor.stage("packages", "running", 25, "Enumerating packages")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                monitor._render_terminal(monitor.refresh())
            self.assertIn("AI SERVER EXPORT MONITOR", output.getvalue())
            self.assertIn("packages", output.getvalue())
            monitor.close()

    def test_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="off")
            monitor.checkpoint({"packages": {"dpkg": []}}, "fingerprint")
            data = json.loads((Path(td) / ".monitor/checkpoint.json").read_text())
            self.assertEqual(data["fingerprint"], "fingerprint")
            self.assertIn("packages", data["stages"])
            monitor.close()


if __name__ == "__main__":
    unittest.main()
