#!/usr/bin/env python3
"""Measure a private G17S ICD after current-boot admission; never change policy."""
import argparse
import csv
import fcntl
import json
from pathlib import Path
import re
import runpy
import statistics
import subprocess
import tempfile
import time

BASE = Path(__file__).resolve().parent
WORK = (BASE / '../../work/build').resolve()


def power_context():
    return {'profile': subprocess.check_output(['powerprofilesctl', 'get'], text=True).strip(),
            'ac_online': int(Path('/sys/class/power_supply/macsmc-ac/online').read_text()),
            'panel': {p.parent.name: int(p.read_text()) for p in Path('/sys/class/backlight').glob('*/actual_brightness')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compute_receipt', type=Path)
    parser.add_argument('icd', type=Path)
    parser.add_argument('validation_layer_dir', type=Path)
    args = parser.parse_args()
    receipt, icd, layer_dir = (p.resolve(strict=True) for p in (args.compute_receipt, args.icd, args.validation_layer_dir))
    guard = runpy.run_path(str(BASE / 'guard-vulkan.py'))
    admitted = guard['admit'](receipt, icd, 'Vulkan0')
    uuid = admitted['device_uuid'].replace('-', '').lower()
    if not re.fullmatch(r'[0-9a-f]{32}', uuid):
        raise RuntimeError('Compute receipt lacks a Vulkan device UUID')
    lib = (icd.parent / json.loads(icd.read_text())['ICD']['library_path']).resolve(strict=True)
    layer = json.loads((layer_dir / 'VkLayer_khronos_validation.json').read_text())['layer']
    layer_lib = (layer_dir / layer['library_path']).resolve(strict=True)
    if layer['name'] != 'VK_LAYER_KHRONOS_validation' or layer_lib.parent != lib.parent:
        raise RuntimeError('Expected Khronos validation from the same private library directory')
    bench = runpy.run_path(str(BASE / 'compare-candidate.py'))
    paths = bench['sensor_paths']()
    run = Path(tempfile.mkdtemp(prefix='g17s-probe.', dir=WORK))
    print(f'G17S probe workspace: {run}', flush=True)
    home = run / 'home'
    home.mkdir()
    read_spv, chain_spv, binary = (run / name for name in ('read.spv', 'chain.spv', 'probe'))
    sdk = WORK / 'llama-j716s/vulkan-headers/include'
    tool_env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C'}
    subprocess.run(['/usr/bin/gcc', '-std=c17', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(sdk),
                    str(BASE / 'g17s-probe.c'), '/usr/lib/libvulkan.so', '-o', str(binary)],
                   env=tool_env, check=True, timeout=30)
    for spv, flags in ((read_spv, []), (chain_spv, ['-DCHAIN'])):
        subprocess.run(['/usr/bin/glslc', '-O', '--target-env=vulkan1.2', *flags,
                        str(BASE / 'g17s-probe.comp'), '-o', str(spv)], env=tool_env, check=True, timeout=15)
        subprocess.run(['/usr/bin/spirv-val', '--target-env', 'vulkan1.2', str(spv)], env=tool_env, check=True, timeout=15)
    env = {**tool_env, 'VK_DRIVER_FILES': str(icd), 'LD_LIBRARY_PATH': str(lib.parent),
           'ASAHI_M5_BRINGUP': '1', 'VK_LOADER_LAYERS_DISABLE': '~implicit~'}
    cmd = ['/usr/bin/timeout', '--kill-after=5s', '30s', str(binary), str(read_spv), str(chain_spv), uuid]
    start = time.monotonic()
    samples = {}
    power = {}
    kernel_start = int(time.time())
    with (WORK / '.llama-compare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for stage in ('validation', 'measured'):
            power[stage] = power_context()
            if not 0 < int(paths['die_mC'].read_text()) < 80000:
                raise RuntimeError('Probe requires readable die temperature below 80°C')
            trace = dict(env)
            if stage == 'validation':
                trace.update(VK_LAYER_PATH=str(layer_dir), VK_LOADER_LAYERS_ENABLE='VK_LAYER_KHRONOS_validation',
                             VK_LOADER_DEBUG='layer')
            errors = run / f'{stage}.log'
            with (run / f'{stage}.csv').open('x') as out, errors.open('x') as err, subprocess.Popen(
                    cmd, env=trace, stdout=out, stderr=err, start_new_session=True) as child:
                samples[stage] = []
                try:
                    while True:
                        point = bench['sample_sensors'](paths, start)
                        samples[stage].append(point)
                        if not 0 < point['die_mC'] < 80000:
                            raise RuntimeError('Probe exceeded the 80°C ceiling or lost die telemetry')
                        try:
                            status = child.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            continue
                        if status:
                            raise RuntimeError(f'{stage} exited {status}; see {errors}')
                        break
                finally:
                    if child.returncode is None:
                        bench['stop_group'](child)
            log = errors.read_text() + (run / f'{stage}.csv').read_text()
            if ('All bandwidth checksums and dispatch outputs matched' not in log or
                re.search(r'VUID-|Validation (?:Error|Warning)|VK_ERROR_DEVICE_LOST', log, re.I) or
                (stage == 'validation' and 'Insert instance layer "VK_LAYER_KHRONOS_validation"' not in log)):
                raise RuntimeError(f'{stage} validation/readback failed; see {run}')
            if power_context() != power[stage]:
                raise RuntimeError(f'Power or display state changed during {stage}')
    kernel = subprocess.check_output(['journalctl', '-b', '-k', '--since', f'@{kernel_start}', '-q', '--no-pager'], text=True)
    (run / 'kernel.log').write_text(kernel)
    if re.search(r'GPU .*timed out|GPU.*fault|DART.*fault|GPU Recovery|device lost|recovering from.*timeout', kernel, re.I):
        raise RuntimeError(f'GPU fault in probe window; see {run}')
    results = {}
    with (run / 'measured.csv').open() as data:
        for row in csv.DictReader(data):
            probe = row['probe']
            # Retain driver timestamps in the CSV for diagnosis. The rate
            # uses host elapsed time: some GPU intervals are implausibly short.
            wall_ns = float(row['submit_to_fence_ns'])
            if probe.startswith('read-'):
                rates = {'submit_to_fence_GBps': int(row['bytes']) / wall_ns}
            else:
                divisor = 1000 * int(row['dispatches'])
                rates = {'submit_to_fence_us_per_dispatch': wall_ns / divisor}
            results.setdefault(probe, []).append(rates)
    probes = ('read-64MiB', 'read-256MiB', 'read-256MiB-burst', 'independent', 'dependent')
    if set(results) != set(probes) or any(len(results[key]) != 7 for key in probes):
        raise RuntimeError('Incomplete probe sample set')
    summary = {key: {field: {'median': statistics.median(r[field] for r in rows),
                             'min': min(r[field] for r in rows), 'max': max(r[field] for r in rows)}
                     for field in rows[0]} for key, rows in results.items()}
    hashes = {p.name: guard['digest'](p) for p in (receipt, icd, lib, layer_lib, binary, read_spv, chain_spv,
                                                 BASE / 'g17s-probe.c', BASE / 'g17s-probe.comp')}
    record = {'device_uuid': uuid, 'boot_id': admitted['boot_id'], 'hashes': hashes, 'context': power,
              'samples': samples, 'summary': summary}
    (run / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
