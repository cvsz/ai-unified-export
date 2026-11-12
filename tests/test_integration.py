"""Integration tests — end-to-end execution of the real scripts with mocked
subprocess/host, verifying output files, exit codes, and end-state contracts.

These tests import the actual script modules and drive their main() functions
with patched I/O, so they catch regressions in the real execution paths
(report writing, checkpoint resume, config loading, export exit codes,
monitor lifecycle) rather than only unit-level helpers.
"""
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

GGUF = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + b"\x00" * 32


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exporter = _load("ai_unified_export_integration", "ai_unified_export.py")
model_manager = _load("ai_model_manager_integration", "ai_model_manager.py")
installer = _load("ai_tools_installer_integration", "ai_tools_installer.py")


class ExporterEndToEndTests(unittest.TestCase):
    """Full main() runs with the host, discovery, and subprocess patched."""

    def _platform(self):
        return {"wsl2": True, "distribution": "Ubuntu 26.04", "kernel": "WSL2 test",
                "python": "3.14.4", "architecture": "x86_64"}

    def _empty_run_result(self, returncode=0, output=""):
        return {"returncode": returncode, "output": output, "truncated": False}

    def _run_main(self, args: list[str]):
        original = sys.argv[:]
        try:
            sys.argv = args
            return exporter.main()
        finally:
            sys.argv = original

    def test_full_quick_run_writes_reports_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            args = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                    "--skip-cli-help", "--output", str(td)]
            with mock.patch.object(exporter, "validate_host", return_value=self._platform()), \
                 mock.patch.object(exporter, "discover_executables", return_value=[
                     {"name": "opencode", "path": "/usr/local/bin/opencode",
                      "source": "native-PATH", "kind": "native-executable"}]), \
                 mock.patch.object(exporter, "package_inventory",
                                   return_value={"dpkg": [{"name": "ollama", "version": "1.0"}],
                                                 "snap": [], "npm_global": [],
                                                 "cargo": [], "python_metadata": []}), \
                 mock.patch.object(exporter, "system_runtime_inventory",
                                   return_value={"processes": [],
                                                 "systemd_services": [],
                                                 "likely_ai_listening_sockets": []}), \
                 mock.patch.object(exporter, "container_inventory",
                                   return_value={"docker_containers": [], "docker_images": [],
                                                 "podman_containers": [], "podman_images": []}), \
                 mock.patch.object(exporter, "scan_filesystem",
                                   return_value={"directories": [], "metadata_candidates": [],
                                                 "model_artifacts": [], "manifests": [],
                                                 "entries_examined": 0, "coverage": [],
                                                 "entry_limit_reached": False}):
                rc = self._run_main(args)
            self.assertEqual(rc, 0)
            inv = json.loads((out / "ai-inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inv["schema_version"], exporter.SCHEMA_VERSION)
            self.assertEqual(inv["profile"], "quick")
            self.assertEqual(len(inv["executables"]), 1)
            self.assertEqual(inv["executables"][0]["name"], "opencode")
            report = (out / "AI-INVENTORY-REPORT.md").read_text(encoding="utf-8")
            self.assertIn("opencode", report)
            self.assertIn("ollama", report)
            checkpoint = out / ".monitor" / "checkpoint.json"
            self.assertTrue(checkpoint.is_file())
            # state.json is only written when a monitor is active; with --monitor off
            # only the checkpoint and reports are produced.

    def test_config_file_supplies_defaults_cli_wins(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            cfg = Path(td) / "config.json"
            cfg.write_text(json.dumps({"max-total-entries": 12345, "profile": "full"}),
                           encoding="utf-8")
            # CLI --profile quick overrides config profile full.
            args = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                    "--skip-cli-help", "--output", str(td),
                    "--config", str(cfg), "--profile", "quick"]
            with mock.patch.object(exporter, "validate_host", return_value=self._platform()), \
                 mock.patch.object(exporter, "discover_executables", return_value=[]), \
                 mock.patch.object(exporter, "package_inventory",
                                   return_value={"dpkg": [], "snap": [], "npm_global": [],
                                                 "cargo": [], "python_metadata": []}), \
                 mock.patch.object(exporter, "system_runtime_inventory",
                                   return_value={"processes": [], "systemd_services": [],
                                                 "likely_ai_listening_sockets": []}), \
                 mock.patch.object(exporter, "container_inventory",
                                   return_value={"docker_containers": [], "docker_images": [],
                                                 "podman_containers": [], "podman_images": []}), \
                 mock.patch.object(exporter, "scan_filesystem",
                                   return_value={"directories": [], "metadata_candidates": [],
                                                 "model_artifacts": [], "manifests": [],
                                                 "entries_examined": 0, "coverage": [],
                                                 "entry_limit_reached": False}):
                rc = self._run_main(args)
            self.assertEqual(rc, 0)
            inv = json.loads((out / "ai-inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(inv["profile"], "quick")
            self.assertEqual(inv["scan_exclusions"], [str(out)])

    def test_config_file_missing_is_error(self):
        args = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                "--skip-cli-help", "--config", "/nonexistent/config.json"]
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(args)
        self.assertEqual(ctx.exception.code, 2)  # parser.error exits 2

    def test_resume_with_mismatched_fingerprint_starts_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            # First run writes a checkpoint.
            args = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                    "--skip-cli-help", "--output", str(td)]
            with mock.patch.object(exporter, "validate_host", return_value=self._platform()), \
                 mock.patch.object(exporter, "discover_executables", return_value=[]), \
                 mock.patch.object(exporter, "package_inventory",
                                   return_value={"dpkg": [], "snap": [], "npm_global": [],
                                                 "cargo": [], "python_metadata": []}), \
                 mock.patch.object(exporter, "system_runtime_inventory",
                                   return_value={"processes": [], "systemd_services": [],
                                                 "likely_ai_listening_sockets": []}), \
                 mock.patch.object(exporter, "container_inventory",
                                   return_value={"docker_containers": [], "docker_images": [],
                                                 "podman_containers": [], "podman_images": []}), \
                 mock.patch.object(exporter, "scan_filesystem",
                                   return_value={"directories": [], "metadata_candidates": [],
                                                 "model_artifacts": [], "manifests": [],
                                                 "entries_examined": 0, "coverage": [],
                                                 "entry_limit_reached": False}):
                self.assertEqual(self._run_main(args), 0)
            # Resume with different profile -> fingerprint mismatch -> fresh run.
            args2 = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                     "--skip-cli-help", "--resume", "--output", str(td), "--profile", "full"]
            with mock.patch.object(exporter, "validate_host", return_value=self._platform()), \
                 mock.patch.object(exporter, "discover_executables", return_value=[]) as discover, \
                 mock.patch.object(exporter, "package_inventory",
                                   return_value={"dpkg": [], "snap": [], "npm_global": [],
                                                 "cargo": [], "python_metadata": []}), \
                 mock.patch.object(exporter, "system_runtime_inventory",
                                   return_value={"processes": [], "systemd_services": [],
                                                 "likely_ai_listening_sockets": []}), \
                 mock.patch.object(exporter, "container_inventory",
                                   return_value={"docker_containers": [], "docker_images": [],
                                                 "podman_containers": [], "podman_images": []}), \
                 mock.patch.object(exporter, "scan_filesystem",
                                   return_value={"directories": [], "metadata_candidates": [],
                                                 "model_artifacts": [], "manifests": [],
                                                 "entries_examined": 0, "coverage": [],
                                                 "entry_limit_reached": False}):
                self.assertEqual(self._run_main(args2), 0)
            # Fresh run: discovery executed again (not skipped from cache).
            self.assertEqual(discover.call_count, 1)

    def test_scope_error_rejects_non_wsl2(self):
        args = [str(SCRIPTS / "ai_unified_export.py"), "--monitor", "off",
                "--skip-cli-help", "--output", "/tmp/integration-scope-check"]
        with mock.patch.object(exporter, "validate_host",
                               side_effect=exporter.ScopeError("not WSL2 Ubuntu")), \
             self.assertRaises(exporter.ScopeError):
            self._run_main(args)


class ModelManagerIntegrationTests(unittest.TestCase):
    def test_export_then_validate_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "model.gguf"
            source.write_bytes(GGUF)
            inventory = base / "inventory.json"
            inventory.write_text(json.dumps({
                "filesystem_scan": {"model_artifacts": [{"path": str(source)}]}
            }), encoding="utf-8")
            output = base / "export"
            result = model_manager.export_models(inventory, output, 1024 * 1024, False)
            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(result["error_count"], 0)
            exported = output / "models" / "model.gguf"
            self.assertTrue(exported.is_file())
            self.assertEqual(model_manager.sha256_file(source),
                             model_manager.sha256_file(exported))
            manifest = json.loads((output / "model-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["exported_count"], 1)
            report = (output / "MODEL-EXPORT-REPORT.md").read_text(encoding="utf-8")
            self.assertIn("PASS", report)

    def test_validate_cli_passes_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.gguf"
            good.write_bytes(GGUF)
            bad = Path(td) / "bad.gguf"
            bad.write_bytes(b"NOPE" + b"\x00" * 64)
            rc_pass = model_manager.main(["validate", str(good)])
            self.assertEqual(rc_pass, 0)
            rc_fail = model_manager.main(["validate", str(bad)])
            self.assertEqual(rc_fail, 2)


class InstallerIntegrationTests(unittest.TestCase):
    def test_check_catalog_and_list(self):
        self.assertEqual(installer.check_catalog(), [])
        rc = installer.main(["check-catalog"])
        self.assertEqual(rc, 0)
        rc = installer.main(["list", "--category", "agent"])
        self.assertEqual(rc, 0)


class MonitorIntegrationTests(unittest.TestCase):
    def test_monitor_lifecycle_start_stop(self):
        from scripts.ai_export_monitor import ExportMonitor
        with tempfile.TemporaryDirectory() as td:
            monitor = ExportMonitor(Path(td), mode="off")
            monitor.stage("filesystem", "running", 50, "Scanning")
            monitor.finish("completed", "Finished")
            state = json.loads((Path(td) / ".monitor/state.json").read_text())
            self.assertEqual(state["status"], "completed")
            events = (Path(td) / ".monitor/events.jsonl").read_text().splitlines()
            self.assertGreaterEqual(len(events), 3)
            monitor.close()


if __name__ == "__main__":
    unittest.main()
