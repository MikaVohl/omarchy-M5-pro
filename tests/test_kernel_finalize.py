"""Exercise the finalizer CLI on isolated returned artifacts, without hardware access."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/finalize-kernel-candidate.py"
VARIANT = "bringup-ane-start"
RELEASE = "7.3.0-j716s-8ab10100a4c1-bringup-ane-start"
COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
ARCHIVE_SHA = "06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2"
CONFIG_SHA = "e7ff6452a937de895bf351f8539636cbf98998493835b80113da5d5ca4ecba14"
ARTIFACTS = ("Image", "Image.gz", "t6050-j716s.dtb", ".config", "System.map",
             "Module.symvers", "kernel.release", "modules.tar.gz")
METADATA = ("build-manifest.json", "build-provenance.json", "SHA256SUMS")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def byte_record(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def image_bytes(release=RELEASE, machine=b"ARM\x64", text_offset=0x80000, image_size=4096):
    image = bytearray(4096)
    struct.pack_into("<QQ", image, 8, text_offset, image_size)
    image[0x38:0x3c] = machine
    banner = ("Linux version " + release + " (codex@j716s-kernel-build) #1\0").encode()
    image[128:128 + len(banner)] = banner
    return bytes(image)


def module_bytes(machine=183, release=RELEASE, signed=True):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", header, 16, 1, machine, 1)
    module = bytes(header) + b"\0vermagic=" + release.encode() + b" SMP mod_unload aarch64\0"
    if signed:
        signature = b"isolated PKCS#7 trailer test bytes"
        module += signature + b"\0\0\x02\0\0\0\0\0" + struct.pack(">I", len(signature))
        module += b"~Module signature appended~\n"
    return module


def cross_sysroot():
    flags = "--sysroot=/opt/arm64-sysroot --gcc-toolchain=/opt/arm64-sysroot/usr -fuse-ld=lld"
    packages = []
    for name, version, release, arch in (("gcc", "15.2.1", "3.fc43", "aarch64"),
                                         ("glibc-devel", "2.42", "4.fc43", "aarch64"),
                                         ("libgcc", "15.2.1", "3.fc43", "aarch64"),
                                         ("tzdata", "2026b", "1.fc42", "noarch")):
        packages.append({"name": name, "epoch": 0, "version": version, "release": release,
                         "arch": arch, **byte_record((name + " signed RPM fixture").encode()),
                         "signature_verified": True})
    return {"path": "/opt/arm64-sysroot", "distribution": "Fedora", "version": "43",
            "arch": "aarch64", "packages": packages, "usercflags": flags,
            "userldflags": flags, "signed_rpms": True}


class FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kernel-finalizer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "candidate"
        self.output.mkdir()
        self.baseline = self.root / "baseline.dtb"
        fixtures = load("finalizer_dtb_fixture", ROOT / "tests/test_sep_probe_dtb.py")
        self.fixtures = fixtures
        self.baseline.write_bytes(fixtures.fixture_dtb(disabled=(0x2a2280000, 0x2a2f00000, 0x2a2f80000)))
        self.raw_dtb = fixtures.fixture_dtb()
        raw_image = image_bytes()
        payloads = {"Image": raw_image, "Image.gz": gzip.compress(raw_image, mtime=0),
                    "t6050-j716s.dtb": self.raw_dtb,
                    ".config": (ROOT / "outputs/kernel-bringup-candidate/.config").read_bytes(),
                    "System.map": b"0000000000000000 T _text\n",
                    "Module.symvers": b"0x01234567 symbol kernel EXPORT_SYMBOL\n",
                    "kernel.release": (RELEASE + "\n").encode()}
        for name, data in payloads.items():
            if name != "Image":  # A returned remote candidate intentionally omits raw Image.
                (self.output / name).write_bytes(data)
        self.module_payload = module_bytes()
        self.write_archive()
        pins = load("finalizer_runtime_contract", ROOT / "outputs/persistent-install/candidate_boot.py").VARIANTS[VARIANT]["patches"]
        self.receipt = {
            "schema": 1, "commit": COMMIT, "variant": VARIANT,
            "qualification": "unqualified-experimental", "kernel_release": RELEASE,
            "artifacts": {name: byte_record(data) for name, data in payloads.items()},
            "patches": dict(pins),
            "ordered_patches": [{"name": name, "sha256": digest} for name, digest in pins.items()],
            "archive_sha256": ARCHIVE_SHA, "baseline_config_sha256": CONFIG_SHA,
            "config": "outputs/kernel-bringup-candidate/.config",
            "host": {"arch": "x86_64", "distribution": "Fedora", "version": "43"},
            "target": {"arch": "arm64"}, "toolchain": {"clang": "21.1.8", "rust": "1.99.1"},
            "target_sysroot": cross_sysroot(),
            "worker_sha256": byte_record((ROOT / "scripts/kernel-build-worker.sh").read_bytes())["sha256"],
            "signing": {"private_key_exported": False, "certificate": byte_record(b"generated certificate")},
            "build_identity": {"user": "codex", "host": "j716s-kernel-build",
                               "timestamp": "Thu Nov 12 00:00:00 UTC 2026", "version": 1},
        }
        self.update_artifact("modules.tar.gz")
        self.bind_inputs()
        self.receipt_path = self.output / "compile-receipt.json"

    def write_archive(self, release=RELEASE, extra=None, module=None):
        prefix = "lib/modules/" + release
        with tarfile.open(self.output / "modules.tar.gz", "w:gz") as archive:
            for name in ("lib", "lib/modules", prefix, prefix + "/kernel"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name, payload in ((prefix + "/modules.dep", b"kernel/test.ko:\n"),
                                  (prefix + "/kernel/test.ko", self.module_payload if module is None else module)):
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            if extra is not None:
                archive.addfile(extra, io.BytesIO(b"bad") if extra.isfile() else None)

    def update_artifact(self, name):
        self.receipt["artifacts"][name] = byte_record((self.output / name).read_bytes())

    def bind_inputs(self):
        keys = ("commit", "variant", "kernel_release", "archive_sha256", "config",
                "baseline_config_sha256", "ordered_patches", "worker_sha256", "host", "target", "toolchain", "target_sysroot")
        binding = {key: self.receipt[key] for key in keys}
        self.receipt["input_signature"] = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def invoke(self, arch="x86_64"):
        self.receipt_path.write_text(json.dumps(self.receipt))
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--variant", VARIANT,
                               "--output", str(self.output), "--baseline-dtb", str(self.baseline),
                               "--worker-receipt", str(self.receipt_path), "--builder-arch", arch],
                              text=True, capture_output=True, check=False)

    def refuses(self, message, arch="x86_64", image_present=False):
        before = {path.name: path.read_bytes() for path in self.output.iterdir() if path.is_file()}
        baseline_before = self.baseline.read_bytes()
        result = self.invoke(arch)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(any((self.output / name).exists() for name in METADATA))
        self.assertEqual((self.output / "Image").exists(), image_present)
        for name, data in before.items():
            if name != "compile-receipt.json":
                self.assertEqual((self.output / name).read_bytes(), data)
        self.assertEqual(self.baseline.read_bytes(), baseline_before)
        self.assertFalse(any(path.name.startswith(".kernel-finalize-") for path in self.output.iterdir()))

    @unittest.skipUnless(shutil.which("gcc") and (ROOT / "work/sources/aura/src/libfdt").is_dir(),
                         "requires prepared public Aura libfdt source and native gcc")
    def test_returned_cross_build_is_locally_finalized_and_hash_closed(self):
        baseline_before = self.baseline.read_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["artifacts"], 8)
        self.assertEqual((self.output / "Image").read_bytes(), image_bytes())
        self.assertEqual(self.baseline.read_bytes(), baseline_before)
        manifest = json.loads((self.output / METADATA[0]).read_text())
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["commit"], COMMIT)
        self.assertEqual(manifest["variant"], VARIANT)
        self.assertEqual(manifest["kernel_release"], RELEASE)
        self.assertEqual(manifest["qualification"], "unqualified-experimental")
        self.assertEqual(set(manifest["artifacts"]), set(ARTIFACTS))
        for name in ARTIFACTS:
            self.assertEqual(manifest["artifacts"][name], byte_record((self.output / name).read_bytes()))
        provenance = json.loads((self.output / METADATA[1]).read_text())
        self.assertEqual(provenance["host"]["arch"], "x86_64")
        self.assertEqual(provenance["target"], {"arch": "arm64"})
        self.assertEqual(provenance["toolchain"]["guest"], "Fedora 43 x86_64")
        self.assertEqual(provenance["toolchain"]["clang"], "21.1.8")
        self.assertEqual(provenance["target_sysroot"], self.receipt["target_sysroot"])
        self.assertEqual(provenance["compile_receipt"], byte_record(self.receipt_path.read_bytes()))
        self.assertEqual(provenance["signing"], self.receipt["signing"])
        self.assertEqual(provenance["modules"]["kernel/test.ko"]["sha256"], byte_record(self.module_payload)["sha256"])
        self.assertEqual(provenance["modules"]["kernel/test.ko"]["signature"]["format"], "PKCS#7")
        transform = provenance["dtb_transform"]
        self.assertEqual(transform["input_sha256"], byte_record(self.raw_dtb)["sha256"])
        self.assertEqual(transform["baseline_sha256"], byte_record(baseline_before)["sha256"])
        self.assertEqual(transform["output_sha256"], manifest["artifacts"]["t6050-j716s.dtb"]["sha256"])
        self.assertTrue(transform["exact_node_names"])
        self.assertNotEqual(transform["input_sha256"], transform["output_sha256"])
        before_nodes = self.fixtures.helper.dtb.dtb_nodes(self.raw_dtb)
        baseline_nodes = self.fixtures.helper.dtb.dtb_nodes(baseline_before)
        after_nodes = self.fixtures.helper.dtb.dtb_nodes((self.output / "t6050-j716s.dtb").read_bytes())
        expected_nodes = {name: dict(properties) for name, properties in before_nodes.items()}
        for name in expected_nodes:
            if name.startswith("/soc/"):
                expected_nodes[name]["status"] = baseline_nodes[name]["status"]
        self.assertEqual(after_nodes, expected_nodes)
        self.assertEqual(provenance["safety"], {
            "persistent_selection_allowed": True, "sensor_enabled": True,
            "enrollment_allowed": True, "matching_allowed": True,
            "required_module_options": {"xart_writes": 1, "provision_keybag": 1,
                                        "trusted_keys": 0, "filevault_api": 0, "xart_guard": 1}})
        sums = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 10)
        for line in sums:
            digest, name = line.split("  ")
            self.assertEqual(digest, byte_record((self.output / name).read_bytes())["sha256"])
        self.assertEqual(byte_record((self.output / "modules.tar.gz").read_bytes()),
                         self.receipt["artifacts"]["modules.tar.gz"])

    @unittest.skipUnless(shutil.which("gcc") and (ROOT / "work/sources/aura/src/libfdt").is_dir(),
                         "requires prepared public Aura libfdt source and native gcc")
    def test_native_build_with_raw_image_preserves_original_image(self):
        self.receipt["host"]["arch"] = "aarch64"
        self.receipt["target_sysroot"] = None
        self.bind_inputs()
        (self.output / "Image").write_bytes(image_bytes())
        result = self.invoke("aarch64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "Image").read_bytes(), image_bytes())
        provenance = json.loads((self.output / "build-provenance.json").read_text())
        self.assertEqual(provenance["toolchain"]["guest"], "Fedora 43 aarch64")
        self.assertIsNone(provenance["target_sysroot"])

    def test_wrong_builder_architecture_refuses(self):
        self.refuses("host/target/toolchain", arch="aarch64")

    def test_wrong_target_architecture_refuses(self):
        self.receipt["target"]["arch"] = "x86_64"
        self.bind_inputs()
        self.refuses("host/target/toolchain")

    def test_cross_sysroot_architecture_refuses(self):
        self.receipt["target_sysroot"]["arch"] = "x86_64"
        self.bind_inputs()
        self.refuses("target sysroot identity/architecture differs")

    def test_cross_sysroot_package_architecture_refuses(self):
        self.receipt["target_sysroot"]["packages"][0]["arch"] = "x86_64"
        self.bind_inputs()
        self.refuses("target sysroot package architecture differs")

    def test_cross_sysroot_unsafe_compiler_and_linker_flags_refuse(self):
        for key in ("usercflags", "userldflags"):
            with self.subTest(key=key):
                original = self.receipt["target_sysroot"][key]
                self.receipt["target_sysroot"][key] += " -include /tmp/unsafe.h"
                self.bind_inputs()
                self.refuses("target sysroot compiler flags differ")
                self.receipt["target_sysroot"][key] = original
                self.bind_inputs()

    def test_cross_sysroot_missing_package_identity_refuses(self):
        del self.receipt["target_sysroot"]["packages"][0]["version"]
        self.bind_inputs()
        self.refuses("target sysroot package identity is missing or invalid")

    def test_cross_sysroot_missing_required_aarch64_package_refuses(self):
        self.receipt["target_sysroot"]["packages"].pop(0)
        self.bind_inputs()
        self.refuses("required AArch64 package identity is missing")

    def test_cross_sysroot_unsigned_rpm_and_invalid_epoch_refuse(self):
        package = self.receipt["target_sysroot"]["packages"][0]
        package["signature_verified"] = False
        self.bind_inputs()
        self.refuses("target sysroot package signature is unverified")
        package["signature_verified"] = True
        package["epoch"] = True
        self.bind_inputs()
        self.refuses("target sysroot package epoch identity differs")

    def test_cross_builder_missing_sysroot_refuses(self):
        self.receipt["target_sysroot"] = None
        self.bind_inputs()
        self.refuses("target sysroot schema differs")

    def test_native_builder_must_not_use_cross_sysroot(self):
        self.receipt["host"]["arch"] = "aarch64"
        self.bind_inputs()
        self.refuses("native builder must not use a target sysroot", arch="aarch64")

    def test_target_sysroot_is_bound_into_input_signature(self):
        self.receipt["target_sysroot"]["packages"][0]["sha256"] = "0" * 64
        self.refuses("compile input signature differs")

    def test_wrong_module_architecture_refuses_before_dtb_transform(self):
        self.write_archive(module=module_bytes(machine=62))
        self.update_artifact("modules.tar.gz")
        self.refuses("not AArch64 ELF")

    def test_wrong_module_archive_release_refuses(self):
        self.write_archive(release="7.3.0-wrong")
        self.update_artifact("modules.tar.gz")
        self.refuses("wrong module release")

    def test_wrong_module_vermagic_refuses(self):
        self.write_archive(module=module_bytes(release=RELEASE + "-wrong"))
        self.update_artifact("modules.tar.gz")
        self.refuses("vermagic release differs")

    def test_unsigned_module_refuses(self):
        self.write_archive(module=module_bytes(signed=False))
        self.update_artifact("modules.tar.gz")
        self.refuses("signature is missing")

    def test_traversal_absolute_backslash_and_ambiguous_paths_refuse(self):
        for name in ("lib/modules/" + RELEASE + "/../../escape", "/tmp/escape",
                     "lib/modules/" + RELEASE + "/kernel\\escape", "lib//modules/" + RELEASE + "/escape",
                     "lib/modules/" + RELEASE + "/./escape"):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.size = 3
                self.write_archive(extra=member)
                self.update_artifact("modules.tar.gz")
                self.refuses("unsafe module archive path")
        self.assertFalse((self.root / "escape").exists())

    def test_symlink_hardlink_and_special_archive_entries_refuse(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("lib/modules/" + RELEASE + "/unsafe")
                member.type = kind
                member.linkname = "/tmp/escape"
                self.write_archive(extra=member)
                self.update_artifact("modules.tar.gz")
                self.refuses("link or special file")

    def test_duplicate_module_paths_refuse(self):
        member = tarfile.TarInfo("lib/modules/" + RELEASE + "/modules.dep")
        member.size = 3
        self.write_archive(extra=member)
        self.update_artifact("modules.tar.gz")
        self.refuses("duplicate module archive path")

    def test_linked_artifact_refuses(self):
        artifact = self.output / "System.map"
        target = self.root / "external-map"
        artifact.replace(target)
        artifact.symlink_to(target)
        self.refuses("canonical regular file")

    def test_forged_artifact_hash_refuses(self):
        self.receipt["artifacts"]["Module.symvers"]["sha256"] = "0" * 64
        self.refuses("candidate artifact differs: Module.symvers")

    def test_forged_raw_image_hash_refuses(self):
        self.receipt["artifacts"]["Image"]["sha256"] = "0" * 64
        self.refuses("decompressed Image differs")

    def test_changed_signed_archive_refuses(self):
        with (self.output / "modules.tar.gz").open("ab") as stream:
            stream.write(b"changed signing bytes")
        self.refuses("candidate artifact differs: modules.tar.gz")

    def test_wrong_kernel_release_file_refuses(self):
        (self.output / "kernel.release").write_bytes(b"7.3.0-wrong\n")
        self.update_artifact("kernel.release")
        self.refuses("kernel.release differs")

    def test_image_architecture_release_and_header_window_refuse(self):
        for raw, message in ((image_bytes(machine=b"x86!"), "not an ARM64 Image"),
                             (image_bytes(release=RELEASE + "-wrong"), "Image kernel release differs"),
                             (image_bytes(image_size=128 * 1024**2), "header exceeds")):
            with self.subTest(message=message):
                self.receipt["artifacts"]["Image"] = byte_record(raw)
                (self.output / "Image.gz").write_bytes(gzip.compress(raw, mtime=0))
                self.update_artifact("Image.gz")
                self.refuses(message)

    def test_oversized_recorded_image_refuses_without_reconstructing(self):
        self.receipt["artifacts"]["Image"]["bytes"] = 128 * 1024**2 + 1
        self.refuses("recorded Image exceeds")

    def test_inflated_image_cannot_exceed_recorded_bound(self):
        # A small advertised Image must not let an attacker allocate its expanded gzip size.
        (self.output / "Image.gz").write_bytes(gzip.compress(image_bytes() + b"\0" * 1024**2, mtime=0))
        self.update_artifact("Image.gz")
        self.refuses("recorded size")

    def test_multiple_gzip_members_and_trailing_data_refuse(self):
        original = (self.output / "Image.gz").read_bytes()
        for suffix in (gzip.compress(b"second member", mtime=0), b"trailing bytes"):
            with self.subTest(suffix=suffix):
                (self.output / "Image.gz").write_bytes(original + suffix)
                self.update_artifact("Image.gz")
                self.refuses("trailing data or multiple members")

    def test_truncated_gzip_refuses(self):
        path = self.output / "Image.gz"
        path.write_bytes(path.read_bytes()[:-8])
        self.update_artifact("Image.gz")
        self.refuses("Image.gz is truncated")

    def test_forged_fixed_source_and_config_pins_refuse(self):
        for key in ("commit", "archive_sha256", "baseline_config_sha256"):
            with self.subTest(key=key):
                before = self.receipt[key]
                self.receipt[key] = "0" * len(before)
                self.bind_inputs()
                self.refuses("pin differs")
                self.receipt[key] = before
                self.bind_inputs()

    def test_changed_config_bytes_with_self_consistent_receipt_refuse(self):
        (self.output / ".config").write_bytes(b"CONFIG_MODULE_SIG_ALL=n\n")
        self.update_artifact(".config")
        self.refuses("compiled config differs")

    def test_changed_patch_set_and_order_refuse(self):
        self.receipt["ordered_patches"].reverse()
        self.bind_inputs()
        self.refuses("patch set/order differs")

    def test_forged_input_binding_refuses(self):
        self.receipt["input_signature"] = "0" * 64
        self.refuses("input signature differs")

    def test_changed_worker_recipe_refuses(self):
        self.receipt["worker_sha256"] = "0" * 64
        self.bind_inputs()
        self.refuses("worker recipe differs")

    def test_missing_inventory_and_boolean_sizes_refuse(self):
        removed = self.receipt["artifacts"].pop("Image")
        self.refuses("artifact inventory differs")
        self.receipt["artifacts"]["Image"] = removed
        self.receipt["artifacts"]["Image"]["bytes"] = True
        self.refuses("invalid artifact record")

    def test_private_key_or_missing_certificate_refuses(self):
        self.receipt["signing"]["private_key_exported"] = True
        self.refuses("unsafe signing provenance")
        self.receipt["signing"]["private_key_exported"] = False
        self.receipt["signing"]["certificate"] = None
        self.refuses("signing certificate")

    def test_bad_baseline_topology_never_publishes(self):
        self.baseline.write_bytes(self.fixtures.fixture_dtb(omit=0x2a2280000))
        self.refuses("Kernel finalization refused")

    def test_archive_file_cannot_be_an_ancestor_directory(self):
        member = tarfile.TarInfo("lib/modules/" + RELEASE + "/modules.dep/nested")
        member.size = 3
        self.write_archive(extra=member)
        self.update_artifact("modules.tar.gz")
        self.refuses("file/directory path collision")

    def test_extra_receipt_schema_field_refuses(self):
        self.receipt["invented"] = "not part of the worker contract"
        self.refuses("compile receipt schema differs")

    def test_forged_patch_hash_refuses(self):
        name = next(iter(self.receipt["patches"]))
        self.receipt["patches"][name] = "0" * 64
        self.receipt["ordered_patches"][0]["sha256"] = "0" * 64
        self.bind_inputs()
        self.refuses("patch set/order differs")

    def test_existing_metadata_is_never_overwritten(self):
        sentinel = self.output / "build-manifest.json"
        sentinel.write_bytes(b"existing owner bytes\n")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already finalized or partial", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"existing owner bytes\n")
        self.assertEqual((self.output / "t6050-j716s.dtb").read_bytes(), self.raw_dtb)
        self.assertFalse((self.output / "Image").exists())


if __name__ == "__main__":
    unittest.main()
