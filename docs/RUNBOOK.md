# Operations Runbook — Unified AI Server & CLI Exporter

Target runtime: native WSL2 Ubuntu. All tools are read-only, dependency-free
(Python standard library only), and bounded by explicit timeouts and budgets.

## Component overview

| Script | Role | CLI entry |
|--------|------|-----------|
| `scripts/ai_unified_export.py` | Orchestrator: host validation, discovery, filesystem scan, CLI help export, reports, checkpoints | `python3 scripts/ai_unified_export.py [options]` |
| `scripts/ai_export_monitor.py` | Live terminal TUI + loopback web dashboard (SSE), JSONL event journal, atomic state | imported by the exporter; standalone usage possible |
| `scripts/ai_model_manager.py` | Model export / structural validate / checksum-pinned download / Ollama verification | `export`, `validate`, `download`, `ollama-validate` |
| `scripts/ai_tools_installer.py` | Dry-run-by-default installer for 33 free/open-source AI tools | `list`, `plan`, `install`, `script-hash`, `check-catalog` |
| `scripts/discover_ai_server.py` | **Legacy** standalone inventory (superseded) | `python3 scripts/discover_ai_server.py` |
| `scripts/export_ai_cli.py` | **Legacy** standalone CLI help exporter (superseded) | `python3 scripts/export_ai_cli.py` |

## Common operations

### 1. Run a quick AI inventory (default)

```bash
python3 scripts/ai_unified_export.py --profile quick --output ./ai-inventory-export
```

### 2. Run a bounded full scan

```bash
python3 scripts/ai_unified_export.py \
  --profile full \
  --output ./ai-inventory-export \
  --max-total-entries 2000000 \
  --max-entries-per-root 500000 \
  --max-invocations 5000 \
  --per-tool-invocations 64 \
  --max-depth 4 \
  --timeout 5 \
  --monitor both \
  --monitor-host 127.0.0.1 \
  --monitor-port 8765
```

### 3. Inventory without CLI help (fast, no subprocess execution)

```bash
python3 scripts/ai_unified_export.py --profile full --skip-cli-help --output ./ai-inventory-export
```

### 4. Resume a matching checkpoint

```bash
python3 scripts/ai_unified_export.py --profile quick --resume --output ./ai-inventory-export
```
Resume only reuses stages when the configuration fingerprint matches (profile,
roots, budgets, skip_cli_help, exclude_roots, schema version, exporter source
hash). Any change starts a fresh scan.

### 5. Use a configuration file (defaults; CLI flags override)

Create `export-config.json`:

```json
{
  "max-total-entries": 500000,
  "max-entries-per-root": 150000,
  "max-invocations": 500,
  "per-tool-invocations": 16,
  "max-depth": 3,
  "timeout": 3.0,
  "profile": "quick",
  "monitor": "web",
  "monitor-host": "127.0.0.1",
  "monitor-port": 8765
}
```

Run:

```bash
python3 scripts/ai_unified_export.py --config export-config.json --output ./ai-inventory-export
```

TOML config is also supported (`export-config.toml`) on Python 3.11+.

### 6. Exclude a subtree without dropping the containing root

```bash
python3 scripts/ai_unified_export.py --profile full \
  --exclude-root /home/user/big-non-ai-tree \
  --output ./ai-inventory-export
```

The output directory is always pruned automatically.

### 7. Model lifecycle

Export structurally validated models from an inventory:

```bash
python3 scripts/ai_model_manager.py export \
  --inventory ./ai-inventory-export/ai-inventory.json \
  --output ./model-export \
  --max-total-bytes 107374182400
```

Include PARTIAL candidates for manual review:

```bash
python3 scripts/ai_model_manager.py export \
  --inventory ./ai-inventory-export/ai-inventory.json \
  --output ./model-export --include-partial
```

Validate model files without loading or deserializing weights:

```bash
python3 scripts/ai_model_manager.py validate model.gguf model.safetensors
```

