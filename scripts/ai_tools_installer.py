#!/usr/bin/env python3
"""Explicit, dry-run-by-default installer for free / open-source AI CLIs and runtimes.

Safety model (mirrors the model downloader):
  * Nothing is installed unless --apply is given and the tool is named explicitly.
  * Fixed argument vectors only; no shell, no sudo, no environment secrets read.
  * Package-manager installs (npm, pipx, brew, go) are preferred.
  * Remote installer scripts run ONLY after an exact HTTPS host match, a size cap,
    no redirects, and a SHA-256 supplied by the operator (--script-sha256).
  * Every attempt is appended to a JSONL log; failures are reported, never hidden.

Catalog commands come from vendor documentation as known when this file was written.
They are NOT re-verified automatically: confirm against the official page first
(`plan` prints the exact command and the docs URL) and re-check free-tier terms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9@._:/=+\[\],~-]+$")
MAX_SCRIPT_BYTES = 1_000_000

# cost: oss-local   = free software, runs without any account
#       oss-byok    = free software; you supply a model API key or a local model
#       free-tier   = free software plus a provider free quota that needs an account
CATALOG: list[dict[str, Any]] = [
    # --- coding agents
    {"id": "gemini", "name": "Gemini CLI", "category": "agent", "cost": "free-tier",
     "binary": "gemini", "docs": "https://github.com/google-gemini/gemini-cli",
     "methods": [{"manager": "npm", "argv": ["npm", "install", "-g", "@google/gemini-cli"]}]},
    {"id": "opencode", "name": "OpenCode", "category": "agent", "cost": "oss-byok",
     "binary": "opencode", "docs": "https://opencode.ai",
     "methods": [{"manager": "npm", "argv": ["npm", "install", "-g", "opencode-ai"]}]},
    {"id": "codex", "name": "OpenAI Codex CLI", "category": "agent", "cost": "oss-byok",
     "binary": "codex", "docs": "https://github.com/openai/codex",
     "methods": [{"manager": "npm", "argv": ["npm", "install", "-g", "@openai/codex"]}]},
    {"id": "aider", "name": "Aider", "category": "agent", "cost": "oss-byok",
     "binary": "aider", "docs": "https://aider.chat",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "aider-chat"]}]},
    {"id": "qwen", "name": "Qwen Code", "category": "agent", "cost": "oss-byok",
     "binary": "qwen", "docs": "https://github.com/QwenLM/qwen-code",
     "methods": [{"manager": "npm", "argv": ["npm", "install", "-g", "@qwen-code/qwen-code"]}]},
    {"id": "crush", "name": "Crush", "category": "agent", "cost": "oss-byok",
     "binary": "crush", "docs": "https://github.com/charmbracelet/crush",
     "methods": [{"manager": "npm", "argv": ["npm", "install", "-g", "@charmland/crush"]},
                 {"manager": "brew", "argv": ["brew", "install", "charmbracelet/tap/crush"]}]},
    {"id": "goose", "name": "Goose", "category": "agent", "cost": "oss-byok",
     "binary": "goose", "docs": "https://github.com/block/goose",
     "methods": [{"manager": "brew", "argv": ["brew", "install", "block-goose-cli"]}]},
    # --- local runtimes and servers
    {"id": "ollama", "name": "Ollama", "category": "runtime", "cost": "oss-local",
     "binary": "ollama", "docs": "https://ollama.com/download",
     "methods": [{"manager": "script", "url": "https://ollama.com/install.sh",
                  "host": "ollama.com", "interpreter": "sh",
                  "note": "script may invoke sudo itself; read it first"}]},
    {"id": "llama.cpp", "name": "llama.cpp", "category": "runtime", "cost": "oss-local",
     "binary": "llama-cli", "docs": "https://github.com/ggml-org/llama.cpp",
     "methods": [{"manager": "brew", "argv": ["brew", "install", "llama.cpp"]}]},
    {"id": "vllm", "name": "vLLM", "category": "runtime", "cost": "oss-local",
     "binary": "vllm", "docs": "https://docs.vllm.ai",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "vllm"]}],
     "note": "needs a supported GPU; heavy install"},
    {"id": "ramalama", "name": "RamaLama", "category": "runtime", "cost": "oss-local",
     "binary": "ramalama", "docs": "https://github.com/containers/ramalama",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "ramalama"]}]},
    # --- gateways and helpers
    {"id": "litellm", "name": "LiteLLM proxy", "category": "gateway", "cost": "oss-byok",
     "binary": "litellm", "docs": "https://docs.litellm.ai",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "litellm[proxy]"]}]},
    {"id": "llm", "name": "llm (Simon Willison)", "category": "helper", "cost": "oss-byok",
     "binary": "llm", "docs": "https://llm.datasette.io",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "llm"]}]},
    {"id": "sgpt", "name": "ShellGPT", "category": "helper", "cost": "oss-byok",
     "binary": "sgpt", "docs": "https://github.com/TheR1D/shell_gpt",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "shell-gpt"]}]},
    {"id": "mods", "name": "Mods", "category": "helper", "cost": "oss-byok",
     "binary": "mods", "docs": "https://github.com/charmbracelet/mods",
     "methods": [{"manager": "brew", "argv": ["brew", "install", "charmbracelet/tap/mods"]}]},
    {"id": "fabric", "name": "Fabric", "category": "helper", "cost": "oss-byok",
     "binary": "fabric", "docs": "https://github.com/danielmiessler/fabric",
     "methods": [{"manager": "go", "argv": ["go", "install", "github.com/danielmiessler/fabric@latest"]}]},
    # --- model hub and speech
    {"id": "hf", "name": "Hugging Face CLI", "category": "hub", "cost": "oss-local",
     "binary": "hf", "docs": "https://huggingface.co/docs/huggingface_hub",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "huggingface_hub[cli]"]}]},
    {"id": "whisper", "name": "OpenAI Whisper (local)", "category": "speech", "cost": "oss-local",
     "binary": "whisper", "docs": "https://github.com/openai/whisper",
     "methods": [{"manager": "pipx", "argv": ["pipx", "install", "openai-whisper"]}],
     "note": "needs ffmpeg installed separately"},
    # --- added: more agents, helpers, MLOps and local-UI tools
    {"id": "cline", "name": "Cline CLI", "category": "agent", "cost": "oss-byok",
     "binary": "cline", "docs": "https://github.com/cline/cline",
     "methods": [{"manager": "npm", "argv": ['npm', 'install', '-g', 'cline']}]},
    {"id": "continue", "name": "Continue CLI", "category": "agent", "cost": "oss-byok",
     "binary": "cn", "docs": "https://docs.continue.dev",
     "methods": [{"manager": "npm", "argv": ['npm', 'install', '-g', '@continuedev/cli']}]},
    {"id": "copilot", "name": "GitHub Copilot CLI", "category": "agent", "cost": "free-tier",
     "binary": "copilot", "docs": "https://github.com/features/copilot/cli",
     "methods": [{"manager": "npm", "argv": ['npm', 'install', '-g', '@github/copilot']}],
     "note": "free plan has limits and needs a GitHub account"},
    {"id": "gptme", "name": "gptme", "category": "agent", "cost": "oss-byok",
     "binary": "gptme", "docs": "https://gptme.org",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'gptme']}]},
    {"id": "gpt-engineer", "name": "GPT Engineer", "category": "agent", "cost": "oss-byok",
     "binary": "gpte", "docs": "https://github.com/gpt-engineer-org/gpt-engineer",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'gpt-engineer']}]},
    {"id": "open-interpreter", "name": "Open Interpreter", "category": "agent", "cost": "oss-byok",
     "binary": "interpreter", "docs": "https://github.com/openinterpreter/open-interpreter",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'open-interpreter']}],
     "note": "executes code on your machine; review before use"},
    {"id": "aichat", "name": "AIChat", "category": "helper", "cost": "oss-byok",
     "binary": "aichat", "docs": "https://github.com/sigoden/aichat",
     "methods": [{"manager": "brew", "argv": ['brew', 'install', 'aichat']},
                 {"manager": "cargo", "argv": ['cargo', 'install', 'aichat']}]},
    {"id": "open-webui", "name": "Open WebUI", "category": "runtime", "cost": "oss-local",
     "binary": "open-webui", "docs": "https://docs.openwebui.com",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'open-webui']}],
     "note": "self-hosted chat UI; pairs with Ollama"},
    {"id": "comfy", "name": "comfy-cli (ComfyUI)", "category": "image", "cost": "oss-local",
     "binary": "comfy", "docs": "https://github.com/Comfy-Org/comfy-cli",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'comfy-cli']}]},
    {"id": "piper", "name": "Piper TTS", "category": "speech", "cost": "oss-local",
     "binary": "piper", "docs": "https://github.com/OHF-Voice/piper1-gpl",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'piper-tts']}]},
    {"id": "dvc", "name": "DVC", "category": "mlops", "cost": "oss-local",
     "binary": "dvc", "docs": "https://dvc.org",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'dvc']}]},
    {"id": "mlflow", "name": "MLflow", "category": "mlops", "cost": "oss-local",
     "binary": "mlflow", "docs": "https://mlflow.org",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'mlflow']}]},
    {"id": "bentoml", "name": "BentoML", "category": "mlops", "cost": "oss-local",
     "binary": "bentoml", "docs": "https://docs.bentoml.com",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'bentoml']}]},
    {"id": "label-studio", "name": "Label Studio", "category": "mlops", "cost": "oss-local",
     "binary": "label-studio", "docs": "https://labelstud.io",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'label-studio']}]},
    {"id": "wandb", "name": "Weights & Biases CLI", "category": "mlops", "cost": "free-tier",
     "binary": "wandb", "docs": "https://docs.wandb.ai",
     "methods": [{"manager": "pipx", "argv": ['pipx', 'install', 'wandb']}],
     "note": "free personal tier needs an account"},
]
BY_ID = {t["id"]: t for t in CATALOG}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise urllib.error.URLError("redirects are refused for installer scripts")


def check_catalog() -> list[str]:
    """Return catalog problems (empty list = well-formed)."""
    problems, seen = [], set()
    for t in CATALOG:
        tid = t.get("id", "?")
        if tid in seen:
            problems.append(f"duplicate id {tid}")
        seen.add(tid)
        problems.extend(f"{tid}: missing {key}" for key in ("name", "category", "cost", "binary", "docs", "methods") if not t.get(key))
        if t.get("cost") not in {"oss-local", "oss-byok", "free-tier"}:
            problems.append(f"{tid}: invalid cost")
        if not str(t.get("docs", "")).startswith("https://"):
            problems.append(f"{tid}: docs must be https")
        for m in t.get("methods", []):
            if m["manager"] == "script":
                if not m.get("url", "").startswith("https://") or m.get("host") not in m.get("url", ""):
                    problems.append(f"{tid}: script url/host invalid")
            else:
                if m["argv"][0] != m["manager"]:
                    problems.append(f"{tid}: argv[0] must equal manager")
                bad = [a for a in m["argv"] if not SAFE_ARG_RE.match(a)]
                if bad:
                    problems.append(f"{tid}: unsafe argv {bad}")
                if "sudo" in m["argv"]:
                    problems.append(f"{tid}: sudo is not allowed")
    return problems


def is_installed(tool: dict[str, Any]) -> str | None:
    return shutil.which(tool["binary"])


def choose_method(tool: dict[str, Any], manager: str | None) -> dict[str, Any] | None:
    for m in tool["methods"]:
        if manager and m["manager"] != manager:
            continue
        if m["manager"] == "script" or shutil.which(m["manager"]):
            return m
    return None


def fetch_script(method: dict[str, Any]) -> bytes:
    url = method["url"]
    if not url.startswith("https://"):
        raise ValueError("installer script URL must be HTTPS")
    from urllib.parse import urlsplit
    if (urlsplit(url).hostname or "").lower() != method["host"]:
        raise ValueError("installer script host does not match the catalog entry")
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "ai-tools-installer/1"})
    with opener.open(req, timeout=30) as resp:
        data = resp.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ValueError("installer script exceeds the size ceiling")
    if not data.strip():
        raise ValueError("installer script is empty")
    return data


def log_event(log_dir: Path, event: dict[str, Any]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    event = {"time_unix": int(time.time()), **event}
    with (log_dir / "installer-log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def install_one(tool_id: str, apply: bool, manager: str | None, script_sha256: str | None,
                log_dir: Path, timeout: int) -> dict[str, Any]:
    tool = BY_ID.get(tool_id)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        # Terminal PASS/FAIL outcomes are always logged so failures are visible
        # in installer-log.jsonl even when they happen before execution.
        if result["status"] in {"PASS", "FAIL"}:
            log_event(log_dir, result)
        return result

    if tool is None:
        return finish({"tool": tool_id, "status": "FAIL", "reason": "unknown-tool"})
    existing = is_installed(tool)
    if existing:
        return {"tool": tool_id, "status": "SKIPPED", "reason": "already-installed", "path": existing}
    method = choose_method(tool, manager)
    if method is None:
        wanted = manager or "/".join(m["manager"] for m in tool["methods"])
        return finish({"tool": tool_id, "status": "FAIL", "reason": f"no-usable-installer ({wanted} not found)",
                       "docs": tool["docs"]})
    cmd = ([method.get("interpreter", "sh"), "<downloaded-script>"] if method["manager"] == "script"
           else method["argv"])
    result: dict[str, Any] = {"tool": tool_id, "manager": method["manager"], "command": cmd,
                              "docs": tool["docs"], "cost": tool["cost"]}
    if not apply:
        result["status"] = "DRY-RUN"
        if method["manager"] == "script":
            result["requires"] = "--apply and --script-sha256 (see script-hash)"
        return result
    try:
        if method["manager"] == "script":
            if not script_sha256 or not re.fullmatch(r"[0-9a-fA-F]{64}", script_sha256):
                return finish({**result, "status": "FAIL", "reason": "script-sha256-required"})
            data = fetch_script(method)
            digest = hashlib.sha256(data).hexdigest()
            if digest != script_sha256.lower():
                return finish({**result, "status": "FAIL", "reason": "script-sha256-mismatch", "actual": digest})
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "install.sh"
                path.write_bytes(data)
                proc = subprocess.run([method.get("interpreter", "sh"), str(path)], stdin=subprocess.DEVNULL,
                                      timeout=timeout, check=False)
        else:
            proc = subprocess.run(method["argv"], stdin=subprocess.DEVNULL, timeout=timeout, check=False)
        result["returncode"] = proc.returncode
        found = is_installed(tool)
        # A clean exit without the binary on PATH is NOT a pass (PATH may need a new shell).
        result["status"] = "PASS" if proc.returncode == 0 and found else "FAIL"
        if proc.returncode == 0 and not found:
            result["reason"] = f"installer-exited-0-but-{tool['binary']}-not-on-PATH (open a new shell or fix PATH)"
        elif found:
            result["path"] = found
    except (OSError, subprocess.TimeoutExpired, ValueError, urllib.error.URLError) as exc:
        result.update(status="FAIL", reason=f"{type(exc).__name__}: {exc}")
    return finish(result)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dry-run-by-default installer for free AI tools")
    ap.add_argument("--version", action="version", version="%(prog)s 1.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="List catalog tools and whether each is installed")
    ls.add_argument("--category")
    ls.add_argument("--installed", action="store_true", help="only installed tools")
    ls.add_argument("--missing", action="store_true", help="only tools that are not installed")
    ls.add_argument("--json", action="store_true")
    for name, help_ in (("plan", "Show what would run (never installs)"),
                        ("install", "Install named tools; dry-run unless --apply")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("tools", nargs="+", help="tool ids from `list`")
        p.add_argument("--manager", choices=["npm", "pipx", "brew", "cargo", "go", "script"])
        p.add_argument("--script-sha256", help="expected SHA-256 for a script-based installer")
        p.add_argument("--log-dir", type=Path, default=Path("./ai-installer-logs"))
        p.add_argument("--timeout", type=int, default=1800)
        if name == "install":
            p.add_argument("--apply", action="store_true", help="actually run the installers")
    sh = sub.add_parser("script-hash", help="Download an installer script (no execution) and print its SHA-256")
    sh.add_argument("tool")
    sub.add_parser("check-catalog", help="Validate catalog structure and argument safety")
    args = ap.parse_args(argv)

    if args.cmd == "check-catalog":
        problems = check_catalog()
        print("\n".join(problems) if problems else f"Catalog OK: {len(CATALOG)} tools")
        return 1 if problems else 0
    if args.cmd == "list":
        rows = []
        for t in CATALOG:
            if args.category and t["category"] != args.category:
                continue
            path = is_installed(t)
            if (args.installed and not path) or (args.missing and path):
                continue
            rows.append({"id": t["id"], "name": t["name"], "category": t["category"], "cost": t["cost"],
                         "installed": bool(path), "path": path or "",
                         "managers": [m["manager"] for m in t["methods"]], "docs": t["docs"]})
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['id']:<10} {r['category']:<8} {r['cost']:<10} "
                      f"{'installed' if r['installed'] else 'missing':<9} {','.join(r['managers']):<12} {r['name']}")
        return 0
    if args.cmd == "script-hash":
        tool = BY_ID.get(args.tool)
        method = next((m for m in (tool or {}).get("methods", []) if m["manager"] == "script"), None)
        if method is None:
            print(f"ERROR: {args.tool!r} has no script-based installer", file=sys.stderr)
            return 2
        try:
            data = fetch_script(method)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"tool": tool["id"], "url": method["url"], "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "next": "Read the script yourself, then pass this digest to install --script-sha256"}, indent=2))
        return 0

    apply = getattr(args, "apply", False)
    results = [install_one(t, apply, args.manager, args.script_sha256, args.log_dir, args.timeout)
               for t in args.tools]
    print(json.dumps(results, indent=2))
    return 0 if all(r["status"] in {"PASS", "SKIPPED", "DRY-RUN"} for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
