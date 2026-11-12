#!/usr/bin/env python3
"""Unified read-only AI server inventory and AI CLI command exporter.

Scope: native WSL2 Ubuntu filesystem only. Never scans /mnt, /proc, /sys, /dev.
No environment values, credentials, .env contents, process arguments, or config
file contents are collected. CLI help is executed with bounded timeouts.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

from ai_export_monitor import ExportMonitor

SCHEMA_VERSION = 4


# ---------------------------------------------------------------------------
# Structured error hierarchy — layer boundaries raise typed exceptions instead
# of ad-hoc ValueError/SystemExit so callers can distinguish scope, config,
# budget, and export failures and preserve explicit failure states.
# ---------------------------------------------------------------------------
class ExportError(Exception):
    """Base exception for all exporter failures."""


class ScopeError(ExportError):
    """Host or path falls outside the permitted WSL2 Ubuntu native scope."""


class ConfigError(ExportError):
    """Invalid or inconsistent scan configuration."""


class BudgetError(ExportError):
    """A scan budget (entries, invocations, depth, bytes) was exceeded."""


# ---------------------------------------------------------------------------
# Typed contracts for the inventory document and intermediate structures.
# These describe the JSON schema (version 4) and are used for signatures and
# documentation; runtime remains duck-typed so existing behaviour is unchanged.
# ---------------------------------------------------------------------------
class PlatformInfo(TypedDict, total=False):
    wsl2: bool
    distribution: str
    kernel: str
    python: str
    architecture: str


class ExecutableEntry(TypedDict, total=False):
    name: str
    path: str
    source: str
    kind: str


class PackageEntry(TypedDict, total=False):
    name: str
    version: str
    location: str


class ProcessEntry(TypedDict, total=False):
    pid: str
    command: str


class ServiceEntry(TypedDict, total=False):
    unit: str
    source: str


class ContainerRow(TypedDict, total=False):
    pass  # Arbitrary docker/podman JSON rows; keys vary by engine.


class ModelArtifact(TypedDict, total=False):
    path: str
    resolved_path: str
    symlink: bool
    size_bytes: int
    format: str
    evidence: str


class CoverageEntry(TypedDict, total=False):
    root: str
    status: str
    entries_examined: int
    directories: int
    artifacts: int
    models: int
    manifests: int


class CliCommandRecord(TypedDict, total=False):
    tool: str
    command: str
    help_arguments: list[str]
    depth: int
    help_file: str


class CliToolStats(TypedDict, total=False):
    path: str
    status: str
    commands: list[CliCommandRecord]
    errors: list[dict[str, str]]
    timeouts: int
    invocations: int
    cap_reached: bool


class CliExportResult(TypedDict, total=False):
    tools: dict[str, CliToolStats]
    total_invocations: int
    total_commands: int
    global_cap_reached: bool
    semantics: str


class FilesystemScanResult(TypedDict, total=False):
    directories: list[dict[str, str]]
    metadata_candidates: list[dict[str, Any]]
    model_artifacts: list[ModelArtifact]
    manifests: list[dict[str, Any]]
    entries_examined: int
    coverage: list[CoverageEntry]
    entry_limit_reached: bool


class RuntimeInfo(TypedDict, total=False):
    processes: list[ProcessEntry]
    systemd_services: list[ServiceEntry]
    likely_ai_listening_sockets: list[str]


class ContainersInfo(TypedDict, total=False):
    docker_containers: list[ContainerRow]
    docker_images: list[ContainerRow]
    podman_containers: list[ContainerRow]
    podman_images: list[ContainerRow]


class InventoryData(TypedDict, total=False):
    schema_version: int
    generated_at_unix: int
    platform: PlatformInfo
    scope: dict[str, str]
    profile: str
    scan_roots: list[str]
    scan_exclusions: list[str]
    executables: list[ExecutableEntry]
    packages: dict[str, Any]
    runtime: RuntimeInfo
    containers: ContainersInfo
    filesystem_scan: FilesystemScanResult
    cli_export: CliExportResult
    duration_seconds: float


class CheckpointData(TypedDict, total=False):
    schema_version: int
    fingerprint: str
    updated_at: int
    stages: dict[str, Any]
AI_NAME_RE = re.compile(
    r"(?:^|[-_.])(ai|llm|gpt|claude|codex|gemini|copilot|opencode|aider|"
    r"ollama|llama|mistral|qwen|deepseek|huggingface|transformers|"
    r"vllm|litellm|comfyui|stable[._-]?diffusion|torch|pytorch|tensorflow|"
    r"onnx|mlx|inference|model|agent|embedding|vector|chromadb|qdrant|"
    r"cursor|windsurf|cline|roo|goose|continue|tabby|jan|lmstudio|"
    r"localai|local[._-]?ai|llamacpp|llama[._-]?cpp|llama[._-]?factory|"
    r"autogen|crewai|langchain|langgraph|haystack|dspy|guidance|"
    r"vscode[._-]?continue|amazon[._-]?q|kiro|amp|zed[._-]?ai|"
    r"open[._-]?webui|text[._-]?generation|tgi|triton|ray[._-]?serve|"
    r"mlflow|wandb|weights[._-]?biases|bentoml|xinference|sglang|"
    r"whisper|faster[._-]?whisper|stable[._-]?audio|demucs|kokoro|"
    r"piper|coqui|tts|stt|speech[._-]?to[._-]?text|voice[._-]?conversion|"
    r"openai|anthropic|cohere|mistral|groq|replicate|together|fireworks|"
    r"perplexity|pinecone|weaviate|milvus|chroma|faiss|pgvector|llamaindex|"
    r"semantic[._-]?kernel|agno|pydantic[._-]?ai|smolagents|browser[._-]?use|"
    r"instructor|mem0|vertex[._-]?ai|bedrock|azure[._-]?ai|generative[._-]?ai|"
    r"crush|nanocoder|gptme|plandex|aichat|sgpt|shell[._-]?gpt|ramalama|"
    r"lemonade|open[._-]?interpreter|llamafile|koboldcpp|openhands|kilo[._-]?code|"
    r"kimi|auggie|oterm|gpt[._-]?engineer|gpte|comfy[._-]?cli|"
    r"hermes|hermes[._-]?agent|craft[._-]?anything|openclaw|codebuff|iflow|"
    r"letta|swe[._-]?agent|mini[._-]?swe[._-]?agent|claude[._-]?flow|mcp|"
    r"codewhale|kolk|bitfun|opensquilla|ouroboros|deerflow|kirocrew|paseo|"
    r"oh[._-]?my[._-]?opencode|deepagents|trae[._-]?agent|junie|"
    r"xai|grok|cerebras|sambanova|openrouter|moonshot|zhipu|minimax|ai21|"
    r"voyage[._-]?ai|elevenlabs|deepgram|assemblyai|google[._-]?genai|"
    r"google[._-]?generativeai|genai|siliconflow|novita|hyperbolic|firectl)"
    r"(?:$|[-_.])",
    re.IGNORECASE,
)
# Agent processes whose short or generic names are too ambiguous for AI_NAME_RE
# (it also scans package metadata and file names). Matched exactly against the
# process command name only, so no process arguments are ever read.
PROCESS_EXACT_NAMES = {"rea", "hermes", "kilo", "kilocode", "opencode", "crush"}
AI_DIR_NAMES = {
    ".agents", ".aider", ".aider-desk", ".ai", ".ai-coder", ".claude",
    ".codex", ".copilot", ".continue", ".cursor", ".gemini", ".opencode",
    ".roo", ".windsurf", ".tabby", ".jan", ".ollama", ".cache",
    ".huggingface", "comfyui", "llama.cpp",
    ".hermes", ".kilocode", ".openclaw", ".crush", ".goose", ".qwen", ".kimi",
}
AI_DIR_RE = re.compile(
    r"(?:^|[-_.])(ai|llm|gpt|claude|codex|gemini|copilot|opencode|aider|"
    r"ollama|llama|mistral|qwen|deepseek|huggingface|transformers|"
    r"vllm|litellm|comfyui|inference|models?|agents?|embeddings?|"
    r"chromadb|qdrant|cursor|windsurf|cline|roo|goose|continue|tabby|jan|"
    r"lmstudio|localai|autogen|crewai|langchain|langgraph|whisper|tts|stt|"
    r"openai|anthropic|cohere|mistral|groq|replicate|together|fireworks|"
    r"pinecone|weaviate|milvus|chroma|faiss|llamaindex|agno|pydantic[._-]?ai|"
    r"hermes|kilo[._-]?code|craft[._-]?anything|openclaw)"
    r"(?:$|[-_.])",
    re.IGNORECASE,
)
CANDIDATES = [
    # Coding assistants and agentic developer CLIs.
    "agy", "amp", "amazon-q", "aider", "claude", "cline", "codex",
    "copilot", "continue", "cursor-agent", "droid", "gemini", "goose",
    "kiro-cli", "opencode", "roo", "windsurf", "zed-ai",
    "craft-anything-cli", "llm", "openai", "anthropic", "mistral", "groq",
    "cohere", "replicate", "together", "perplexity", "pinecone", "weaviate",
    "llamaindex", "agno", "smolagents", "pydantic-ai", "mem0",
    # Model runtimes, serving stacks, and local model managers.
    "ollama", "llama-server", "llama-cli", "llama-quantize",
    "llama-gguf-split", "llama-bench", "llama-embedding", "llama-parallel",
    "vllm", "sglang", "tritonserver", "triton", "tgi", "local-ai",
    "localai", "lmstudio", "lms", "jan", "koboldcpp", "llamafile",
    "mlx_lm", "xinference", "ray", "litellm", "open-webui", "tabby",
    # Model hubs, training, evaluation, and experiment tooling.
    "huggingface-cli", "hf", "transformers-cli", "accelerate", "peft",
    "deepspeed", "torchrun", "mlflow", "wandb", "dvc", "clearml",
    "bentoml", "comet", "label-studio", "argilla",
    # Image, video, audio, speech and multimodal tooling.
    "comfy", "comfyui", "automatic1111", "invokeai", "fooocus",
    "whisper", "whisper-cli", "faster-whisper", "stable-audio",
    "demucs", "piper", "coqui-tts", "kokoro-tts",
    # Framework and agent tool CLIs.
    "autogen", "crewai", "langchain", "langgraph", "dspy", "gradio",
    # Added: additional free/open-source agents, helpers and runtimes.
    "crush", "qwen", "nanocoder", "gptme", "plandex", "aichat", "mods",
    "sgpt", "fabric", "interpreter", "ramalama", "text-generation-launcher",
    "lemonade", "ollama-launch", "litellm-proxy", "docker-model",
    "cn", "gpte", "gpt-engineer", "open-interpreter", "openhands", "kilo",
    "kilocode", "kimi", "auggie", "vibe", "cody", "oterm", "text-generation-webui",
    # Added: agents seen running on the target host and similar agent CLIs.
    # Names are matched on PATH only; absent tools are simply not reported.
    "hermes", "hermes-agent", "rea", "craft-anything", "openclaw",
    "codebuff", "iflow", "letta", "swe-agent", "mini-swe-agent", "claude-flow",
    # Added (agent names taken from public 2026 CLI-agent directories; provider
    # CLI names are common conventions). Unverified until seen on a real host.
    "codewhale", "kolk", "bitfun", "opensquilla", "ouroboros", "deerflow",
    "kirocrew", "paseo", "junie", "trae-agent", "deepagents",
    "grok", "xai", "deepseek", "moonshot", "cerebras", "openrouter", "ngc",
    "firectl", "elevenlabs", "deepgram", "assemblyai", "ai21", "sambanova",
    "comfy-cli", "piper-tts", "sglang-router", "llama-run", "llama-mtmd-cli",
    "llama-perplexity", "llama-imatrix", "llama-tokenize",
]
CLAUDE_MAX_INVOCATIONS = 40
CLAUDE_MAX_DEPTH = 2

# File formats recognized by safe_model_file(); used as a pre-filter so the
# scanner only opens plausible model files instead of every regular file.
MODEL_EXTENSIONS = {
    ".gguf", ".safetensors", ".onnx", ".tflite", ".mlmodel",
    ".mlpackage", ".engine", ".plan", ".ggml", ".model",
    ".pt", ".pth",
}
SKIP_DIRS = {
    "/mnt", "/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp",
    "/var/log", "/var/cache", "/lost+found",
}
SKIP_NAMES = {
    ".git", ".svn", ".hg", "__pycache__", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
    ".gradle", ".terraform",
    # Dependency/runtime trees are intentionally skipped: AI CLIs installed
    # there are still covered by PATH discovery and package metadata, and the
    # trees routinely consume the whole entry budget.
    "node_modules", ".venv", "venv", ".npm", ".cargo", ".rustup",
}
DEFAULT_ROOTS = [
    str(Path.home()),
    "/opt", "/usr/local", "/srv", "/var/lib",
]

def run(args: list[str], timeout: float = 8.0, limit: int = 2_000_000) -> dict[str, Any]:
    """Run a fixed executable argument vector; never invokes a shell."""
    try:
        p = subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace",
            timeout=timeout, check=False, env={"PATH": os.pathsep.join(native_path_entries()),
                                               "HOME": str(Path.home()),
                                               "LANG": "C.UTF-8"},
        )
        out = p.stdout[:limit]
        return {"returncode": p.returncode, "output": out, "truncated": len(p.stdout) > limit}
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return {"returncode": None, "output": str(raw)[:limit], "timeout": True}
    except (OSError, ValueError) as exc:
        return {"returncode": None, "output": "", "error": str(exc)}


def require_native_path(path: str) -> str:
    """Validate a path is native WSL2 filesystem (not /mnt, /proc, /sys, /dev).

    Raises ScopeError when out of scope; returns the resolved path otherwise.
    """
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ScopeError(f"Path not resolvable: {path}") from exc
    s = str(resolved)
    if s == "/mnt" or s.startswith("/mnt/") or any(
        s == root or s.startswith(root + "/") for root in ("/proc", "/sys", "/dev", "/run")
    ):
        raise ScopeError(f"Path outside native WSL2 scope: {path}")
    if not resolved.is_absolute():
        raise ScopeError(f"Path is not absolute: {path}")
    return s

def validate_host() -> PlatformInfo:
    version = platform.version().lower()
    release = platform.release().lower()
    distro = ""
    try:
        for line in Path("/etc/os-release").read_text(errors="replace").splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        pass
    is_wsl2 = "wsl2" in version or "wsl2" in release
    if not is_wsl2 or "ubuntu" not in distro.lower():
        raise ScopeError(
            f"This exporter is restricted to WSL2 Ubuntu. Detected distro={distro!r}, "
            f"kernel={platform.release()!r}"
        )
    return {
        "wsl2": True, "distribution": distro, "kernel": platform.release(),
        "python": platform.python_version(), "architecture": platform.machine(),
    }

def native_path_entries() -> list[str]:
    entries = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        try:
            p = Path(item).resolve()
            if str(p).startswith("/mnt/") or str(p) == "/mnt":
                continue
            if p.is_dir() and p.is_absolute():
                entries.append(str(p))
        except OSError:
            continue
    return list(dict.fromkeys(entries))

def path_is_native(path: str) -> bool:
    try:
        require_native_path(path)
    except ScopeError:
        return False
    return True


_CONFIG_KEY_MAP = {
    "max-total-entries": "max_total_entries",
    "max-entries-per-root": "max_entries_per_root",
    "max-invocations": "max_invocations",
    "per-tool-invocations": "per_tool_invocations",
    "max-depth": "max_depth",
    "timeout": "timeout",
    "profile": "profile",
    "monitor": "monitor",
    "monitor-host": "monitor_host",
    "monitor-port": "monitor_port",
    "monitor-token-file": "monitor_token_file",
    "monitor-keepalive": "monitor_keepalive",
    "output": "output",
    "skip-cli-help": "skip_cli_help",
}


def load_config(config_path: str | None) -> dict[str, Any]:
    """Load scan defaults from a JSON or TOML config file.

    Returns an empty dict when no config is given or the file is absent.
    Raises ConfigError on unreadable or malformed files.
    """
    if not config_path:
        return {}
    path = Path(config_path).expanduser()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        if path.suffix.lower() == ".toml":
            import tomllib  # Python 3.11+; runtime is 3.10+ so guard the import
            data = tomllib.loads(text)
        else:
            data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Malformed JSON config file {path}: {exc}") from exc
    except ImportError as exc:
        raise ConfigError("TOML config requires Python 3.11+ (tomllib unavailable)") from exc
    except Exception as exc:  # tomllib.TOMLDecodeError and other parse errors
        raise ConfigError(f"Malformed config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object / TOML table")
    return data


def apply_config(args: argparse.Namespace, config: dict[str, Any]) -> None:
    """Apply config-file values only for attributes not already set on the CLI.

    CLI flags always win over config-file defaults. Unknown keys are ignored
    with a recorded warning so future config keys never crash older binaries.
    """
    for raw_key, value in config.items():
        key = _CONFIG_KEY_MAP.get(raw_key, raw_key.replace("-", "_"))
        if not hasattr(args, key):
            continue
        if getattr(args, key) is not None:
            continue  # CLI explicitly provided; config is a default only
        setattr(args, key, value)

def is_windows_pe(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False

def discover_executables() -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    search_path = os.pathsep.join(native_path_entries())
    # Resolve explicit well-known entry points first, including tools whose names
    # do not contain an obvious AI token (for example `lms`).
    for name in CANDIDATES:
        p = shutil.which(name, path=search_path)
        if p and path_is_native(p) and not is_windows_pe(p):
            found[name] = {"name": name, "path": str(Path(p).resolve()),
                           "source": "native-PATH", "kind": "native-executable"}

    # Discover additional and newly released AI CLIs by inspecting executable
    # filenames in native PATH directories. This does not execute candidates;
    # only the bounded help-export stage executes discovered tools.
    for raw_dir in native_path_entries():
        try:
            with os.scandir(raw_dir) as entries:
                for entry in entries:
                    if not AI_NAME_RE.search(entry.name) or entry.name in found:
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=True) or not os.access(entry.path, os.X_OK):
                            continue
                        if not path_is_native(entry.path) or is_windows_pe(entry.path):
                            continue
                        found[entry.name] = {
                            "name": entry.name, "path": str(Path(entry.path).resolve()),
                            "source": "native-PATH-pattern-discovery", "kind": "native-executable",
                        }
                    except OSError:
                        continue
        except OSError:
            continue

    # Include known npm and user-local launchers when not on PATH.
    home = Path.home()
    roots = [
        home / ".local/bin", home / ".cargo/bin", home / ".opencode/bin",
        home / ".npm-global/bin", home / ".local/share/mise/shims",
        home / ".local/share/pnpm", home / ".bun/bin", home / ".volta/bin",
        home / ".asdf/shims", home / ".local/share/npm/bin",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in os.scandir(root):
                if entry.is_file(follow_symlinks=True) and AI_NAME_RE.search(entry.name):
                    p = entry.path
                    if path_is_native(p) and not is_windows_pe(str(Path(p).resolve())):
                        found.setdefault(entry.name, {
                            "name": entry.name, "path": str(Path(p).resolve()),
                            "source": "native-user-bin", "kind": "native-executable",
                        })
        except OSError:
            continue
    # Known npm package entry points. Resolve package metadata without running it.
    npm = home / ".npm-global/lib/node_modules"
    npm_packages = [
        ("@anthropic-ai/claude-code", "claude", "bin/claude.js"),
        ("@openai/codex", "codex", "bin/codex.js"),
        ("@google/gemini-cli", "gemini", "bundle/gemini.js"),
    ]
    for package, name, rel in npm_packages:
        p = npm / package / rel
        if p.is_file() and path_is_native(str(p)) and not is_windows_pe(str(p)):
            found.setdefault(name, {"name": name, "path": str(p.resolve()),
                                    "source": "npm-package-entry", "kind": "node-entrypoint"})
    return sorted(found.values(), key=lambda x: x["name"].casefold())

def package_inventory(quick: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"dpkg": [], "snap": [], "npm_global": [],
                              "cargo": [], "python_metadata": []}
    dpkg = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"], 5 if quick else 12)
    if dpkg.get("returncode") == 0:
        for line in dpkg["output"].splitlines():
            name, _, version = line.partition("\t")
            if AI_NAME_RE.search(name):
                result["dpkg"].append({"name": name, "version": version})
    snap = run(["snap", "list"], 3 if quick else 8)
    if snap.get("returncode") == 0:
        for line in snap["output"].splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and AI_NAME_RE.search(parts[0]):
                result["snap"].append({"name": parts[0], "version": parts[1]})
    npm = run(["npm", "list", "-g", "--depth=0", "--json"], 6 if quick else 15)
    if npm["output"]:
        try:
            data = json.loads(npm["output"])
            for name, item in data.get("dependencies", {}).items():
                if AI_NAME_RE.search(name):
                    result["npm_global"].append({"name": name, "version": item.get("version", "")})
        except (json.JSONDecodeError, AttributeError):
            pass
    cargo = run(["cargo", "install", "--list"], 4 if quick else 10)
    if cargo.get("returncode") == 0:
        for line in cargo["output"].splitlines():
            if line and not line[0].isspace():
                name = line.split(" ", 1)[0].rstrip(":")
                if AI_NAME_RE.search(name):
                    result["cargo"].append({"name": name})
    # Current interpreter's installed metadata only; no package imports.
    try:
        from importlib import metadata
        for dist in metadata.distributions():
            name = dist.metadata.get("Name", "")
            if name and AI_NAME_RE.search(name):
                result["python_metadata"].append({
                    "name": name, "version": dist.version,
                    "location": str(dist.locate_file("")),
                })
    except Exception as exc:  # noqa: BLE001  # metadata enumeration is best-effort; any failure is recorded, not raised
        result["python_metadata_error"] = str(exc)
    return result

def parse_json_lines(output: str) -> list[dict[str, Any]]:
    rows = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows

def container_inventory(quick: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "docker_containers": [], "docker_images": [],
        "podman_containers": [], "podman_images": [],
    }
    for engine in ("docker", "podman"):
        if not shutil.which(engine, path=os.pathsep.join(native_path_entries())):
            continue
        engine_timeout = 4 if quick else 12
        ps = run([engine, "ps", "-a", "--no-trunc", "--format", "{{json .}}"], engine_timeout)
        images = run([engine, "images", "--no-trunc", "--format", "{{json .}}"], engine_timeout)
        if ps.get("returncode") == 0:
            out[f"{engine}_containers"] = [
                row for row in parse_json_lines(ps["output"])
                if AI_NAME_RE.search(json.dumps(row, sort_keys=True))
            ]
        if images.get("returncode") == 0:
            out[f"{engine}_images"] = [
                row for row in parse_json_lines(images["output"])
                if AI_NAME_RE.search(json.dumps(row, sort_keys=True))
            ]
    return out

def system_runtime_inventory(quick: bool = False) -> dict[str, Any]:
    processes = []
    command_timeout = 3 if quick else 8
    ps = run(["ps", "-eo", "pid=,comm="], command_timeout)
    if ps.get("returncode") == 0:
        for line in ps["output"].splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and (AI_NAME_RE.search(parts[1]) or parts[1].lower() in PROCESS_EXACT_NAMES):
                processes.append({"pid": parts[0], "command": parts[1]})
    services = []
    for cmd in (
        ["systemctl", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"],
        ["systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"],
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
        ["systemctl", "--user", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
    ):
        res = run(cmd, command_timeout)
        if res.get("returncode") == 0:
            for line in res["output"].splitlines():
                token = line.split()[0] if line.split() else ""
                if token and AI_NAME_RE.search(token):
                    services.append({"unit": token, "source": " ".join(cmd[:2])})
    listeners = []
    ss = run(["ss", "-lnt"], command_timeout)
    if ss.get("returncode") == 0:
        listeners.extend(line.strip() for line in ss["output"].splitlines()[1:]
                         if re.search(r":(?:8000|8080|8081|8082|11434|1234|3000|4096|4000|5000|5001|5005|5678|7860|8001|8188|9000|49995|49996|49997)\b", line))
    return {"processes": processes, "systemd_services": services,
            "likely_ai_listening_sockets": listeners}

def safe_model_file(path: Path, size: int) -> tuple[bool, str]:
    ext = path.suffix.lower()
    try:
        with path.open("rb") as f:
            head = f.read(16)
            if ext == ".gguf":
                return head[:4] == b"GGUF", "GGUF-magic"
            if ext == ".safetensors":
                if len(head) < 8:
                    return False, "invalid-header"
                header_len = int.from_bytes(head[:8], "little")
                return 2 <= header_len <= min(size - 8, 100_000_000), "safetensors-header-length"
            if ext == ".tflite":
                return len(head) >= 8 and head[4:8] == b"TFL3", "TFL3-magic"
            if ext == ".onnx":
                return size >= 1024 * 1024, "onnx-extension-size-candidate"
            if ext in {".pt", ".pth"}:
                return size >= 50 * 1024 * 1024 and (head[:2] == b"PK" or head[:1] == b"\x80"), "pytorch-size-signature-candidate"
            if ext in {".mlmodel", ".engine", ".ggml", ".model"}:
                return size >= 1024 * 1024, "extension-size-candidate"
    except OSError:
        return False, "unreadable"
    return False, "unsupported"

def root_allowed(path: Path) -> bool:
    try:
        require_native_path(str(path))
    except ScopeError:
        return False
    return True

def scan_filesystem(roots: list[str], max_total: int, max_per_root: int,
                    monitor: ExportMonitor | None = None,
                    exclusions: list[str] | None = None) -> dict[str, Any]:
    dirs, artifacts, models, manifests = [], [], [], []
    coverage = []
    total = 0
    seen_files: set[str] = set()
    # Subtrees whose realpath matches an exclusion are never traversed; the
    # containing root remains scanned so an --output directory inside a root
    # (for example the home directory) does not silently drop that root.
    exclusion_set = {str(Path(x).expanduser().resolve()) for x in (exclusions or []) if x}
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.exists() or not root.is_dir() or not root_allowed(root):
            coverage.append({"root": str(root), "status": "skipped-unavailable-or-out-of-scope",
                             "entries_examined": 0})
            continue
        q = collections.deque([root])
        visited_dirs: set[str] = set()
        local_count = 0
        inner_capped = False
        root_hits = {"directories": 0, "artifacts": 0, "models": 0, "manifests": 0}
        if monitor:
            monitor.stage("filesystem", "running", min(95, total / max_total * 100), f"Scanning {root}")
        while q and total < max_total and local_count < max_per_root:
            current = q.popleft()
            try:
                real = str(current.resolve(strict=True))
            except OSError:
                continue
            if real in visited_dirs or not root_allowed(current):
                continue
            if any(real == x or real.startswith(x + "/") for x in exclusion_set):
                continue
            visited_dirs.add(real)
            try:
                entries = os.scandir(current)
            except (OSError, PermissionError):
                continue
            for entry in entries:
                if total >= max_total or local_count >= max_per_root:
                    # A further entry existed but the budget stopped the scan;
                    # the root is genuinely incomplete even with an empty queue.
                    inner_capped = True
                    break
                total += 1
                local_count += 1
                name = entry.name
                if current.name == ".cache" and name not in {"huggingface", "ollama", "torch", "llama.cpp", "models", "lm-studio", "transformers"} and not AI_DIR_RE.search(name):
                    continue
                if entry.is_symlink():
                    # File symlinks are accepted only when their resolved target stays
                    # within this root's native filesystem boundary. Directory symlinks
                    # are not traversed to avoid cycles and tree escapes.
                    try:
                        target = Path(entry.path).resolve(strict=True)
                        if target.is_file() and root_allowed(target):
                            key = str(target)
                            if key not in seen_files:
                                seen_files.add(key)
                                size = target.stat().st_size
                                if target.suffix.lower() in MODEL_EXTENSIONS:
                                    ok, evidence = safe_model_file(target, size)
                                    if ok:
                                        models.append({"path": entry.path, "resolved_path": key,
                                                       "symlink": True, "size_bytes": size,
                                                       "format": target.suffix.lower(),
                                                       "evidence": evidence})
                                        root_hits["models"] += 1
                    except OSError:
                        pass
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if (name in SKIP_NAMES or name in SKIP_DIRS or name.startswith(".cache")) and \
                                name not in AI_DIR_NAMES and not AI_DIR_RE.search(name):
                            # Keep AI-named cache roots; omit unrelated caches.
                            continue
                        if name in AI_DIR_NAMES or AI_DIR_RE.search(name):
                            dirs.append({"name": name, "path": entry.path})
                            root_hits["directories"] += 1
                        q.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        p = Path(entry.path)
                        size = entry.stat(follow_symlinks=False).st_size
                        key = str(p)
                        if key in seen_files:
                            continue
                        seen_files.add(key)
                        if p.name in {"package.json", "Cargo.toml", "go.mod"}:
                            manifests.append({"path": key, "name": p.name, "size_bytes": size})
                            root_hits["manifests"] += 1
                        if p.suffix.lower() in MODEL_EXTENSIONS:
                            ok, evidence = safe_model_file(p, size)
                            if ok:
                                models.append({"path": key, "size_bytes": size,
                                               "format": p.suffix.lower(), "evidence": evidence})
                                root_hits["models"] += 1
                        elif AI_NAME_RE.search(p.name) and p.suffix.lower() in {
                            ".yaml", ".yml", ".json", ".toml", ".lock", ".txt"
                        }:
                            artifacts.append({"path": key, "size_bytes": size,
                                              "classification": "AI-named-metadata-candidate"})
                            root_hits["artifacts"] += 1
                except OSError:
                    continue
                if monitor and total % 2000 == 0:
                    monitor.update(f"Filesystem scan: {total:,} entries", filesystem_entries=total)
                    monitor.stage("filesystem", "running", min(95, total / max_total * 100), f"{root}: {local_count:,} entries")
            entries.close()
        if monitor:
            monitor.update(f"Completed root {root}", filesystem_entries=total)
        hit_cap = bool(q) or total >= max_total or inner_capped
        coverage.append({
            "root": str(root), "status": "incomplete-cap-reached" if hit_cap else "completed",
            "entries_examined": local_count, **root_hits,
        })
    return {
        "directories": dirs, "metadata_candidates": artifacts, "model_artifacts": models,
        "manifests": manifests, "entries_examined": total, "coverage": coverage,
        "entry_limit_reached": total >= max_total or any(
            x["status"] == "incomplete-cap-reached" for x in coverage
        ),
    }

def parse_child_commands(help_text: str) -> list[str]:
    children: set[str] = set()
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("-", "Usage:", "usage:", "Examples:", "Options:")):
            continue
        # Recognize common help layouts: "  auth  ...", "auth - ...",
        # and "Commands: auth, config, models".
        match = re.match(r"^([a-zA-Z][a-zA-Z0-9:_-]{0,50})(?:\s{2,}|\s+-\s+|\s{1,}—)", stripped)
        if match:
            token = match.group(1)
            if token.lower() not in {"usage", "options", "arguments", "commands", "global"}:
                children.add(token)
        if stripped.lower().startswith("commands:"):
            tail = stripped.split(":", 1)[1]
            for token in re.findall(r"(?<![\w-])([a-z][a-z0-9-]{1,30})(?![\w-])", tail, re.IGNORECASE):
                children.add(token)
    return sorted(children)

def command_export(executables: list[dict[str, Any]], out_dir: Path,
                   max_invocations: int, per_tool: int, max_depth: int,
                   timeout: float, monitor: ExportMonitor | None = None) -> dict[str, Any]:
    raw_dir = out_dir / "cli-help"
    raw_dir.mkdir(parents=True, exist_ok=True)
    queue = collections.deque()
    tool_stats: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for tool in executables:
        name = tool["name"]
        tool_stats[name] = {"path": tool["path"], "status": "pending",
                            "commands": [], "errors": [], "timeouts": 0,
                            "invocations": 0, "cap_reached": False}
        queue.append((name, tool["path"], (), 0))
        if monitor:
            monitor.tool_status(name, "pending")
    total = 0
    while queue and total < max_invocations:
        name, executable, args, depth = queue.popleft()
        key = (name, args)
        if key in seen:
            continue
        seen.add(key)
        stats = tool_stats[name]
        # Claude Code exposes a broad, evolving command surface. Its help output
        # can advertise many nested commands, so enforce a non-overridable
        # traversal budget even when the operator raises global/per-tool limits.
        # The npm-package entry point resolves to bin/claude.js, so both the
        # tool name and the executable base name/stem must be considered.
        exe_base = Path(executable).name.casefold()
        is_claude = (name.casefold() == "claude" or exe_base == "claude"
                     or exe_base == "claude.js" or Path(executable).stem.casefold() == "claude")
        tool_invocation_cap = min(per_tool, CLAUDE_MAX_INVOCATIONS) if is_claude else per_tool
        tool_depth_cap = min(max_depth, CLAUDE_MAX_DEPTH) if is_claude else max_depth
        if stats["invocations"] >= tool_invocation_cap:
            stats["cap_reached"] = True
            continue
        if depth > tool_depth_cap:
            continue
        attempts = []
        attempts = [args + suffix for suffix in (("--help",), ("-h",), ("help",))]
        best = ""
        chosen = None
        for suffix in attempts:
            total += 1
            stats["invocations"] += 1
            result = run([executable, *suffix], timeout=timeout, limit=500_000)
            if monitor:
                counters = {"cli_invocations": total,
                            "timeouts": sum(x["timeouts"] for x in tool_stats.values()),
                            "errors": sum(len(x["errors"]) for x in tool_stats.values())}
                progress = sum(min(1.0, x["invocations"] / max(1, per_tool)) for x in tool_stats.values()) / max(1, len(tool_stats)) * 100
                monitor.update(f"CLI help: {name} {' '.join(args) or '<root>'}", **counters)
                monitor.tool_status(name, "running", stats["invocations"], len(stats["commands"]), stats["timeouts"])
                monitor.stage("cli_help", "running", min(95, progress), f"{name}: {total:,}/{max_invocations:,} invocations")
            output = result.get("output", "")
            if result.get("timeout"):
                stats["timeouts"] += 1
            if output and (len(output) > len(best)):
                best, chosen = output, suffix
            if result.get("returncode") == 0 and output.strip():
                break
            if total >= max_invocations or stats["invocations"] >= tool_invocation_cap:
                break
        if best:
            safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
            rel = Path(safe_name) / ("root" if not args else "__".join(args))
            dest = raw_dir / (str(rel) + ".txt")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(best, encoding="utf-8", errors="replace")
            record = {
                "tool": name, "command": " ".join((name, *args)),
                "help_arguments": list(chosen or ()),
                "depth": depth, "help_file": str(dest.relative_to(out_dir)),
            }
            stats["commands"].append(record)
            if depth < tool_depth_cap:
                for child in parse_child_commands(best):
                    if child.casefold() in {name.casefold(), Path(executable).stem.casefold()}:
                        continue
                    child_args = (*args, child)
                    if len(child_args) <= max_depth and (name, child_args) not in seen:
                        queue.append((name, executable, child_args, depth + 1))
        else:
            stats["errors"].append({
                "command": " ".join((name, *args)),
                "reason": "no usable help output from --help, -h, or help",
            })
    # Mark tools whose queued work was left unprocessed by a global or
    # per-tool budget. This prevents a capped traversal being reported as done.
    queued_tools = {item[0] for item in queue}
    for name, stats in tool_stats.items():
        if name in queued_tools:
            stats["cap_reached"] = True
        if stats["cap_reached"]:
            stats["status"] = "per-tool-cap-reached"
        elif stats["errors"] and not stats["commands"]:
            stats["status"] = "no-help-adapter"
        elif stats["commands"]:
            stats["status"] = "help-tree-exhausted"
        else:
            stats["status"] = "not-tested"
        if monitor:
            monitor.tool_status(name, stats["status"], stats["invocations"],
                                len(stats["commands"]), stats["timeouts"],
                                f"{len(stats['errors'])} help errors" if stats["errors"] else None)
    return {
        "tools": tool_stats, "total_invocations": total,
        "total_commands": sum(len(x["commands"]) for x in tool_stats.values()),
        "global_cap_reached": total >= max_invocations and bool(queue),
        "semantics": (
            "Commands are help-tree discoveries, not a guarantee of every hidden, "
            "plugin-gated, account-gated, dynamically registered, or undocumented command."
        ),
    }

def write_reports(out_dir: Path, data: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ai-inventory.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Unified AI Server & CLI Inventory", "",
        f"- Distribution: {data['platform']['distribution']}",
        f"- Kernel: {data['platform']['kernel']}",
        f"- Generated (UTC epoch): {data['generated_at_unix']}",
        f"- Duration: {data['duration_seconds']:.2f}s",
        f"- Profile: {data.get('profile', 'unspecified')}",
        "- Monitor state: `.monitor/state.json`; event journal: `.monitor/events.jsonl`",
        f"- Native WSL filesystem entries examined: {data['filesystem_scan']['entries_examined']}",
        f"- Filesystem cap reached: {data['filesystem_scan']['entry_limit_reached']}",
        "", "## Scope and safety", "",
        "- WSL2 Ubuntu native filesystem roots only; `/mnt/*`, `/proc`, `/sys`, `/dev` excluded.",
        "- No environment values, `.env` contents, credentials, or process arguments collected.",
        "- Read-only inventory; CLI help commands run with bounded timeouts.",
        "", "## Installed AI executables", "",
    ]
    lines.extend(f"- `{x['name']}` — `{x['path']}` ({x['source']})" for x in data["executables"])
    lines += ["", "## AI packages", ""]
    for group, items in data["packages"].items():
        # Non-list values (for example a collection error note) are not package
        # groups; render them as a note instead of crashing the report stage.
        if isinstance(items, list):
            if items:
                lines.append(f"### {group}")
                for x in items:
                    lines.append(f"- `{x.get('name', '')}` `{x.get('version', '')}`")
                lines.append("")
        else:
            lines.append(f"### {group}")
            lines.append(f"- Collection error: {items}")
            lines.append("")
    lines += ["## Running processes", ""]
    for x in data["runtime"]["processes"]:
        lines.append(f"- PID `{x['pid']}` — `{x['command']}`")
    lines += ["", "## Systemd services", ""]
    for x in data["runtime"]["systemd_services"]:
        lines.append(f"- `{x['unit']}`")
    if not data["runtime"]["systemd_services"]:
        lines.append("- No matches detected.")
    lines += ["", "## Containers and images", ""]
    for key in ("docker_containers", "docker_images", "podman_containers", "podman_images"):
        lines.append(f"### {key.replace('_', ' ').title()}")
        for x in data["containers"].get(key, []):
            lines.append(f"- `{json.dumps(x, ensure_ascii=False, sort_keys=True)}`")
        if not data["containers"].get(key):
            lines.append("- No matches detected.")
        lines.append("")
    lines += ["## Model artifacts", ""]
    for x in data["filesystem_scan"]["model_artifacts"]:
        lines.append(f"- `{x['path']}` — {x['size_bytes']} bytes ({x['format']}, {x['evidence']})")
    if not data["filesystem_scan"]["model_artifacts"]:
        lines.append("- No verified model artifacts detected.")
    lines += ["", "## AI-related directories", ""]
    for x in data["filesystem_scan"]["directories"]:
        lines.append(f"- `{x['path']}`")
    lines += ["", "## CLI help-tree export", "",
              f"- Invocations: {data['cli_export']['total_invocations']}",
              f"- Help nodes exported: {data['cli_export']['total_commands']}",
              f"- Global cap reached: {data['cli_export']['global_cap_reached']}", ""]
    for name, stats in data["cli_export"]["tools"].items():
        lines.append(f"### {name} — {stats['status']}")
        lines.append(f"- Executable: `{stats['path']}`")
        lines.append(f"- Invocations: {stats['invocations']}; help nodes: {len(stats['commands'])}; timeouts: {stats['timeouts']}")
        for cmd in stats["commands"]:
            lines.append(f"  - `{cmd['command']}` → `{cmd['help_file']}`")
        for err in stats["errors"]:
            lines.append(f"  - Note: {err['reason']} (`{err['command']}`)")
        lines.append("")
    lines += ["## Filesystem coverage", ""]
    for x in data["filesystem_scan"]["coverage"]:
        lines.append(f"- `{x['root']}`: {x['status']}, {x['entries_examined']} entries")
    lines += ["", "## Interpretation", "",
              data["cli_export"]["semantics"], "",
              "A completed bounded scan means the configured roots were traversed within the configured budgets; it does not prove discovery of inaccessible paths, unmounted volumes, hidden runtime state, or software inside container images.", ""]
    (out_dir / "AI-INVENTORY-REPORT.md").write_text("\n".join(lines), encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser(description="Unified WSL2 AI inventory + CLI exporter with live monitors")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {SCHEMA_VERSION} (inventory schema v{SCHEMA_VERSION})")
    parser.add_argument("--output", default="./ai-inventory-export", help="Output directory (may be on /mnt; scanning never enters /mnt)")
    parser.add_argument("--profile", choices=("quick", "full"), default="quick", help="Quick targeted discovery or bounded deep scan")
    parser.add_argument("--max-total-entries", type=int, default=None)
    parser.add_argument("--max-entries-per-root", type=int, default=None)
    parser.add_argument("--max-invocations", type=int, default=None)
    parser.add_argument("--per-tool-invocations", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--skip-cli-help", action="store_true", help="Inventory only; do not execute CLI help")
    parser.add_argument("--scan-root", action="append", default=[], help="Additional native Linux root; may be repeated")
    parser.add_argument("--exclude-root", action="append", default=[], metavar="PATH",
                        help="Native subtree to skip during filesystem scanning; may be repeated")
    parser.add_argument("--monitor", choices=("both", "terminal", "web", "off"), default="both")
    parser.add_argument("--monitor-host", default="127.0.0.1", help="Dashboard bind address; remote bind requires token file")
    parser.add_argument("--monitor-port", type=int, default=8765)
    parser.add_argument("--monitor-token-file", default=None, help="0600 token file; required for non-loopback dashboard binding")
    parser.add_argument("--monitor-keepalive", type=int, default=120, help="Keep dashboard available after export for N seconds; 0 disables")
    parser.add_argument("--resume", action="store_true", help="Reuse completed stages from a matching checkpoint")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="Optional JSON/TOML config file with scan defaults; CLI flags override it")
    args = parser.parse_args()

    # Config file supplies defaults; CLI flags always win.
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))
    apply_config(args, config)

    defaults = ({"max_total_entries": 50_000, "max_entries_per_root": 15_000,
                 "max_invocations": 60, "per_tool_invocations": 8, "max_depth": 3, "timeout": 3.0}
                if args.profile == "quick" else
                {"max_total_entries": 1_000_000, "max_entries_per_root": 300_000,
                 "max_invocations": 5_000, "per_tool_invocations": 64, "max_depth": 4, "timeout": 5.0})
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    if min(args.max_total_entries, args.max_entries_per_root, args.max_invocations,
           args.per_tool_invocations, args.max_depth) < 1 or args.timeout <= 0 or args.monitor_keepalive < 0:
        parser.error("scan limits must be positive; timeout must be positive; keepalive must be non-negative")
    if not 0 <= args.monitor_port <= 65535:
        parser.error("monitor port must be between 0 and 65535")

    started = time.monotonic()
    host = validate_host()
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        monitor = None if args.monitor == "off" else ExportMonitor(
            output, args.monitor, args.monitor_host, args.monitor_port, args.monitor_token_file
        )
    except ValueError as exc:
        parser.error(str(exc))
    checkpoint_path = output / ".monitor" / "checkpoint.json"
    roots = (list(dict.fromkeys([
        str(Path.home() / ".local/share"), str(Path.home() / ".config"),
        str(Path.home() / ".cache"), str(Path.home() / ".ollama"),
        str(Path.home() / "models"), "/opt", "/usr/local/share", "/srv/models",
    ])) if args.profile == "quick" else list(dict.fromkeys(DEFAULT_ROOTS + args.scan_root)))
    if args.profile == "quick":
        roots.extend(args.scan_root)
    # Only an output directory that IS a configured root is removed from the
    # list; when the output merely lives under a root, the root is kept and the
    # output subtree is pruned during traversal (exclusions below).
    roots = [r for r in roots if Path(r).expanduser().resolve() != output]
    exclusions = [str(output)] + [str(Path(x).expanduser()) for x in args.exclude_root]
    config = {"platform": host, "profile": args.profile, "roots": roots, "max_total_entries": args.max_total_entries,
              "max_entries_per_root": args.max_entries_per_root, "max_invocations": args.max_invocations,
              "per_tool_invocations": args.per_tool_invocations, "max_depth": args.max_depth,
              "timeout": args.timeout, "skip_cli_help": args.skip_cli_help,
              "exclude_roots": sorted(exclusions),
              "schema_version": SCHEMA_VERSION,
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    saved: dict[str, Any] = {}
    if args.resume and checkpoint_path.is_file():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            candidate_stages = checkpoint.get("stages", {})
            if checkpoint.get("fingerprint") == fingerprint and isinstance(candidate_stages, dict):
                saved = candidate_stages
                cached_cli = saved.get("cli_help", {})
                if not isinstance(cached_cli, dict):
                    saved.pop("cli_help", None)
                else:
                    cached_files = [output / item.get("help_file", "")
                                    for tool in cached_cli.get("tools", {}).values() if isinstance(tool, dict)
                                    for item in tool.get("commands", []) if isinstance(item, dict)]
                    if any(not path.is_file() for path in cached_files):
                        saved.pop("cli_help", None)
            elif monitor:
                monitor.emit("resume_rejected", {"reason": "configuration fingerprint mismatch"})
        except (OSError, json.JSONDecodeError):
            if monitor:
                monitor.emit("resume_rejected", {"reason": "checkpoint unreadable"})
    results: dict[str, Any] = {}

    def persist_checkpoint() -> None:
        merged = dict(saved)
        merged.update(results)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "fingerprint": fingerprint,
                                         "updated_at": int(time.time()), "stages": merged},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, checkpoint_path)

    def stage(name: str, fn: Any) -> Any:
        if name in saved:
            if monitor:
                monitor.stage(name, "cached", 100, "Restored from matching checkpoint")
            return saved[name]
        if monitor:
            monitor.stage(name, "running", 0, f"Starting {name}")
        value = fn()
        results[name] = value
        if monitor:
            monitor.stage(name, "completed", 100, f"Completed {name}")
        persist_checkpoint()
        return value

    try:
        host = stage("host", lambda: host)
        executables = stage("executables", discover_executables)
        if monitor:
            monitor.update(f"Discovered {len(executables)} AI executables", executables=len(executables))
        packages = stage("packages", lambda: package_inventory(quick=args.profile == "quick"))
        runtime = stage("runtime", lambda: system_runtime_inventory(quick=args.profile == "quick"))
        containers = stage("containers", lambda: container_inventory(quick=args.profile == "quick"))
        fs = stage("filesystem", lambda: scan_filesystem(roots, args.max_total_entries,
                                                           args.max_entries_per_root, monitor,
                                                           exclusions))
        if monitor:
            monitor.update("Filesystem discovery complete", filesystem_entries=fs["entries_examined"],
                           models=len(fs["model_artifacts"]), ai_directories=len(fs["directories"]))
        if args.skip_cli_help:
            cli = {"tools": {}, "total_invocations": 0, "total_commands": 0,
                   "global_cap_reached": False, "semantics": "CLI help export skipped by request."}
            if monitor:
                monitor.stage("cli_help", "completed", 100, "Skipped by request")
        else:
            cli = stage("cli_help", lambda: command_export(
                executables, output, args.max_invocations, args.per_tool_invocations,
                args.max_depth, args.timeout, monitor))
        if monitor:
            monitor.update("Preparing JSON and Markdown reports", cli_invocations=cli["total_invocations"],
                           cli_commands=cli["total_commands"], timeouts=sum(x["timeouts"] for x in cli["tools"].values()),
                           errors=sum(len(x["errors"]) for x in cli["tools"].values()))
            monitor.stage("reports", "running", 0, "Writing inventory and report")
        data = {
            "schema_version": SCHEMA_VERSION, "generated_at_unix": int(time.time()),
            "platform": host,
            "scope": {"filesystem": "bounded native WSL2 Ubuntu filesystem roots only",
                      "excluded": ["/mnt/*", "/proc", "/sys", "/dev", "Windows PATH entries"],
                      "secrets": "No environment values, .env contents, credentials, or process arguments are collected.",
                      "mode": "read-only inventory; bounded CLI help invocations only"},
            "profile": args.profile, "scan_roots": roots, "scan_exclusions": exclusions,
            "executables": executables, "packages": packages, "runtime": runtime,
            "containers": containers, "filesystem_scan": fs, "cli_export": cli,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        write_reports(output, data)
        if monitor:
            monitor.stage("reports", "completed", 100, "Reports written")
            persist_checkpoint()
            monitor.finish("completed", "Export completed successfully")
        print(f"Export complete: {output}")
        print(f"Profile: {args.profile} | Executables: {len(executables)} | Model artifacts: {len(fs['model_artifacts'])} | AI directories: {len(fs['directories'])}")
        print(f"CLI help nodes: {cli['total_commands']} | Invocations: {cli['total_invocations']} | Filesystem entries: {fs['entries_examined']} | Cap reached: {fs['entry_limit_reached']}")
        if monitor and args.monitor in {"both", "web"}:
            if monitor.dashboard_url:
                print(f"Dashboard: {monitor.dashboard_url}")
            else:
                print("Dashboard unavailable; inspect .monitor/events.jsonl for the startup error.")
            if monitor.dashboard_url and args.monitor_keepalive:
                print(f"Dashboard will remain available for {args.monitor_keepalive}s after completion.")
                deadline = time.monotonic() + args.monitor_keepalive
                try:
                    while time.monotonic() < deadline:
                        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
                except KeyboardInterrupt:
                    print("Dashboard keepalive interrupted; the export is already complete.")
        return 0
    except BaseException as exc:
        if monitor:
            monitor.finish("failed", f"Export failed: {type(exc).__name__}: {exc}")
        raise
    else:
        return 0
    finally:
        if monitor:
            monitor.close()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; partial reports may not have been written.", file=sys.stderr)
        raise SystemExit(130) from None
