# Changelog

## 2026-11-12 — Enterprise-grade full implementation (second pass)

Validated by: `python -m unittest discover -s tests -v` (66/66 OK), `make validate`
(compile + tests + smoke PASS), `ruff check scripts/ tests/` (clean), and
end-to-end integration tests.

### Added — `scripts/ai_unified_export.py`
- **Type safety:** TypedDict contracts for all major data structures
  (`PlatformInfo`, `ExecutableEntry`, `ModelArtifact`, `CoverageEntry`,
  `CliExportResult`, `FilesystemScanResult`, `InventoryData`, `CheckpointData`,
  and related) documenting the schema v4 contracts at API boundaries.
- **Structured error handling:** `ExportError` base with `ScopeError`,
  `ConfigError`, `BudgetError` subclasses; `validate_host()` and
  `require_native_path()` raise typed exceptions so callers can distinguish
  scope, configuration, budget, and export failures.
- **Configuration file:** `--config PATH` (JSON or TOML on Python 3.11+) supplies
  scan defaults; CLI flags override; unknown keys ignored; config is part of the
  resume fingerprint. `load_config()` / `apply_config()` helpers.
- **`require_native_path()`:** centralized WSL2 native scope validation that
  raises `ScopeError` for `/mnt`, `/proc`, `/sys`, `/dev`, `/run` and non-absolute
  paths; used by `path_is_native()` and `root_allowed()`.

### Added — `tests/test_integration.py`
- Nine end-to-end integration tests driving real `main()` with mocked
  subprocess/host: full run writes reports + checkpoint, config defaults + CLI
  override, missing config exits 2, resume fingerprint mismatch → fresh run,
  ScopeError on non-WSL2, model export→validate roundtrip (copy + SHA-256 verify
  + manifest + report), validate CLI pass/fail exit codes, installer catalog
  check + list, monitor lifecycle.

### Added — CI and operations
- `.github/workflows/ci.yml`: GitHub Actions pipeline — lint (ruff), compile
  (py_compile + compileall), unit + integration tests, smoke (`make validate`),
  shellcheck on Makefile.
- `docs/RUNBOOK.md`: common operations — quick/full scans, resume, config files,
  model lifecycle, installer, Makefile targets, monitor dashboard, exit codes,
  safety invariants.
- `docs/TROUBLESHOOTING.md`: diagnostic guide — host validation, dashboard
  access, config errors, resume fingerprints, model export exit codes, Claude
  caps, discovery gaps, scan caps, installer PATH issues, stalled dashboards.
- `Final-Report.md`: full implementation, validation, and release report.

### Updated — documentation
- `docs/ARCHITECTURE.md`: type-safety / structured-error / config-file sections.
- `docs/OUTPUT-SCHEMA.md`: `config_source` field and config-file reference.
- `README.md`: docs index expanded with RUNBOOK.md and TROUBLESHOOTING.md.

### Tests
- New `tests/test_integration.py` (9 tests). Total suite: 66 tests, all OK.

## 2026-11-12 — Independent audit, fixes, and documentation

Validated by: `python -m unittest discover -s tests -v` (57/57 OK), `make validate`
(compile + tests + smoke PASS), and an end-to-end quick-scan + model-export run.

### Fixed — `scripts/ai_unified_export.py`
- **Report-stage crash:** a package-collection error note (`python_metadata_error`) made
  `write_reports` iterate a string as a list and crash the whole export at the final stage;
  non-list package values are now rendered as report notes.
- **Claude ceiling bypass:** the non-overridable Claude Code cap matched only the executable
  base name `claude`; the npm entry point `bin/claude.js` escaped it. The cap now matches the
  tool name, base name, and stem.
- **Silent root drop:** when `--output` lived under a scan root (e.g. a full-profile scan with
  the output in the home directory), the entire containing root was removed from the scan.
  Roots are now kept and the output subtree is pruned during traversal.
- **False cap status:** a per-root budget hit exactly at the last examined entry, or a flat
  directory capped with no queued subdirectories, could be mislabeled `completed` (or the
  reverse); cap detection now uses the pending queue, the global budget, and an explicit
  inner-loop break flag.
- **Scan I/O waste:** `MODEL_EXTENSIONS` was defined but unused and every scanned file was
  opened for a header check; the set (now including `.pt`/`.pth`) pre-filters which files are
  opened.
- **Dependency-tree burn:** `node_modules`, `.venv`, `venv`, `.npm`, `.cargo`, `.rustup` are
  now skipped during filesystem scanning (PATH and package metadata still cover CLIs there).

### Added — `scripts/ai_unified_export.py`
- `--version` flag; `--exclude-root PATH` (repeatable) to prune native subtrees without
  dropping the containing root; `scan_exclusions` recorded in output and the checkpoint
  fingerprint.

### Fixed — `scripts/ai_model_manager.py`
- **Exit-code semantics:** `export` exited 2 whenever any candidate was skipped, although
  skipping non-PASS models is the documented default; expected skips now exit 0, copy/hash
  failures are recorded as `error` records and exit 2, and `--fail-on-skip` restores strict
  behavior. Manifest/report distinguish `skipped_count` from `error_count`.

### Added — `scripts/ai_model_manager.py`
- `--version` flag; `main()` accepts an argv for testability.

### Fixed — `scripts/ai_tools_installer.py`
- **Hidden failures:** early FAIL results (`unknown-tool`, `no-usable-installer`,
  `script-sha256-required`, `script-sha256-mismatch`) were never appended to
  `installer-log.jsonl`; every terminal PASS/FAIL outcome is now logged.

### Added — `scripts/ai_tools_installer.py`
- `--version` flag.

### Fixed — `scripts/discover_ai_server.py` (legacy)
- Docker/Podman rows were split on `\t` while `--format` emitted `|` separators, so no
  container or image ever matched; rows now split on `|`, and repository basenames
  (`ollama/ollama`) are matched. Removed dead regex alternatives containing literal spaces.

### Tests
- New `tests/test_fixes.py` (14 tests): Claude ceiling on `claude.js`, report-writer
  resilience, output-subtree pruning, `--exclude-root`, dependency-tree skips, per-root cap
  status edge cases, legacy Docker parsing, export exit semantics, copy-error records,
  installer logging, `--version` smoke. Total suite: 57 tests.

### Documentation
- Added `docs/ARCHITECTURE.md`, `docs/OUTPUT-SCHEMA.md`, `docs/LEGACY-SCRIPTS.md`,
  `SECURITY.md`, `AUDIT-PLAN.md` (audit record), and this changelog; updated `README.md`
  (new options, legacy scripts, docs index, model-export exit semantics).
- Makefile: `check-tools` verifies legacy scripts; smoke checks cover `--version` and both
  legacy script help screens; `EXCLUDE_ROOTS` variable plumbed into all inventory targets.
