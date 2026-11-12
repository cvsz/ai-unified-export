import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "ai_tools_installer.py"
spec = importlib.util.spec_from_file_location("ai_tools_installer", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InstallerTests(unittest.TestCase):
    def test_catalog_is_well_formed(self):
        self.assertEqual(mod.check_catalog(), [])

    def test_catalog_rejects_unsafe_argv_and_sudo(self):
        bad = {"id": "x", "name": "x", "category": "c", "cost": "oss-local", "binary": "x",
               "docs": "https://e.example", "methods": [{"manager": "npm", "argv": ["npm", "i", "a; rm -rf /"]}]}
        with mock.patch.object(mod, "CATALOG", [bad]):
            self.assertTrue(any("unsafe argv" in p for p in mod.check_catalog()))

    def test_unknown_tool_fails(self):
        r = mod.install_one("nope", False, None, None, Path("/tmp"), 5)
        self.assertEqual((r["status"], r["reason"]), ("FAIL", "unknown-tool"))

    def test_dry_run_never_executes(self):
        with mock.patch.object(mod.shutil, "which", side_effect=lambda n, **k: "/usr/bin/npm" if n == "npm" else None), \
             mock.patch.object(mod.subprocess, "run") as run:
            r = mod.install_one("opencode", False, None, None, Path("/tmp"), 5)
        run.assert_not_called()
        self.assertEqual(r["status"], "DRY-RUN")
        self.assertEqual(r["command"], ["npm", "install", "-g", "opencode-ai"])

    def test_already_installed_is_skipped(self):
        with mock.patch.object(mod.shutil, "which", return_value="/usr/bin/aider"), \
             mock.patch.object(mod.subprocess, "run") as run:
            r = mod.install_one("aider", True, None, None, Path("/tmp"), 5)
        run.assert_not_called()
        self.assertEqual(r["status"], "SKIPPED")

    def test_apply_uses_argv_without_shell_and_requires_binary_on_path(self):
        calls = {"n": 0}

        def which(name, **_k):
            if name == "pipx":
                return "/usr/bin/pipx"
            if name == "aider":  # absent before install, present after
                return "/home/u/.local/bin/aider" if calls["n"] else None
            return None

        def fake_run(argv, **kw):
            calls["n"] += 1
            self.assertIsInstance(argv, list)
            self.assertNotIn("shell", kw)
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod.shutil, "which", side_effect=which), \
             mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
            r = mod.install_one("aider", True, None, None, Path(td), 5)
            self.assertEqual(r["status"], "PASS")
            self.assertTrue((Path(td) / "installer-log.jsonl").is_file())

    def test_exit_zero_without_binary_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod.shutil, "which", side_effect=lambda n, **k: "/usr/bin/pipx" if n == "pipx" else None), \
             mock.patch.object(mod.subprocess, "run", return_value=mock.Mock(returncode=0)):
            r = mod.install_one("aider", True, None, None, Path(td), 5)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("not-on-PATH", r["reason"])

    def test_missing_package_manager_reports_failure(self):
        with mock.patch.object(mod.shutil, "which", return_value=None):
            r = mod.install_one("fabric", True, None, None, Path("/tmp"), 5)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("no-usable-installer", r["reason"])

    def test_script_requires_pinned_sha256_and_rejects_mismatch(self):
        body = b"#!/bin/sh\necho hi\n"
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod.shutil, "which", return_value=None), \
             mock.patch.object(mod, "fetch_script", return_value=body), \
             mock.patch.object(mod.subprocess, "run") as run:
            no_pin = mod.install_one("ollama", True, None, None, Path(td), 5)
            wrong = mod.install_one("ollama", True, None, "0" * 64, Path(td), 5)
        run.assert_not_called()
        self.assertEqual(no_pin["reason"], "script-sha256-required")
        self.assertEqual(wrong["reason"], "script-sha256-mismatch")
        self.assertEqual(wrong["actual"], hashlib.sha256(body).hexdigest())

    def test_script_with_matching_hash_runs(self):
        body = b"#!/bin/sh\necho hi\n"
        digest = hashlib.sha256(body).hexdigest()
        state = {"ran": False}

        def which(name, **_k):
            return "/usr/local/bin/ollama" if (name == "ollama" and state["ran"]) else None

        def fake_run(argv, **_kw):
            state["ran"] = True
            self.assertEqual(argv[0], "sh")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mod.shutil, "which", side_effect=which), \
             mock.patch.object(mod, "fetch_script", return_value=body), \
             mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
            r = mod.install_one("ollama", True, None, digest, Path(td), 5)
        self.assertEqual(r["status"], "PASS")

    def test_fetch_script_enforces_host_and_https(self):
        m = {"url": "http://ollama.com/install.sh", "host": "ollama.com"}
        with self.assertRaises(ValueError):
            mod.fetch_script(m)
        m = {"url": "https://evil.example/install.sh", "host": "ollama.com"}
        with self.assertRaises(ValueError):
            mod.fetch_script(m)

    def test_cli_list_and_check_catalog(self):
        self.assertEqual(mod.main(["check-catalog"]), 0)
        self.assertEqual(mod.main(["list", "--category", "agent"]), 0)


if __name__ == "__main__":
    unittest.main()
