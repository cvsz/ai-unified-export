import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "ai_model_manager.py"
spec = importlib.util.spec_from_file_location("ai_model_manager", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GGUF_OK = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + b"\x00" * 32  # valid v3 fixed header


class ModelManagerTests(unittest.TestCase):
    def test_valid_gguf_magic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tiny.gguf"
            path.write_bytes(GGUF_OK)
            result = mod.validate_model(path)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["gguf_version"], 3)

    def test_gguf_bad_version_and_truncation_fail(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "v0.gguf"
            bad.write_bytes(b"GGUF" + b"\x00" * 64)
            self.assertEqual(mod.validate_model(bad)["reason"], "unsupported-GGUF-version:0")
            short = Path(td) / "short.gguf"
            short.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 4)
            self.assertEqual(mod.validate_model(short)["status"], "FAIL")
            huge = Path(td) / "huge.gguf"
            huge.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 10**9, 0))
            self.assertEqual(mod.validate_model(huge)["reason"], "implausible-GGUF-counts")

    def test_invalid_gguf_magic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.gguf"
            path.write_bytes(b"NOPE" + b"\x00" * 64)
            self.assertEqual(mod.validate_model(path)["status"], "FAIL")

    def test_safetensors_offsets_validated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "weights.safetensors"
            header = json.dumps({"weight": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
            path.write_bytes(struct.pack("<Q", len(header)) + header + b"12345678")
            result = mod.validate_model(path)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["tensor_count"], 1)

    def test_safetensors_rejects_out_of_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "weights.safetensors"
            header = json.dumps({"weight": {"dtype": "F32", "shape": [2], "data_offsets": [0, 80]}}).encode()
            path.write_bytes(struct.pack("<Q", len(header)) + header + b"12345678")
            self.assertEqual(mod.validate_model(path)["status"], "FAIL")

    def test_rejects_mnt_sources(self):
        with self.assertRaises((ValueError, OSError)):
            mod.native_source(Path("/mnt/c/Windows/not-a-model.gguf"))

    def test_requires_allowlisted_https_host(self):
        with self.assertRaises(ValueError):
            mod.validate_public_https_url("http://huggingface.co/model.gguf", {"huggingface.co"})

    def test_rejects_unallowlisted_host_before_dns(self):
        with self.assertRaises(ValueError):
            mod.validate_public_https_url("https://example.invalid/model.gguf", {"huggingface.co"})

    def test_download_digest_format(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            mod.download_model("https://huggingface.co/model.gguf", Path(td) / "m.gguf",
                               "not-a-digest", 1024, {"huggingface.co"})


    def test_model_export_copies_and_hash_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "source.gguf"
            source.write_bytes(GGUF_OK)
            inventory = base / "inventory.json"
            inventory.write_text(json.dumps({
                "filesystem_scan": {"model_artifacts": [{"path": str(source)}]}
            }), encoding="utf-8")
            output = base / "export"
            result = mod.export_models(inventory, output, 1024 * 1024, False)
            self.assertEqual(result["exported_count"], 1)
            exported = output / "models" / "source.gguf"
            self.assertTrue(exported.is_file())
            self.assertEqual(mod.sha256_file(source), mod.sha256_file(exported))
            self.assertTrue((output / "model-manifest.json").is_file())

    def test_downloader_streams_verifies_and_atomically_publishes(self):
        payload = b"GGUF" + b"\x00" * 128
        expected = hashlib.sha256(payload).hexdigest()

        class FakeResponse:
            headers: ClassVar[dict[str, str]] = {"Content-Length": str(len(payload))}
            def __init__(self):
                self._sent = False
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def geturl(self):
                return "https://huggingface.co/models/test.gguf"
            def read(self, _size):
                if self._sent:
                    return b""
                self._sent = True
                return payload

        class FakeOpener:
            def open(self, _request, timeout):
                self.timeout = timeout
                return FakeResponse()

        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "model.gguf"
            with mock.patch.object(mod, "validate_public_https_url"), \
                 mock.patch.object(mod.urllib.request, "build_opener", return_value=FakeOpener()):
                result = mod.download_model(
                    "https://huggingface.co/models/test.gguf", destination,
                    expected, 1024, {"huggingface.co"}
                )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_name("model.gguf.part").exists())


    def _ollama_store(self, base, blob=b"layer-bytes"):
        digest = hashlib.sha256(blob).hexdigest()
        (base / "blobs").mkdir(parents=True)
        (base / "blobs" / f"sha256-{digest}").write_bytes(blob)
        mf = base / "manifests" / "registry.ollama.ai" / "library" / "tiny" / "latest"
        mf.parent.mkdir(parents=True)
        layer = {"digest": f"sha256:{digest}", "size": len(blob), "mediaType": "x"}
        mf.write_text(json.dumps({"config": layer, "layers": [layer]}))
        return digest

    def test_ollama_validate_pass_and_detect_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "models"
            digest = self._ollama_store(base)
            ok = mod.validate_ollama(base)
            self.assertEqual([r["status"] for r in ok], ["PASS"])
            self.assertEqual(ok[0]["model"], "tiny:latest")
            blob = base / "blobs" / f"sha256-{digest}"
            blob.write_bytes(b"layer-bytez")  # same size, different content
            bad = mod.validate_ollama(base)[0]
            self.assertEqual(bad["status"], "FAIL")
            self.assertTrue(bad["problems"][0].startswith("sha256-mismatch"))
            self.assertEqual(mod.validate_ollama(base, verify_hash=False)[0]["status"], "PASS")
            blob.unlink()
            self.assertTrue(mod.validate_ollama(base)[0]["problems"][0].startswith("missing-blob"))

    def test_ollama_validate_rejects_non_store_and_mnt(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            mod.validate_ollama(Path(td))
        with self.assertRaises((ValueError, OSError)):
            mod.validate_ollama(Path("/mnt/c"))


if __name__ == "__main__":
    unittest.main()
