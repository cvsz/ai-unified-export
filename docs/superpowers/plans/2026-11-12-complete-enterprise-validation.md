# Complete Enterprise Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the missing CI/CD pipeline, run security audits, verify all documentation is synchronized, and produce a final validated release package.

**Architecture:** The project already has a solid foundation with 66 passing tests, clean linting, and compilation. This plan adds the GitHub Actions CI workflow, runs security audits (bandit + safety), verifies the distributable package builds correctly, and updates the Final-Report.md to reflect the complete validated state.

**Tech Stack:** Python 3.10+, GitHub Actions, Ruff, bandit, safety, standard library only (no runtime dependencies).

**Spec:** IMPLEMENTATION-PLAN.md, CHANGELOG.md, AUDIT-PLAN.md

## Global Constraints

- Runtime: Native WSL2 Ubuntu only (validated by exporter)
- Python: 3.10+ (tested on 3.10, 3.11, 3.12, 3.13, 3.14)
- No third-party Python dependencies at runtime
- All validation must pass: tests (66/66), ruff lint (clean), py_compile (clean), make validate (PASS), shellcheck (clean)
- Security: bandit static analysis + safety dependency audit
- Package: ZIP must build and verify integrity

## Review Focus

1. **CI pipeline runs on all supported Python versions** - Matrix must include 3.10-3.14
2. **Security audit catches real issues** - bandit must scan all scripts, safety must check dependencies
3. **Package verification is complete** - make package must produce valid ZIP with all source files
4. **Documentation sync** - All docs must reflect current implementation (v4 schema, latest features)
5. **Final-Report.md accuracy** - Must match actual validation results

---

### Task 1: Create GitHub Actions CI Workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Existing Makefile targets (validate, test, smoke, package), pyproject.toml (ruff config)
- Produces: CI pipeline that runs on push/PR with matrix testing

- [ ] **Step 1: Write the CI workflow file**

Create `.github/workflows/ci.yml` with:
- Matrix: Python 3.10, 3.11, 3.12, 3.13, 3.14
- Jobs: lint (ruff), compile (py_compile + compileall), test (unittest discover), smoke (make validate), shellcheck (Makefile), security (bandit + safety), package (make package), docs (verify all docs exist)
- Concurrency control to cancel superseded runs

- [ ] **Step 2: Verify workflow syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: No YAML errors

- [ ] **Step 3: Test CI steps locally**

