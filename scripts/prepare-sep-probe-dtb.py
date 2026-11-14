#!/usr/bin/env python3
"""Retain the baseline KIS safety states in a generated SEP-probe DTB."""
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("j716s_dtb", PROJECT / "work/aura-audit/compose-linux-rescue.py")
dtb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtb)


def prepare(path, baseline_path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError("expected a canonical generated DTB file")
    original = path.read_bytes()
    before = dtb.dtb_nodes(original)
    targets = dtb.kis_targets(before)
    baseline_path = Path(baseline_path)
    if baseline_path.is_symlink() or not baseline_path.is_file() or baseline_path.resolve() != baseline_path:
        raise ValueError("expected a canonical baseline boot DTB")
    baseline_bytes = baseline_path.read_bytes()
    baseline = dtb.dtb_nodes(baseline_bytes)
    if dtb.kis_targets(baseline) != targets:
        raise ValueError("baseline KIS topology differs")
    statuses = {name: baseline[name].get("status") for name in targets}
    if any(value not in (b"okay\0", b"disabled\0") for value in statuses.values()):
        raise ValueError("unexpected baseline KIS state")
    for name in targets:
        if before[name].get("status") not in (b"okay\0", b"disabled\0"):
            raise ValueError("unexpected KIS status: " + name)
    libfdt = PROJECT / "work/sources/aura/src/libfdt"
    sources = [libfdt / name for name in (
        "fdt.c", "fdt_addresses.c", "fdt_empty_tree.c", "fdt_ro.c",
        "fdt_rw.c", "fdt_strerror.c", "fdt_sw.c", "fdt_wip.c")]
    with tempfile.TemporaryDirectory(prefix="j716s-exact-fdt-") as temporary:
        library = Path(temporary) / "libfdt.so"
        subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-I", str(libfdt),
                        *map(str, sources), "-o", str(library)], check=True)
        fdt = ctypes.CDLL(str(library))
        pointer = ctypes.c_void_p
        integer = ctypes.c_int
        for name, arguments, result in (
                ("fdt_open_into", [pointer, pointer, integer], integer),
                ("fdt_first_subnode", [pointer, integer], integer),
                ("fdt_next_subnode", [pointer, integer], integer),
                ("fdt_get_name", [pointer, integer, ctypes.POINTER(integer)], ctypes.c_char_p),
                ("fdt_setprop", [pointer, integer, ctypes.c_char_p, pointer, integer], integer),
                ("fdt_pack", [pointer], integer)):
            function = getattr(fdt, name)
            function.argtypes, function.restype = arguments, result
        buffer = ctypes.create_string_buffer(original, len(original) + 4096)

        def checked(result, operation):
            if result < 0:
                raise ValueError(operation + " failed: libfdt " + str(result))
            return result

        checked(fdt.fdt_open_into(buffer, buffer, len(buffer)), "opening DTB")
        for name in targets:
            # libfdt's path lookup treats 'soc' as a match for 'soc@...'.
            # Walk siblings and compare their complete names at every level.
            node = 0
            for segment in name.strip("/").split("/"):
                child = fdt.fdt_first_subnode(buffer, node)
                while child >= 0 and fdt.fdt_get_name(buffer, child, None) != segment.encode():
                    child = fdt.fdt_next_subnode(buffer, child)
                node = checked(child, "exact path " + name)
            value = statuses[name]
            checked(fdt.fdt_setprop(buffer, node, b"status", value, len(value)), "preserving " + name)
        checked(fdt.fdt_pack(buffer), "packing DTB")
        size = struct.unpack_from(">I", buffer, 4)[0]
        result = bytes(memoryview(buffer).cast("B")[:size])
    after = dtb.dtb_nodes(result)
    if before.keys() != after.keys():
        raise ValueError("KIS transform changed topology")
    for name, properties in before.items():
        expected = dict(properties)
        if name in targets:
            expected["status"] = statuses[name]
        if after[name] != expected:
            raise ValueError("KIS transform changed unrelated property: " + name)
    path.write_bytes(result)
    return {"input_sha256": hashlib.sha256(original).hexdigest(),
            "output_sha256": hashlib.sha256(result).hexdigest(),
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "kis_status": {name: value.rstrip(b"\0").decode() for name, value in statuses.items()},
            "exact_node_names": True}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("baseline_dtb", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.dtb.absolute(), args.baseline_dtb.absolute()), indent=2))
