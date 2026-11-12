"""Regression tests for the audit fixes (see AUDIT-PLAN.md)."""
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("ai_unified_export", ROOT / "scripts" / "ai_unified_export.py")
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)

_mspec = importlib.util.spec_from_file_location("ai_model_manager", ROOT / "scripts" / "ai_model_manager.py")
model_manager = importlib.util.module_from_spec(_mspec)
_mspec.loader.exec_module(model_manager)

_ispec = importlib.util.spec_from_file_location("ai_tools_installer", ROOT / "scripts" / "ai_tools_installer.py")
installer = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(installer)

_dspec = importlib.util.spec_from_file_location("discover_ai_server", ROOT / "scripts" / "discover_ai_server.py")
legacy_discover = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(legacy_discover)

GGUF = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + b"\x00" * 32


class ClaudeCapTests(unittest.TestCase):
    def test_claude_ceiling_applies_to_npm_entrypoint_claude_js(self):
        with tempfile.TemporaryDirectory() as td:
            help_text = "Commands:\n" + "\n".join(
                f"  sub-{index:02d}  Description" for index in range(60)
            )
            calls = []

            def fake_run(args, timeout=8.0, limit=2_000_000):
                calls.append(args)
                return {"returncode": 1, "output": help_text}

            with mock.patch.object(exporter, "run", side_effect=fake_run):
                result = exporter.command_export(
                    [{"name": "claude", "path": "/home/u/.npm-global/lib/node_modules/"
                                             "@anthropic-ai/claude-code/bin/claude.js"}],
                    Path(td), max_invocations=50_000, per_tool=50_000,
                    max_depth=20, timeout=1.0,
                )
            claude = result["tools"]["claude"]
            self.assertLessEqual(claude["invocations"], exporter.CLAUDE_MAX_INVOCATIONS)
            self.assertLessEqual(len(calls), exporter.CLAUDE_MAX_INVOCATIONS)
            self.assertTrue(claude["cap_reached"])


class ReportWriterTests(unittest.TestCase):
    def _data(self, packages):
        return {
            "platform": {"distribution": "Ubuntu test", "kernel": "WSL2 test"},
            "generated_at_unix": 0, "duration_seconds": 1.0, "profile": "quick",
            "executables": [], "packages": packages,
            "runtime": {"processes": [], "systemd_services": []},
            "containers": {},
            "filesystem_scan": {"entries_examined": 0, "entry_limit_reached": False,
                                "model_artifacts": [], "directories": [], "coverage": []},
            "cli_export": {"tools": {}, "total_invocations": 0, "total_commands": 0,
                           "global_cap_reached": False, "semantics": "s"},
        }

    def test_write_reports_survives_package_collection_error(self):
        with tempfile.TemporaryDirectory() as td:
            data = self._data({"python_metadata_error": "boom"})
            exporter.write_reports(Path(td), data)
            text = (Path(td) / "AI-INVENTORY-REPORT.md").read_text(encoding="utf-8")
            self.assertIn("Collection error: boom", text)

    def test_write_reports_normal_packages(self):
        with tempfile.TemporaryDirectory() as td:
            data = self._data({"dpkg": [{"name": "ollama", "version": "1.0"}]})
            exporter.write_reports(Path(td), data)
            text = (Path(td) / "AI-INVENTORY-REPORT.md").read_text(encoding="utf-8")
            self.assertIn("`ollama` `1.0`", text)


