#!/usr/bin/env python3
"""One bounded CPU or explicitly admitted Vulkan benchmark; never start a server."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import signal
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sensor_paths():
    hwmon, = [path for path in Path('/sys/class/hwmon').glob('hwmon*')
              if (path / 'name').read_text().strip() == 'macsmc_hwmon']
    die, = [label.with_name(label.name.replace('_label', '_input'))
            for label in hwmon.glob('temp*_label') if label.read_text().strip() == 'TCMb']
    battery = Path('/sys/class/power_supply/macsmc-battery')
    return {'die_mC': die, 'battery_uw': battery / 'power_now',
            'fan1_rpm': hwmon / 'fan1_input', 'fan2_rpm': hwmon / 'fan2_input'}


def sample_sensors(paths, start):
    return {'elapsed_s': time.monotonic() - start,
            **{name: int(path.read_text()) for name, path in paths.items()}}


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("route", choices=("cpu", "vulkan"))
    parser.add_argument("model", choices=("small", "medium", "7b"))
    parser.add_argument("--prompt", type=int, choices=(64, 256, 512), default=64)
    parser.add_argument("--depth", type=int, choices=(0, 512, 2048, 4096), default=0,
                        help="preload this many KV tokens before each timed row; preload is not timed")
    parser.add_argument("--profile", action="store_true", help="collect Vulkan per-operation timings, not baseline throughput")
    parser.add_argument("--threads", type=int, choices=(2, 4, 6), default=2)
    parser.add_argument("--generation", type=int, choices=(16, 128, 256), default=16)
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--workspace", type=Path, default=BASE / "../../work/build/llama-j716s")
    parser.add_argument("--compute-receipt", type=Path)
    parser.add_argument("--icd", type=Path)
    parser.add_argument("--device", default="Vulkan0")
    args = parser.parse_args()
    work = args.workspace.resolve(strict=True)
    if not work.is_relative_to((BASE / "../../work/build").resolve()):
        parser.error("Workspace must remain inside this checkout's private work/build")
    if args.route == "cpu" and (args.compute_receipt or args.icd):
        parser.error("CPU comparison is independent of Vulkan; omit GPU options")
    if args.profile and args.route != "vulkan":
        parser.error("Per-operation timing requires the admitted Vulkan route")
    if args.route == "vulkan":
        if not args.compute_receipt or not args.icd:
            parser.error("Vulkan blocked: --compute-receipt and --icd are mandatory; do not bypass features")
        args.compute_receipt = args.compute_receipt.resolve(strict=True)
        args.icd = args.icd.resolve(strict=True)
        subprocess.run([sys.executable, str(BASE / "guard-vulkan.py"), str(args.compute_receipt), str(args.icd), args.device], check=True)
    manifest = json.loads((BASE / "inputs.json").read_text())
    if args.model == "7b":
        model_files = json.loads((BASE / "benchmark-7b.json").read_text())["files"]
    else:
        model_files = [next(item for item in manifest["models"] if item["id"] == args.model)]
    for item in model_files:
        path = work / item["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise RuntimeError(f"Model file differs from immutable public input: {path}")
    model_path = work / model_files[0]["path"]
    build_receipt = work / f"build-{args.route}-receipt.json"
    built = json.loads(build_receipt.read_text())
    if built["route"] != args.route or built["inputs_sha256"] != digest(BASE / "inputs.json"):
        raise RuntimeError("Build receipt does not match this candidate manifest/route")
    binary = work / f"build-{args.route}/bin/llama-bench"
    if digest(binary) != built["outputs_sha256"]["llama-bench"]:
        raise RuntimeError("Benchmark binary differs from the isolated build receipt")
    # Serialize variants too: timings are invalid if two workspaces compete for the GPU.
    with (BASE / "../../work/build/.llama-compare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = Path(tempfile.mkdtemp(prefix=f"comparison-{args.model}-{args.route}-", dir=work))
        home = run / "home"
        home.mkdir()
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C", "TZ": "UTC",
                       "OMP_NUM_THREADS": str(args.threads)}
        gpu_layers = "0" if args.route == "cpu" else "99"
        command = ["/usr/bin/timeout", "--signal=TERM", "--kill-after=5s", "180s", "/usr/bin/nice", "-n", "10",
                   str(binary), "-m", str(model_path), "-p", str(args.prompt), "-n", str(args.generation),
                   "-b", str(args.prompt), "-ub", str(args.prompt), "-t", str(args.threads), "--poll", "0", "-r", str(args.repetitions),
                   "-d", str(args.depth), "-ctk", "f16", "-ctv", "f16", "-fa", "auto", "-ngl", gpu_layers, "-o", "json", "-v"]
        if args.route == "vulkan":
            # Loader selection only: never fake Vulkan API/features or disable kernels.
            environment["VK_DRIVER_FILES"] = str(args.icd)
            library = json.loads(args.icd.read_text())["ICD"]["library_path"]
            library_dir = (args.icd.parent / library).resolve(strict=True).parent
            environment["LD_LIBRARY_PATH"] = str(library_dir)
            environment["ASAHI_M5_BRINGUP"] = "1"
            command += ["-dev", args.device]
            if args.profile:
                environment["GGML_VK_PERF_LOGGER"] = "1"
                environment["GGML_VK_PERF_LOGGER_FREQUENCY"] = "16"
        start = time.monotonic()
        sensors = sensor_paths()
        samples = [sample_sensors(sensors, start)]
        if not 0 < samples[0]['die_mC'] < 80000:
            raise RuntimeError('Benchmark requires a readable die sensor below 80°C')
        abort = None
        with (run / "bench.json").open("x") as output, (run / "bench.stderr.log").open("x") as errors:
            with subprocess.Popen(command, env=environment, cwd=work, stdout=output, stderr=errors,
                                  start_new_session=True) as child:
                try:
                    while True:
                        try:
                            exit_code = child.wait(timeout=1)
                            break
                        except subprocess.TimeoutExpired:
                            samples.append(sample_sensors(sensors, start))
                            if not 0 < samples[-1]['die_mC'] < 80000:
                                abort = 'Stopped at the 80°C benchmark ceiling or invalid die reading'
                                stop_group(child)
                                exit_code = child.returncode
                                break
                except BaseException:
                    stop_group(child)
                    raise
        record = {"schema": 1, "status": "benchmark-failed", "route": args.route, "model": args.model,
                  "model_files_sha256": {item["path"]: item["sha256"] for item in model_files},
                  "build_receipt_sha256": digest(build_receipt),
                  "source_patches": built.get("source_patches", {}),
                  "context_depth": args.depth,
                  "vulkan_profile": args.profile,
                  "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                  "command": command, "environment": environment, "exit_code": exit_code,
                  "samples": samples,
                  "wall_seconds": time.monotonic() - start, "max_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                  "note": "Bounded throughput; not accuracy or sustained-performance qualification."}
        if args.route == "vulkan":
            record["compute_receipt_sha256"] = digest(args.compute_receipt)
        failure = None
        if exit_code != 0 or abort:
            failure = abort or f"Benchmark exited {exit_code}; no retry or CPU fallback"
        else:
            try:
                rows = json.loads((run / "bench.json").read_text())
                if len(rows) != 2 or {(row["n_prompt"], row["n_gen"]) for row in rows} != {(args.prompt, 0), (0, args.generation)}:
                    raise RuntimeError("Missing expected prompt/generation result rows")
                if any(row["n_depth"] != args.depth for row in rows):
                    raise RuntimeError("Benchmark rows do not match the requested context depth")
                if any(not math.isfinite(row["avg_ts"]) or row["avg_ts"] <= 0 or row["n_gpu_layers"] != int(gpu_layers) for row in rows):
                    raise RuntimeError("Invalid throughput/offload result")
                errors = (run / "bench.stderr.log").read_text()
                if args.route == "vulkan":
                    proof = json.loads(args.compute_receipt.read_text())
                    if any(proof["device_name"] not in row["gpu_info"] or args.device not in row["devices"] for row in rows):
                        raise RuntimeError("Benchmark did not identify the qualified hardware/device")
                    offload = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", errors)
                    if not offload or any(int(done) <= 0 or done != total for done, total in offload):
                        raise RuntimeError("Full actual GPU layer offload was not observed; reject CPU fallback")
                    if re.search(r"VUID-|Validation (?:Error|Warning)|VK_ERROR_DEVICE_LOST|Unsupported device", errors, re.I):
                        raise RuntimeError("Vulkan diagnostics are not clean")
                    fa = re.findall(r"resolve_fused_ops: Flash Attention (enabled|disabled)", errors)
                    if fa != ["enabled"] * len(rows):
                        raise RuntimeError(f"Expected Flash Attention on both Vulkan runs, got {fa}")
                    record["flash_attention"] = "enabled"
                elif any(row["gpu_info"] or "Vulkan" in row["backends"] for row in rows):
                    raise RuntimeError("CPU-only candidate unexpectedly reports a GPU backend")
                record["status"] = "bounded-benchmark-completed-not-runtime-qualified"
                record["throughput_tokens_per_second"] = {f"pp{args.prompt}" if row["n_prompt"] else f"tg{args.generation}": row["avg_ts"] for row in rows}
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                failure = str(error)
        if failure:
            record["failure"] = failure
        with (run / "result.json").open("x") as output:
            json.dump(record, output, indent=2, sort_keys=True)
            output.write("\n")
        print(f"{record['status']}: {run}")
        if failure:
            print(failure, file=sys.stderr)
            return exit_code or 1
        print(json.dumps(record["throughput_tokens_per_second"]))
        return 0


if __name__ == "__main__":
    sys.exit(main())
