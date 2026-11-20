#!/usr/bin/env python3
"""Compare selected synthetic ggml operators on G17S with the upstream CPU reference."""
import argparse
import csv
import fcntl
import io
import json
from pathlib import Path
import math
import re
import runpy
import statistics
import subprocess
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
WORK = (BASE / '../../work/build').resolve()
# Bounded subsets of the pinned upstream eval fixtures. Keep matvec, matmul,
# routed experts, attention, gather and norm independently accountable.
CONTIGUOUS = r'bs=\[1,1\],nr=\[1,1\],per=\[0,1,2,3\],k_v=0,o=1,'
CASES = (
    ('quant-matvec', 'MUL_MAT', r'^type_a=(q4_K|q6_K|q8_0),type_b=f32,m=(1537|3968|8191|8192|8193|12288|18944),n=1,k=(768|1024|1280|3584),' + CONTIGUOUS),
    ('quant-matmul', 'MUL_MAT', r'^type_a=(q4_K|q6_K|q8_0),type_b=f32,m=1000,n=(8|21),k=(2304|4608),' + CONTIGUOUS),
    ('float-matmul', 'MUL_MAT', r'^type_a=(f32|f16|bf16),type_b=f32,m=16,n=(1|16),k=256,bs=\[(1,1|2,3)\],nr=\[1,1\],'),
    ('expert-routing', 'MUL_MAT_ID', r'^type_a=(q4_K|iq2_xs|f16),type_b=f32,n_mats=4,n_used=4,b=0,m=512,n=(1|15|16|17|31|32|33|47|48|49),k=256,'),
    ('attention', 'FLASH_ATTN_EXT', r'^hsk=64,hsv=64,nh=4,nr23=\[1,1\],kv=(113|512),nb=(1|32),mask=1,sinks=0,max_bias=0\.000000,logit_softcap=0\.000000,prec=f32,type_K=(f16|bf16|q8_0),type_V=(f16|bf16|q8_0),permute=\[0,1,2,3\],'),
    ('gather', 'GET_ROWS', r'^type=(f32|f16|bf16|q4_K|q6_K|q8_0),n=256,m=5,r=4,be1=(1|7),be2=1,v=[01],vs0=[01],offset_cols=0$'),
    ('rms-norm', 'RMS_NORM', r'^type=f32,ne=\[(64|1025),5,4,3\],v=[01],eps=[^,]+,inplace=[01]$'),
)


def run_group(binary, run, label, op, pattern, env, sensors, helpers, mode='test'):
    csv_file, log_file = (run / f'{label}.{ext}' for ext in ('csv', 'log'))
    command = ['/usr/bin/timeout', '--kill-after=5s', '45s', '/usr/bin/nice', '-n', '10', str(binary),
               mode, '-b', 'Vulkan0', '-j', '1', '-o', op, '-p', pattern, '--output', 'csv']
    start = time.monotonic()
    samples = [helpers['sample_sensors'](sensors, start)]
    if not 0 < samples[0]['die_mC'] < 80000:
        raise RuntimeError('Operator run requires readable die temperature below 80°C')
    with csv_file.open('x') as out, log_file.open('x') as err, subprocess.Popen(
            command, env=env, stdout=out, stderr=err, start_new_session=True) as child:
        try:
            while True:
                try:
                    status = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    point = helpers['sample_sensors'](sensors, start)
                    samples.append(point)
                    if not 0 < point['die_mC'] < 80000:
                        raise RuntimeError('Stopped at 80°C benchmark ceiling or lost die telemetry')
        finally:
            if child.returncode is None:
                helpers['stop_group'](child)
    raw = csv_file.read_text()
    log = log_file.read_text() + raw
    # The Vulkan loader can interleave validation messages with stdout CSV.
    # Preserve the unmodified logs and separately gate any such diagnostics.
    rows = list(csv.DictReader(io.StringIO('\n'.join(line for line in raw.splitlines() if line.startswith('"')))))
    expected_backend = all(r.get('backend_name') == 'Vulkan0' and r.get('op_name') == op and
                           r.get('test_mode') == mode and re.search(pattern, r.get('op_params') or '')
                           for r in rows if r.get('supported') == '1')
    supported = [r for r in rows if r.get('supported') == '1']
    passed = [r for r in supported if r.get('error_message') == '']
    validation_clean = not re.search(r'VUID-|Validation (?:Error|Warning)|VK_ERROR_DEVICE_LOST', log, re.I)
    layer_loaded = 'Insert instance layer "VK_LAYER_KHRONOS_validation"' in log
    validation_clean = validation_clean and layer_loaded == (mode == 'test')
    coverage_ok = bool(rows)
    if label == 'quant-matvec':
        coverage_ok = len(rows) == len({r.get('op_params') for r in rows}) == 33
    result = {'selected': len(rows), 'passed': len(passed), 'unsupported': len(rows) - len(supported),
            'failed': len(supported) - len(passed), 'validation_clean': validation_clean,
            'exit_code': status, 'peak_die_mC': max(s['die_mC'] for s in samples),
            'passed_gate': coverage_ok and expected_backend and len(passed) == len(rows) and validation_clean and status == 0}
    if mode == 'perf' and result['passed_gate']:
        times = {r['op_params']: {'us': float(r['time_us']), 'runs': int(r['n_runs'])} for r in rows}
        result['passed_gate'] = all(math.isfinite(t['us']) and t['us'] > 0 and t['runs'] > 0 for t in times.values())
        result['cases'] = times
    return result


