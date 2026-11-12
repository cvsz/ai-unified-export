# Unified AI Server & CLI Exporter — WSL2 Ubuntu

A dependency-free Python exporter for native WSL2 Ubuntu that combines AI-server discovery, AI CLI help-tree export, a terminal TUI, and a browser dashboard. The terminal and web monitors consume the same event/state stream.

## Highlights

- AI CLI discovery from native Linux PATH, common user-local launchers, and dynamic filename matching for newly released AI tools.
- Coverage for coding agents, model runtimes/servers, model hubs, training/evaluation tools, vector databases, agent frameworks, image/video/audio/speech tooling, and major AI provider CLIs/SDK packages.
- Installed dpkg, snap, npm, Cargo and Python package metadata, process, systemd, Docker and Podman inventory.
- Targeted or bounded deep filesystem discovery of AI directories and model artifacts.
- Bounded CLI help-tree export with round-robin scheduling, per-tool/global invocation caps, recursion protection, and per-call timeouts. Claude Code has a non-overridable ceiling of 40 invocations and depth 2 to prevent runaway help-tree traversal.
- **Live terminal monitor + browser dashboard** with stage progress, elapsed time, ETA, counters, recent events, memory, disk, and load averages.
- JSONL event journal and atomically-written monitor state under `.monitor/`.
- Stage-level checkpoints and `--resume` for matching configurations.
- Quick profile by default; full profile for bounded deep scans.
- No third-party Python dependencies.

## Documentation

- `docs/ARCHITECTURE.md` — module map, pipeline stages, monitor and checkpoint design; type-safety and structured-error contracts; configuration-file support.
- `docs/OUTPUT-SCHEMA.md` — full field reference for `ai-inventory.json` and side-car files.
- `docs/LEGACY-SCRIPTS.md` — status and known limits of the two superseded standalone scripts.
- `docs/RUNBOOK.md` — common operations: quick/full scans, resume, config files, model lifecycle, installer, Makefile targets, monitor dashboard, exit codes, safety invariants.
- `docs/TROUBLESHOOTING.md` — diagnostic guide for host validation, dashboard access, config errors, resume fingerprints, model export exit codes, Claude caps, discovery gaps, scan caps, installer PATH issues, and stalled dashboards.
- `SECURITY.md` — threat model and safety controls per component.
- `CHANGELOG.md` — release notes.
- `AUDIT-PLAN.md` — the record of the last full independent audit.

## Requirements

- WSL2 Ubuntu; the exporter validates the WSL2 kernel marker and Ubuntu distribution.
- Python 3.10+.
- Optional: Docker, Podman, npm, Cargo, dpkg, snap, systemd, `ss`.

## Quick start

```bash
unzip ai-unified-server-cli-exporter-wsl2-full.zip
cd ai-unified-export
python3 scripts/ai_unified_export.py \
  --output /mnt/d/data/ai-cli-command/ai-inventory-export
```

The default profile is `quick`, the monitor is `both`, and the dashboard binds to loopback only:

- Terminal TUI: runs in the current interactive terminal.
- Browser dashboard: http://127.0.0.1:8765
- Default dashboard keepalive after completion: 120 seconds.

If the command is run without an interactive TTY, the web dashboard still runs and the TUI is disabled automatically.

## Quick vs full profile

Quick is the default and uses targeted AI-related roots, a 50,000-entry filesystem budget, 60 CLI invocations, and shorter per-command timeouts to reduce routine scan time. This is a discovery pass, not an exhaustive audit. Executable discovery checks known AI command names and scans native PATH directory entry names for AI-related patterns; package metadata is matched by AI/provider/framework names. Agents added from a live host review: Hermes (`hermes`, `~/.hermes`), `craft-anything-cli`, `rea`, Kilo/KiloCode, OpenClaw, MCP-named servers and similar. Running-process detection uses the process command name only (process arguments are never collected), so an agent that runs under a generic interpreter name such as `python3` or `node` (Hermes gateway, `rea mcp`) is detected through its PATH launcher or `~/.hermes`-style directory instead of the process list. Unknown tools with non-descriptive executable names or tools hidden inside container images may not be identifiable automatically:

```bash
python3 scripts/ai_unified_export.py --profile quick --output ./ai-inventory-export
```

Run a deeper bounded scan when needed:

```bash
python3 scripts/ai_unified_export.py \
  --profile full \
  --output ./ai-inventory-export \
  --max-total-entries 1000000 \
  --max-entries-per-root 300000 \
  --max-invocations 5000 \
  --per-tool-invocations 64 \
  --max-depth 4 \
  --timeout 5
```

Inventory only, without executing CLI help:

