#!/usr/bin/env python3
"""Fair, WSL-native AI CLI help exporter.

LEGACY: superseded by scripts/ai_unified_export.py, whose `cli_help` stage
provides the same recursive help export plus monitors, budgets, checkpoints and
reporting. Kept for compatibility with earlier runs.

Discovery is PATH-only. Windows-mounted paths (/mnt/*), Windows executables,
and non-native PATH entries are excluded. No filesystem-wide find is performed.
Only version/help interfaces are invoked; discovered commands are never executed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

CANDIDATES = [
    "agy", "claude", "codex", "copilot", "craft-anything-cli", "gcloud",
    "gemini", "llama-server", "opencode", "aider", "goose", "ollama",
    "llm", "amp", "cursor-agent", "gh", "qwen", "cline", "roo",
    "continue", "windsurf", "droid", "kiro-cli", "amazon-q",
]
HELP_ARGS = (("--help",), ("-h",), ("help",))
VERSION_ARGS = (("--version",), ("-V",), ("version",))
COMMAND_HEADINGS = re.compile(
    r"^\s*(commands?|subcommands?|available commands|command groups)\s*:?\s*$",
    re.IGNORECASE,
)
COMMAND_LINE = re.compile(r"^\s{0,16}([A-Za-z0-9][A-Za-z0-9_.:-]*)"
                          r"(?:\s{2,}|\s+\[|\s+<|\s*$)")
SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)(\s*[=:]\s*)"
                r"([^\s,;]+)"), r"\1\2[REDACTED]"),
]

def require_target_platform() -> dict[str, str]:
    os_release: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_release[k] = v.strip().strip('"')
    except OSError as exc:
        raise RuntimeError(f"Cannot read /etc/os-release: {exc}") from exc

    version = os_release.get("VERSION_ID", "")
    proc_version = ""
    try:
        proc_version = Path("/proc/version").read_text(errors="replace").lower()
    except OSError:
        pass

    is_ubuntu_2604 = os_release.get("ID") == "ubuntu" and version == "26.04"
    is_wsl = "microsoft" in proc_version or "wsl" in proc_version or bool(
        os.environ.get("WSL_DISTRO_NAME")
    )
    if not is_ubuntu_2604 or not is_wsl:
        raise RuntimeError(
            "This exporter is restricted to WSL2 Ubuntu 26.04. "
            f"Detected ID={os_release.get('ID')!r}, VERSION_ID={version!r}, "
            f"WSL={is_wsl!r}."
        )
    return {"distribution": "Ubuntu", "version": version, "wsl": "true"}

def is_native_wsl_path(raw: str) -> bool:
    if not raw or re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    try:
        path = Path(raw).expanduser().resolve(strict=False)
        # Exclude Windows-mounted filesystems and anything resolved below /mnt.
        if path == Path("/mnt") or Path("/mnt") in path.parents:
            return False
        return path.is_absolute()
    except (OSError, RuntimeError):
        return False

def native_path() -> str:
    entries = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if is_native_wsl_path(entry) and entry not in entries:
            entries.append(entry)
    return os.pathsep.join(entries)

def redact(value: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value

def run_help(executable: str, args: tuple[str, ...], timeout: int,
             env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [executable, *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=timeout, env=env,
            check=False,
        )
        return {
            "ok": True, "returncode": proc.returncode,
            "output": redact(proc.stdout or ""),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": None,
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return {
            "ok": False, "returncode": None, "output": redact(output),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"timeout after {timeout}s",
        }
    except OSError as exc:
        return {
            "ok": False, "returncode": None, "output": "",
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }

def extract_subcommands(output: str) -> list[str]:
    names: list[str] = []
    in_section = False
    for line in output.splitlines():
        if COMMAND_HEADINGS.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[A-Z][A-Za-z0-9 /_-]{2,}:$", stripped):
            in_section = False
            continue
        if stripped.startswith(("-", "Usage:", "Options:", "Global")):
            continue
        match = COMMAND_LINE.match(line)
        if match:
            name = match.group(1)
            if name.lower() not in {"command", "commands", "help", "options"} and name not in names:
                names.append(name)
    return names

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export installed AI CLI commands with fair WSL-native discovery."
    )
    parser.add_argument("--output", default="./ai-cli-command-v2")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--max-invocations", type=int, default=10000)
    parser.add_argument("--per-tool-invocations", type=int, default=1000)
    args = parser.parse_args()

    if min(args.max_depth, args.timeout, args.max_invocations,
           args.per_tool_invocations) < 1:
        parser.error("all limits must be positive integers")

    platform_info = require_target_platform()
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["PATH"] = native_path()
    env["LC_ALL"] = "C"
    candidates = list(dict.fromkeys(CANDIDATES + args.extra))

    tools: list[dict[str, Any]] = []
    for name in candidates:
        found = shutil.which(name, path=env["PATH"])
        if not found:
            continue
        resolved = str(Path(found).resolve())
        if not is_native_wsl_path(resolved):
            continue
        tools.append({
            "name": name, "executable": resolved, "version": None,
            "commands": [], "discovery_status": "partial",
            "discovery_reason": "root help not yet processed",
            "invocations": 0, "errors": 0, "timeouts": 0,
            "_queue": deque(), "_seen": set(), "_raw": [],
            "_root_ok": False,
        })

    if not tools:
        raise RuntimeError(
            "No candidate AI CLIs found on the native WSL PATH. "
            "Windows-mounted /mnt paths are intentionally excluded."
        )

    invocation_count = 0
    errors = 0
    timeouts = 0
    commands_exported = 0

    # Phase A: probe every tool before any recursive command discovery.
    for tool in tools:
        for probe in VERSION_ARGS:
            if invocation_count >= args.max_invocations:
                break
            result = run_help(tool["executable"], probe, args.timeout, env)
            invocation_count += 1
            tool["invocations"] += 1
            if result["error"]:
                errors += 1
                tool["errors"] += 1
                if "timeout" in result["error"]:
                    timeouts += 1
                    tool["timeouts"] += 1
            if result["output"].strip():
                tool["version"] = result["output"].strip()[:2000]
                break

        for probe in HELP_ARGS:
            if invocation_count >= args.max_invocations:
                break
            result = run_help(tool["executable"], probe, args.timeout, env)
            invocation_count += 1
            tool["invocations"] += 1
            tool["_raw"].append((["root", *probe], result))
            if result["error"]:
                errors += 1
                tool["errors"] += 1
                if "timeout" in result["error"]:
                    timeouts += 1
                    tool["timeouts"] += 1
            if result["output"].strip():
                tool["_root_ok"] = True
                tool["_queue"].append(((), 0, result["output"]))
                break

    # Parse root output and enqueue direct children for fair recursive discovery.
    for tool in tools:
        if tool["_root_ok"] and tool["_queue"]:
            _, _, root_text = tool["_queue"].popleft()
            for command in extract_subcommands(root_text):
                tool["_queue"].append(((command,), 1, ""))
                tool["_seen"].add((command,))
        elif not tool["_root_ok"]:
            tool["discovery_status"] = "failed"
            tool["discovery_reason"] = "all root help probes failed or returned no output"

    # Phase B: round-robin, at most one queued branch per tool per round.
    while invocation_count < args.max_invocations:
        made_progress = False
        for tool in tools:
            queue = tool["_queue"]
            if not queue or tool["invocations"] >= args.per_tool_invocations:
                continue
            command_path, depth, _ = queue.popleft()
            made_progress = True
            if depth > args.max_depth:
                tool["discovery_reason"] = "maximum recursion depth reached"
                continue

            result = None
            for suffix in (("--help",), ("-h",)):
                if invocation_count >= args.max_invocations:
                    break
                result = run_help(
                    tool["executable"], (*command_path, *suffix), args.timeout, env
                )
                invocation_count += 1
                tool["invocations"] += 1
                tool["_raw"].append((list(command_path) + list(suffix), result))
                if result["error"]:
                    errors += 1
                    tool["errors"] += 1
                    if "timeout" in result["error"]:
                        timeouts += 1
                        tool["timeouts"] += 1
                if result["output"].strip():
                    break

            if result is None:
                queue.appendleft((command_path, depth, ""))
                break

            command_record = {
                "path": list(command_path),
                "depth": depth,
                "returncode": result["returncode"],
                "help_available": bool(result["output"].strip()),
                "duration_seconds": result["duration_seconds"],
                "error": result["error"],
                "raw_file": None,
            }
            tool["commands"].append(command_record)
            commands_exported += 1

            if result["output"].strip() and depth < args.max_depth:
                for child in extract_subcommands(result["output"]):
                    child_path = (*command_path, child)
                    if child_path not in tool["_seen"]:
                        tool["_seen"].add(child_path)
                        queue.append((child_path, depth + 1, ""))

        if not made_progress:
            break

    # Persist raw help outputs and stable JSON records.
    for tool in tools:
        tool_raw_dir = raw_dir / re.sub(r"[^A-Za-z0-9_.-]", "_", tool["name"])
        tool_raw_dir.mkdir(parents=True, exist_ok=True)
        for index, (command_args, result) in enumerate(tool["_raw"], start=1):
            filename = f"{index:04d}-{'-'.join(command_args)}.txt"
            filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
            target = tool_raw_dir / filename
            target.write_text(result["output"], encoding="utf-8")
            if command_args and command_args[0] != "root":
                key = tuple(command_args[:-1])
                for command in tool["commands"]:
                    if tuple(command["path"]) == key:
                        command["raw_file"] = str(target.relative_to(output_dir))
                        break

        if tool["discovery_status"] != "failed":
            if tool["_queue"]:
                if tool["invocations"] >= args.per_tool_invocations:
                    reason = "per-tool invocation budget reached"
                elif invocation_count >= args.max_invocations:
                    reason = "global invocation budget reached"
                else:
                    reason = "undiscovered branches remain"
                tool["discovery_status"] = "partial"
                tool["discovery_reason"] = reason
            elif tool["_root_ok"]:
                tool["discovery_status"] = "complete"
                tool["discovery_reason"] = "all discovered help branches processed"

    public_tools = [{k: v for k, v in tool.items() if not k.startswith("_")}
                    for tool in tools]
    cap_reached = invocation_count >= args.max_invocations and any(
        t["discovery_status"] == "partial" for t in public_tools
    )
    inventory = {
        "schema_version": 2,
        "platform": platform_info,
        "scope": {
            "discovery": "native WSL PATH only",
            "excluded": ["/mnt/*", "Windows PATH entries", "Windows executables"],
            "filesystem_scan": False,
        },
        "coverage": {
            "tools_detected": len(public_tools),
            "commands_exported": commands_exported,
            "help_invocations": invocation_count,
            "errors": errors,
            "timeouts": timeouts,
            "discovery_cap_reached": cap_reached,
        },
        "tools": public_tools,
    }
    (output_dir / "inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "# AI CLI Command Reference", "",
        f"- Platform: WSL2 Ubuntu {platform_info['version']}",
        "- Discovery scope: native WSL PATH only; `/mnt/*` excluded",
        f"- Tools: {len(public_tools)}",
        f"- Commands: {commands_exported}",
        f"- Help invocations: {invocation_count}", "",
    ]
    for tool in public_tools:
        lines += [
            f"## {tool['name']}",
            f"- Executable: `{tool['executable']}`",
            f"- Status: **{tool['discovery_status']}** — {tool['discovery_reason']}",
            f"- Version: `{(tool['version'] or 'unknown').replace('`', '')}`",
            f"- Help invocations: {tool['invocations']}",
            "",
        ]
        for command in tool["commands"]:
            lines.append("- `" + " ".join(command["path"]) + "`")
        lines.append("")
    (output_dir / "COMMAND-REFERENCE.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (output_dir / "export-summary.json").write_text(
        json.dumps(inventory["coverage"], indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Exported AI CLI Commands\n\n"
        "Generated by the WSL2 Ubuntu 26.04 native-path exporter.\n\n"
        "- `inventory.json`: structured inventory\n"
        "- `COMMAND-REFERENCE.md`: command reference\n"
        "- `export-summary.json`: coverage summary\n"
        "- `raw/`: captured version/help output\n",
        encoding="utf-8",
    )

    print(f"Export complete: {output_dir}")
    print(
        f"Tools: {len(public_tools)} | Commands: {commands_exported} | "
        f"Invocations: {invocation_count} | Errors: {errors} | "
        f"Timeouts: {timeouts} | Cap reached: {cap_reached}"
    )
    for tool in public_tools:
        print(f"{tool['name']:24} {tool['discovery_status']:12} "
              f"{len(tool['commands']):5} commands")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
