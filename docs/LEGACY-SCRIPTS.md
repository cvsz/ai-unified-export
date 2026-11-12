# Legacy scripts

Two standalone scripts predate the unified exporter. They remain in the bundle for
compatibility with earlier runs, are compiled and smoke-checked by `make validate`, but are
**superseded** and receive no new features.

| Script | Superseded by | Notes |
|---|---|---|
| `scripts/discover_ai_server.py` | `ai_unified_export.py` (all stages) | Restricted to WSL2 **Ubuntu 26.04** exactly; no monitors, checkpoints, or CLI help export. |
| `scripts/export_ai_cli.py` | `ai_unified_export.py --profile ...` (cli_help stage) | PATH-only discovery, fixed candidate list, no pattern discovery, no monitors. |

## Fixed in this update

- `discover_ai_server.py`: Docker/Podman parsing used `|` separators in `--format` but split
  rows on `\t`, so **no container or image ever matched**; rows are now split on `|`, and
  image repository basenames (`ollama/ollama`) are matched in addition to the full string.
- `discover_ai_server.py`: removed accidental literal spaces inside the AI name regex
  (`" LocalAI"`, `" vllm"` alternatives that could never match).

## Known remaining limits (by design, documented for honesty)

- `discover_ai_server.py` `SKIP_DIRS` contains entry names such as `.cache/pip` that cannot
  match a single path component; they are inert. The entry still skips any `.cache*` directory.
- `export_ai_cli.py` subcommand extraction requires an explicit `Commands:` style heading;
  CLIs that list commands inline are reported as `partial`/failed rather than mis-parsed.
- Neither legacy script is covered by the checkpoint/monitor machinery; use the unified
  exporter for new inventories.