class ScanFilesystemTests(unittest.TestCase):
    def test_output_subtree_pruned_but_root_kept(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out = base / "ai-inventory-export"
            (out / "cli-help").mkdir(parents=True)
            (out / "keep.gguf").write_bytes(GGUF)
            (base / "real-data").mkdir()
            (base / "real-data" / "model.gguf").write_bytes(GGUF)
            result = exporter.scan_filesystem([str(base)], 1000, 1000,
                                              exclusions=[str(out)])
            paths = [item["path"] for item in result["model_artifacts"]]
            self.assertEqual(paths, [str(base / "real-data" / "model.gguf")])
            self.assertEqual(result["coverage"][0]["status"], "completed")

    def test_dependency_tree_directories_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for name in ("node_modules", ".venv", "venv", ".npm", ".cargo", ".rustup"):
                sub = base / name / "pkg"
                sub.mkdir(parents=True)
                (sub / "model.gguf").write_bytes(GGUF)
            result = exporter.scan_filesystem([str(base)], 1000, 1000)
            self.assertEqual(result["model_artifacts"], [])

    def test_per_root_cap_reported_incompletely_only_when_entries_remain(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            # Exactly at the cap with no further entries: completed.
            for index in range(3):
                (base / f"m{index}.gguf").write_bytes(GGUF)
            result = exporter.scan_filesystem([str(base)], 1000, 3)
            self.assertEqual(result["coverage"][0]["status"], "completed")
            # One more file than the cap: incomplete.
            base2 = Path(td) / "more"
            base2.mkdir()
            for index in range(4):
                (base2 / f"m{index}.gguf").write_bytes(GGUF)
            result = exporter.scan_filesystem([str(base2)], 1000, 3)
            self.assertEqual(result["coverage"][0]["status"], "incomplete-cap-reached")

    def test_exclude_root_cli_option_prunes_subtree(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            keep = base / "keep"
            keep.mkdir()
            (keep / "model.gguf").write_bytes(GGUF)
            drop = base / "drop"
            drop.mkdir()
            (drop / "model.gguf").write_bytes(GGUF)
            result = exporter.scan_filesystem([str(base)], 1000, 1000,
                                              exclusions=[str(drop)])
            self.assertEqual([item["path"] for item in result["model_artifacts"]],
                             [str(keep / "model.gguf")])


class LegacyDiscoverTests(unittest.TestCase):
    def test_docker_pipe_separated_rows_are_parsed(self):
        out_ps = "ollama|ollama/ollama:latest|Up 2 hours\nother|nginx|Up\n"
        out_images = "ollama/ollama|latest|sha256:abc\nnginx|latest|sha256:def\n"
        def fake_run(args, timeout=8):
            if args[1] == "ps":
                return out_ps, "", 0
            if args[1] == "images":
                return out_images, "", 0
            return "", "", 1
        with mock.patch.object(legacy_discover.shutil, "which",
                               side_effect=lambda n, **k: "/usr/bin/docker" if n == "docker" else None), \
             mock.patch.object(legacy_discover, "run", side_effect=fake_run):
            result = legacy_discover.discover_containers()
        self.assertEqual(len(result["docker_containers"]), 1)
        self.assertEqual(result["docker_containers"][0]["name"], "ollama")
        self.assertEqual(len(result["docker_images"]), 1)
        self.assertEqual(result["docker_images"][0]["repository"], "ollama/ollama")

    def test_ai_regex_matches_localai_and_vllm_tokens(self):
        self.assertRegex("localai", legacy_discover.AI_RE)
        self.assertRegex("vllm", legacy_discover.AI_RE)


class ModelExportExitTests(unittest.TestCase):
    def _inventory(self, path: Path, source: Path):
        path.write_text(json.dumps({
            "filesystem_scan": {"model_artifacts": [{"path": str(source)}]}
        }), encoding="utf-8")

    def test_expected_skips_exit_zero_and_fail_on_skip_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            partial = base / "tiny.onnx"
            partial.write_bytes(b"\x00" * 2048)
            inv = base / "inv.json"
            self._inventory(inv, partial)
            argv = ["export", "--inventory", str(inv), "--output", str(base / "out")]
            self.assertEqual(model_manager.main(argv), 0)
            self.assertEqual(model_manager.main([*argv, "--fail-on-skip"]), 2)

    def test_copy_error_marks_record_error(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "model.gguf"
            source.write_bytes(GGUF)
            inv = base / "inv.json"
            self._inventory(inv, source)
            output = base / "out"
            digest = model_manager.sha256_file(source)
            # Occupy both the plain name and the digest-deduplicated fallback so
            # the copy step itself fails instead of being silently renamed.
            (output / "models" / "model.gguf").mkdir(parents=True)
            (output / "models" / f"model-{digest[:12]}.gguf").mkdir(parents=True)
            manifest = model_manager.export_models(inv, output, 1024 * 1024, False)
            record = manifest["models"][0]
            self.assertEqual(record["export_status"], "error")
            self.assertEqual(manifest["error_count"], 1)
            self.assertEqual(manifest["skipped_count"], 0)


class InstallerLoggingTests(unittest.TestCase):
    def test_early_failures_are_logged(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(installer.shutil, "which", return_value=None):
            result = installer.install_one("fabric", True, None, None, Path(td), 5)
            self.assertEqual(result["status"], "FAIL")
            unknown = installer.install_one("nope", False, None, None, Path(td), 5)
            self.assertEqual(unknown["status"], "FAIL")
            lines = (Path(td) / "installer-log.jsonl").read_text(encoding="utf-8").splitlines()
        reasons = [json.loads(x)["reason"] for x in lines]
        self.assertTrue(any("no-usable-installer" in r for r in reasons))
        self.assertIn("unknown-tool", reasons)

    def test_dry_run_is_not_logged_and_never_executes(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(installer.shutil, "which",
                               side_effect=lambda n, **k: "/usr/bin/npm" if n == "npm" else None), \
             mock.patch.object(installer.subprocess, "run") as run:
            result = installer.install_one("opencode", False, None, None, Path(td), 5)
        run.assert_not_called()
        self.assertEqual(result["status"], "DRY-RUN")
        self.assertFalse((Path(td) / "installer-log.jsonl").exists())


class VersionFlagTests(unittest.TestCase):
    def test_version_flags_exit_zero(self):
        for script in ("ai_unified_export.py", "ai_model_manager.py", "ai_tools_installer.py"):
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script), "--version"],
                check=False, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(proc.stdout.strip())


if __name__ == "__main__":
    unittest.main()
