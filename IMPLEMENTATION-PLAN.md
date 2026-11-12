# Implementation Plan — Enterprise-Grade Completion

## Baseline (already valid)
- 57/57 unit tests OK; `py_compile` OK; `ruff` clean; `make validate` PASS
- All AUDIT-PLAN.md fixes applied
- Python stdlib only; WSL2 Ubuntu target; read-only inventory

## Goals (enterprise / production-grade)
1. **Type safety**: TypedDicts + Protocols for all major data structures; eliminate unchecked `dict[str, Any]` at API boundaries.
2. **Structured error handling**: custom exception hierarchy (ScopeError, ConfigError, BudgetError, ExportError) replacing ad-hoc ValueError/SystemExit at layer boundaries.
3. **Configuration file**: optional `--config` (JSON/TOML via stdlib `tomllib`) providing defaults, CLI overrides win.
4. **Integration tests**: end-to-end tests running real scripts with mocked subprocess, verifying output files and exit codes.
5. **CI/CD**: GitHub Actions workflow (lint ruff, py_compile, unittest, smoke, shellcheck Makefile).
6. **Operations docs**: RUNBOOK.md, TROUBLESHOOTING.md; refresh ARCHITECTURE.md / OUTPUT-SCHEMA.md.
7. **Final-Report.md**: full validation summary, evidence, gate results.
8. **Git/GH workflow**: init repo, commit as `cvsz` (GPG), open PR, audit/review/approve as `policedbc`, merge as `cvsz`.

## Constraints (from AGENTS.md / zeaz.md)
- Runtime deps: Python stdlib only (no new packages)
- Validate: `python -m unittest discover -s tests -v` and `py_compile scripts/ai_unified_export.py scripts/ai_export_monitor.py`
- Never scan /mnt, /proc, /sys, /dev; never collect env/secrets/process args
- Do not claim WSL2 validation unless actually run on WSL2
- Exporter/monitor read-only; installer dry-run by default, --apply required

## Phases
1. Inspect & understand (complete)
2. Type safety + structured errors in scripts/ (apply)
3. Config file support (apply)
4. Integration tests + CI workflow (apply)
5. Operations docs + doc sync (apply)
6. Test & verify: unittest, py_compile, ruff, make validate, smoke (verify)
7. Final-Report.md (document)
8. Git init, commit as cvsz, PR, audit as policedbc, merge as cvsz (document/apply)

Status: Phase 2 starting.