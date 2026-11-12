#!/usr/bin/env python3
"""Inventory AI-related software and model artifacts on native WSL2 Ubuntu 26.04.

LEGACY: superseded by scripts/ai_unified_export.py (profiles, monitors,
checkpoints and CLI help-tree export). Kept for compatibility with earlier
runs; new workflows should use the unified exporter.

Read-only discovery. Does not install, start, stop, modify, or invoke AI workloads.
Never scans /mnt, /proc, /sys, /dev, or Windows-mounted filesystems.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

AI_RE = re.compile(
    r"(?:^|[-_.])(ai|llm|ml|model|inference|transformer|neural|"
    r"ollama|llama|llamacpp|llama\.cpp|vllm|sglang|triton|"
    r"torch|pytorch|tensorflow|keras|jax|mlx|onnx|"
    r"huggingface|hugging-face|transformers|diffusers|"
    r"openai|anthropic|claude|gemini|copilot|codex|"
    r"opencode|aider|continue|cline|roo|litellm|"
    r"llamafile|localai|lmstudio|jan|"
    r"whisper|stable-diffusion|comfyui|automatic1111|"
    r"reranker|embedding|sentence-transformers|"
    r"chromadb|qdrant|weaviate|milvus|faiss|"
    r"langchain|llamaindex|semantic-kernel|"
    r"autogen|crewai|agno|dspy|haystack|"
    r"ray-serve|bentoml|mlflow|kubeflow|"
    r"nvidia-triton|tensorrt)(?:$|[-_.])",
    re.IGNORECASE,
)
MODEL_EXTS = {
    ".gguf", ".ggml", ".safetensors", ".onnx", ".pt", ".pth",
    ".ckpt", ".model", ".tflite", ".engine", ".mlmodel",
}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".npm", ".cargo", ".rustup", ".cache/pip",
}
MAX_OUTPUT = 2_000_000


def run(args: list[str], timeout: int = 8) -> tuple[str, str, int]:
    try:
        p = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, errors="replace",
            timeout=timeout, check=False,
            env={**os.environ, "PAGER": "cat", "GIT_PAGER": "cat",
                 "NO_COLOR": "1", "TERM": "dumb"},
        )
        return p.stdout[:MAX_OUTPUT], p.stderr[:20000], p.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc), 124


def require_platform() -> dict[str, Any]:
    release = platform.release().lower()
    version = platform.version().lower()
    wsl2 = "microsoft-standard-wsl2" in release or "wsl2" in version
    distro = ""
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        pass
    if not wsl2 or "ubuntu 26.04" not in distro.lower():
        raise SystemExit(
            f"Refusing to run: expected WSL2 Ubuntu 26.04; detected "
            f"wsl2={wsl2}, kernel={platform.release()}, distro={distro or 'unknown'}"
        )
    return {"wsl2": wsl2, "distribution": distro, "kernel": platform.release(),
            "python": platform.python_version(), "architecture": platform.machine()}


def native_path_entries() -> list[Path]:
    entries: list[Path] = []
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        p = Path(raw)
        try:
            resolved = p.resolve()
            if str(resolved).startswith("/mnt/") or str(resolved) == "/mnt":
                continue
            if not resolved.is_dir():
                continue
            if resolved not in entries:
                entries.append(resolved)
        except OSError:
            continue
    return entries


def add_unique(items: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    key = str(item.get("path") or item.get("name") or json.dumps(item, sort_keys=True))
    if key not in seen:
        seen.add(key)
        items.append(item)


def discover_binaries() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in native_path_entries():
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = entry.name
                    if not AI_RE.search(name):
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False) and os.access(entry.path, os.X_OK):
                            resolved = str(Path(entry.path).resolve())
                            if not resolved.startswith("/mnt/"):
                                add_unique(found, seen, {
                                    "name": name, "path": resolved,
                                    "source": "native-PATH", "kind": "executable",
                                })
                    except OSError:
                        continue
        except OSError:
            continue
    # Known AI command candidates are checked explicitly, even if their names
    # do not contain an AI keyword.
    candidates = [
        "claude", "codex", "copilot", "gemini", "opencode",
        "craft-anything-cli", "agy", "aider", "goose", "ollama",
        "llama-server", "llama-cli", "llama-run", "llamafile",
        "vllm", "sglang", "lmstudio", "lms", "jan", "local-ai",
        "litellm", "open-webui", "comfy", "comfyui", "whisper",
        "whisper-cli", "text-generation-launcher", "xinference",
        "dify", "flowise", "n8n", "continue", "cline", "roo",
        "qwen", "qwen2", "qwen3", "llm", "huggingface-cli",
        "hf", "transformers-cli", "torchrun", "mlflow", "ray",
        "tritonserver", "ollama",
    ]
    for name in candidates:
        path = shutil.which(name, path=os.pathsep.join(map(str, native_path_entries())))
        if path:
            resolved = str(Path(path).resolve())
            if not resolved.startswith("/mnt/"):
                add_unique(found, seen, {
                    "name": name, "path": resolved,
                    "source": "known-candidate", "kind": "executable",
                })
    return sorted(found, key=lambda x: (x["name"].lower(), x["path"]))


def discover_python_packages() -> list[dict[str, str]]:
    results = []
    try:
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name", "")
            if name and AI_RE.search(name.replace(" ", "-")):
                results.append({"name": name, "version": dist.version,
                                "source": "python-environment"})
    except Exception as exc:  # noqa: BLE001  # legacy best-effort enumeration; a failure is recorded as a row, never fatal
        results.append({"error": type(exc).__name__})
    return sorted(results, key=lambda x: x.get("name", "").lower())


def discover_system_packages() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {"dpkg": [], "snap": [], "npm_global": [], "cargo": []}
    out, _, rc = run(["dpkg-query", "-W", "-f=${binary:Package}\\t${Version}\\n"])
    if rc == 0:
        for line in out.splitlines():
            name, _, version = line.partition("\t")
            if AI_RE.search(name.replace(":", "-")):
                result["dpkg"].append({"name": name, "version": version})
    out, _, rc = run(["snap", "list"])
    if rc == 0:
        for line in out.splitlines()[1:]:
            parts = line.split()
            if parts and AI_RE.search(parts[0]):
                result["snap"].append({"name": parts[0], "version": parts[1] if len(parts) > 1 else ""})
    out, _, rc = run(["npm", "list", "-g", "--depth=0", "--json"], timeout=15)
    if rc in (0, 1):
        try:
            data = json.loads(out or "{}")
            for name, meta in data.get("dependencies", {}).items():
                if AI_RE.search(name.replace("@", "").replace("/", "-")):
                    result["npm_global"].append({"name": name, "version": str(meta.get("version", ""))})
        except json.JSONDecodeError:
            pass
    out, _, rc = run(["cargo", "install", "--list"], timeout=15)
    if rc == 0:
        for line in out.splitlines():
            if line and not line[0].isspace():
                name = line.split(" ", 1)[0].rstrip(":")
                if AI_RE.search(name):
                    result["cargo"].append({"name": name})
    return result


def discover_processes() -> list[dict[str, str]]:
    out, _, rc = run(["ps", "-eo", "pid=,comm="])
    found = []
    if rc == 0:
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and AI_RE.search(parts[1]):
                found.append({"pid": parts[0], "command": parts[1]})
    return found


def discover_systemd() -> list[dict[str, str]]:
    out, _, rc = run(["systemctl", "list-units", "--all", "--type=service",
                      "--no-legend", "--no-pager"])
    found = []
    if rc == 0:
        for line in out.splitlines():
            parts = line.split()
            if parts and AI_RE.search(parts[0]):
                found.append({"unit": parts[0], "load": parts[1] if len(parts) > 1 else "",
                              "active": parts[2] if len(parts) > 2 else "",
                              "sub": parts[3] if len(parts) > 3 else ""})
    return found


def discover_containers() -> dict[str, list[dict[str, str]]]:
    result = {"docker_containers": [], "docker_images": [], "podman_containers": []}
    out, _, rc = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"], timeout=12)
    if rc == 0:
        for line in out.splitlines():
            p = line.split("|")
            if len(p) >= 2 and (AI_RE.search(p[0]) or AI_RE.search(p[1])
                                or AI_RE.search(p[1].rsplit("/", 1)[-1])):
                result["docker_containers"].append({"name": p[0], "image": p[1],
                                                    "status": p[2] if len(p) > 2 else ""})
    out, _, rc = run(["docker", "images", "--format", "{{.Repository}}|{{.Tag}}|{{.ID}}"], timeout=12)
    if rc == 0:
        for line in out.splitlines():
            p = line.split("|")
            if p and (AI_RE.search(p[0]) or AI_RE.search(p[0].rsplit("/", 1)[-1])):
                result["docker_images"].append({"repository": p[0],
                                                "tag": p[1] if len(p) > 1 else "",
                                                "id": p[2] if len(p) > 2 else ""})
    out, _, rc = run(["podman", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"], timeout=12)
    if rc == 0:
        for line in out.splitlines():
            p = line.split("|")
            if len(p) >= 2 and (AI_RE.search(p[0]) or AI_RE.search(p[1])
                                or AI_RE.search(p[1].rsplit("/", 1)[-1])):
                result["podman_containers"].append({"name": p[0], "image": p[1],
                                                    "status": p[2] if len(p) > 2 else ""})
    return result


def scan_tree(roots: list[Path], max_depth: int, max_entries: int) -> dict[str, list[dict[str, str]]]:
    files: list[dict[str, str]] = []
    dirs: list[dict[str, str]] = []
    visited = 0
    seen_paths: set[str] = set()
    for root in roots:
        try:
            root = root.resolve()
            if not root.is_dir() or str(root) == "/mnt" or str(root).startswith("/mnt/"):
                continue
        except OSError:
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and visited < max_entries:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if visited >= max_entries:
                            break
                        visited += 1
                        if entry.name in SKIP_DIRS or entry.name.startswith(".cache"):
                            # Model caches are handled through explicit model roots below.
                            continue
                        try:
                            if entry.is_symlink():
                                continue
                            path = Path(entry.path)
                            resolved = str(path)
                            if resolved in seen_paths or resolved == "/mnt" or resolved.startswith("/mnt/"):
                                continue
                            seen_paths.add(resolved)
                            if entry.is_dir(follow_symlinks=False):
                                if AI_RE.search(entry.name):
                                    dirs.append({"name": entry.name, "path": resolved})
                                if depth < max_depth:
                                    stack.append((path, depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                suffix = path.suffix.lower()
                                if suffix in MODEL_EXTS:
                                    files.append({"name": entry.name, "path": resolved,
                                                  "type": "model-artifact", "size_bytes": str(entry.stat(follow_symlinks=False).st_size)})
                                elif AI_RE.search(entry.name) and suffix in {".json", ".yaml", ".yml", ".toml", ".lock", ".txt"}:
                                    files.append({"name": entry.name, "path": resolved,
                                                  "type": "AI-related-file"})
                        except OSError:
                            continue
            except OSError:
                continue
    return {"directories": dirs, "artifacts": files, "entries_examined": visited,
            "entry_limit_reached": visited >= max_entries}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only AI server inventory for WSL2 Ubuntu 26.04.")
    parser.add_argument("--output", default="./ai-server-inventory", help="Output directory")
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--max-entries", type=int, default=150000)
    args = parser.parse_args()
    if not 1 <= args.max_depth <= 12 or not 1000 <= args.max_entries <= 1000000:
        parser.error("max-depth must be 1..12 and max-entries 1000..1000000")

    platform_info = require_platform()
    home = Path.home()
    roots = [
        home, home / ".local/bin", home / ".local/share", home / ".config",
        Path("/opt"), Path("/usr/local/bin"), Path("/usr/local/share"),
        Path("/srv"), Path("/var/lib"),
    ]
    model_roots = [
        home / ".ollama/models", home / ".cache/huggingface/hub",
        home / ".cache/modelscope", home / ".cache/torch",
        Path("/var/lib/ollama"), Path("/opt/models"), Path("/srv/models"),
        Path("/models"),
    ]
    started = time.time()
    data: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_unix": int(started),
        "platform": platform_info,
        "scope": {
            "filesystem": "bounded native WSL filesystem roots only",
            "excluded": ["/mnt/*", "/proc", "/sys", "/dev", "Windows PATH entries"],
            "secrets": "No environment values, .env contents, credentials, or process arguments are collected.",
            "mode": "read-only inventory; no installations or workload execution",
        },
        "executables": discover_binaries(),
        "python_packages": discover_python_packages(),
        "system_packages": discover_system_packages(),
        "processes": discover_processes(),
        "systemd_services": discover_systemd(),
        **discover_containers(),
        "filesystem_scan": scan_tree(roots, args.max_depth, args.max_entries),
        "model_store_scan": scan_tree(model_roots, 6, min(args.max_entries, 100000)),
    }
    data["duration_seconds"] = round(time.time() - started, 2)

    outdir = Path(args.output).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "ai-server-inventory.json"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    sections = [
        ("Executables", data["executables"]),
        ("Python packages", data["python_packages"]),
        ("System packages", [x for group in data["system_packages"].values() for x in group]),
        ("Running processes", data["processes"]),
        ("Systemd services", data["systemd_services"]),
        ("Docker containers", data["docker_containers"]),
        ("Docker images", data["docker_images"]),
        ("Podman containers", data["podman_containers"]),
        ("AI-related directories", data["filesystem_scan"]["directories"]),
        ("AI/model files", data["filesystem_scan"]["artifacts"] + data["model_store_scan"]["artifacts"]),
    ]
    lines = [
        "# AI Server Inventory", "",
        f"- Distribution: {platform_info['distribution']}",
        f"- Kernel: {platform_info['kernel']}",
        f"- Generated: {data['generated_at_unix']}",
        f"- Duration: {data['duration_seconds']} seconds",
        f"- Filesystem entries examined: {data['filesystem_scan']['entries_examined']}",
        f"- Filesystem entry cap reached: {data['filesystem_scan']['entry_limit_reached']}",
        "", "## Scope and safety", "",
        "- Native WSL2 Ubuntu filesystem only; `/mnt/*`, `/proc`, `/sys`, `/dev` excluded.",
        "- No environment variables, `.env` contents, credentials, or process arguments collected.",
        "- Read-only discovery. No AI CLI commands, models, or services are started.", "",
    ]
    for heading, items in sections:
        lines += [f"## {heading}", ""]
        if not items:
            lines += ["_No matches detected._", ""]
        else:
            for item in items:
                lines.append("- `" + " | ".join(f"{k}={v}" for k, v in item.items()) + "`")
            lines.append("")
    (outdir / "AI-SERVER-INVENTORY.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Inventory complete: {json_path.resolve()}")
    for heading, items in sections:
        print(f"{heading:26} {len(items):6}")
    print(f"Filesystem entries: {data['filesystem_scan']['entries_examined']}")
    print(f"Duration: {data['duration_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
