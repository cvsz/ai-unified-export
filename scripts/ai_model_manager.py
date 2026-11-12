#!/usr/bin/env python3
"""Safe local AI model export, structural validation, and checksum-pinned HTTPS download.

Never deserializes model weights or executes downloaded content. Downloads require
an explicit SHA-256 digest and an HTTPS host allowlist.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MODEL_SUFFIXES = {
    ".gguf", ".safetensors", ".onnx", ".tflite", ".pt", ".pth",
    ".ggml", ".mlmodel", ".engine", ".plan", ".model",
}
DEFAULT_HOSTS = {
    "huggingface.co", "cdn-lfs.huggingface.co",
    "cas-bridge.xethub.hf.co", "cdn-lfs-us-1.huggingface.co",
}
CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def native_source(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    value = str(resolved)
    if not resolved.is_file() or not resolved.is_absolute():
        raise ValueError(f"Not a regular model file: {path}")
    if value == "/mnt" or value.startswith("/mnt/") or any(
        value == root or value.startswith(root + "/")
        for root in ("/proc", "/sys", "/dev", "/run")
    ):
        raise ValueError(f"Model source is outside the native WSL2 filesystem: {path}")
    if resolved.suffix.lower() not in MODEL_SUFFIXES:
        raise ValueError(f"Unsupported model extension: {path.suffix}")
    return resolved


def _read_safetensors(path: Path, size: int) -> dict[str, Any]:
    with path.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            return {"status": "FAIL", "level": "header", "reason": "truncated-length"}
        header_size = struct.unpack("<Q", prefix)[0]
        if header_size < 2 or header_size > min(size - 8, 100_000_000):
            return {"status": "FAIL", "level": "header", "reason": "invalid-header-length"}
        raw = stream.read(header_size)
    try:
        header = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "FAIL", "level": "header", "reason": "invalid-header-json"}
    if not isinstance(header, dict):
        return {"status": "FAIL", "level": "header", "reason": "header-not-object"}
    data_size = size - 8 - header_size
    tensor_count = 0
    for name, item in header.items():
        if name == "__metadata__":
            if not isinstance(item, dict):
                return {"status": "FAIL", "level": "header", "reason": "invalid-metadata"}
            continue
        if not isinstance(item, dict):
            return {"status": "FAIL", "level": "header", "reason": f"invalid-tensor:{name}"}
        offsets = item.get("data_offsets")
        shape = item.get("shape")
        dtype = item.get("dtype")
        if (not isinstance(offsets, list) or len(offsets) != 2
                or not all(isinstance(x, int) for x in offsets)
                or offsets[0] < 0 or offsets[1] < offsets[0]
                or offsets[1] > data_size
                or not isinstance(shape, list)
                or not all(isinstance(x, int) and x >= 0 for x in shape)
                or not isinstance(dtype, str)):
            return {"status": "FAIL", "level": "header", "reason": f"invalid-tensor-layout:{name}"}
        tensor_count += 1
    if tensor_count == 0:
        return {"status": "FAIL", "level": "header", "reason": "no-tensors"}
    return {"status": "PASS", "level": "header-structure", "reason": "safetensors-header-and-offsets-valid",
            "tensor_count": tensor_count, "payload_bytes": data_size,
            "note": "Tensor payload values are not numerically decoded."}


def _read_gguf(head: bytes, size: int) -> dict[str, Any]:
    """Check the fixed GGUF header: magic, version, plausible counts, minimum size."""
    if head[:4] != b"GGUF":
        return {"status": "FAIL", "level": "magic", "reason": "invalid-GGUF-magic"}
    if len(head) < 8:
        return {"status": "FAIL", "level": "header", "reason": "truncated-GGUF-header"}
    version = struct.unpack("<I", head[4:8])[0]
    if version not in (1, 2, 3):
        return {"status": "FAIL", "level": "header", "reason": f"unsupported-GGUF-version:{version}"}
    if version == 1:  # v1 used 32-bit counts; v2/v3 use 64-bit counts
        fixed, fmt = 16, "<II"
    else:
        fixed, fmt = 24, "<QQ"
    if len(head) < fixed or size < fixed:
        return {"status": "FAIL", "level": "header", "reason": "truncated-GGUF-header"}
    tensors, kvs = struct.unpack(fmt, head[8:fixed])
    if tensors > 1_000_000 or kvs > 100_000:
        return {"status": "FAIL", "level": "header", "reason": "implausible-GGUF-counts"}
    return {"status": "PASS", "level": "header", "reason": "GGUF-header-valid",
            "gguf_version": version, "tensor_count": tensors, "kv_count": kvs,
            "note": "Magic, version and counts verified; tensor data is not decoded."}


def validate_model(path: Path, enforce_native: bool = False) -> dict[str, Any]:
    try:
        source = native_source(path) if enforce_native else path.expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in MODEL_SUFFIXES:
            raise ValueError("Not a supported regular model file.")
        size = source.stat().st_size
        suffix = source.suffix.lower()
        with source.open("rb") as stream:
            head = stream.read(32)
        if size <= 0:
            result = {"status": "FAIL", "level": "header", "reason": "empty-file"}
        elif suffix == ".gguf":
            result = _read_gguf(head, size)
        elif suffix == ".safetensors":
            result = _read_safetensors(source, size)
        elif suffix == ".tflite":
            result = ({"status": "PASS", "level": "magic", "reason": "TFL3-magic-valid"}
                      if len(head) >= 8 and head[4:8] == b"TFL3" else
                      {"status": "FAIL", "level": "magic", "reason": "invalid-TFL3-magic"})
        elif suffix in {".pt", ".pth"}:
            if size < 50 * 1024 * 1024:
                result = {"status": "FAIL", "level": "candidate", "reason": "below-minimum-pytorch-size"}
            elif head[:2] == b"PK":
                try:
                    with zipfile.ZipFile(source) as archive:
                        bad = archive.testzip()
                    result = ({"status": "PASS", "level": "zip-crc", "reason": "pytorch-zip-container-crc-valid"}
                              if bad is None else
                              {"status": "FAIL", "level": "zip-crc", "reason": f"corrupt-zip-member:{bad}"})
                except (OSError, zipfile.BadZipFile):
                    result = {"status": "FAIL", "level": "zip-container", "reason": "invalid-pytorch-zip"}
            elif head[:1] == b"\x80":
                result = {"status": "PARTIAL", "level": "signature", "reason": "pickle-based-pytorch-signature",
                          "note": "Not deserialized; full integrity cannot be established safely."}
            else:
                result = {"status": "FAIL", "level": "signature", "reason": "unknown-pytorch-container"}
        elif suffix == ".onnx":
            result = {"status": "PARTIAL", "level": "candidate", "reason": "onnx-extension-and-size-only",
                      "note": "Install onnx separately for protobuf/checker validation; model is not loaded."}
        elif suffix in {".ggml", ".mlmodel", ".engine", ".plan", ".model"}:
            result = {"status": "PARTIAL", "level": "candidate", "reason": "extension-and-size-only",
                      "note": "Format-specific runtime validation is not performed."}
        else:
            result = {"status": "FAIL", "level": "extension", "reason": "unsupported-format"}
        return {"path": str(source), "format": suffix.lstrip("."), "size_bytes": size, **result}
    except (OSError, ValueError) as exc:
        return {"path": str(path), "status": "FAIL", "level": "access", "reason": str(exc)}


def default_ollama_dirs() -> list[Path]:
    return [Path.home() / ".ollama" / "models", Path("/usr/share/ollama/.ollama/models"),
            Path("/var/lib/ollama/models")]


def validate_ollama(models_dir: Path, verify_hash: bool = True) -> list[dict[str, Any]]:
    """Validate every Ollama manifest: each layer blob must exist, match its size and,
    unless verify_hash is False, hash to the digest embedded in the blob filename."""
    base = models_dir.expanduser().resolve(strict=True)
    value = str(base)
    if value == "/mnt" or value.startswith("/mnt/"):
        raise ValueError(f"Ollama store is outside the native WSL2 filesystem: {models_dir}")
    manifests, blobs = base / "manifests", base / "blobs"
    if not manifests.is_dir() or not blobs.is_dir():
        raise ValueError(f"Not an Ollama models directory (need manifests/ and blobs/): {base}")
    results = []
    for mf in sorted(p for p in manifests.rglob("*") if p.is_file()):
        parts = mf.relative_to(manifests).parts
        if len(parts) < 3:
            continue
        name = "/".join(parts[1:-1]).removeprefix("library/") + ":" + parts[-1]
        rec: dict[str, Any] = {"model": name, "manifest": str(mf), "layers": 0, "problems": []}
        try:
            doc = json.loads(mf.read_text(encoding="utf-8"))
            layers = [x for x in [doc.get("config"), *doc.get("layers", [])] if isinstance(x, dict)]
        except (OSError, ValueError) as exc:
            rec.update(status="FAIL", problems=[f"manifest-unreadable:{exc}"])
            results.append(rec)
            continue
        for layer in layers:
            digest, size = layer.get("digest", ""), layer.get("size")
            algo, _, hexd = str(digest).partition(":")
            if algo != "sha256" or len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
                rec["problems"].append(f"bad-digest:{digest}")
                continue
            rec["layers"] += 1
            blob = blobs / f"sha256-{hexd}"
            if not blob.is_file():
                rec["problems"].append(f"missing-blob:{hexd[:12]}")
            elif isinstance(size, int) and blob.stat().st_size != size:
                rec["problems"].append(f"size-mismatch:{hexd[:12]}")
            elif verify_hash and sha256_file(blob) != hexd:
                rec["problems"].append(f"sha256-mismatch:{hexd[:12]}")
        rec["status"] = "FAIL" if rec["problems"] or not rec["layers"] else "PASS"
        rec["level"] = "sha256" if verify_hash else "size"
        results.append(rec)
    return results


def validate_public_https_url(url: str, allowed_hosts: set[str]) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Downloads require an explicit HTTPS URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL userinfo and fragments are not allowed.")
    host = parsed.hostname.rstrip(".").lower()
    if host not in allowed_hosts:
        raise ValueError(f"Host {host!r} is not allowlisted; add it explicitly with --allow-host.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"DNS resolution failed for {host}: {exc}") from exc
    if not addresses:
        raise ValueError("Host did not resolve to an address.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"Refusing non-public destination address: {ip}")
    return parsed


class AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_https_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_model(url: str, destination: Path, expected_sha256: str,
                   max_bytes: int, allowed_hosts: set[str]) -> dict[str, Any]:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256):
        raise ValueError("--sha256 must be a 64-character hexadecimal digest.")
    if max_bytes < 1:
        raise ValueError("--max-bytes must be positive.")
    validate_public_https_url(url, allowed_hosts)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Destination already exists; refusing overwrite: {destination}")
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        raise FileExistsError(f"Partial file already exists; inspect or remove it explicitly: {partial}")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        AllowlistedRedirectHandler(allowed_hosts),
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ai-model-manager/1.0", "Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    received = 0
    try:
        with opener.open(request, timeout=30) as response, partial.open("xb") as output:
            final_url = response.geturl()
            validate_public_https_url(final_url, allowed_hosts)
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ValueError(f"Content-Length exceeds --max-bytes ({max_bytes}).")
                except ValueError as exc:
                    if "exceeds" in str(exc):
                        raise
                    raise ValueError("Invalid Content-Length response header.") from exc
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError(f"Download exceeded --max-bytes ({max_bytes}).")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256.lower()}, received {actual}")
        os.replace(partial, destination)
        return {"path": str(destination), "size_bytes": received, "sha256": actual,
                "source_host": urllib.parse.urlsplit(final_url).hostname, "status": "PASS"}
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def safe_export_name(path: Path) -> str:
    # Keep a readable basename but prevent path traversal and collisions.
    basename = PurePosixPath(path.name).name
    return basename if basename not in {"", ".", ".."} else "model.bin"


def export_models(inventory_path: Path, output: Path, max_total_bytes: int,
                  include_partial: bool) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict) or not isinstance(inventory.get("filesystem_scan"), dict):
        # ValueError is this function's documented error type for bad documents.
        raise ValueError("Input is not a supported AI inventory JSON document.")  # noqa: TRY004
    entries = inventory["filesystem_scan"].get("model_artifacts", [])
    if not isinstance(entries, list):
        # ValueError is this function's documented error type; TypeError would break callers.
        raise ValueError("Inventory model_artifacts must be a list.")  # noqa: TRY004
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    model_dir = output / "models"
    model_dir.mkdir(exist_ok=True)
    records, used, total = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        source = Path(entry["resolved_path"] if isinstance(entry.get("resolved_path"), str) else entry["path"])
        try:
            safe_source = native_source(source)
        except (OSError, ValueError) as exc:
            records.append({"source": str(source), "export_status": "skipped",
                            "validation": {"status": "FAIL", "reason": str(exc)}})
            continue
        validation = validate_model(safe_source)
        status = validation["status"]
        record = {"source": str(safe_source), "validation": validation, "export_status": "skipped"}
        if status != "PASS" and not (include_partial and status == "PARTIAL"):
            record["reason"] = "Only PASS models are exported by default; use --include-partial to include PARTIAL candidates."
            records.append(record)
            continue
        try:
            size = safe_source.stat().st_size
            if total + size > max_total_bytes:
                record["reason"] = "total-export-byte-budget-reached"
                records.append(record)
                continue
            digest = sha256_file(safe_source)
            base = safe_export_name(safe_source)
            name = base
            if name in used or (model_dir / name).exists():
                name = f"{safe_source.stem}-{digest[:12]}{safe_source.suffix.lower()}"
            used.add(name)
            target = model_dir / name
            shutil.copyfile(safe_source, target)
            copied_hash = sha256_file(target)
            if copied_hash != digest:
                target.unlink(missing_ok=True)
                raise OSError("Post-copy SHA-256 verification failed.")
            total += size
            record.update({"export_status": "PASS", "export_path": str(target),
                           "size_bytes": size, "sha256": digest})
        except (OSError, ValueError) as exc:
            # Copy or verification failures are errors, not expected skips, so
            # they can drive the process exit code independently.
            record["export_status"] = "error"
            record["reason"] = str(exc)
        records.append(record)
    manifest = {"schema_version": 1, "generated_at_unix": int(time.time()),
                "source_inventory": str(inventory_path.resolve()),
                "models": records, "exported_count": sum(x["export_status"] == "PASS" for x in records),
                "skipped_count": sum(x["export_status"] == "skipped" for x in records),
                "error_count": sum(x["export_status"] == "error" for x in records),
                "skipped_or_failed_count": sum(x["export_status"] != "PASS" for x in records),
                "exported_bytes": total, "max_total_bytes": max_total_bytes}
    temp = output / "model-manifest.json.tmp"
    temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, output / "model-manifest.json")
    report = [
        "# AI Model Export Report", "",
        f"- Exported: {manifest['exported_count']}",
        f"- Skipped (expected): {manifest['skipped_count']}",
        f"- Errors: {manifest['error_count']}",
        f"- Bytes copied: {total}",
        f"- Total byte budget: {max_total_bytes}", "",
        "| Source | Status | SHA-256 |",
        "|---|---|---|",
    ]
    report.extend(
        f"| `{item['source']}` | {item['export_status']} ({item['validation']['status']}) | `{item.get('sha256', '')}` |"
        for item in records
    )
    (output / "MODEL-EXPORT-REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe AI model export, validation, and checksum-pinned downloader")
    parser.add_argument("--version", action="version", version="%(prog)s 1.1")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Copy validated models from an AI inventory")
    export.add_argument("--inventory", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--max-total-bytes", type=int, default=100 * 1024**3)
    export.add_argument("--include-partial", action="store_true")
    export.add_argument("--fail-on-skip", action="store_true",
                        help="Exit 2 when any candidate is skipped (non-PASS or byte budget)")
    validate = sub.add_parser("validate", help="Structurally validate model files without loading weights")
    validate.add_argument("paths", nargs="+", type=Path)
    ollama = sub.add_parser("ollama-validate", help="Verify Ollama manifests and blobs (size and SHA-256)")
    ollama.add_argument("--models-dir", type=Path, action="append", default=[],
                        help="Ollama models directory (repeatable); defaults to common native locations")
    ollama.add_argument("--skip-hash", action="store_true", help="Check existence and size only")
    download = sub.add_parser("download", help="Download one explicitly supplied public HTTPS model URL")
    download.add_argument("--url", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--sha256", required=True)
    download.add_argument("--max-bytes", type=int, default=20 * 1024**3)
    download.add_argument("--allow-host", action="append", default=[], help="Additional exact HTTPS hostname allowlist entry; repeatable")
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            results = [validate_model(path) for path in args.paths]
            print(json.dumps(results, indent=2))
            return 0 if all(x["status"] == "PASS" for x in results) else 2
        if args.command == "ollama-validate":
            dirs = args.models_dir or [d for d in default_ollama_dirs() if d.is_dir()]
            if not dirs:
                parser.error("no Ollama models directory found; pass --models-dir")
            results = [r for d in dirs for r in validate_ollama(d, not args.skip_hash)]
            print(json.dumps(results, indent=2))
            return 0 if results and all(x["status"] == "PASS" for x in results) else 2
        if args.command == "export":
            if args.max_total_bytes < 1:
                parser.error("--max-total-bytes must be positive")
            result = export_models(args.inventory, args.output, args.max_total_bytes, args.include_partial)
            print(json.dumps(result, indent=2))
            if result["error_count"]:
                return 2
            return 2 if args.fail_on_skip and result["skipped_count"] else 0
        hosts = set(DEFAULT_HOSTS)
        for host in args.allow_host:
            normalized = host.strip().lower().rstrip(".")
            if not normalized or "/" in normalized or ":" in normalized:
                parser.error(f"Invalid --allow-host: {host!r}")
            hosts.add(normalized)
        result = download_model(args.url, args.output, args.sha256, args.max_bytes, hosts)
        print(json.dumps(result, indent=2))
        # Validate downloaded artifact. A checksum proves transport integrity relative
        # to the operator-supplied digest; it does not establish publisher provenance.
        validation = validate_model(args.output, enforce_native=False)
        print(json.dumps({"post_download_validation": validation}, indent=2))
        return 0 if validation["status"] == "PASS" else 2
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