def time_pair(run, arms, env, sensors, helpers):
    context = runpy.run_path(str(BASE / 'measure-g17s.py'))['power_context']
    settings = context()
    validation_env = dict(env)
    env = {key: value for key, value in env.items() if key not in ('VK_LAYER_PATH', 'VK_LOADER_LAYERS_ENABLE')}
    env['VK_LOADER_LAYERS_DISABLE'] = '*'
    rows = {}
    record = {'context': settings, 'correctness': {}, 'rounds': rows}
    for name, binary in arms.items():
        arm_dir = run / f'check-{name}'
        arm_dir.mkdir()
        result = run_group(binary, arm_dir, *CASES[0], validation_env, sensors, helpers)
        record['correctness'][name] = result
        if not result['passed_gate']:
            return record, False

    for slot, name in enumerate(('baseline', 'candidate', 'candidate', 'baseline')):
        if context() != settings:
            raise RuntimeError('Power or panel settings changed before a timing arm')
        arm_dir = run / f'{slot}-{name}'
        arm_dir.mkdir()
        result = run_group(arms[name], arm_dir, *CASES[0], env, sensors, helpers, mode='perf')
        rows[f'{slot}-{name}'] = result
        print(f'{slot}-{name}: {result["passed"]} timed cases', flush=True)
        if not result['passed_gate'] or context() != settings:
            return record, False
    baseline = [rows[f'{slot}-baseline']['cases'] for slot in (0, 3)]
    candidate = [rows[f'{slot}-candidate']['cases'] for slot in (1, 2)]
    if any(base.keys() != baseline[0].keys() for base in baseline + candidate):
        return record, False
    record['speedup'] = {case: [base[case]['us'] / cand[case]['us'] for base, cand in zip(baseline, candidate)]
                         for case in baseline[0]}
    print(f'Paired median speedup: {statistics.median(statistics.median(v) for v in record["speedup"].values()):.3f}x',
          flush=True)
    return record, True