```bash
python3 scripts/ai_unified_export.py --skip-cli-help --output ./ai-inventory-export
```

Exclude native subtrees from the filesystem scan without dropping the containing root
(repeatable; the output directory is always pruned automatically):

```bash
python3 scripts/ai_unified_export.py --profile full \
  --exclude-root /home/USER/big-non-ai-tree --output ./ai-inventory-export
```

## Monitor modes

```bash
# Both terminal TUI and web dashboard (default)
python3 scripts/ai_unified_export.py --monitor both

# Terminal only
python3 scripts/ai_unified_export.py --monitor terminal

# Browser only
python3 scripts/ai_unified_export.py --monitor web

# No live monitor
python3 scripts/ai_unified_export.py --monitor off
```

Dashboard options:

```bash
python3 scripts/ai_unified_export.py \
  --monitor web --monitor-host 127.0.0.1 --monitor-port 8765 \
  --monitor-keepalive 300
```

### Remote access

The dashboard is loopback-only by default. Do not bind it to `0.0.0.0` on an untrusted network. Remote binding requires a token file with at least 32 characters and POSIX permissions `0600`; place it behind a TLS-terminating reverse proxy and restrict network access.

```bash
install -m 600 /dev/null "$HOME/.ai-export-monitor.token"
python3 -c 'import secrets,pathlib; pathlib.Path.home().joinpath(".ai-export-monitor.token").write_text(secrets.token_urlsafe(48))'
chmod 600 "$HOME/.ai-export-monitor.token"
MONITOR_BIND_IP="$(hostname -I | awk '{print $1}')"
python3 scripts/ai_unified_export.py \
  --monitor web --monitor-host "$MONITOR_BIND_IP" \
  --monitor-token-file "$HOME/.ai-export-monitor.token"
```

For a remote bind, set `--monitor-host` to the intended private interface address and keep the reverse proxy/TLS and firewall configuration under operator control. The dashboard is read-only; it has no endpoint to execute commands or mutate the host.

## Resume and checkpoints

Every completed stage is saved atomically to `.monitor/checkpoint.json`. Resume only reuses stage data when the scan configuration fingerprint matches:

```bash
python3 scripts/ai_unified_export.py --resume --output ./ai-inventory-export
```

Changing profile, roots, or budgets invalidates the matching checkpoint and starts a fresh scan. The JSONL event history is `.monitor/events.jsonl`; current monitor state is `.monitor/state.json`.

## Output

```text
ai-inventory-export/
├── ai-inventory.json
├── AI-INVENTORY-REPORT.md
├── cli-help/
│   └── <tool>/...
└── .monitor/
    ├── checkpoint.json
    ├── events.jsonl
    └── state.json
```

## Monitoring telemetry

- Pipeline stage state and progress.
- Elapsed duration and estimated remaining time (ETA is an estimate, not a guarantee).
- Filesystem entries examined, CLI invocation count, help nodes, discovered executables/models/directories, errors and timeouts.
- Host memory availability, output-volume disk usage, and Linux load averages.
- Last 2,000 events in memory for live clients; the append-only JSONL event journal persists the run history.

The monitor reads `/proc/meminfo` only for memory metrics; it does not traverse `/proc` during filesystem discovery.

## Scope and safety

- Filesystem discovery excludes `/mnt/*`, `/proc`, `/sys`, `/dev`, `/run`, and temporary/log/cache roots except explicitly selected AI cache subdirectories.
- Windows PATH entries and Windows PE executables are excluded.
- Output may be written to `/mnt/d/...`; the scanner will not traverse `/mnt`.
- No environment values, `.env` contents, credentials, process arguments, or configuration file contents are collected.
- The exporter itself installs, updates, removes, and configures nothing. Installing tools is a separate, explicit, dry-run-by-default operation (`scripts/ai_tools_installer.py`).
- Docker/Podman image contents are not inspected.
- CLI help is the only executable workload. Calls use fixed argument arrays (no shell), output caps, and timeouts.
- Web monitor binds to `127.0.0.1` by default and exposes read-only state/events only.

## Completeness and evidence

A completed bounded scan means configured roots were traversed within configured budgets. It does not prove discovery of inaccessible paths, unmounted volumes, hidden runtime state, software inside container images, or every undocumented/plugin/account-gated CLI command. Tools with unusual help protocols may need a dedicated adapter. The report preserves per-root coverage and cap status. Do not interpret a skipped, failed, blocked, or unverified check as a pass.

## ZeaZDev policy

