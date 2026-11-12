import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "ai_unified_export.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("ai_unified_export", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ExporterTests(unittest.TestCase):
    def test_parse_child_commands(self):
        text = """Commands:
  auth       Manage authentication
  models     List models
  config -   Configure app
Options:
  --help
"""
        got = mod.parse_child_commands(text)
        self.assertIn("auth", got)
        self.assertIn("models", got)
        self.assertIn("config", got)

    def test_native_scope_rejects_mnt(self):
        self.assertFalse(mod.root_allowed(Path("/mnt")))

    def test_ai_name_matcher_covers_major_ai_tool_families(self):
        names = [
            "claude", "codex", "gemini", "cursor-agent", "windsurf",
            "cline", "roo", "goose", "ollama", "llama-server",
            "vllm", "litellm", "comfyui", "huggingface-cli",
            "langgraph", "crewai", "whisper-cli", "kokoro-tts",
            "openai", "anthropic", "cohere", "pinecone", "llamaindex",
            "pydantic-ai", "smolagents",
            "hermes", "hermes-agent", "craft-anything-cli", "craft-anything-",
            "openclaw", "kilocode", "kilo-code", "mcp-proxy",
            "codewhale", "deerflow", "grok-cli", "xai-sdk", "cerebras-cloud-sdk",
            "openrouter", "google-genai", "elevenlabs", "deepgram-sdk",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertRegex(name, mod.AI_NAME_RE)

    def test_known_agent_candidates_and_dirs_present(self):
        for name in ("hermes", "rea", "craft-anything-cli", "kilo", "kilocode", "opencode"):
            with self.subTest(name=name):
                self.assertIn(name, mod.CANDIDATES)
        self.assertIn(".hermes", mod.AI_DIR_NAMES)
        self.assertRegex("hermes-agent", mod.AI_DIR_RE)

    def test_process_filter_matches_comm_only_names(self):
        ps_out = "1 rea\n2 python3\n3 craft-anything-\n4 sshd\n5 opencode\n"
        with mock.patch.object(mod, "run", side_effect=lambda a, t=8.0, **k:
                               {"returncode": 0, "output": ps_out} if a[0] == "ps"
                               else {"returncode": 1, "output": ""}):
            procs = mod.system_runtime_inventory(quick=True)["processes"]
        self.assertEqual(sorted(x["command"] for x in procs), ["craft-anything-", "opencode", "rea"])

    def test_discovers_unknown_ai_named_executable_from_native_path(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "new-ai-runner"
            candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            candidate.chmod(0o755)
            with mock.patch.object(mod, "native_path_entries", return_value=[td]), \
                 mock.patch.object(mod.shutil, "which", return_value=None):
                found = mod.discover_executables()
            match = next(item for item in found if item["name"] == "new-ai-runner")
            self.assertEqual(match["source"], "native-PATH-pattern-discovery")
            self.assertEqual(match["path"], str(candidate.resolve()))

    def test_claude_help_tree_has_non_overridable_invocation_and_depth_caps(self):
        with tempfile.TemporaryDirectory() as td:
            help_text = "Commands:\n" + "\n".join(
                f"  command-{index:02d}  Description for command {index}" for index in range(100)
            )
            calls = []

            def fake_run(args, timeout=8.0, limit=2_000_000):
                calls.append(args)
                # Non-zero exit forces the exporter to exercise all help fallbacks.
                return {"returncode": 1, "output": help_text}

            with mock.patch.object(mod, "run", side_effect=fake_run):
                result = mod.command_export(
                    [{"name": "claude", "path": "/usr/local/bin/claude"}],
                    Path(td), max_invocations=50_000, per_tool=50_000,
                    max_depth=20, timeout=1.0,
                )

            claude = result["tools"]["claude"]
            self.assertLessEqual(len(calls), mod.CLAUDE_MAX_INVOCATIONS)
            self.assertLessEqual(claude["invocations"], mod.CLAUDE_MAX_INVOCATIONS)
            self.assertLessEqual(len(claude["commands"]), mod.CLAUDE_MAX_INVOCATIONS)
            self.assertEqual(claude["status"], "per-tool-cap-reached")
            self.assertTrue(claude["cap_reached"])
            self.assertTrue(all(item["depth"] <= mod.CLAUDE_MAX_DEPTH for item in claude["commands"]))

    def test_gguf_signature(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "model.gguf"
            p.write_bytes(b"GGUF" + b"\x00" * 32)
            self.assertEqual(mod.safe_model_file(p, p.stat().st_size), (True, "GGUF-magic"))

    def test_resume_reuses_completed_stages_without_monitor(self):
        with tempfile.TemporaryDirectory() as td:
            platform_data = {"distribution": "Ubuntu test", "kernel": "WSL2 test"}
            fs_data = {"directories": [], "metadata_candidates": [], "model_artifacts": [],
                       "manifests": [], "entries_examined": 0, "coverage": [],
                       "entry_limit_reached": False}
            runtime_data = {"processes": [], "systemd_services": [], "likely_ai_listening_sockets": []}
            containers_data = {"docker_containers": [], "docker_images": [],
                               "podman_containers": [], "podman_images": []}
            with mock.patch.object(mod, "validate_host", return_value=platform_data), \
                 mock.patch.object(mod, "discover_executables", return_value=[]) as discover, \
                 mock.patch.object(mod, "package_inventory", return_value={"dpkg": []}) as packages, \
                 mock.patch.object(mod, "system_runtime_inventory", return_value=runtime_data), \
                 mock.patch.object(mod, "container_inventory", return_value=containers_data), \
                 mock.patch.object(mod, "scan_filesystem", return_value=fs_data):
                original_argv = sys.argv[:]
                try:
                    sys.argv = [str(SCRIPT), "--monitor", "off", "--skip-cli-help", "--output", td]
                    self.assertEqual(mod.main(), 0)
                    sys.argv = [str(SCRIPT), "--monitor", "off", "--skip-cli-help", "--resume", "--output", td]
                    self.assertEqual(mod.main(), 0)
                finally:
                    sys.argv = original_argv
                self.assertEqual(discover.call_count, 1)
                self.assertEqual(packages.call_count, 1)
                self.assertTrue((Path(td) / ".monitor/checkpoint.json").is_file())

    def test_cache_scan_only_descends_into_ai_cache_roots(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / ".cache"
            (cache / "pip").mkdir(parents=True)
            (cache / "pip" / "large-unrelated.bin").write_bytes(b"x" * 2048)
            (cache / "huggingface" / "hub").mkdir(parents=True)
            model = cache / "huggingface" / "hub" / "model.gguf"
            model.write_bytes(b"GGUF" + b"\x00" * 64)
            result = mod.scan_filesystem([str(cache)], 1000, 1000)
            self.assertEqual(len(result["model_artifacts"]), 1)
            self.assertEqual(result["model_artifacts"][0]["path"], str(model))
            self.assertNotIn(str(cache / "pip" / "large-unrelated.bin"),
                             [item["path"] for item in result["model_artifacts"]])

    def test_rejects_tiny_pt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "not-a-model.pt"
            p.write_bytes(b"PK\x03\x04")
            self.assertFalse(mod.safe_model_file(p, p.stat().st_size)[0])

if __name__ == "__main__":
    unittest.main()