def build_operator(work, variant, run, tools, digest):
    subprocess.run([sys.executable, '-B', str(BASE / 'prepare-candidate.py'), '--verify-only', '--toolchain',
                    '--variant', variant, str(work)], env=tools, timeout=30, check=True)
    build = work / 'build-vulkan'
    build_receipt = json.loads((work / 'build-vulkan-receipt.json').read_text())
    source_receipt = work / 'source-receipt.json'
    if (build_receipt['route'] != 'vulkan' or build_receipt['source_receipt_sha256'] != digest(source_receipt)
            or build_receipt['inputs_sha256'] != digest(BASE / 'inputs.json')
            or digest(build / 'bin/llama-bench') != build_receipt['outputs_sha256']['llama-bench']):
        raise RuntimeError('Operator workspace does not match its prepared/build receipts')
    src = work / 'source'
    source = src / 'tests/test-backend-ops.cpp'
    fixture = BASE / 'operator-shapes.patch'
    patched = run / source.name
    subprocess.run(['/usr/bin/patch', '--batch', '--fuzz=0', '-o', str(patched), str(source)],
                   input=fixture.read_bytes(), env=tools, timeout=15, check=True)
    libs = [build / 'ggml/src' / name for name in ('libggml.a', 'libggml-cpu.a',
                        'ggml-vulkan/libggml-vulkan.a', 'libggml-base.a')]
    binary = run / 'test-backend-ops'
    compile_cmd = ['/usr/bin/nice', '-n', '10', '/usr/bin/g++', '-std=gnu++17', '-O2',
                   f'-I{src}/ggml/include', str(patched), '-o', str(binary),
                   *map(str, libs), '-lgomp', '-pthread', '-ldl', '/usr/lib/libvulkan.so']
    subprocess.run(compile_cmd, env=tools, timeout=120, check=True)
    return binary, {p.name: digest(p) for p in (source_receipt, fixture, patched, *libs, binary)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path, help='Private Vulkan candidate workspace')
    parser.add_argument('variant', choices=('borealis', 'borealis-gemv'))
    parser.add_argument('compute_receipt', type=Path)
    parser.add_argument('icd', type=Path)
    parser.add_argument('validation_layer_dir', type=Path)
    parser.add_argument('--timing-peer', type=Path, help='Compare this borealis build against a private borealis-gemv workspace')
    args = parser.parse_args()
    work, receipt, icd, layer_dir = (p.resolve(strict=True) for p in
                                     (args.workspace, args.compute_receipt, args.icd, args.validation_layer_dir))
    if not work.is_relative_to(WORK):
        parser.error('Workspace must remain inside work/build')
    peer = args.timing_peer.resolve(strict=True) if args.timing_peer else None
    if peer and (peer == work or not peer.is_relative_to(WORK) or args.variant != 'borealis'):
        parser.error('Timing requires a borealis baseline and a separate private borealis-gemv workspace')
    guard = runpy.run_path(str(BASE / 'guard-vulkan.py'))
    admitted = guard['admit'](receipt, icd, 'Vulkan0')
    digest = guard['digest']
    lib = (icd.parent / json.loads(icd.read_text())['ICD']['library_path']).resolve(strict=True)
    layer = json.loads((layer_dir / 'VkLayer_khronos_validation.json').read_text())['layer']
    layer_lib = (layer_dir / layer['library_path']).resolve(strict=True)
    if layer['name'] != 'VK_LAYER_KHRONOS_validation' or layer_lib.parent != lib.parent:
        raise RuntimeError('Expected Khronos validation from the same private library directory')
    run = Path(tempfile.mkdtemp(prefix=f'operator-{args.variant}.', dir=work))
    print(f'Operator workspace: {run}', flush=True)
    home = run / 'home'
    home.mkdir()
    tools = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C'}
    binary, baseline_hashes = build_operator(work, args.variant, run, tools, digest)
    if peer:
        peer_run = run / 'peer'
        peer_run.mkdir()
        peer_binary, peer_hashes = build_operator(peer, 'borealis-gemv', peer_run, tools, digest)
    helpers = runpy.run_path(str(BASE / 'compare-candidate.py'))
    sensors = helpers['sensor_paths']()
    env = {**tools, 'VK_DRIVER_FILES': str(icd), 'LD_LIBRARY_PATH': str(lib.parent), 'ASAHI_M5_BRINGUP': '1',
           'VK_LAYER_PATH': str(layer_dir), 'VK_LOADER_LAYERS_DISABLE': '~implicit~',
           'VK_LOADER_LAYERS_ENABLE': 'VK_LAYER_KHRONOS_validation', 'VK_LOADER_DEBUG': 'layer',
           'TEST_ARG_HARDWARE_CONCURRENCY': '2', 'OMP_NUM_THREADS': '1'}
    report = {'variant': args.variant, 'boot_id': admitted['boot_id'], 'device_uuid': admitted['device_uuid'],
              'hashes': {**{p.name: digest(p) for p in (receipt, icd, lib, layer_lib)}, **baseline_hashes},
              'groups': {}}
    start = int(time.time())
    with (WORK / '.llama-compare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if peer:
            report['peer_hashes'] = peer_hashes
            report['timing'], passed = time_pair(run, {'baseline': binary, 'candidate': peer_binary}, env, sensors, helpers)
        else:
            for label, op, pattern in CASES:
                group = report['groups'][label] = run_group(binary, run, label, op, pattern, env, sensors, helpers)
                print(label, json.dumps(group), flush=True)
                if not group['validation_clean'] or group['exit_code'] not in (0, 1):
                    break
            passed = len(report['groups']) == len(CASES) and all(g['passed_gate'] for g in report['groups'].values())
    kernel = subprocess.check_output(['journalctl', '-b', '-k', '--since', f'@{start}', '-q', '--no-pager'], text=True)
    (run / 'kernel.log').write_text(kernel)
    report['kernel_fault'] = bool(re.search(r'GPU .*timed out|GPU.*fault|DART.*fault|GPU Recovery|device lost', kernel, re.I))
    report['passed'] = not report['kernel_fault'] and passed
    (run / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
