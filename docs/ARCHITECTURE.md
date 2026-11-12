# Architecture

The bundle is a dependency-free (Python 3.10+ standard library only) read-only
inventory pipeline for native WSL2 Ubuntu, plus a bounded CLI help-tree exporter,
live monitors, and separate explicit model/installer lifecycle tools.

## Module map

| Module | Role | Entry points |
|---|---|---|
| `scripts/ai_unified_export.py` | Orchestrator: host validation, discovery, filesystem scan, CLI help export, reports, checkpoints | `python3 scripts/ai_unified_export.py [options]` |
| `scripts/ai_export_monitor.py` | `ExportMonitor`: JSONL event journal, atomic state, terminal TUI, loopback web dashboard (SSE) | imported by the exporter; also usable standalone |
| `scripts/ai_model_manager.py` | Model export/validate/download/Ollama verification; never deserializes weights | `export`, `validate`, `download`, `ollama-validate` |
| `scripts/ai_tools_installer.py` | Explicit, dry-run-by-default installer for 33 free/open-source AI tools | `list`, `plan`, `install`, `script-hash`, `check-catalog` |
| `scripts/discover_ai_server.py` | **Legacy** standalone inventory (superseded) | see `docs/LEGACY-SCRIPTS.md` |
| `scripts/export_ai_cli.py` | **Legacy** standalone CLI help exporter (superseded) | see `docs/LEGACY-SCRIPTS.md` |

## Export pipeline

Stages run in order; each completed stage is checkpointed atomically.

```
host → executables → packages → runtime → containers → filesystem → cli_help → reports
  2         8           10         5           5            35           30         5     (weights %)
```

1. **host** — validates the WSL2 kernel marker and Ubuntu distribution (`/etc/os-release`).
2. **executables** — resolves known candidate names on the native PATH, then pattern-matches
   executable filenames in native PATH directories and user-local launchers (`~/.local/bin`,
   `~/.cargo/bin`, `~/.opencode/bin`, mise/pnpm/bun/volta/asdf shims), plus three npm package
   entry points (`claude`, `codex`, `gemini`). Windows `/mnt/*` paths and PE (`MZ`) binaries are
   rejected. No candidate is executed here.
3. **packages** — dpkg, snap, npm global, Cargo and the current interpreter's importlib metadata,
   all filtered by the AI name regex. Collection errors are recorded as notes, never hidden.
4. **runtime** — process command names only (`ps -eo pid=,comm=`; arguments are never read),
   systemd unit lists, and listening sockets on well-known AI ports.
5. **containers** — Docker/Podman `ps`/`images` JSON rows filtered by the AI name regex.
   Image contents are not inspected.
6. **filesystem** — bounded BFS from the configured roots with a global entry budget, a
   per-root budget, symlink-file handling (target must stay in scope), AI-directory detection,
   model-artifact header checks (extension pre-filtered), manifest listing, and per-root
   coverage records (`completed` / `incomplete-cap-reached` / `skipped-*`). The output
   directory subtree is always pruned; `--exclude-root` adds more exclusions without dropping
   the containing root.
7. **cli_help** — bounded recursive help-tree export. Fixed argument vectors, round-robin
   queue, per-tool and global invocation caps, `--help`/`-h`/`help` fallbacks, per-call
   timeouts. Claude Code has a non-overridable ceiling (40 invocations, depth 2) that also
   applies to npm entry points (`claude.js`). Statuses preserve caps explicitly:
   `help-tree-exhausted`, `per-tool-cap-reached`, `no-help-adapter`, `not-tested`.
8. **reports** — `ai-inventory.json` + `AI-INVENTORY-REPORT.md` (see `docs/OUTPUT-SCHEMA.md`).

## Monitors

