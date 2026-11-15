#!/usr/bin/env python3
"""Read-only J716s thermal evidence; never loads CPUs or writes fan targets.

Use --native as root to derive the CE map from the current bound read-only ADT.
Use --samples 60 alongside a separately authorized bounded workload. Output is
JSON evidence, not an automatic hardware qualification or manual-fan admission.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import stat
import struct
import time

PROJECT = Path(__file__).resolve().parents[1]
DT = Path('/sys/firmware/devicetree/base')
PARSER_SHA256 = 'cfef5935f35c412e006ae70229406b0eac51e830a175ca9b98fcdabe873389f5'


def derive_mapping(cpus, ce_map, ptd_ranges):
    """Reject another SKU, ambiguous lanes, or a different PTD geometry."""
    if len(ce_map) != 3 * 44 or len(ptd_ranges) % 32:
        raise ValueError('Incomplete native CE/PTD records')
    lanes = []
    for offset in range(0, len(ce_map), 44):
        words = struct.unpack_from('<7I', ce_map, offset)
        name = ce_map[offset + 28:offset + 44].rstrip(b'\0').decode('ascii')
        lanes.append((words[2], name))
    if lanes != [(0, 'MACC0'), (1, 'MACC1'), (2, 'PACC_ANE')]:
        raise ValueError('J716s native CE lanes differ')
    ranges = {}
    for offset in range(0, len(ptd_ranges), 32):
        rid, first, count, flags, name = struct.unpack_from('<4I16s', ptd_ranges, offset)
        name = name.rstrip(b'\0').decode('ascii')
        if name in ranges:
            raise ValueError('Duplicate native PTD range')
        ranges[name] = (rid, first, count, flags)
    if ranges.get('FAST-DIE-CE') != (16, 0x258, 2, 12):
        raise ValueError('Native fast-die report geometry differs')
    result = []
    if len(cpus) != 18:
        raise ValueError('Expected this unit\'s 18 CPU nodes')
    for cluster, name in lanes:
        members = sorted(cpu for cpu, cid, kind in cpus if cid == cluster)
        kind = b'apple,sawtooth' if cluster < 2 else b'apple,everest'
        if members != list(range(cluster * 6, cluster * 6 + 6)) or any(
                actual != kind for cpu, cid, actual in cpus if cid == cluster):
            raise ValueError('Native CPU membership differs')
        result.append({'lane': cluster, 'name': name, 'cpus': members})
    return result


def native_evidence():
    path = PROJECT / 'work/sources/kernel/tools/aurora-sep/aurora-adt-extract.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != PARSER_SHA256:
        raise ValueError('Pinned read-only ADT parser differs')
    parser = runpy.run_path(str(path), run_name='_thermal_adt')
    host = parser['Host']()
    raw = parser['read_device'](parser['check_binding'](None, host), host)
    root, used, _, _ = parser['parse_adt'](raw)
    nodes = {}
    for path, node in parser['walk'](root):
        if path in nodes:
            raise ValueError('Ambiguous or placeholder ADT node')
        props = {prop.name: prop.value for prop in node.props}
        if len(props) != len(node.props):
            raise ValueError('Duplicate ADT property')
        if (path.startswith('/cpus/cpu') or path == '/arm-io/pmp0/iop-pmp0-nub') and any(
                prop.placeholder for prop in node.props):
            raise ValueError('Placeholder native thermal property')
        nodes[path] = props
    cpus = []
    for path, props in nodes.items():
        if path.startswith('/cpus/cpu'):
            cpus.append((struct.unpack('<I', props['cpu-id'])[0],
                         struct.unpack('<I', props['cluster-id'])[0],
                         props['compatible'].split(b'\0')[0]))
    nub = nodes['/arm-io/pmp0/iop-pmp0-nub']
    return {'region_sha256': hashlib.sha256(raw).hexdigest(), 'used_bytes': used,
            'clusters': derive_mapping(cpus, nub['fast-die-ctrl-ce-map'], nub['ptd-range']),
            'manual_fan_semantics_proven': False}


def text(path):
    return path.read_text().strip()


def snapshot():
    hwmons = [p for p in Path('/sys/class/hwmon').glob('hwmon*')
              if text(p / 'name') == 'macsmc_hwmon']
    if len(hwmons) != 1:
        raise ValueError('Expected one macsmc hwmon')
    hwmon = hwmons[0]
    channels = {}
    for pattern in ('fan*', 'temp*_input', 'temp*_label', 'thermal_pressure_level'):
        for path in sorted(hwmon.glob(pattern)):
            channels[path.name] = {'value': text(path),
                                   'mode': oct(stat.S_IMODE(path.stat().st_mode))}
    policies = {}
    for path in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*')):
        policies[path.name] = {name: text(path / name) for name in
                              ('related_cpus', 'scaling_driver', 'scaling_cur_freq',
                               'scaling_min_freq', 'scaling_max_freq', 'cpuinfo_max_freq')}
    reports = {path.name: text(path) for path in
               Path('/sys/kernel/debug/apple-pmp-report').glob('*') if path.is_file()}
    return {'monotonic_seconds': time.monotonic(), 'hwmon': channels,
            'policies': policies, 'pmp_reports': reports,
            'taint': int(text(Path('/proc/sys/kernel/tainted'))),
            'fan_control': text(Path('/sys/module/macsmc_hwmon/parameters/fan_control'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, choices=range(1, 61), default=1)
    parser.add_argument('--native', action='store_true')
    args = parser.parse_args()
    if not {b'apple,j716s', b'apple,t6050'}.issubset((DT / 'compatible').read_bytes().split(b'\0')):
        raise ValueError('Not J716s/T6050')
    for fw, boot in (('asahi,os-fw-version', 'asahi,iboot2-version'),
                     ('asahi,system-fw-version', 'asahi,iboot1-version')):
        if ((DT / 'chosen' / fw).read_bytes() != b'27.3 beta1\0' or
                (DT / 'chosen' / boot).read_bytes() != b'mBoot-20457.60.65.0.1\0'):
            raise ValueError('Exact firmware pairing differs')
    report = {'kernel': text(Path('/proc/sys/kernel/osrelease')),
              'hardware_qualified': False, 'fan_writes_performed': False,
              'native': native_evidence() if args.native else None, 'samples': []}
    for index in range(args.samples):
        if index:
            time.sleep(1)
        report['samples'].append(snapshot())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