Download a public model (explicit URL + SHA-256 required):

```bash
python3 scripts/ai_model_manager.py download \
  --url 'https://huggingface.co/ORG/REPO/resolve/main/model.gguf' \
  --output ./models/model.gguf \
  --sha256 '<64-hex-digest>' \
  --max-bytes 21474836480
```

Validate Ollama manifests and blobs:

```bash
python3 scripts/ai_model_manager.py ollama-validate
python3 scripts/ai_model_manager.py ollama-validate --models-dir ~/.ollama/models --skip-hash
```

### 8. Free AI tool installer (dry-run by default)

List catalog and install status:

```bash
python3 scripts/ai_tools_installer.py list
python3 scripts/ai_tools_installer.py list --missing
```

Show what would run (never installs):

```bash
python3 scripts/ai_tools_installer.py plan opencode aider
```

Install (requires explicit `--apply`):

```bash
python3 scripts/ai_tools_installer.py install opencode --apply
```

Script-based installers (ollama) also require `--script-sha256`:

```bash
python3 scripts/ai_tools_installer.py script-hash ollama
# review the script, then:
python3 scripts/ai_tools_installer.py install ollama --apply --script-sha256 <digest>
```

Validate catalog structure:

```bash
python3 scripts/ai_tools_installer.py check-catalog
```

## Makefile targets

| Target | Purpose |
|--------|---------|
| `make help` | List all targets and variables |
| `make validate` | Compile + unit tests + CLI smoke checks |
| `make test` | Run all unit tests |
| `make test-verbose` | Run all unit tests verbosely |
| `make smoke` | Verify all CLI entry points and subcommands |
| `make inventory` | Quick WSL2 discovery (default) |
| `make ai-all` | Comprehensive bounded discovery |
| `make ai-all-quiet` | Full discovery without CLI help |
| `make monitor-web` | Quick discovery + loopback dashboard |
| `make model-export` | Export PASS models from inventory |
| `make model-validate MODEL_FILES="..."` | Validate model files |
| `make tools-list` | List free AI tool catalog with status |
| `make tools-check` | Validate installer catalog |
| `make package` | Build and verify the distributable ZIP |
| `make clean` | Remove Python/tool caches only |

## Monitor dashboard

- Default: http://127.0.0.1:8765 (loopback only, no auth required)
- Remote bind: requires `--monitor-host <private-IP>`, `--monitor-token-file` with a
  ≥32-character token at POSIX mode 0600, and a TLS-terminating reverse proxy.
- Read-only endpoints: `/health`, `/api/state`, `/api/events` (SSE), `/api/auth` (login).
- No endpoint executes commands or mutates state.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | CLI/argument error, copy/hash failure, or validation failure |
| 130 | Interrupted by user (Ctrl-C) |

## Safety invariants (never violated)

- Never scans `/mnt/*`, `/proc`, `/sys`, `/dev`, `/run`.
- Never collects environment values, `.env` contents, credentials, or process arguments.
- Process detection reads command name only (`comm`), never arguments.
- CLI help executed with fixed argument vectors (never a shell), output caps, per-call
  timeouts, per-tool and global invocation budgets.
- Windows PE (`MZ`) executables excluded from discovery.
- Dashboard loopbound by default; remote bind requires token + 0600 permissions.
- Model downloader: HTTPS only, exact hostname allowlist, public DNS, operator SHA-256,
  byte ceiling, temp-file write, digest verify, atomic rename, no overwrite.

## Logs and state

- `.monitor/state.json` — atomically rewritten live monitor state.
- `.monitor/events.jsonl` — append-only event journal (`{id, run_id, time, type, payload}`).
- `.monitor/checkpoint.json` — `{schema_version, fingerprint, updated_at, stages}` for `--resume`.
- `ai-installer-logs/installer-log.jsonl` — every installer PASS/FAIL attempt.
