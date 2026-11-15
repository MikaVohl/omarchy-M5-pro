"""Offline consumers of the exact kernel patch, not a second auth implementation.

Rust fixtures exercise real, allocation-free admission helpers extracted from the
new-file hunk. Public structural DER fixtures are NOT signed factory material and
never enter firmware directories, sensor transports, or hardware authentication.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/kernel-j716s-sep-sensor.patch"


def sections():
    result = {}
    name = None
    for line in PATCH.read_text().splitlines(keepends=True):
        if line.startswith("+++ b/"):
            name = line[6:].strip()
            result[name] = []
        elif name and not line.startswith(("--- ", "@@", "diff ")):
            result[name].append(line)
    return {name: "".join(lines) for name, lines in result.items()}


RUST_TESTS = r'''
#[cfg(test)]
mod offline_contract {
    use super::*;

    fn safe() -> Policy {
        Policy { j716s: true, opt_in: 1, xart_writes: 0, provision_keybag: 0,
            xart_guard: 2, trusted_keys: 0, filevault_api: 0,
            discovery_only: true, spi_override: false, test_fault: 0 }
    }

    #[test]
    fn every_policy_requirement_is_mandatory() {
        assert!(safe().admitted());
        let mut p = safe(); p.j716s = false; assert!(!p.admitted());
        for v in [0, 2, u32::MAX] { let mut p = safe(); p.opt_in = v; assert!(!p.admitted()); }
        for v in [1, 2, u8::MAX] {
            let mut p = safe(); p.xart_writes = v; assert!(!p.admitted());
            let mut p = safe(); p.provision_keybag = v; assert!(!p.admitted());
            let mut p = safe(); p.trusted_keys = v; assert!(!p.admitted());
            let mut p = safe(); p.filevault_api = v; assert!(!p.admitted());
            let mut p = safe(); p.test_fault = v; assert!(!p.admitted());
        }
        for v in [-1, 0, 1, 3, i8::MAX] { let mut p = safe(); p.xart_guard = v; assert!(!p.admitted()); }
        let mut p = safe(); p.discovery_only = false; assert!(!p.admitted());
        let mut p = safe(); p.spi_override = true; assert!(!p.admitted());
    }

    #[test]
    fn only_expected_idle_identity_and_real_values_pass_admission() {
        assert!(identity_admitted(0x3356, true, false, false));
        for id in [0, 0x3352, 0x335e, u16::MAX] { assert!(!identity_admitted(id, true, false, false)); }
        assert!(!identity_admitted(0x3356, false, false, false));
        assert!(!identity_admitted(0x3356, true, true, false));
        assert!(!identity_admitted(0x3356, true, false, true));
        assert!(!nonempty_value(&[]));
        assert!(!nonempty_value(&[0; 32]));
        assert!(!nonempty_value(&[0xff; 32]));
        assert!(nonempty_value(&[0, 1]));
    }

    #[test]
    fn raw_command_surface_is_exact_and_write_free() {
        let allowed = [0x73, 0x48, 0x80, 0x17, 0x81, 0x86, 0x15, 0x16];
        for opcode in 0..=u16::MAX {
            if !allowed.contains(&opcode) {
                assert!(!request_admitted(opcode, &[1; 40]), "opcode {opcode:#x}");
            }
        }
        assert!(request_admitted(0x73, &6u32.to_le_bytes()));
        assert!(!request_admitted(0x73, &1u32.to_le_bytes()));
        assert!(request_admitted(0x80, &0x3356u16.to_le_bytes()));
        assert!(!request_admitted(0x80, &0x3352u16.to_le_bytes()));
        assert!(request_admitted(0x15, &1u32.to_le_bytes()));
        assert!(!request_admitted(0x15, &2u32.to_le_bytes()));
        for (opcode, size) in [(0x48,12), (0x17,16), (0x81,32), (0x16,40)] {
            assert!(request_admitted(opcode, &vec![1; size]));
            for wrong in [0, size-1, size+1] { assert!(!request_admitted(opcode, &vec![1; wrong])); }
            assert!(!request_admitted(opcode, &vec![0; size]));
            assert!(!request_admitted(opcode, &vec![0xff; size]));
        }
        assert_eq!(MAX_ROOT_TRANSFER, 0x4b000);
        assert!(request_admitted(0x86, &vec![1; MAX_ROOT_TRANSFER]));
        assert!(!request_admitted(0x86, &vec![1; MAX_ROOT_TRANSFER+1]));
        assert!(!request_admitted(0x86, &[]));
    }

    fn tlv(tag: u8, body: &[u8]) -> Vec<u8> {
        let mut result = vec![tag];
        if body.len() < 128 { result.push(body.len() as u8); }
        else {
            let bytes = (body.len() as u32).to_be_bytes();
            let at = bytes.iter().position(|b| *b != 0).unwrap();
            result.push(0x80 | (4-at) as u8); result.extend_from_slice(&bytes[at..]);
        }
        result.extend_from_slice(body); result
    }
    fn named(name: &[u8], parts: &[Vec<u8>]) -> Vec<u8> {
        let mut body = tlv(0x16, name);
        for p in parts { body.extend_from_slice(p); }
        tlv(0x30, &body)
    }
    // Shape fixture only: NONE of these bytes has a valid hardware signature.
    fn container(kind: &[u8], data: &[u8], signature: &[u8], trust: &[u8]) -> Vec<u8> {
        let payload = named(b"IM4P", &[tlv(0x16,kind), tlv(0x16,b"fixture"), tlv(0x04,data)]);
        let manifest = named(b"IM4M", &[tlv(0x02,&[0]), tlv(0x31,&tlv(0x02,&[1])),
            tlv(0x04,signature), tlv(0x30,&tlv(0x30,&tlv(0x02,&[1]))) ]);
        let image = named(b"IMG4", &[payload, tlv(0xa0,&manifest)]);
        named(b"comb", &[named(b"fdrd", &[tlv(0x04,&image)]), named(b"secb", &[tlv(0x04,trust)])])
    }

    #[test]
    fn container_shape_type_and_nested_bounds_are_enforced() {
        let root = container(b"MSRk", b"opaque-payload", b"not-a-signature", b"not-a-trust-chain");
        assert!(container_admitted(&root, b"MSRk")); // shape only, not authentication
        assert!(!container_admitted(&root, b"FSC2"));
        let cal = container(b"FSC2", b"CALB opaque calibration", b"not-a-signature", b"not-a-trust-chain");
        assert!(container_admitted(&cal, b"FSC2"));
        assert!(!container_admitted(&container(b"FSC2", b"not calibration", b"sig", b"trust"), b"FSC2"));
        assert!(!container_admitted(&container(b"FSC2", &[vec![1; 256], b"CALB".to_vec()].concat(), b"sig", b"trust"), b"FSC2"));
        for (data,sig,trust) in [(b"".as_slice(),b"sig".as_slice(),b"trust".as_slice()),
            (b"payload",b"",b"trust"), (b"payload",b"sig",b"")] {
            assert!(!container_admitted(&container(b"MSRk", data,sig,trust), b"MSRk"));
        }
        for end in 0..root.len() { assert!(!container_admitted(&root[..end], b"MSRk")); }
        let mut trailing = root.clone(); trailing.push(0); assert!(!container_admitted(&trailing,b"MSRk"));
        let mut indefinite = root.clone(); indefinite[1] = 0x80; assert!(!container_admitted(&indefinite,b"MSRk"));
        assert!(!container_admitted(&[0x30,0x81,0x7f],b"MSRk"));
        assert!(!container_admitted(&[0x30,0x82,0,0x80],b"MSRk"));
        assert!(!container_admitted(&vec![0; MAX_CONTAINER+1],b"MSRk"));
        let oversized = container(b"MSRk", &vec![1; 4*1024*1024],b"sig",b"trust");
        assert!(!container_admitted(&oversized,b"MSRk"));
    }
}
'''


class SepSensorKernelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("rustc"), "Rust admission fixture runs in the pinned build guest")
    def test_actual_kernel_admission_helpers_offline(self):
        source = sections()["drivers/soc/apple/sensor_auth.rs"]
        self.assertTrue(all(line.startswith("+") for line in source.splitlines()))
        source = "".join(line[1:] for line in source.splitlines(keepends=True))
        compiler = shutil.which("rustc")
        self.assertIsNotNone(compiler, "rustc required for actual kernel admission fixtures")
        with tempfile.TemporaryDirectory(prefix="sep-sensor-kernel-test-") as temp:
            root = Path(temp)
            fixture = root / "admission.rs"
            binary = root / "admission-tests"
            fixture.write_text(source + RUST_TESTS)
            compiled = subprocess.run([compiler, "--edition=2021", "--test", "-A", "dead_code",
                                       str(fixture), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)



if __name__ == "__main__":
    unittest.main()
