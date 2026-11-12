# Audit plan (Inspect → Understand → Plan → Validate → Apply → Test → Verify → Document)

Baseline (validated): `python -m unittest discover -s tests -v` → 43/43 OK; `py_compile` OK; smoke OK.

## Confirmed bugs to fix

### scripts/ai_unified_export.py
1. **Report crash (high):** `package_inventory` can set `python_metadata_error` (a string) inside
   `packages`; `write_reports` iterates every value as a list → `AttributeError`, whole export fails
   at the final stage. Fix: skip non-list values in the report writer and surface the error as a note.
2. **Claude cap bypass (high):** the non-overridable Claude ceiling checks
   `Path(executable).name == "claude"`, but the npm entry point resolves to `bin/claude.js`
   (`npm-package-entry`), so the ceiling never applies on npm installs. Fix: match base name and
   stem (covers `claude.js`).
3. **Silent root drop (high):** when `--output` lives under a scan root (e.g. `--profile full
   --output ~/ai-inventory-export`), the whole `~` root is filtered out, silently scanning almost
   nothing. Fix: keep the root and prune the output subtree during traversal via an exclusion set.
4. **False "cap reached" (medium):** `hit_cap` reports `incomplete-cap-reached` when
   `local_count == max_per_root` even if the queue is empty (everything examined). Fix: rely on
   pending queue / global budget only.
5. **Dead code / I/O waste (medium):** `MODEL_EXTENSIONS` was defined but unused; every scanned file
   was opened to read 16 header bytes. Fix: extend the set (`.pt`, `.pth`) and use it as a
   pre-filter before opening files. `safe_model_file` behavior unchanged.
6. **Scan burn on dependency trees (medium):** `SKIP_NAMES` lacked `node_modules`, `.venv`, `venv`,
   `.npm`, `.cargo`, `.rustup` (AI CLIs are still covered by PATH/npm metadata discovery).
7. **Missing options:** `--version`, `--exclude-root` (repeatable subtree exclusion, now that the
   pruning machinery exists). `exclude_roots` added to the checkpoint fingerprint config.

### scripts/ai_model_manager.py
8. **Exit-code semantics (medium):** `export` exits 2 whenever any candidate is skipped — but
   skipping non-PASS models is the *documented default behavior*, so routine runs fail `make`.
   Fix: distinguish `skipped` (expected) from `error` (copy/hash failure); exit 2 only on errors,
   add opt-in `--fail-on-skip`; `--version`.

### scripts/ai_tools_installer.py
9. **Missing log entries (medium):** FAIL results from early returns (`no-usable-installer`,
   `script-sha256-required`, `script-sha256-mismatch`) were never appended to
   `installer-log.jsonl`, hiding failures. Fix: log every PASS/FAIL terminal status. `--version`.

### scripts/discover_ai_server.py (legacy)
10. **Docker/Podman parsing broken (high):** `--format "{{.Names}}|{{.Image}}|{{.Status}}"` emits
    `|` separators but the code splits on `\t` → no container/image ever matched. Fix split.
11. Regex contains accidental literal spaces (` LocalAI`, ` vllm`) — harmless duplicates, cleaned.
12. Mark as legacy in the docstring (superseded by `ai_unified_export.py`).

### scripts/export_ai_cli.py (legacy)
13. Mark as legacy in the docstring (superseded by the unified exporter's `cli_help` stage).

## Missing documentation to add
- `docs/ARCHITECTURE.md` — module map, pipeline, monitor/checkpoint design.
- `docs/OUTPUT-SCHEMA.md` — `ai-inventory.json` schema v4 field reference.
- `docs/LEGACY-SCRIPTS.md` — status and known limits of the two legacy scripts.
- `SECURITY.md` — threat model and safety controls.
- `CHANGELOG.md` — this update.
- README updates: new options, legacy scripts section, docs links, exclusion notes.

## Test additions
- Claude ceiling applies to `claude.js` npm entry points.
- Report writer tolerates `python_metadata_error`.
- Output subtree is pruned while the containing root is still scanned.
- `node_modules` / `.venv` are skipped.
- `--exclude-root` prunes a subtree.
- Exporter/model-manager/installer `--version`.
- Installer early failures are logged.
- Legacy docker parsing fixed.
- Model export exit semantics (`error` vs `skipped`).

## Verification gates (final)
- `python -m unittest discover -s tests -v` → 0 failures
- `python -m py_compile scripts/ai_unified_export.py scripts/ai_export_monitor.py`
- `make validate`
