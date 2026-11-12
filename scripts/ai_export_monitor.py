#!/usr/bin/env python3
"""Live terminal + browser monitor for the unified AI exporter (stdlib only)."""
from __future__ import annotations

import collections
import hmac
import http.cookies
import json
import os
import secrets
import shutil
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

STAGE_WEIGHTS = {
    "host": 2, "executables": 8, "packages": 10, "runtime": 5,
    "containers": 5, "filesystem": 35, "cli_help": 30, "reports": 5,
}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ExportMonitor:
    def __init__(self, output: Path, mode: str = "both", host: str = "127.0.0.1",
                 port: int = 8765, token_file: str | None = None):
        self.output = output
        self.monitor_dir = output / ".monitor"
        self.events_path = self.monitor_dir / "events.jsonl"
        self.state_path = self.monitor_dir / "state.json"
        self.checkpoint_path = self.monitor_dir / "checkpoint.json"
        self.mode, self.host, self.port = mode, host, port
        self.token = ""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            if not token_file:
                raise ValueError("Remote dashboard binding requires --monitor-token-file")
            token_path = Path(token_file).expanduser()
            if not token_path.is_file():
                raise ValueError("Monitor token file does not exist")
            if os.name != "nt" and token_path.stat().st_mode & 0o077:
                raise ValueError("Monitor token file must have permissions 0600 or stricter")
            self.token = token_path.read_text(encoding="utf-8").strip()
            if len(self.token) < 32:
                raise ValueError("Monitor token must contain at least 32 characters")
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.events: collections.deque[dict[str, Any]] = collections.deque(maxlen=2000)
        self.sequence = 0
        if self.events_path.is_file():
            try:
                with self.events_path.open("rb") as previous:
                    previous.seek(max(0, self.events_path.stat().st_size - 8192))
                    lines = previous.read().decode("utf-8", "replace").splitlines()
                for line in reversed(lines):
                    try:
                        self.sequence = int(json.loads(line).get("id", 0))
                        break
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
            except OSError:
                pass
        self.last_activity_monotonic = time.monotonic()
        self._cpu_previous: tuple[int, int] | None = None
        self.dashboard_url = f"http://{host}:{port}" if mode in {"both", "web"} else None
        self.started = time.monotonic()
        self.started_at = datetime.now(UTC).isoformat()
        self.state: dict[str, Any] = {
            "run_id": secrets.token_hex(8), "status": "starting", "started_at": self.started_at,
            "updated_at": self.started_at, "elapsed_seconds": 0.0, "eta_seconds": None,
            "progress_percent": 0.0, "current_stage": "initializing", "message": "Starting exporter",
            "stages": {k: {"status": "pending", "progress_percent": 0.0, "message": "Waiting"}
                       for k in STAGE_WEIGHTS},
            "counters": {"filesystem_entries": 0, "cli_invocations": 0, "cli_commands": 0,
                         "executables": 0, "models": 0, "ai_directories": 0,
                         "errors": 0, "timeouts": 0},
            "system": {}, "tools": {}, "stalled": False, "stalled_seconds": 0,
            "throughput_entries_per_second": 0.0, "throughput_cli_calls_per_second": 0.0,
            "recent_events": [], "output_dir": str(output),
        }
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.tui_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._persist()
        self.emit("run_started", {"mode": mode, "dashboard": f"http://{host}:{port}" if mode in {"both", "web"} else None})
        if mode in {"both", "web"}:
            self._start_web()
        if mode in {"both", "terminal"} and os.isatty(1):
            self.tui_thread = threading.Thread(target=self._terminal_loop, name="ai-export-tui", daemon=True)
            self.tui_thread.start()

    def _system_metrics(self) -> dict[str, Any]:
        mem_total = mem_available = None
        try:
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        try:
            disk = shutil.disk_usage(self.output)
            disk_data = {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free}
        except OSError:
            disk_data = {}
        try:
            load = list(os.getloadavg())
        except (AttributeError, OSError):
            load = []
        cpu_percent = None
        try:
            fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
            values = [int(x) for x in fields]
            total_ticks = sum(values)
            idle_ticks = values[3] + (values[4] if len(values) > 4 else 0)
            current = (total_ticks, idle_ticks)
            if self._cpu_previous:
                total_delta = current[0] - self._cpu_previous[0]
                idle_delta = current[1] - self._cpu_previous[1]
                if total_delta > 0:
                    cpu_percent = round(max(0.0, min(100.0, 100.0 * (1 - idle_delta / total_delta))), 1)
            self._cpu_previous = current
        except (OSError, ValueError, IndexError):
            pass
        return {"cpu_percent": cpu_percent, "cpu_count": os.cpu_count(),
                "memory_total_bytes": mem_total, "memory_available_bytes": mem_available,
                "disk": disk_data, "load_average": load}

    def _recalculate(self) -> None:
        stages = self.state["stages"]
        weight_total = sum(STAGE_WEIGHTS.values())
        completed = sum(STAGE_WEIGHTS[k] * (1.0 if v["status"] == "completed" else
                         max(0.0, min(1.0, v.get("progress_percent", 0.0) / 100.0)))
                         for k, v in stages.items())
        progress = min(100.0, completed / weight_total * 100.0)
        elapsed = time.monotonic() - self.started
        eta = elapsed * (100.0 - progress) / progress if progress > 0.5 and progress < 100 else (0.0 if progress >= 100 else None)
        counters = self.state["counters"]
        self.state.update({"elapsed_seconds": round(elapsed, 1), "eta_seconds": round(eta, 1) if eta is not None else None,
                           "progress_percent": round(progress, 1), "system": self._system_metrics(),
                           "throughput_entries_per_second": round(counters["filesystem_entries"] / max(elapsed, 0.001), 1),
                           "throughput_cli_calls_per_second": round(counters["cli_invocations"] / max(elapsed, 0.001), 1),
                           "stalled_seconds": int(time.monotonic() - self.last_activity_monotonic),
                           "stalled": bool(self.state["status"] == "running" and time.monotonic() - self.last_activity_monotonic > 60),
                           "updated_at": datetime.now(UTC).isoformat()})

    def _persist(self) -> None:
        self._recalculate()
        _atomic_json(self.state_path, self.state)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        with self.condition:
            self.sequence += 1
            self.last_activity_monotonic = time.monotonic()
            event = {"id": self.sequence, "run_id": self.state["run_id"],
                     "time": datetime.now(UTC).isoformat(), "type": event_type, "payload": payload}
            self.events.append(event)
            self.state["recent_events"] = list(self.events)[-100:]
            self._persist()
            self.condition.notify_all()
            self.monitor_dir.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                stream.flush()

    def stage(self, name: str, status: str, progress: float | None = None, message: str = "") -> None:
        if name not in self.state["stages"]:
            return
        with self.lock:
            stage = self.state["stages"][name]
            stage["status"] = status
            if progress is not None:
                stage["progress_percent"] = max(0.0, min(100.0, float(progress)))
            if status == "completed":
                stage["progress_percent"] = 100.0
            stage["message"] = message
            if status == "running":
                self.state["status"] = "running"
                self.state["current_stage"] = name
            self.state["message"] = message or f"{name}: {status}"
        self.emit("stage", {"stage": name, "status": status, "progress_percent": progress, "message": message})

    def update(self, message: str | None = None, **counters: int) -> None:
        with self.lock:
            for key, value in counters.items():
                if key in self.state["counters"]:
                    self.state["counters"][key] = int(value)
            if message:
                self.state["message"] = message
        self.emit("progress", {"message": message, "counters": counters})

    def tool_status(self, name: str, status: str, invocations: int = 0,
                    commands: int = 0, timeouts: int = 0, error: str | None = None) -> None:
        with self.lock:
            self.state["tools"][name] = {"status": status, "invocations": invocations,
                                         "commands": commands, "timeouts": timeouts, "error": error}
        self.emit("tool_status", {"tool": name, "status": status, "invocations": invocations,
                                   "commands": commands, "timeouts": timeouts, "error": error})

    def refresh(self) -> dict[str, Any]:
        with self.lock:
            self._persist()
            return json.loads(json.dumps(self.state))

    def checkpoint(self, stages: dict[str, Any], fingerprint: str) -> None:
        _atomic_json(self.checkpoint_path, {"schema_version": 1, "fingerprint": fingerprint,
                                             "updated_at": datetime.now(UTC).isoformat(),
                                             "stages": stages})

    def finish(self, status: str, message: str) -> None:
        with self.lock:
            self.state["status"] = status
            self.state["message"] = message
            if status == "completed":
                for stage in self.state["stages"].values():
                    if stage["status"] == "running":
                        stage["status"] = "completed"
                        stage["progress_percent"] = 100.0
            else:
                active = self.state["stages"].get(self.state.get("current_stage", ""))
                if active and active["status"] == "running":
                    active["status"] = "failed"
                    active["message"] = message
        self.emit("run_finished", {"status": status, "message": message})
        self.stop_event.set()

    def _start_web(self) -> None:
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AIExportMonitor/1.0"
            def log_message(self, fmt: str, *args: Any) -> None:
                return
            def end_headers(self) -> None:
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
                super().end_headers()
            def _valid_host(self) -> bool:
                if monitor.host not in {"127.0.0.1", "localhost", "::1"}:
                    return True
                port = monitor.server.server_port if monitor.server else monitor.port
                allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
                return self.headers.get("Host", "").lower() in allowed
            def _authorized(self) -> bool:
                if not monitor.token:
                    return True
                cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
                session = cookie.get("ai_monitor_session")
                return bool(session and hmac.compare_digest(session.value, monitor._session_token))
            def _json(self, value: Any, status: int = 200) -> None:
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            def do_GET(self) -> None:
                if not self._valid_host():
                    self.send_error(421, "Misdirected Request")
                    return
                route = urlparse(self.path).path
                if route == "/health":
                    self._json({"ok": True, "status": monitor.state["status"]})
                    return
                if not self._authorized():
                    if route == "/":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(LOGIN_HTML.encode())
                        return
                    self._json({"error": "unauthorized"}, 401)
                    return
                if route == "/":
                    raw = DASHBOARD_HTML.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if route == "/api/state":
                    self._json(monitor.refresh())
                    return
                if route == "/api/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    cursor = 0
                    try:
                        while True:
                            with monitor.condition:
                                pending = [e for e in monitor.events if e["id"] > cursor]
                                if not pending:
                                    monitor.condition.wait(timeout=10)
                                    pending = [e for e in monitor.events if e["id"] > cursor]
                            for event in pending:
                                self.wfile.write(f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                                cursor = event["id"]
                            if not pending:
                                if monitor.stop_event.is_set():
                                    break
                                self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    return
                self._json({"error": "not_found"}, 404)
            def do_POST(self) -> None:
                if not self._valid_host():
                    self.send_error(421, "Misdirected Request")
                    return
                if urlparse(self.path).path != "/api/auth":
                    self._json({"error": "not_found"}, 404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 4096:
                        self._json({"error": "invalid_request"}, 400)
                        return
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        self._json({"error": "invalid_request"}, 400)
                        return
                    supplied = str(body.get("token", ""))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._json({"error": "invalid_request"}, 400)
                    return
                if not monitor.token or not hmac.compare_digest(supplied, monitor.token):
                    self._json({"error": "unauthorized"}, 401)
                    return
                raw = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                secure = "; Secure" if monitor.host not in {"127.0.0.1", "localhost", "::1"} else ""
                self.send_header("Set-Cookie", f"ai_monitor_session={monitor._session_token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._session_token = secrets.token_urlsafe(32)
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
            self.server.daemon_threads = True
            self.dashboard_url = f"http://{self.host}:{self.server.server_port}"
            self.state["dashboard_url"] = self.dashboard_url
            self._persist()
            self.server_thread = threading.Thread(target=self.server.serve_forever, name="ai-export-web", daemon=True)
            self.server_thread.start()
            self.emit("dashboard_started", {"url": self.dashboard_url, "authenticated": bool(self.token)})
        except OSError as exc:
            self.dashboard_url = None
            self.state["dashboard_url"] = None
            self.emit("dashboard_error", {"error": str(exc)})
            self.server = None

    def _terminal_loop(self) -> None:
        while not self.stop_event.is_set():
            self._render_terminal(self.refresh())
            self.stop_event.wait(1.0)
        self._render_terminal(self.refresh())
        print("\033[?25h", end="", flush=True)

    @staticmethod
    def _render_terminal(s: dict[str, Any]) -> None:
        width = max(60, min(110, shutil.get_terminal_size((90, 24)).columns))
        pct = s["progress_percent"]
        filled = int(30 * pct / 100)
        bar = "█" * filled + "░" * (30 - filled)
        eta = f"{s['eta_seconds']:.0f}s" if s.get("eta_seconds") is not None else "calculating"
        c = s["counters"]
        sysinfo = s.get("system", {})
        lines = ["AI SERVER EXPORT MONITOR".center(width), "─" * width,
                 f"Status: {s['status'].upper():<12} Stage: {s['current_stage']:<14} Elapsed: {s['elapsed_seconds']:.0f}s  ETA: {eta}",
                 f"[{bar}] {pct:5.1f}%", s.get("message", ""), "",
                 f"Files: {c['filesystem_entries']:,} ({s['throughput_entries_per_second']:.1f}/s)  CLI calls: {c['cli_invocations']:,} ({s['throughput_cli_calls_per_second']:.1f}/s)  Help nodes: {c['cli_commands']:,}",
                 f"Executables: {c['executables']}  Models: {c['models']}  AI dirs: {c['ai_directories']}  Errors: {c['errors']}  Timeouts: {c['timeouts']}",
                 f"CPU: {sysinfo.get('cpu_percent') if sysinfo.get('cpu_percent') is not None else 'sampling'}%  RAM: {((sysinfo.get('memory_total_bytes') or 0) - (sysinfo.get('memory_available_bytes') or 0)) / 1024**3:.1f} GiB  Stalled: {s.get('stalled', False)}", "",
                 "STAGES"]
        for name, st in s["stages"].items():
            marker = {"completed": "✓", "running": "▶", "failed": "!", "cached": "↻"}.get(st["status"], "·")
            lines.append(f" {marker} {name:<14} {st['status']:<10} {st['progress_percent']:5.1f}%  {st.get('message','')[:max(10,width-48)]}")
        for name, tool in list(s.get("tools", {}).items())[:6]:
            lines.append(f" CLI {name:<18} {tool['status']:<22} {tool['invocations']:4} calls / {tool['commands']:4} nodes / {tool['timeouts']:3} timeouts")
        if sysinfo.get("memory_total_bytes"):
            used = sysinfo["memory_total_bytes"] - (sysinfo.get("memory_available_bytes") or 0)
            lines += ["", f"Memory: {used / 1024**3:.1f}/{sysinfo['memory_total_bytes'] / 1024**3:.1f} GiB"]
        lines += ["", f"Dashboard: {s.get('dashboard_url') or 'disabled'}  |  Output: {s['output_dir']}"]
        print("\033[?25l\033[2J\033[H" + "\n".join(lines), end="\033[J", flush=True)

    def close(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.server:
            self.server.shutdown()
            self.server.server_close()


LOGIN_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Export Monitor Login</title><style>body{font:16px system-ui;background:#0b1020;color:#e8eefc;display:grid;place-items:center;min-height:100vh;margin:0}.box{background:#151d31;padding:28px;border:1px solid #33415f;border-radius:16px;width:min(360px,85vw)}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;border-radius:8px;border:1px solid #465879;background:#0c1426;color:white}button{background:#3269db;border:0;cursor:pointer}</style></head><body><form class="box" id="f"><h2>AI Export Monitor</h2><p>Enter the dashboard access token.</p><input id="t" type="password" autocomplete="current-password" required><button>Sign in</button><p id="e"></p></form><script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();let token=document.getElementById('t').value;let r=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});if(r.ok)location.reload();else document.querySelector('#e').textContent='Authentication failed';}</script></body></html>'''

DASHBOARD_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Server Export Monitor</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3854;--muted:#91a1bd;--text:#edf3ff;--blue:#6aa6ff;--green:#4ade80;--amber:#fbbf24;--red:#fb7185}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:1400px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.sub{color:var(--muted)}.pill{border:1px solid var(--line);border-radius:999px;padding:6px 12px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}.panel,.metric{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.metric small{color:var(--muted)}.metric strong{display:block;font-size:26px;margin-top:5px;font-variant-numeric:tabular-nums}.progress{height:12px;background:#26334e;border-radius:9px;overflow:hidden;margin:12px 0}.progress>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#397be8,#4ade80);transition:width .3s}.columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}.stage{display:grid;grid-template-columns:125px 90px 1fr;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);align-items:center}.stage:last-child{border:0}.tag{color:var(--muted)}.bar{height:7px;background:#26334e;border-radius:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--blue)}.events{height:350px;overflow:auto;font:12px ui-monospace,monospace}.event{padding:7px 0;border-bottom:1px solid #25314a;overflow-wrap:anywhere}.ok{color:var(--green)}.run{color:var(--blue)}.bad{color:var(--red)}.warn{color:var(--amber)}.footer{color:var(--muted);margin-top:16px;font-size:12px}@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}.columns{grid-template-columns:1fr}}@media(max-width:480px){.wrap{padding:12px}.grid{gap:8px}.metric{padding:12px}.metric strong{font-size:21px}.stage{grid-template-columns:95px 75px 1fr;font-size:12px}}
</style></head><body><main class="wrap"><div class="top"><div><h1>AI Server Export Monitor</h1><div class="sub">WSL2 inventory · CLI discovery · resumable checkpoints · live telemetry</div></div><div class="pill" id="status">CONNECTING</div></div><section class="panel" style="margin-top:20px"><div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap"><b id="stage">Initializing</b><span id="eta" class="sub">ETA calculating</span></div><div class="progress"><i id="progress"></i></div><div class="sub" id="message">Waiting for exporter events…</div></section><section class="grid"><div class="metric"><small>Elapsed</small><strong id="elapsed">0s</strong></div><div class="metric"><small>Filesystem entries</small><strong id="files">0</strong></div><div class="metric"><small>CLI invocations</small><strong id="calls">0</strong></div><div class="metric"><small>Help nodes exported</small><strong id="nodes">0</strong></div><div class="metric"><small>AI executables</small><strong id="executables">0</strong></div><div class="metric"><small>Model artifacts</small><strong id="models">0</strong></div><div class="metric"><small>Timeouts</small><strong id="timeouts">0</strong></div><div class="metric"><small>Errors</small><strong id="errors">0</strong></div></section><section class="columns"><div class="panel"><h3>Pipeline stages</h3><div id="stages"></div><h3>CLI tool status</h3><div id="tools"></div></div><div class="panel"><h3>System resources</h3><div class="grid" style="grid-template-columns:1fr 1fr;margin:0"><div class="metric"><small>CPU utilization</small><strong id="cpu">—</strong></div><div class="metric"><small>Memory used</small><strong id="memory">—</strong></div><div class="metric"><small>Disk free</small><strong id="disk">—</strong></div><div class="metric"><small>Load average</small><strong id="load">—</strong></div><div class="metric"><small>AI directories</small><strong id="dirs">0</strong></div><div class="metric"><small>Filesystem rate</small><strong id="rate">0/s</strong></div><div class="metric"><small>CLI rate</small><strong id="clirate">0/s</strong></div></div><h3>Recent events</h3><div class="events" id="events"></div></div></section><div class="footer" id="output"></div></main><script>
const $=id=>document.getElementById(id),fmt=n=>Number(n||0).toLocaleString(),dur=n=>n==null?'calculating':n<60?Math.round(n)+'s':Math.floor(n/60)+'m '+Math.round(n%60)+'s';function eventLine(e){let d=document.createElement('div');d.className='event';let t=new Date(e.time).toLocaleTimeString();d.textContent=t+'  '+e.type+'  '+JSON.stringify(e.payload||{});$('events').prepend(d);while($('events').children.length>250)$('events').lastChild.remove()}
function render(s){$('status').textContent=(s.status||'unknown').toUpperCase();$('stage').textContent=(s.current_stage||'—')+' · '+(s.progress_percent||0)+'%';$('eta').textContent='ETA '+dur(s.eta_seconds);$('progress').style.width=(s.progress_percent||0)+'%';$('message').textContent=s.message||'';$('elapsed').textContent=dur(s.elapsed_seconds);let c=s.counters||{};$('files').textContent=fmt(c.filesystem_entries);$('calls').textContent=fmt(c.cli_invocations);$('nodes').textContent=fmt(c.cli_commands);$('executables').textContent=fmt(c.executables);$('models').textContent=fmt(c.models);$('timeouts').textContent=fmt(c.timeouts);$('errors').textContent=fmt(c.errors);$('dirs').textContent=fmt(c.ai_directories);let sys=s.system||{};$('cpu').textContent=sys.cpu_percent==null?'sampling':sys.cpu_percent+'%';$('rate').textContent=fmt(s.throughput_entries_per_second)+'/s';$('clirate').textContent=fmt(s.throughput_cli_calls_per_second)+'/s';if(sys.memory_total_bytes){let used=sys.memory_total_bytes-(sys.memory_available_bytes||0);$('memory').textContent=(used/1073741824).toFixed(1)+' / '+(sys.memory_total_bytes/1073741824).toFixed(1)+' GiB'}let disk=sys.disk||{};$('disk').textContent=disk.free_bytes?(disk.free_bytes/1073741824).toFixed(1)+' GiB':'—';$('load').textContent=(sys.load_average||[]).map(x=>x.toFixed(2)).join(' / ')||'—';$('status').textContent=(s.status||'unknown').toUpperCase()+(s.stalled?' · STALLED':'');$('tools').replaceChildren();for(let [name,tool] of Object.entries(s.tools||{})){let row=document.createElement('div');row.className='stage';let n=document.createElement('b');n.textContent=name;let tag=document.createElement('span');tag.className='tag';tag.textContent=tool.status;let detail=document.createElement('span');detail.className='sub';detail.textContent=tool.invocations+' calls · '+tool.commands+' nodes · '+tool.timeouts+' timeouts'+(tool.error?' · '+tool.error:'');row.append(n,tag,detail);$('tools').append(row)}$('output').textContent='Output: '+(s.output_dir||'—');$('stages').replaceChildren();for(let [name,st] of Object.entries(s.stages||{})){let row=document.createElement('div');row.className='stage';let n=document.createElement('b');n.textContent=name;let tag=document.createElement('span');tag.className='tag';tag.textContent=st.status;let bar=document.createElement('div');bar.className='bar';let i=document.createElement('i');i.style.width=(st.progress_percent||0)+'%';bar.append(i);row.append(n,tag,bar);$('stages').append(row)}}
async function init(){try{let r=await fetch('/api/state');if(r.status===401){location.reload();return}render(await r.json())}catch(e){$('message').textContent='Waiting for monitor…'}let es=new EventSource('/api/events');es.onmessage=e=>{let v=JSON.parse(e.data);eventLine(v);fetch('/api/state').then(r=>r.json()).then(render).catch(()=>{})};es.onerror=()=>{$('status').textContent='RECONNECTING'}}init();setInterval(()=>fetch('/api/state').then(r=>r.json()).then(render).catch(()=>{}),3000);
</script></body></html>'''
