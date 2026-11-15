"""CPU-only consumers of the worker's actual Image validation and receipt code."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/kernel-build-worker.sh"
ARTIFACTS = ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map",
             "Module.symvers", "kernel.release", "modules.tar.gz")


def consumer(marker):
    blocks = re.findall(r"python3 -B - <<'PY'\n(.*?)\nPY", WORKER.read_text(), re.S)
    matches = [block for block in blocks if marker in block]
    if len(matches) != 1:
        raise AssertionError("Expected exactly one worker consumer for " + marker)
    return matches[0]


class KernelWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kernel-worker-cpu-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / "build"
        self.out = self.root / "candidate"
        self.build.mkdir()
        self.out.mkdir()

    def run_consumer(self, marker, **environment):
        env = {**os.environ, "BUILD": str(self.build), "OUT": str(self.out), **environment}
        return subprocess.run([sys.executable, "-B", "-c", consumer(marker)],
                              env=env, capture_output=True, text=True)

    def test_real_source_signature_reuses_only_identical_public_tree_and_toolchain(self):
        binding = {"commit": "public-commit", "archive_sha256": "1" * 64,
                   "ordered_patches": [{"name": "first.patch", "sha256": "2" * 64},
                                       {"name": "second.patch", "sha256": "3" * 64}],
                   "host": {"arch": "x86_64", "distribution": "Fedora", "version": "43"},
                   "target": {"arch": "arm64"}, "toolchain": {"clang": "21.1.8", "rust": "1.99.1"}}
        marker = "source = {name: binding[name]"
        baseline = self.run_consumer(marker, WORKER_BINDING=json.dumps(binding))
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        expected = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(baseline.stdout.strip(), expected)
        metadata = {**binding, "variant": "changed", "config": "changed", "baseline_config_sha256": "4" * 64,
                    "worker_sha256": "5" * 64, "target_sysroot": {"metadata": "changed"}, "input_signature": "6" * 64}
        result = self.run_consumer(marker, WORKER_BINDING=json.dumps(metadata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, baseline.stdout)
        for change in ({"ordered_patches": list(reversed(binding["ordered_patches"]))},
                       {"archive_sha256": "7" * 64}, {"host": {"arch": "aarch64"}},
                       {"toolchain": {"clang": "different"}}):
            with self.subTest(change=change):
                result = self.run_consumer(marker, WORKER_BINDING=json.dumps({**binding, **change}))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(result.stdout, baseline.stdout)

    def test_real_native_sysroot_consumer_binds_explicit_null(self):
        binding = {"host": {"arch": "aarch64"}, "worker_sha256": "1" * 64}
        result = self.run_consumer("sysroot = None", WORKER_BINDING=json.dumps(binding))
        self.assertEqual(result.returncode, 0, result.stderr)
        signature, receipt = result.stdout.splitlines()
        receipt = json.loads(receipt)
        self.assertIsNone(receipt["target_sysroot"])
        expected = hashlib.sha256(json.dumps({**binding, "target_sysroot": None}, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(signature, expected)
        self.assertEqual(receipt["input_signature"], expected)

    def test_real_image_consumer_rejects_wrong_architecture_and_truncation(self):
        image = self.build / "arch/arm64/boot/Image"
        image.parent.mkdir(parents=True)
        header = bytearray(64)
        header[56:60] = b"ARM\x64"
        struct.pack_into("<Q", header, 16, 4096)
        image.write_bytes(header)
        result = self.run_consumer("header = stream.read(64)")
        self.assertEqual(result.returncode, 0, result.stderr)
        for data in (header[:63], bytes(64), header[:56] + b"x86!" + header[60:]):
            with self.subTest(data=data):
                image.write_bytes(data)
                result = self.run_consumer("header = stream.read(64)")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ARM64 Linux Image", result.stderr)
        struct.pack_into("<Q", header, 16, 0)
        image.write_bytes(header)
        self.assertNotEqual(self.run_consumer("header = stream.read(64)").returncode, 0)

    def receipt_fixture(self):
        # Public CPU fixtures, not deployable kernel artifacts or signing material.
        for name in ARTIFACTS:
            (self.out / name).write_bytes(("CPU fixture: " + name + "\n").encode())
        (self.out / ".config").write_text("CONFIG_MODULE_SIG_ALL=y\n")
        certificate = self.build / "certs/signing_key.x509"
        certificate.parent.mkdir()
        certificate.write_bytes(b"public certificate hash fixture\n")
        (self.build / "certs/signing_key.pem").write_bytes(b"must never be exported\n")
        binding = {"ordered_patches": [{"name": "first.patch", "sha256": "1" * 64},
                                       {"name": "second.patch", "sha256": "2" * 64}],
                   "input_signature": "3" * 64, "worker_sha256": "4" * 64}
        return certificate, json.dumps(binding)

    def test_real_receipt_consumer_hashes_all_raw_artifacts_without_exporting_key(self):
        certificate, binding = self.receipt_fixture()
        result = self.run_consumer("receipt = json.loads", WORKER_BINDING=binding)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.out / "compile-receipt.json").read_text())
        self.assertEqual(set(receipt["artifacts"]), set(ARTIFACTS))
        for name, record in receipt["artifacts"].items():
            data = (self.out / name).read_bytes()
            self.assertEqual(record, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        self.assertEqual(list(receipt["patches"]), ["first.patch", "second.patch"])
        self.assertEqual(receipt["signing"]["certificate"]["sha256"],
                         hashlib.sha256(certificate.read_bytes()).hexdigest())
        self.assertFalse(receipt["signing"]["private_key_exported"])
        self.assertEqual({path.name for path in self.out.iterdir()}, {*ARTIFACTS, "compile-receipt.json"})

    def test_real_receipt_consumer_refuses_missing_signing_certificate(self):
        certificate, binding = self.receipt_fixture()
        certificate.unlink()
        result = self.run_consumer("receipt = json.loads", WORKER_BINDING=binding)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no generated signing certificate", result.stderr)
        self.assertFalse((self.out / "compile-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