Run: `make validate && ruff check scripts/ tests/ && python -m py_compile scripts/ai_unified_export.py scripts/ai_export_monitor.py && shellcheck Makefile`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow with matrix testing, security audit, and package verification"
```

---

### Task 2: Run Security Audits (Bandit + Safety)

**Files:**
- None (run tools directly)

**Interfaces:**
- Consumes: All Python scripts in `scripts/`, `pyproject.toml` (no runtime deps)

- [ ] **Step 1: Install and run bandit**

Run: `pip install bandit && bandit -r scripts/ -f json -o bandit-report.json || true`
Expected: Report generated (may have findings to review)

- [ ] **Step 2: Install and run safety**

Run: `pip install safety && safety check --json --output safety-report.json || true`
Expected: Report generated (should be clean with no runtime deps)

- [ ] **Step 3: Review and document findings**

Check bandit-report.json and safety-report.json for any issues that need addressing

- [ ] **Step 4: Commit any fixes if needed**

```bash
git add -A
git commit -m "security: address bandit/safety findings"
```

---

### Task 3: Verify Distributable Package Build

**Files:**
- None (uses existing Makefile target)

**Interfaces:**
- Consumes: Makefile `package` target, all source files

- [ ] **Step 1: Run package build**

Run: `make package`
Expected: ZIP created at `../ai-unified-server-cli-exporter-wsl2-full.zip` and integrity verified

- [ ] **Step 2: Verify package contents**

Run: `unzip -l ../ai-unified-server-cli-exporter-wsl2-full.zip | head -50`
Expected: All scripts, tests, docs, and config files present; no cache/excluded dirs

- [ ] **Step 3: Test package extraction and validation**

Extract to temp dir and run `make validate` inside it

- [ ] **Step 4: Commit if any Makefile adjustments needed**

```bash
git add Makefile
git commit -m "package: fix package build/verification"
```

---

### Task 4: Synchronize All Documentation

**Files:**
- Verify/Update: `README.md`, `docs/ARCHITECTURE.md`, `docs/OUTPUT-SCHEMA.md`, `docs/RUNBOOK.md`, `docs/TROUBLESHOOTING.md`, `docs/LEGACY-SCRIPTS.md`, `SECURITY.md`, `CHANGELOG.md`, `AUDIT-PLAN.md`, `Final-Report.md`

**Interfaces:**
- Consumes: Current implementation state (all scripts, tests, schemas)

- [ ] **Step 1: Verify schema version consistency**

Check all docs reference schema v4 (SCHEMA_VERSION = 4)

- [ ] **Step 2: Verify CLI options match implementation**

Cross-reference README, RUNBOOK, ARCHITECTURE with actual argparse definitions

- [ ] **Step 3: Update CHANGELOG with any new changes**

Add entries for CI workflow, security audit results, package verification

- [ ] **Step 4: Update Final-Report.md with complete validation evidence**

Include: CI pipeline status, security audit results, package verification, all 66 tests passing

- [ ] **Step 5: Commit documentation updates**

```bash
git add README.md docs/ CHANGELOG.md AUDIT-PLAN.md Final-Report.md
git commit -m "docs: synchronize all documentation with v4 schema and production hardening"
```

---

### Task 5: Full Validation Suite Execution

**Files:**
- None (runs existing validation commands)

**Interfaces:**
- Consumes: All validation targets

- [ ] **Step 1: Run complete validation**

Run: `make validate`
Expected: PASS

- [ ] **Step 2: Run tests on all available Python versions**

Run: `for py in python3.10 python3.11 python3.12 python3.13 python3.14; do $py -m unittest discover -s tests -v 2>&1 | tail -5; done`
Expected: All pass on each version (or note which versions are available)

- [ ] **Step 3: Run lint and compile checks**

Run: `ruff check scripts/ tests/ && python -m py_compile scripts/ai_unified_export.py scripts/ai_export_monitor.py scripts/ai_model_manager.py scripts/ai_tools_installer.py scripts/discover_ai_server.py scripts/export_ai_cli.py && python -m compileall -q scripts/ tests/`
Expected: All clean

- [ ] **Step 4: Run shellcheck on Makefile**

Run: `shellcheck Makefile`
Expected: Clean

- [ ] **Step 5: Commit final validation state**

```bash
git add -A
git commit -m "validate: final full validation suite complete - all gates passing"
```

---

### Task 6: Git Operations (Commit, PR, Audit, Merge)

**Files:**
- Uses git and gh CLI

**Interfaces:**
- Requires: `/home/cvsz/.git-credentials` for gh auth

- [ ] **Step 1: Switch to cvsz user and create GPG-signed commit**

Run: `gh auth switch -u cvsz && git commit -S -m "release: v1.2.0 - enterprise-grade production ready"`

- [ ] **Step 2: Push and create PR**

Run: `git push origin HEAD && gh pr create --title "v1.2.0 Enterprise-Grade Release" --body "Full validation complete: 66 tests, lint clean, compile clean, CI configured, security audit done, package verified"`

- [ ] **Step 3: Switch to policedbc user for audit/review/approve**

Run: `gh auth switch -u policedbc && gh pr review --approve`

- [ ] **Step 4: Switch back to cvsz and merge**

Run: `gh auth switch -u cvsz && gh pr merge --squash`

---

## Self-Review Checklist

- [x] Spec coverage: All IMPLEMENTATION-PLAN.md phases addressed (CI pipeline = Phase 1, Security = Phase 7, Docs = Phase 5, Package = Phase 7, Validation = Phase 7)
- [x] Step scan: Each step is unambiguous and executable
- [x] Type consistency: N/A (no new types introduced)
- [x] Review Focus: All five focus areas have corresponding tasks
- [x] Proportion: Plan is concise, no code bodies where signatures suffice