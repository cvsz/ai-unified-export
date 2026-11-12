# Security policy

## Threat model

The tools in this bundle run on a developer workstation (WSL2 Ubuntu). The assets to protect
are: the confidentiality of the filesystem (no secrets leave the machine), the integrity of
the host (nothing is installed or mutated without an explicit, reviewed action), and the
integrity of downloaded artifacts (checksum-pinned, allowlisted HTTPS only).

## Controls by component

### Unified exporter (`scripts/ai_unified_export.py`)
- Host-gated to WSL2 Ubuntu; refuses to run elsewhere.
- Filesystem discovery never enters `/mnt/*` (Windows), `/proc`, `/sys`, `/dev`, `/run`,
  or temp/log/cache roots other than explicitly allowed AI cache subdirectories.
- No environment values, `.env` contents, credentials, or process arguments are collected.
  Process detection reads the command **name** only.
- The only executed workload is CLI help via fixed argument arrays (never a shell), with
  output caps, per-call timeouts, per-tool and global invocation budgets, and a
  non-overridable Claude Code ceiling (40 invocations / depth 2) that also covers the
  `claude.js` npm entry point.
- Sanitized child environment: only `PATH` (native entries only), `HOME`, `LANG`.
- Windows PE (`MZ`) executables are excluded from discovery.

### Monitor dashboard (`scripts/ai_export_monitor.py`)
- Binds to `127.0.0.1` by default; remote binds **require** a token file with ≥ 32 characters
  and POSIX mode `0600`.
- Session cookie is `HttpOnly`, `SameSite=Strict`, `Secure` when bound off-loopback;
  token comparison uses `hmac.compare_digest`.
- Loopback binds validate the `Host` header against the bound port (DNS-rebinding defense).
- Read-only endpoints: `/health`, `/api/state`, `/api/events` (SSE), plus `/api/auth` login.
  No endpoint executes commands or mutates state. Security headers (`nosniff`, CSP,
  `frame-ancestors 'none'`, `no-store`) are set on responses.
- Remote dashboard tokens still allow read-only visibility of inventory data; place remote
  binds behind a TLS-terminating reverse proxy and firewall them.

### Model manager (`scripts/ai_model_manager.py`)
- Never imports model frameworks, deserializes weights, or executes model code. Validation
  is static: magic/header/structure checks (GGUF fixed header, Safetensors header and tensor
  offsets, TFLite magic, PyTorch ZIP CRC) only, with explicit `PARTIAL` states where full
  validation is unavailable.
- Downloads require HTTPS, an exact hostname allowlist (Hugging Face primary/large-file hosts
  by default), public DNS resolution (no private/loopback addresses), and a 64-hex digest
  supplied by the operator. Redirects are re-validated against the same allowlist; a
  byte ceiling, temp-file write, digest verification, and atomic rename precede publication.
  Existing destinations are never overwritten.
- Export copies are re-hashed after copying; a mismatch deletes the copy and records an
  `error` record that drives a non-zero exit code.

### Installer (`scripts/ai_tools_installer.py`)
- Dry-run by default; `--apply` plus explicit tool names are required. No shell, no sudo;
  fixed argument vectors only; package managers preferred.
- Remote installer scripts only after exact HTTPS host match, no redirects, a 1 MiB size
  ceiling, and an operator-supplied SHA-256 (`script-hash` prints the digest without running).
- A zero exit code without the binary on `PATH` is reported `FAIL`, never `PASS`. Every
  terminal PASS/FAIL outcome — including pre-execution failures such as
  `no-usable-installer` or `script-sha256-mismatch` — is appended to
  `ai-installer-logs/installer-log.jsonl`.

## Reporting a vulnerability

This bundle is distributed as source. Report issues to the repository maintainer; do not open
public issues containing exploitable detail.