`ExportMonitor` maintains an in-memory ring of the last 2,000 events, appends every event to
`.monitor/events.jsonl`, and atomically rewrites `.monitor/state.json` on every emit.
The terminal TUI re-renders once per second; the web dashboard polls `/api/state` and streams
`/api/events` (Server-Sent Events). The dashboard is loopback-bound by default; remote binds
require a 0600 token file (≥ 32 chars), a session cookie (`HttpOnly`, `SameSite=Strict`,
`Secure` off-loopback), and a `Host` header check against DNS rebinding on loopback.

## Checkpoints and resume

`--resume` reuses stage results only when the checkpoint fingerprint matches. The fingerprint
is SHA-256 over the full scan configuration: platform data, profile, roots, all budgets,
`skip_cli_help`, `exclude_roots`, schema version, and the exporter source file hash. Any
change (including a code change) starts a fresh scan; cached CLI help is also re-validated
against the files on disk.

## Type safety and structured errors

The exporter layer boundaries use typed contracts (`TypedDict` definitions for
`PlatformInfo`, `ExecutableEntry`, `ModelArtifact`, `CoverageEntry`,
`CliExportResult`, `FilesystemScanResult`, `InventoryData`, `CheckpointData`)
and a structured exception hierarchy (`ExportError` → `ScopeError`,
`ConfigError`, `BudgetError`) so callers can distinguish scope, configuration,
budget, and export failures instead of parsing ad-hoc `ValueError`/`SystemExit`
messages. Runtime remains duck-typed so existing behavior is unchanged.

## Configuration file

An optional `--config PATH` (JSON or TOML on Python 3.11+) supplies scan
defaults. CLI flags always override config-file values; unknown keys are ignored.
The config is merged before profile defaults are applied, so a config file can
set `--max-total-entries`, `--profile`, `--monitor`, budgets, and timeouts.

## Invariants

- Read-only host inventory; the only executed workload is bounded CLI help.
- Never scans `/mnt`, `/proc`, `/sys`, `/dev`, `/run`; never collects environment values,
  `.env` contents, credentials, or process arguments.
- No third-party Python dependencies; sub-processes use fixed argument arrays, never a shell.
- Skipped, capped, failed, and unverified states are preserved in output and reports.

## Signal handling and graceful shutdown

The exporter installs `SIGINT` and `SIGTERM` handlers that set a module-level
`shutdown_requested` flag. All long-running stage functions (`scan_filesystem`,
`command_export`, and the orchestrator's `stage` wrapper) check this flag at
safe points and exit cleanly, persisting the checkpoint before returning. This
ensures that an interrupted run can be resumed with `--resume` without data loss.

## Disk space validation

Before starting the scan, the exporter checks that the output volume has at
least 100 MiB of free space. If the check fails, the exporter exits with a
clear error message rather than failing mid-run with a cryptic I/O error.

## Precise type aliases (PEP 484)

Stage functions declare precise return types using `TypeAlias` (compatible with
Python 3.10+ via `from typing import TypeAlias`):

- `ExecutablesResult = list[ExecutableEntry]`
- `PackagesResult = dict[str, list[PackageEntry] | PackageCollectionError]`
- `RuntimeResult = RuntimeInfo`
- `ContainersResult = ContainersInfo`
- `FilesystemResult = FilesystemScanResult`
- `CliExportStageResult = CliExportResult`

This enables static type checkers to verify stage pipelines without runtime
overhead. The `PackageCollectionError` TypedDict documents non-list package
group values (e.g. `python_metadata_error: str`) so the report writer can
render them correctly instead of crashing.

## CI/CD pipeline

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push and
pull request:

1. **Lint** — `ruff check scripts/ tests/`
2. **Compile** — `python -m py_compile` + `compileall` on all scripts and tests
3. **Test** — Unit + integration tests on Python 3.10, 3.11, 3.12, 3.13, 3.14
4. **Smoke** — `make validate` (compile + tests + CLI smoke checks)
5. **Shellcheck** — Static analysis of `Makefile`
6. **Security** — `bandit` (static analysis) + `safety` (dependency audit)
7. **Package** — `make package` (build ZIP, verify integrity)
8. **Docs** — Verify all documentation files exist

The workflow uses concurrency control to cancel superseded runs.
