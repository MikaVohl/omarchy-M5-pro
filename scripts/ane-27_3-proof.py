#!/usr/bin/env python3
"""Prepare/verify the numerical boundary for a real H17 add job, host-only.

The public compiler target H17 and the pinned kernel's ABI-3 ane-run.py are
an actual LOAD/EXEC path; READY or an accel node is not a job proof. Native
binary_add_1x64x1x1 uses 64 channel lanes at byte stride 64. Its HWX tensor
segment and ABI-3 returned buffer span are 4096 bytes; allocation capacity
must not be mistaken for that returned length. Source: aurora-silicon/mil-hwx-compiler
36feecaa7476968f72feb064c0f389a5b724a85b,
research/oracles/h17/binary_add_1x64x1x1.json (native-byte parity oracle;
hardware execution evidence is recorded separately).

After preparing a new private directory, compile add.mil with the pinned open
compiler using --target H17 --model-root DIR --output DIR/program. After the
kernel's live-pin/cold-ownership gates and attended authorization, run its
production tools/testing/selftests/drivers/ane/ane-run.py with that HWX,
--case DIR/a.bin,DIR/b.bin:DIR/expected.bin --runs 1 --max-ulp 0
--out-dir DIR/result, under a bounded outer timeout. Require exit zero, then
verify result/output_00.bin here. A skip, empty/truncated comparison, untouched
poison or wrong result is not success. Neither command here opens hardware,
compiles a program, invokes the runner nor claims that a device job occurred.
"""
import argparse
import json
from pathlib import Path
import struct

LANES = 64
STRIDE = 64
TENSOR_BYTES = LANES * STRIDE
MIL = '''program(1.3)
[buildInfo = dict<string, string>({})]
{
  func main<ios18>(tensor<fp16, [1, 64, 1, 1]> x, tensor<fp16, [1, 64, 1, 1]> z) {
    tensor<fp16, [1, 64, 1, 1]> y = add(x = x, y = z)[name = string("y")];
  } -> (y);
}
'''


def fixtures():
    a, b = bytearray(TENSOR_BYTES), bytearray(TENSOR_BYTES)
    expected = bytearray(b"\xff" * TENSOR_BYTES)
    for lane in range(LANES):
        x = (lane % 17 - 8) * 0.125
        z = (lane % 11 - 5) * 0.25
        struct.pack_into("<e", a, lane * STRIDE, x)
        struct.pack_into("<e", b, lane * STRIDE, z)
        struct.pack_into("<e", expected, lane * STRIDE, x + z)
    return {"a.bin": bytes(a), "b.bin": bytes(b), "expected.bin": bytes(expected)}


def verify_result(data):
    if len(data) != TENSOR_BYTES:
        raise ValueError(f"result tensor span must be exactly {TENSOR_BYTES} bytes, not {len(data)}")
    expected = fixtures()["expected.bin"]
    for lane in range(LANES):
        offset = lane * STRIDE
        if data[offset:offset + 2] != expected[offset:offset + 2]:
            raise ValueError(f"ANE result differs from exact fp16 addition at lane {lane}")
    return LANES


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="create a fresh private MIL/input/oracle directory")
    prepare.add_argument("directory", type=Path)
    verify = commands.add_parser("verify", help="verify a saved real-run output; not an execution attestation")
    verify.add_argument("result", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        (args.directory / "add.mil").write_text(MIL)
        for name, contents in fixtures().items():
            (args.directory / name).write_bytes(contents)
        print(json.dumps({"fixture": "H17-add-1x64x1x1", "lanes": LANES, "byte_stride": STRIDE,
                          "tensor_bytes": TENSOR_BYTES, "hardware_executed": False}))
    else:
        lanes = verify_result(args.result.read_bytes())
        print(f"PASS: {lanes}/{LANES} fp16 result lanes bit-exact; execution requires separate LOAD/EXEC evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
