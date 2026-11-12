# Troubleshooting — Unified AI Server & CLI Exporter

## 1. Exporter refuses to run: "restricted to WSL2 Ubuntu"

**Cause:** Host validation detected a non-WSL2 kernel or a non-Ubuntu distribution.

**Check:**
```bash
uname -a
cat /etc/os-release | grep PRETTY_NAME
```

**Fix:** This exporter only runs on native WSL2 Ubuntu. Run it inside a WSL2 Ubuntu
instance. Do not bypass the check — it is a safety invariant.

## 2. Dashboard is not reachable

**Loopback (default):**
- Confirm `--monitor-host 127.0.0.1` (default) and `--monitor-port 8765`.
- Confirm the monitor mode is not `off`: `--monitor both` or `--monitor web`.
- Check `.monitor/events.jsonl` for a `dashboard_error` event.
- If another process holds port 8765, use `--monitor-port <other>`.

**Remote bind:**
- `--monitor-host` must be a private interface address, not `0.0.0.0` on untrusted networks.
- `--monitor-token-file` must point to a file with ≥32 characters and POSIX mode `0600`.
- Place the dashboard behind a TLS-terminating reverse proxy and firewall it.
- Loopback `Host` header is validated against the bound port (DNS-rebinding defense).

**Browser shows login page:** A token file was supplied for a non-loopback bind;
authenticate via `/api/auth` with the token, which sets an `HttpOnly` session cookie.

## 3. "Config file not found" or "Malformed config file"

**Cause:** `--config` points to a missing or unparsable JSON/TOML file.

**Fix:** Verify the path exists and the file is valid JSON (or TOML on Python 3.11+).
CLI flags always override config-file values; unknown config keys are ignored.

## 4. Resume reuses stale data or starts a fresh scan unexpectedly

**Cause:** The checkpoint fingerprint must match exactly: platform data, profile,
roots, all budgets, `skip_cli_help`, `exclude_roots`, schema version, and the
exporter source file hash. Any code change invalidates the checkpoint.

**Fix:** To force a fresh scan, omit `--resume` or change a scan option.
Cached CLI help is also re-validated against files on disk.

## 5. Model export exits 2

**Cause:** Exit 2 means copy/hash failures (`error` records), not expected skips.
Expected skips (non-PASS models, byte-budget skips) exit 0.

**Fix:** Inspect `model-manifest.json` for `error_count` records. Use
`--fail-on-skip` to make skips also exit 2. Missing source files or unreadable
permissions produce `error` records.

## 6. Claude Code help tree capped at 40 invocations / depth 2

**Cause:** Non-overridable ceiling for Claude Code (including the `claude.js`
npm entry point) to prevent runaway help-tree traversal.

**Fix:** This is by design. The cap cannot be raised. Other tools use the
configured `--per-tool-invocations` and `--max-depth` budgets.

## 7. No AI executables / packages discovered

**Cause:** None of the known candidate names are on the native PATH, no AI-named
executables match the filename pattern, and no AI-related packages are installed.

**Fix:** This is expected on a host without AI tooling. Verify PATH with
`echo $PATH` and confirm AI tools are installed natively (not under `/mnt/` which
is excluded). Windows PATH entries and PE binaries are excluded.

## 8. Filesystem scan stops early ("cap reached")

**Cause:** A configured budget was hit: `--max-total-entries` (global),
`--max-entries-per-root` (per root), or `--max-invocations` (CLI help).

**Fix:** Inspect `filesystem_scan.coverage` in `ai-inventory.json` for per-root
status (`completed` / `incomplete-cap-reached`). Raise budgets explicitly for
larger hosts. Dependency trees (`node_modules`, `.venv`, etc.) are skipped to
preserve budget.

## 9. Installer reports FAIL: "installer-exited-0-but-<binary>-not-on-PATH"

**Cause:** The installer exited 0 but the binary is not on PATH in the current
shell (common with npm/pipx installs that need a new shell or PATH refresh).

**Fix:** Open a new shell or add the install directory to PATH. The installer
never reports PASS unless the binary is found on PATH after installation.

## 10. Dashboard reports "stalled"

**Cause:** No monitor activity event for over 60 seconds while a stage is still
running. This can happen during very long filesystem scans or CLI help batches.

**Fix:** Check `.monitor/events.jsonl` for recent events. If the scan is genuinely
progressing slowly, increase budgets or run with `--monitor off` to reduce
overhead.

## Getting help

- Check `.monitor/events.jsonl` for the event journal.
- Check `.monitor/state.json` for current stage/counter state.
- Check `ai-installer-logs/installer-log.jsonl` for installer attempts.
- Run `make validate` to confirm compile, tests, and smoke checks all pass.
- Report issues to the repository maintainer; do not post secrets or exploit
  detail in public issues.