This project follows the canonical [ZeaZDev AI & Engineering Operating Policy](https://github.com/cvsz/zeaz/blob/main/zeaz.md), including least privilege, local-first behavior, bounded execution, secret minimization, reproducible evidence, and the inspect/understand/plan/validate/apply/test/verify/document lifecycle. See `zeaz.md` in this bundle for the project-specific policy pointer.


## Model export, download, and validation

The model lifecycle is a separate, explicit operation. It does not download models
automatically during inventory scans.

Export locally discovered model artifacts from the generated inventory. By default,
only models that pass the implemented structural/header checks are copied; candidates
with only extension/size evidence are excluded. Exported files are SHA-256 hashed and
verified again after copying:

```bash
python3 scripts/ai_model_manager.py export \
  --inventory /mnt/d/data/ai-cli-command/ai-inventory-export/ai-inventory.json \
  --output /mnt/d/data/ai-cli-command/model-export \
  --max-total-bytes 107374182400
```

Validate existing model files (including files in the chosen export/download destination) without loading or deserializing their weights:

```bash
python3 scripts/ai_model_manager.py validate /path/to/model.gguf /path/to/model.safetensors
```

Download a public model only when you provide its exact HTTPS URL and expected SHA-256.
The downloader uses an exact hostname allowlist, rejects non-public DNS destinations
and unapproved redirects, enforces a byte ceiling, writes to a temporary file, verifies
the digest, and atomically renames only after success. It refuses to overwrite an
existing destination. Default allowed hosts are Hugging Face's primary and known
large-file delivery hosts; add an exact host only after independently verifying it:

```bash
python3 scripts/ai_model_manager.py download \
  --url 'https://huggingface.co/ORG/REPO/resolve/main/model.gguf' \
  --output /mnt/d/data/ai-cli-command/models/model.gguf \
  --sha256 '<64-character-expected-sha256>' \
  --max-bytes 21474836480
```

For another verified distribution host, add `--allow-host exact.example.com`.
No provider token, environment secret, or implicit authentication is read. Private or
gated repositories are not supported by this unauthenticated downloader.

### Validation semantics

- `PASS`: the implemented format check passed (for example GGUF/TFLite magic,
  Safetensors header/tensor offset structure, or PyTorch ZIP CRC).
- `PARTIAL`: extension/signature evidence only; full format validation is unavailable.
- `FAIL`: the file is malformed, unreadable, empty, outside the permitted source scope,
  or fails a supported structural check.

`export` exit codes: `0` when copies succeeded and any remaining candidates were skipped as
expected (non-PASS or byte budget); `2` on copy/hash errors, or whenever skips occurred with
`--fail-on-skip`. The manifest distinguishes `skipped_count` from `error_count`.

This is static validation, not a guarantee that weights are numerically correct, safe,
compatible with a runtime, or from an authentic publisher. The tool never imports model
frameworks, unpickles weights, executes model code, or loads a model into memory.
For ONNX and some legacy formats, use the corresponding trusted runtime's checker in
an isolated environment if deeper validation is required. A supplied SHA-256 confirms
integrity only relative to that supplied digest; obtain the digest from a trusted,
independent publisher channel.

### Ollama model validation

Ollama stores models as extensionless content-addressed blobs, so extension-based
discovery cannot see them. `ollama-validate` reads each manifest and checks that every
layer blob exists, matches its recorded size and, unless `--skip-hash` is given, hashes to
the SHA-256 embedded in its filename. Nothing is loaded or run:

```bash
python3 scripts/ai_model_manager.py ollama-validate                    # common native locations
python3 scripts/ai_model_manager.py ollama-validate --models-dir ~/.ollama/models --skip-hash
```

GGUF validation now checks the fixed header (magic, version 1-3, plausible tensor/KV
counts), not just the four magic bytes.

## Free AI tools installer

`scripts/ai_tools_installer.py` covers free or open-source agents, runtimes, gateways and
helpers (33 entries): Gemini CLI, OpenCode, Codex CLI, GitHub Copilot CLI, Aider, Cline, Continue,
Qwen Code, Crush, Goose, gptme, GPT Engineer, Open Interpreter, Ollama, llama.cpp, vLLM, RamaLama,
Open WebUI, LiteLLM, llm, ShellGPT, Mods, Fabric, AIChat, Hugging Face CLI, Whisper, Piper,
comfy-cli, DVC, MLflow, BentoML, Label Studio and Weights & Biases.
Cost classes: `oss-local` (no account), `oss-byok` (free tool; you supply a model key or a
local model), `free-tier` (provider free quota with an account). Claude Code is excluded
because it is not free.

```bash
python3 scripts/ai_tools_installer.py list --missing
python3 scripts/ai_tools_installer.py plan opencode aider          # shows commands, installs nothing
python3 scripts/ai_tools_installer.py install opencode --apply     # explicit opt-in
python3 scripts/ai_tools_installer.py script-hash ollama           # download, do not run, print SHA-256
python3 scripts/ai_tools_installer.py install ollama --apply --script-sha256 <digest-you-reviewed>
```

Rules enforced in code: no shell, no sudo, fixed argument vectors, package managers
preferred, remote scripts only after an exact HTTPS host match, no redirects, a size cap and
an operator-supplied SHA-256. A zero exit code without the binary on `PATH` is reported as
FAIL, not PASS. Attempts are appended to `ai-installer-logs/installer-log.jsonl`.

Catalog commands reflect vendor documentation as known when written and are not
re-verified automatically. Confirm against the `docs` URL and current free-tier terms
before relying on them. The installer has not been run against live package registries.

Every terminal PASS/FAIL outcome — including failures that occur before execution, such as a
missing package manager or a script digest mismatch — is appended to
`ai-installer-logs/installer-log.jsonl`.

## Legacy scripts

`scripts/discover_ai_server.py` and `scripts/export_ai_cli.py` are superseded standalone
predecessors, kept for compatibility and still compiled and smoke-checked. Prefer the unified
exporter; see `docs/LEGACY-SCRIPTS.md` for their limits and this update's fixes.

## Makefile operations

The Makefile provides a single command surface for validation, discovery,
monitoring, model lifecycle operations, cleanup, and packaging. The comprehensive
`ai-all` target runs a bounded full scan; it discovers AI-named executables from
native Linux PATH and user-local launchers, installed package metadata, active
processes/services, Docker/Podman inventory, model artifacts, and recursive CLI
help trees. `AI_EXTRA_ROOTS` can add further native Linux paths. `/mnt/*`, Windows
PATH entries, `/proc`, `/sys`, and `/dev` remain excluded from filesystem scanning.
It uses only Python's standard library; `zip` is needed only for packaging.

```bash
make help                 # List all targets
make validate             # Compile, run all tests, and smoke-test CLIs
make inventory            # Quick WSL2 discovery (default)
make ai-all               # Comprehensive bounded discovery of installed AI tools
make ai-all-quiet         # Comprehensive discovery without invoking CLI help
make ai-discover          # Faster inventory-only pass
make ai-tools             # List discovered AI executables after scanning
make ai-packages          # List detected AI packages after scanning
make ai-servers           # List AI processes/services/containers after scanning
make inventory-full       # Bounded deep discovery
make inventory-only       # Discovery without executing CLI help
make inventory-resume     # Resume a matching checkpoint
make monitor-web          # Quick discovery + loopback dashboard
make model-export         # Export PASS models from the inventory
make model-export-partial # Include PARTIAL candidates
make model-validate-ollama # Verify Ollama manifests and blobs
make tools-list           # Free AI tool catalog with install status
make tools-missing        # Catalog tools not yet installed
make tools-plan TOOLS="opencode aider"            # Show commands only
make tools-install TOOLS="opencode" APPLY=1       # Install (dry-run without APPLY=1)
make tools-script-hash TOOLS=ollama               # Print installer-script SHA-256
make package              # Validate and build the distributable ZIP
```

Useful overrides:

```bash
make inventory INVENTORY_DIR=/mnt/d/data/ai-cli-command/ai-inventory-export
make ai-all AI_MAX_ENTRIES=2000000 AI_MAX_INVOCATIONS=30000
make ai-all AI_EXTRA_ROOTS="/home/USER/.local/share /opt/custom-ai"
make ai-all EXCLUDE_ROOTS="/home/USER/big-non-ai-tree"
make model-export \
  INVENTORY_FILE=/mnt/d/data/ai-cli-command/ai-inventory-export/ai-inventory.json \
  MODEL_OUTPUT=/mnt/d/data/ai-cli-command/model-export
make model-validate MODEL_FILES="models/model.gguf models/model.safetensors"
make model-download \
  DOWNLOAD_URL='https://huggingface.co/ORG/REPO/resolve/main/model.gguf' \
  DOWNLOAD_OUTPUT=/mnt/d/data/ai-cli-command/models/model.gguf \
  DOWNLOAD_SHA256='<64-character-expected-sha256>'
```

`model-download` intentionally requires an explicit URL, output path, and
independently obtained SHA-256. The downloader's host allowlist, public-DNS,
redirect, byte-limit, no-overwrite, and atomic-publication checks remain active.
`clean` removes only Python/tool caches; it preserves inventory and model data.
`make package` writes the ZIP to the parent directory by default and verifies
its archive integrity after creation.
