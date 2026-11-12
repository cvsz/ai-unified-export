# Output schema — `ai-inventory.json` (schema_version 4)

Produced by `scripts/ai_unified_export.py`. UTF-8 JSON, one document per run.

## Top-level fields

| Field | Type | Description |
|---|---|---|
| `schema_version` | int | `4` |
| `generated_at_unix` | int | Completion time (UTC epoch seconds). |
| `platform` | object | `wsl2`, `distribution`, `kernel`, `python`, `architecture` from host validation. |
| `scope` | object | Human-readable scope statements: filesystem scope, excluded roots, secrets policy, mode. |
| `profile` | string | `quick` or `full`. |
| `scan_roots` | list[string] | Configured native roots actually used for filesystem scanning. |
| `scan_exclusions` | list[string] | Resolved subtrees pruned during traversal (output directory plus `--exclude-root` values). Added in schema v4. |
| `config_source` | string|null | Path to the `--config` file used, or `null` when none was supplied. Recorded in the checkpoint fingerprint so config changes invalidate resume. |

Configuration files (optional `--config`, JSON or TOML on Python 3.11+) supply scan
defaults; CLI flags override them. The resolved config is part of the resume
fingerprint, so changing config invalidates a cached checkpoint.
| `executables` | list[object] | `{name, path, source, kind}`; sources: `native-PATH`, `native-PATH-pattern-discovery`, `native-user-bin`, `npm-package-entry`. |
| `packages` | object | Groups `dpkg`, `snap`, `npm_global`, `cargo`, `python_metadata`, each a list of `{name, version, ...}`; a collection failure appears as a non-list note (e.g. `python_metadata_error`) and is rendered as a report note, not a crash. |
| `runtime` | object | `processes` (`{pid, command}` — command name only), `systemd_services` (`{unit, source}`), `likely_ai_listening_sockets` (raw `ss` lines). |
| `containers` | object | `docker_containers`, `docker_images`, `podman_containers`, `podman_images` (matched JSON rows). |
| `filesystem_scan` | object | See below. |
| `cli_export` | object | See below. |
| `duration_seconds` | number | Wall-clock duration of the run. |

## `filesystem_scan`

| Field | Description |
|---|---|
| `directories` | AI-named directories: `{name, path}`. |
| `metadata_candidates` | AI-named `.yaml/.yml/.json/.toml/.lock/.txt` files: `{path, size_bytes, classification}`. |
| `model_artifacts` | Files passing structural checks: `{path, size_bytes, format, evidence}` (plus `resolved_path`, `symlink` for symlinked files). |
| `manifests` | `package.json`, `Cargo.toml`, `go.mod` paths with sizes (contents are never read for collection). |
| `entries_examined` | Global entry count across all roots. |
| `coverage` | Per root: `{root, status, entries_examined, directories, artifacts, models, manifests}`; status is `completed`, `incomplete-cap-reached`, or `skipped-unavailable-or-out-of-scope`. |
| `entry_limit_reached` | True when the global budget or any root reported a cap. |

## `cli_export`

| Field | Description |
|---|---|
| `tools` | Per tool: `{path, status, commands, errors, timeouts, invocations, cap_reached}`; `commands` entries carry `{tool, command, help_arguments, depth, help_file}`. Statuses: `help-tree-exhausted`, `per-tool-cap-reached`, `no-help-adapter`, `not-tested`. |
| `total_invocations`, `total_commands` | Bounded totals. |
| `global_cap_reached` | True when the global invocation budget stopped queued work. |
| `semantics` | Statement that help trees are discoveries, not guarantees of hidden/gated commands. |

When `--skip-cli-help` is given, `cli_export` contains empty counters and the semantics note
`"CLI help export skipped by request."`

## Side-car files

| Path | Content |
|---|---|
| `AI-INVENTORY-REPORT.md` | Human-readable rendering of the JSON, including coverage and cap status. |
| `cli-help/<tool>/<path>.txt` | Captured help text per command path (`root` for the top level). |
| `.monitor/state.json` | Atomically rewritten live monitor state. |
| `.monitor/events.jsonl` | Append-only event journal (`{id, run_id, time, type, payload}`). |
| `.monitor/checkpoint.json` | `{schema_version, fingerprint, updated_at, stages}` for `--resume`. |
