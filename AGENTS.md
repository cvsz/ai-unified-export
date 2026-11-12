# READ FIRST — ZeaZDev Engineering Policy

Canonical policy: https://github.com/cvsz/zeaz/blob/main/zeaz.md

Before modifying this project, read the canonical policy, this file, and `README.md`.
Follow the required lifecycle: **Inspect → Understand → Plan → Validate → Apply → Test → Verify → Document**.

## Project-specific constraints

- Target runtime is native WSL2 Ubuntu; do not broaden filesystem discovery into `/mnt`, Windows paths, `/proc`, `/sys`, or `/dev`.
- Do not collect environment values, secrets, process arguments, or configuration-file contents.
- Keep CLI execution bounded by explicit allowlisted discovery, fixed argument arrays, output limits, invocation budgets, and timeouts.
- Keep the web monitor read-only, loopback-bound by default, authenticated for remote binding, and free of external assets.
- Preserve explicit failure, skip, cap, and unverified states; do not report incomplete discovery as complete.
- Keep runtime dependencies at Python standard library unless a documented requirement justifies otherwise.
- Validate with `python -m unittest discover -s tests -v` and `python -m py_compile scripts/ai_unified_export.py scripts/ai_export_monitor.py`.
- Do not claim WSL2 integration validation unless the exporter has actually run on a WSL2 Ubuntu host.
- The exporter stays read-only. Installation lives only in `scripts/ai_tools_installer.py`: dry-run by default, `--apply` required, no shell, no sudo, remote scripts only with an exact host match and a pinned SHA-256. Do not add auto-install behavior to the exporter or monitor.
- Installer catalog commands are unverified against live registries; keep `check-catalog` passing and never mark a catalog entry verified without evidence.
