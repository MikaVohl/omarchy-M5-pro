#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit-identity J716s arming and real-ESP Stage 1 preparation; default read-only."""
import argparse
import ctypes
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import zlib

# Imported publication helpers must not leave cache files during --check.
sys.dont_write_bytecode = True
import preflight
from partition_metadata import parts, validate_layout

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parent / 'boot-candidate'
PROJECT = HERE.parent.parent
KERNEL_REF = '8ab10100a4c1c944797e3fc8345589aff57777df'
BUILD = '26C5035r'
FIRMWARE = 'mBoot-20457.60.65.0.1'
AURA_REF = '54678354b70d64506408d9b157f85ac6065d194b'
RESCUE = CANDIDATE / 'j716s-rescue-qualified-kis.bin'
UUID_RE = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def uuid(value):
    require(isinstance(value, str) and UUID_RE.fullmatch(value), 'missing or malformed UUID: ' + str(value))
    return value.upper()


def digest(path, algorithm='sha256'):
    result = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def command(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, timeout=120)
    require(result.returncode == 0, 'command failed: ' + ' '.join(str(a) for a in args) + '\n' + result.stderr.decode(errors='replace'))
    return result.stdout


def diskinfo(target):
    return plistlib.loads(command('/usr/sbin/diskutil', 'info', '-plist', target))


def contained(root, relative, must_exist=True):
    path = root / relative
    require(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'unsafe relative path: ' + str(relative))
    # Reject symlinks in every component, not just escapes outside the volume.
    current = root
    for part in Path(relative).parts:
        current = current / part
        require(not current.is_symlink(), 'symlink target is not supported: ' + str(current))
    require(path.resolve().is_relative_to(root.resolve()), 'path escapes selected root')
    if must_exist:
        require(path.is_file(), 'required file missing: ' + str(path))
    return path


def mountpoint(info):
    value = info.get('MountPoint')
    require(isinstance(value, str) and value.startswith('/'), 'target must already be mounted: ' + info.get('DeviceIdentifier', '?'))
    path = Path(value)
    require(path.is_dir() and str(path.resolve()) == str(path), 'ambiguous or symlink mount point: ' + value)
    require(value != '/' and not value.startswith('/System/Volumes/'), 'running macOS mount is not a target: ' + value)
    actual = diskinfo(path)
    require(actual.get('DeviceIdentifier') == info.get('DeviceIdentifier') and actual.get('MountPoint') == value, 'mount point does not resolve to selected volume')
    return path


def source_disk(apfs, recorded):
    partuuid, container_uuid = uuid(recorded['partition_uuid']), uuid(recorded['container_uuid'])
    matches = [(c, p) for c in apfs['Containers'] for p in c.get('PhysicalStores', [])
               if uuid(p.get('DiskUUID')) == partuuid]
    require(len(matches) == 1, 'recorded retained macOS physical store missing or duplicated')
    container, store = matches[0]
    require(uuid(container.get('APFSContainerUUID')) == container_uuid and len(container['PhysicalStores']) == 1,
            'recorded retained source container changed')
    info = diskinfo(store['DeviceIdentifier'])
    require(uuid(info.get('DiskUUID')) == partuuid and info.get('Content') == 'Apple_APFS', 'source partition identity/type mismatch')
    whole = info['ParentWholeDisk']
    physical = diskinfo(whole)
    require(physical.get('Internal') is True and physical.get('SolidState') is True and physical.get('WholeDisk') is True,
            'source disk is not the recorded internal SSD')
    return whole, container


def mounted_esp(partuuid, whole):
    partuuid = uuid(partuuid)
    listing = plistlib.loads(command('/usr/sbin/diskutil', 'list', '-plist'))
    candidates = [p for d in listing['AllDisksAndPartitions'] for p in d.get('Partitions', [])
                  if p.get('DiskUUID', '').upper() == partuuid]
    require(len(candidates) == 1, 'ESP GPT GUID must identify exactly one physical partition')
    part = candidates[0]
    info = diskinfo(part['DeviceIdentifier'])
    require(uuid(info.get('DiskUUID')) == partuuid and info.get('ParentWholeDisk') == whole, 'ESP partition GUID/disk mismatch')
    require(part.get('Content') == info.get('Content') == 'EFI', 'ESP must have EFI GPT partition type, not FAT volume UUID')
    require('FAT' in str(info.get('FilesystemName', '')).upper() or str(info.get('FilesystemType', '')).lower() == 'msdos', 'ESP is not FAT')
    return info, mountpoint(info)


def load_tool(name):
    path = CANDIDATE / 'enrollment-tools' / name
    require(digest(path) == provenance()['tools_sha256'][name], 'deployment tool differs: ' + name)
    spec = importlib.util.spec_from_file_location(name.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def provenance():
    value = json.loads((HERE / 'finish-install-derivative.json').read_text())
    names = {'finish-install-j716s.sh', 'finish-install-j716s.py', 'prepare-stage1-j716s.sh',
             'finish-install-step2-j716s.sh', 'mount-targets-j716s.sh', 'prep-stub-j716s.py',
             'prep-stub-j716s.sh', 'preflight.py', 'partition_metadata.py', 'update-deployment-provenance.py'}
    require(value.get('schema') == 1 and set(value.get('files_sha256', {})) == names,
            'local deployment derivative provenance incomplete')
    require(all(digest(HERE / name) == expected for name, expected in value['files_sha256'].items()),
            'local deployment derivative provenance mismatch')
    require(value.get('source_commit') == '7974606ab1de0d41737f2c8784a7e86987612ce1', 'deployment source pin differs')
    require(digest(HERE / 'reference-source/finish-install.sh') == 'f548b0af4862927635b4d93694cb52c336219e4afad31d51fe6fb5775a733105',
            'pinned reference finish source changed')
    require(set(value.get('tools_sha256', {})) == {'fill_stage1_config.py', 'enroll-stage1.sh'}
            and all(digest(CANDIDATE / 'enrollment-tools' / name) == expected for name, expected in value['tools_sha256'].items()),
            'enrollment derivative tool provenance differs')
    return value


def artifact_record(path, record):
    require(path.is_file() and not path.is_symlink() and isinstance(record, dict)
            and digest(path) == record.get('sha256') and path.stat().st_size == record.get('bytes'),
            'fresh build artifact differs: ' + str(path))


def artifacts():
    provenance()
    loader_path = CANDIDATE / 'loader/build-manifest.json'
    loader = json.loads(loader_path.read_text())
    require(loader.get('schema') == 1 and loader.get('base_commit') == AURA_REF,
            'loader build source pin differs')
    patches = loader.get('patches', [])
    require(isinstance(patches, list) and [entry.get('path') for entry in patches] == [
        'work/aura-audit/j716s-26C5035r-integration.patch', 'patches/aura-j716s-fixed-clocks.patch',
        'patches/aura-j716s-kis-debug.patch'], 'qualified KIS/fixedclock loader patch provenance differs')
    for entry in patches:
        require(digest(contained(PROJECT, entry['path'])) == entry['sha256'], 'loader patch differs: ' + entry['path'])
    raw = CANDIDATE / 'loader/stage1/aura.bin'
    for stage in ('stage1', 'stage2'):
        records = loader.get('stages', {}).get(stage, {})
        require(isinstance(records, dict) and 'aura.bin' in records, 'loader build stage artifact missing')
        artifact_record(CANDIDATE / 'loader' / stage / 'aura.bin', records['aura.bin'])
    kernel_path = HERE.parent / 'kernel-candidate/build-manifest.json'
    kernel = json.loads(kernel_path.read_text())
    require(kernel.get('schema') == 1 and kernel.get('commit') == KERNEL_REF
            and kernel.get('kernel_release') == '7.3.0-j716s-8ab10100a4c1', 'kernel source/release pin differs')
    artifact_record(HERE.parent / 'kernel-candidate/Image.gz', kernel.get('artifacts', {}).get('Image.gz'))
    manifest = json.loads(RESCUE.with_suffix('.bin.json').read_text())
    require(manifest.get('schema') == 1 and manifest.get('kernel_build_manifest_sha256') == digest(kernel_path)
            and manifest.get('loader_build_manifest_sha256') == digest(loader_path), 'rescue selects different build manifests')
    artifact_record(RESCUE, {'sha256': manifest.get('sha256'), 'bytes': manifest.get('size')})
    return raw.read_bytes(), RESCUE


def payload_files(partuuid):
    _, stage2 = artifacts()
    manifest = json.loads(stage2.with_suffix('.bin.json').read_text())
    require(manifest.get('esp_partuuid') == uuid(partuuid).lower(), 'rescue bundle selects a different ESP')
    files = manifest.get('files', [])
    expected = {'aurora/rescue/Image.gz': 'outputs/kernel-candidate/Image.gz',
                'aurora/rescue/t6050-j716s.dtb': 'outputs/boot-candidate/t6050-j716s-kis02.dtb',
                'aurora/rescue/initramfs.cpio.gz': 'outputs/boot-candidate/j716s-rescue-initramfs.cpio.gz'}
    require(len(files) == 3 and {entry['path']: entry.get('source') for entry in files} == expected,
            'rescue dependency set/source mapping differs')
    for entry in files:
        path = contained(PROJECT, entry['source'])
        artifact_record(path, {'sha256': entry['sha256'], 'bytes': entry['size']})
    return PROJECT, files


def json_write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def prepare(args):
    target = target_receipt(args.stub_receipt)
    whole, _, _, _, _, _ = target_identity(target['system_volume_uuid'], target['stub_partition_uuid'], args.stub_receipt)
    generic, rescue = artifacts()
    fill = load_tool('fill_stage1_config.py')
    partuuid = uuid(args.esp_partuuid).lower()
    require(uuid(partuuid) == uuid(target['esp_partition_uuid']), 'selected ESP differs from fresh target receipt')
    require(fill.valid_path(args.stage2_path), 'invalid ESP-relative Stage 2 path')
    raw = fill.fill(generic, partuuid, args.stage2_path, 0)
    payload_files(partuuid)
    info = diskinfo(partuuid)
    require(uuid(info.get('DiskUUID')) == uuid(partuuid) and info.get('Content') == 'EFI'
            and info.get('ParentWholeDisk') == whole, 'ESP identity/type/disk mismatch')
    output = args.output_dir.absolute()
    require(not output.exists() and output.parent.is_dir() and str(output.parent.resolve()) == str(output.parent),
            'output directory must be new under an existing non-symlink workspace')
    require(not str(output).startswith('/Volumes/'), 'prepared workspace must not be on a mounted target')
    if args.mode == 'check':
        print('Read-only raw Stage 1 preparation check passed; no workspace or ESP writes.')
        return
    output.mkdir(mode=0o755)
    image = output / 'stage1-boot.bin'
    image.write_bytes(raw)
    receipt = {
        'schema': 2, 'kind': 'j716s-configured-stage1', 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'enrollment_format': 'raw', 'entry_point': 2048, 'lowest_virtual_address': 0,
        'aura_source_ref': AURA_REF, 'tools_sha256': provenance()['tools_sha256'],
        'generic_raw_sha256': hashlib.sha256(generic).hexdigest(),
        'esp_partuuid': partuuid, 'esp_device_at_preparation': info['DeviceIdentifier'], 'physical_disk': whole,
        'stage2_path': args.stage2_path, 'stage2_sha256': digest(rescue),
        'rescue_manifest_sha256': digest(rescue.with_suffix('.bin.json')),
        'loader_build_manifest_sha256': digest(CANDIDATE / 'loader/build-manifest.json'),
        'kernel_build_manifest_sha256': digest(HERE.parent / 'kernel-candidate/build-manifest.json'),
        'target_receipt_sha256': digest(args.stub_receipt),
        'raw_file': image.name, 'raw_sha256': digest(image), 'image_file': image.name,
        'image_sha256': digest(image), 'image_md5': digest(image, 'md5'), 'image_size': image.stat().st_size,
        'config_version': 1, 'window_ms': 0, 'helper_sha256': digest(Path(__file__)),
        'derivative_sha256': digest(HERE / 'finish-install-derivative.json'),
        'status': 'prepared locally; no ESP, policy or enrollment changes',
    }
    json_write(output / 'receipt.json', receipt)
    print('Prepared raw Stage 1 and receipt: ' + str(image))


def image_receipt(args, whole):
    generic, rescue = artifacts()
    receipt_path = args.receipt.resolve()
    receipt = json.loads(receipt_path.read_text())
    require(receipt.get('schema') == 2 and receipt.get('kind') == 'j716s-configured-stage1', 'wrong Stage 1 receipt schema')
    require(receipt.get('tools_sha256') == provenance()['tools_sha256'] and receipt.get('aura_source_ref') == AURA_REF
            and receipt.get('generic_raw_sha256') == hashlib.sha256(generic).hexdigest(), 'receipt source/tool provenance mismatch')
    require(receipt.get('helper_sha256') == digest(Path(__file__)), 'preparation helper changed since receipt; regenerate in new workspace')
    require(receipt.get('derivative_sha256') == digest(HERE / 'finish-install-derivative.json'), 'deployment derivative changed since preparation receipt')
    require(receipt.get('window_ms') == 0 and receipt.get('config_version') == 1, 'normal rescue config must be version 1 with zero interception window')
    require(receipt.get('stage2_sha256') == digest(rescue)
            and receipt.get('rescue_manifest_sha256') == digest(rescue.with_suffix('.bin.json'))
            and receipt.get('loader_build_manifest_sha256') == digest(CANDIDATE / 'loader/build-manifest.json')
            and receipt.get('kernel_build_manifest_sha256') == digest(HERE.parent / 'kernel-candidate/build-manifest.json')
            and receipt.get('target_receipt_sha256') == digest(args.stub_receipt), 'receipt target/build/rescue provenance mismatch')
    fill = load_tool('fill_stage1_config.py')
    partuuid = receipt.get('esp_partuuid')
    require(uuid(partuuid).lower() == partuuid, 'ESP GUID must be canonical lowercase')
    configured = fill.fill(generic, partuuid, receipt['stage2_path'], 0)
    raw_path = contained(receipt_path.parent, receipt['raw_file'])
    require(raw_path.read_bytes() == configured and digest(raw_path) == receipt.get('raw_sha256'), 'filled raw image does not derive from pinned Aura/config')
    offset = configured.index(fill.MAGIC) + len(fill.MAGIC)
    body = configured[offset:offset + fill.BODY.size]
    require(int.from_bytes(configured[offset + fill.BODY.size:offset + fill.BODY.size + 4], 'little') == zlib.crc32(body), 'configuration CRC mismatch')
    image = args.image.resolve()
    require(image == contained(receipt_path.parent, receipt['image_file']).resolve(), 'image does not match receipt filename')
    require(receipt.get('enrollment_format') == 'raw' and receipt.get('entry_point') == 2048
            and receipt.get('lowest_virtual_address') == 0, 'receipt must select raw enrollment')
    require(image.stat().st_size == len(configured) == receipt['image_size']
            and image.read_bytes() == configured and digest(image) == receipt.get('image_sha256'),
            'raw image/hash/configuration mismatch')
    require(digest(image, 'md5') == receipt.get('image_md5'), 'image MD5 receipt mismatch')
    payload_files(partuuid)
    esp_info, esp = mounted_esp(partuuid, whole)
    stage2 = contained(esp, receipt['stage2_path'], must_exist=False)
    if stage2.exists():
        require(digest(stage2) == receipt['stage2_sha256'], 'ESP active payload differs from prepared qualified rescue')
    return receipt, esp_info, stage2


def target_receipt(value):
    receipt = value if isinstance(value, dict) else json.loads(Path(value).read_text())
    require(receipt.get('schema') == 2 and isinstance(receipt.get('source'), dict)
            and isinstance(receipt.get('baseline_partitions'), dict), 'fresh target receipt schema 2 required')
    require(uuid(receipt.get('source_partition_uuid')) == uuid(receipt['source']['partition_uuid'])
            and uuid(receipt.get('source_volume_group_uuid')) == uuid(receipt['source']['booted_volume_group']),
            'receipt source identities disagree')
    require(receipt.get('source_build') == BUILD and receipt.get('source_firmware') == {
        'system': FIRMWARE, 'booted_pairing': FIRMWARE}, 'receipt lacks exact source build/firmware')
    require(receipt.get('backup_confirmation', {}).get('statement') ==
            'current independently recoverable backup verified by operator', 'prep backup confirmation missing')
    return receipt


def target_identity(system_uuid, stub_partuuid, source_receipt):
    receipt = target_receipt(source_receipt)
    system_uuid, stub_partuuid = uuid(system_uuid), uuid(stub_partuuid)
    require(system_uuid == uuid(receipt['system_volume_uuid']) and stub_partuuid == uuid(receipt['stub_partition_uuid']),
            'explicit target identities differ from fresh receipt')
    require(stub_partuuid != uuid(receipt['source']['partition_uuid']), 'retained macOS partition cannot be a stub')
    preflight.revalidate_source(receipt)
    apfs = plistlib.loads(command('/usr/sbin/diskutil', 'apfs', 'list', '-plist'))
    whole, source = source_disk(apfs, receipt['source'])
    matches = [(c, v) for c in apfs['Containers'] for v in c.get('Volumes', []) if uuid(v.get('APFSVolumeUUID')) == system_uuid]
    require(len(matches) == 1, 'System UUID does not uniquely select one volume')
    container, system = matches[0]
    require(container['ContainerReference'] != source['ContainerReference'], 'selected stub is retained macOS container')
    stores = container.get('PhysicalStores', [])
    require(len(stores) == 1 and uuid(stores[0].get('DiskUUID')) == stub_partuuid, 'stub physical store UUID mismatch or multiple stores')
    listing = plistlib.loads(command('/usr/sbin/diskutil', 'list', '-plist'))
    validate_layout(receipt, parts(listing, whole))
    partitions = [p for d in listing['AllDisksAndPartitions'] for p in d.get('Partitions', []) if str(p.get('DiskUUID', '')).upper() == stub_partuuid]
    require(len(partitions) == 1 and partitions[0].get('DeviceIdentifier') == stores[0]['DeviceIdentifier'] and partitions[0].get('Content') == 'Apple_APFS', 'stub physical partition UUID is duplicated or inconsistent')
    part = diskinfo(stores[0]['DeviceIdentifier'])
    require(uuid(part.get('DiskUUID')) == stub_partuuid and part.get('ParentWholeDisk') == whole and part.get('Content') == 'Apple_APFS', 'stub must be separate ordinary APFS partition on source internal disk; ISC/Recovery forbidden')
    require(abs(part.get('TotalSize', 0) - 6_000_000_000) <= 16 * 1024**2 and abs(container.get('CapacityCeiling', 0) - 6_000_000_000) <= 16 * 1024**2, 'stub must be approximately decimal 6 GB (16 MiB alignment tolerance)')
    root = diskinfo('/')
    require(root.get('APFSContainerReference') != container['ContainerReference'], 'stub contains running root installation')
    volumes = container.get('Volumes', [])
    require(len(volumes) == 4 and sorted(tuple(v.get('Roles', [])) for v in volumes) == [('Data',), ('Preboot',), ('Recovery',), ('System',)], 'stub requires exactly one each System/Data/Preboot/Recovery with no extra roles')
    require(len({v['DeviceIdentifier'] for v in volumes}) == 4 and len({uuid(v.get('APFSVolumeUUID')) for v in volumes}) == 4, 'stub volume device/UUID identities are duplicated')
    require(system.get('Roles') == ['System'], '--system-uuid is not System role')
    infos = {v['Roles'][0]: diskinfo(v['DeviceIdentifier']) for v in volumes}
    mounts = {}
    for role, info in infos.items():
        require(info.get('APFSContainerReference') == container['ContainerReference'] and uuid(info.get('VolumeUUID')) == uuid(next(v['APFSVolumeUUID'] for v in volumes if v['Roles'] == [role])), 'volume info/container UUID mismatch')
        mounts[role] = mountpoint(info)
    require(len(set(mounts.values())) == 4 and all(not a.is_relative_to(b) for a in mounts.values() for b in mounts.values() if a != b), 'overlapping or duplicate target mounts')
    group = uuid(infos['System'].get('APFSVolumeGroupID'))
    require(group == uuid(receipt['volume_group_uuid'])
            and uuid(container['APFSContainerUUID']) == uuid(receipt['container_uuid'])
            and set(receipt['volume_uuids']) == set(infos)
            and all(uuid(receipt['volume_uuids'][role]) == uuid(info['VolumeUUID']) for role, info in infos.items()),
            'live target container/group/role UUIDs differ from receipt')
    require(group == uuid(infos['Data'].get('APFSVolumeGroupID')) and group != str(root.get('APFSVolumeGroupID', '')).upper(), 'System/Data group missing, mismatched or running group')
    grouped = []
    for c in apfs['Containers']:
        for v in c.get('Volumes', []):
            if v.get('Roles') in (['System'], ['Data']):
                info = infos[v['Roles'][0]] if c['ContainerReference'] == container['ContainerReference'] else diskinfo(v['DeviceIdentifier'])
                if str(info.get('APFSVolumeGroupID', '')).upper() == group:
                    grouped.append(info['DeviceIdentifier'])
    require(sorted(grouped) == sorted([infos['System']['DeviceIdentifier'], infos['Data']['DeviceIdentifier']]), 'volume group is not uniquely the selected System/Data pair')
    return whole, source, container, infos, mounts, group


def recorded_link(root, relative, target):
    """A receipt-recorded relative symlink: exact target, non-symlink parents, resolving inside its group directory."""
    parts = Path(relative).parts
    require(len(parts) >= 2 and target and not target.startswith('/') and '\n' not in target, 'invalid recorded symlink: ' + relative)
    parent = contained(root, str(Path(*parts[:-1])), must_exist=False)
    require(parent.is_dir(), 'recorded symlink parent missing: ' + relative)
    path = parent / parts[-1]
    require(path.is_symlink() and os.readlink(path) == target, 'recorded symlink differs: ' + str(path))
    group_root = (root / parts[0]).resolve()
    resolved = (parent / target).resolve()
    require(resolved.exists() and resolved.is_relative_to(group_root), 'recorded symlink escapes its group: ' + str(path))
    return target


BOOT_SET_RE = re.compile(r'[0-9A-F]{96}\Z')
APTICKET_RE = re.compile(r'apticket\.j716sap\.[0-9A-F]+\.im4m\Z')


def active_boot_set(recovery_root, group):
    """boot/active is rewritten by bless; it must name an existing SHA-384 boot-set directory."""
    active = contained(recovery_root, f'{group}/boot/active').read_text().strip()
    require(BOOT_SET_RE.fullmatch(active), 'Recovery boot/active is not a boot-set hash')
    target = recovery_root / group / 'boot' / active
    require(target.is_dir() and not target.is_symlink(), 'Recovery boot/active names a missing boot set')
    return active


def stub_assets(args, source, container, infos, mounts, group):
    receipt = target_receipt(args.stub_receipt)
    for key, expected in [('stub_partition_uuid', uuid(args.stub_partuuid)), ('system_volume_uuid', uuid(args.system_uuid)), ('volume_group_uuid', group)]:
        require(uuid(receipt.get(key)) == expected, 'stub receipt identity mismatch: ' + key)
    require(uuid(receipt.get('container_uuid')) == uuid(container.get('APFSContainerUUID')), 'stub receipt container UUID mismatch')
    require(uuid(receipt['source']['container_uuid']) == uuid(source['APFSContainerUUID']) and receipt.get('source_build') == BUILD,
            'stub receipt retained source container/build mismatch')
    require(receipt.get('source_firmware') == {'system': FIRMWARE, 'booted_pairing': FIRMWARE}, 'prep receipt lacks exact candidate source firmware pairing')
    require(isinstance(receipt.get('volume_uuids'), dict) and set(receipt['volume_uuids']) == set(infos), 'stub receipt volume role identities incomplete')
    require(all(uuid(receipt['volume_uuids'][role]) == uuid(info.get('VolumeUUID')) for role, info in infos.items()), 'stub receipt volume UUID mismatch')
    source_group = uuid(receipt.get('source_volume_group_uuid'))
    require(source_group != group, 'stub source/target group collide')
    source_systems = [diskinfo(v['DeviceIdentifier']) for v in source['Volumes'] if v.get('Roles') == ['System']]
    require(sum(str(v.get('APFSVolumeGroupID', '')).upper() == source_group for v in source_systems) == 1, 'receipt source volume group not unique in retained macOS container')
    evidence = receipt.get('copy_sha256')
    require(isinstance(evidence, dict) and evidence, 'stub receipt requires copy_sha256 target-path mapping')
    mandatory = [f'Preboot/{group}/restore/BuildManifest.plist', f'Preboot/{group}/var/db/AdminUserRecoveryInfo.plist', f'Recovery/{group}/usr/standalone/firmware/arm64eBaseSystem.dmg']
    require(all(key in evidence and not evidence[key].startswith('symlink:') for key in mandatory), 'stub receipt lacks manifest/admin/paired Recovery hashes')
    links = set()
    # bless --setBoot (during arming) and later boot-policy tools add new boot/<SHA-384> sets to the stub's
    # Recovery group and repoint boot/active. Those are system-managed; copied files stay hash-bound.
    active_key = f'Recovery/{group}/boot/active'
    copied_sets = {Path(k.split('/', 1)[1]).parts[2] for k in evidence
                   if k.startswith(f'Recovery/{group}/boot/') and len(Path(k.split('/', 1)[1]).parts) > 3}
    for key, expected in evidence.items():
        role, sep, relative = key.partition('/')
        require(sep and role in ('System', 'Data', 'Preboot', 'Recovery'), 'invalid stub copy hash entry')
        if key == active_key:
            active_boot_set(mounts['Recovery'], group)
        elif expected.startswith('symlink:'):
            recorded_link(mounts[role], relative, expected[len('symlink:'):])
            links.add(key)
        else:
            require(re.fullmatch('[0-9a-f]{64}', expected), 'invalid stub copy hash entry')
            require(digest(contained(mounts[role], relative)) == expected, 'stub copied asset hash differs: ' + key)
    restore = mounts['Preboot'] / group / 'restore'
    require(restore.is_dir() and any(p.is_file() and p.name != 'BuildManifest.plist' for p in restore.rglob('*')), 'restore bundle is incomplete')
    # Every paired restore/Recovery file and symlink must be recorded, except inside system-managed new boot sets.
    for role, tree in [('Preboot', restore), ('Recovery', mounts['Recovery'] / group)]:
        for directory, dirnames, filenames in os.walk(tree, followlinks=False):
            for name in dirnames + filenames:
                path = Path(directory) / name
                key = role + '/' + str(path.relative_to(mounts[role]))
                parts = path.relative_to(mounts[role]).parts
                if (role == 'Recovery' and len(parts) > 3 and parts[1] == 'boot' and BOOT_SET_RE.fullmatch(parts[2])
                        and parts[2] not in copied_sets):
                    continue
                # bless writes the personalization ticket beside the copied restore bundle.
                if role == 'Preboot' and len(parts) == 3 and parts[1] == 'restore' and APTICKET_RE.fullmatch(parts[2]):
                    require(path.is_file() and not path.is_symlink(), 'APTicket is not a regular file: ' + str(path))
                    continue
                if path.is_symlink():
                    require(key in links, 'unrecorded symlink in paired restore/Recovery assets: ' + str(path))
                elif name in filenames:
                    require(path.is_file() and key in evidence and key not in links, 'unrecorded paired asset: ' + str(path))
    manifest = plistlib.loads(contained(mounts['Preboot'], f'{group}/restore/BuildManifest.plist').read_bytes())
    require(preflight.manifest_build(manifest) == BUILD, 'J716sap restore identities do not uniquely name ' + BUILD)
    admin = plistlib.loads(contained(mounts['Preboot'], f'{group}/var/db/AdminUserRecoveryInfo.plist').read_bytes())
    require(isinstance(admin, dict) and admin and all(isinstance(k, str) and k for k in admin), 'invalid/empty admin recovery identities')
    require(args.admin_user in admin if args.admin_user else os.environ.get('SUDO_USER') in admin, 'select a recorded owner with --admin-user (no first-user fallback)')
    return receipt, args.admin_user or os.environ['SUDO_USER']


def set_bootable(system):
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.fsctl.restype = ctypes.c_int
    libc.fsctl.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
    flag = ctypes.c_int(1)
    require(libc.fsctl(os.fsencode(system), 0xc0044a57, ctypes.byref(flag), 0) == 0, 'APFS bootable flag failed: errno ' + str(ctypes.get_errno()))
    flag.value = 0
    require(libc.fsctl(os.fsencode(system), 0xc0044a57, ctypes.byref(flag), 0) == 0 and flag.value == 1, 'APFS bootable flag readback failed')


def finish(args):
    whole, source, container, infos, mounts, group = target_identity(args.system_uuid, args.stub_partuuid, args.stub_receipt)
    stub_receipt, admin = stub_assets(args, source, container, infos, mounts, group)
    receipt, esp_info, stage2 = image_receipt(args, whole)
    require(uuid(stub_receipt.get('esp_partition_uuid')) == uuid(receipt['esp_partuuid']), 'stub receipt and configured Stage 1 select different ESP GUIDs')
    system = mounts['System']
    cs = system / 'System/Library/CoreServices'
    visible, hidden = cs / 'SystemVersion.plist', cs / 'SystemVersion-disabled.plist'
    require(visible.is_file() != hidden.is_file(), 'exactly one visible/disabled SystemVersion.plist required')
    version = plistlib.loads((visible if visible.is_file() else hidden).read_bytes())
    require(version.get('ProductBuildVersion') == BUILD, 'stub SystemVersion differs from candidate build')
    app = contained(system, 'Finish Installation.app', must_exist=False)
    require(not app.exists() and visible.is_file() and not hidden.exists()
            and not (system / '.IAPhysicalMedia').exists() and not (system / 'IAPhysicalMedia-disabled.plist').exists(),
            'requires a fresh populated, never-armed stub; no replacement/rebind path')
    bundle_root, dependencies = payload_files(receipt['esp_partuuid'])
    esp = mountpoint(esp_info)
    for entry in dependencies:
        destination = contained(esp, entry['path'], must_exist=False)
        require(not destination.exists() or digest(destination) == entry['sha256'], 'existing rescue dependency differs: ' + entry['path'])
    pending = contained(esp, receipt['stage2_path'] + '.new', must_exist=False)
    require(not pending.exists(), 'unreviewed pending payload exists')
    previous_stage2 = digest(stage2) if stage2.exists() else None
    for path in [visible, hidden, system / '.IAPhysicalMedia', system / 'IAPhysicalMedia-disabled.plist', mounts['Data'] / 'private/var/db/dslocal']:
        contained(system if path.is_relative_to(system) else mounts['Data'], str(path.relative_to(system if path.is_relative_to(system) else mounts['Data'])), must_exist=False)
    template = HERE / 'finish-install-step2-j716s.sh'
    derivative = provenance()
    require(derivative['files_sha256'][template.name] == digest(template), 'local derivative template differs')
    print(json.dumps({'mode': args.mode, 'system_uuid': uuid(args.system_uuid), 'stub_partuuid': uuid(args.stub_partuuid), 'volume_group_uuid': group, 'mounts': {k: str(v) for k, v in mounts.items()}, 'esp_partuuid': receipt['esp_partuuid'], 'image_sha256': receipt['image_sha256'], 'admin_user': admin}, indent=2))
    if args.mode == 'check':
        print('Read-only check complete. No target writes, mounts, bless, boot policy or enrollment commands were run.')
        return
    require(os.geteuid() == 0, '--apply requires sudo')
    require(esp_info.get('WritableVolume') is True, 'ESP is not writable')
    preflight.confirm_backup()
    # Re-resolve identities just before the first target write, never name-based discovery.
    require(target_identity(args.system_uuid, args.stub_partuuid, args.stub_receipt)[4] == mounts, 'target identities/mounts changed')
    command('/sbin/mount', '-u', '-w', system)
    require(not app.exists(), 'launcher appeared during owner confirmation')
    # Publish dependencies first and the active payload last; never change GPT or root data.
    for entry in dependencies:
        destination = contained(esp, entry['path'], must_exist=False)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(contained(bundle_root, entry['source']).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        require(digest(destination) == entry['sha256'], 'ESP dependency readback differs')
    _, candidate = artifacts()
    require(not pending.exists(), 'pending payload appeared during preparation')
    pending.parent.mkdir(parents=True, exist_ok=True)
    with pending.open('xb') as stream:
        stream.write(candidate.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    require(digest(pending) == receipt['stage2_sha256'], 'ESP payload readback differs')
    require((digest(stage2) if stage2.exists() else None) == previous_stage2, 'active payload changed before activation')
    os.replace(pending, stage2)
    resources = app / 'Contents/Resources'
    (app / 'Contents/MacOS').mkdir(parents=True)
    (resources / 'en.lproj').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName': 'Finish Installation', 'CFBundleExecutable': 'step2_launcher.sh'}))
    (resources / 'en.lproj/InfoPlist.strings').write_text('"CFBundleDisplayName" = "Finish Installation";\n')
    launcher = app / 'Contents/MacOS/step2_launcher.sh'
    launcher.write_text('#!/bin/sh\nexec /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal "${0%/*}/../Resources/step2.sh"\n')
    launcher.chmod(0o755)
    shutil.copyfile(template, resources / 'step2.sh')
    (resources / 'step2.sh').chmod(0o755)
    shutil.copyfile(HERE / 'mount-targets-j716s.sh', resources / 'mount-targets.sh')
    (resources / 'mount-targets.sh').chmod(0o755)
    shutil.copyfile(args.image.resolve(), resources / 'boot.bin')
    shutil.copyfile(CANDIDATE / 'enrollment-tools/enroll-stage1.sh', resources / 'enroll-stage1.sh')
    require(digest(resources / 'boot.bin') == receipt['image_sha256']
            and digest(resources / 'enroll-stage1.sh') == receipt['tools_sha256']['enroll-stage1.sh'], 'copied enrollment resources differ')
    shutil.copyfile(args.receipt, resources / 'stage1-receipt.json')
    shutil.copyfile(args.stub_receipt, resources / 'stub-receipt.json')
    shutil.copyfile(HERE / 'finish-install-derivative.json', resources / 'finish-install-derivative.json')
    assets = []
    for key in sorted(stub_receipt['copy_sha256']):
        role, relative = key.split('/', 1)
        recorded = stub_receipt['copy_sha256'][key]
        if recorded.startswith('symlink:'):
            assets.append({'role': role, 'path': relative, 'link': recorded_link(mounts[role], relative, recorded[len('symlink:'):])})
        else:
            assets.append({'role': role, 'path': relative, 'md5': digest(contained(mounts[role], relative), 'md5')})
    (resources / 'assets.plist').write_bytes(plistlib.dumps(assets))
    # Shell-readable binding uses only UUIDs/digests/integers and a validated ASCII relative path.
    binding = {
        'SYSTEM_UUID': uuid(args.system_uuid), 'STUB_PARTUUID': uuid(args.stub_partuuid), 'GROUP': group,
        'SOURCE_PARTUUID': uuid(stub_receipt['source']['partition_uuid']),
        'SOURCE_CONTAINER_UUID': uuid(stub_receipt['source']['container_uuid']),
        'SOURCE_GROUP': uuid(stub_receipt['source']['booted_volume_group']),
        'SOURCE_SYSTEM_UUID': uuid(stub_receipt['source']['system_volume_uuid']),
        'SOURCE_DATA_UUID': uuid(stub_receipt['source']['data_volume_uuid']),
        'SOURCE_PREBOOT_UUID': uuid(stub_receipt['source']['preboot_volume_uuid']),
        'SOURCE_RECOVERY_UUID': uuid(stub_receipt['source']['recovery_volume_uuid']),
        'STUB_CONTAINER_UUID': uuid(stub_receipt['container_uuid']),
        'PARTITIONS': [{'uuid': guid, 'content': p['Content'], 'size': p['Size']} for guid, p in
                       parts(plistlib.loads(command('/usr/sbin/diskutil', 'list', '-plist', whole)), whole).items()],
        'ESP_PARTUUID': uuid(receipt['esp_partuuid']), 'STAGE2_PATH': receipt['stage2_path'],
        'BOOT_MD5': receipt['image_md5'], 'STAGE2_MD5': digest(stage2, 'md5'),
        'ENROLL_MD5': digest(resources / 'enroll-stage1.sh', 'md5'), 'STEP2_MD5': digest(resources / 'step2.sh', 'md5'),
        'MOUNT_MD5': digest(resources / 'mount-targets.sh', 'md5'),
        'STAGE1_RECEIPT_MD5': digest(resources / 'stage1-receipt.json', 'md5'), 'STUB_RECEIPT_MD5': digest(resources / 'stub-receipt.json', 'md5'),
        'PROVENANCE_MD5': digest(resources / 'finish-install-derivative.json', 'md5'),
        'ASSETS_MD5': digest(resources / 'assets.plist', 'md5'),
        'PAYLOAD_FILES': [{'path': entry['path'], 'md5': digest(contained(esp, entry['path']), 'md5')} for entry in dependencies],
        'DATA_UUID': uuid(infos['Data'].get('VolumeUUID')), 'PREBOOT_UUID': uuid(infos['Preboot'].get('VolumeUUID')), 'RECOVERY_UUID': uuid(infos['Recovery'].get('VolumeUUID')),
        'ADMIN_MD5': digest(mounts['Preboot'] / group / 'var/db/AdminUserRecoveryInfo.plist', 'md5'),
        'MANIFEST_MD5': digest(mounts['Preboot'] / group / 'restore/BuildManifest.plist', 'md5'),
        'BASESYSTEM_MD5': digest(mounts['Recovery'] / group / 'usr/standalone/firmware/arm64eBaseSystem.dmg', 'md5'),
    }
    # Values are data, never sourced shell code. Recovery reads this plist with plutil.
    (resources / 'target.plist').write_bytes(plistlib.dumps(binding))
    (mounts['Data'] / 'private/var/db/dslocal').mkdir(parents=True, exist_ok=True)
    set_bootable(system)
    # Reference bless mechanism: visible SystemVersion, no media marker during bless.
    if hidden.exists():
        hidden.rename(visible)
    result = subprocess.run(['/usr/sbin/bless', '--setBoot', '--device', '/dev/' + infos['System']['DeviceIdentifier'], '--user', admin])
    require(result.returncode == 0, 'bless failed; no enrollment or media arming performed')
    visible.rename(hidden)
    marker = system / '.IAPhysicalMedia'
    marker.write_bytes(plistlib.dumps({'AppName': 'Finish Installation.app', 'ProductBuildVersion': BUILD, 'ProductVersion': version.get('ProductVersion', '27.3')}))
    print('Install-media launcher armed. No enrollment/reboot performed. Enter the selected stub paired 1TR only when separately authorized; step2 defaults to --check.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='No --stub NAME, --undo, implicit discovery, mount or resume. Invalid identity/hash refuses before target writes. Current preparation does not authorize --apply.')
    commands = parser.add_subparsers(dest='operation', required=True)
    finish_parser = commands.add_parser('finish', help='validate/arm a specific fresh stub; never enroll on this host')
    finish_parser.add_argument('--system-uuid', required=True, help='actual APFS System volume UUID, not group/name/device')
    finish_parser.add_argument('--stub-partuuid', required=True, help='actual GPT physical partition UUID for separate ~6 GB stub')
    finish_parser.add_argument('--stub-receipt', type=Path, required=True, help='prep target.json with identities and copy_sha256 mapping')
    finish_parser.add_argument('--image', type=Path, required=True, help='final configured raw Stage 1')
    finish_parser.add_argument('--receipt', type=Path, required=True, help='prepare helper receipt.json adjacent to raw/image')
    finish_parser.add_argument('--admin-user', help='exact recorded owner username; default SUDO_USER, never first user')
    prep_parser = commands.add_parser('prepare', help='validate/fill raw Stage 1 in a local workspace; no ESP or enrollment writes')
    prep_parser.add_argument('--stub-receipt', type=Path, required=True, help='fresh prep target.json binding source, baseline and created ESP')
    prep_parser.add_argument('--esp-partuuid', required=True, help='actual GPT ESP GUID, not FAT volume UUID')
    prep_parser.add_argument('--stage2-path', required=True, help='printable ASCII relative FAT path; use aurora/boot.bin for persistent deployment')
    prep_parser.add_argument('--output-dir', type=Path, required=True, help='new local workspace directory')
    for sub in [finish_parser, prep_parser]:
        modes = sub.add_mutually_exclusive_group()
        modes.add_argument('--check', action='store_const', const='check', dest='mode', help='default read-only; creates no reports')
        modes.add_argument('--apply', action='store_const', const='apply', dest='mode', help='prepare: local workspace only; finish: explicit sudo-only target writes')
        sub.set_defaults(mode='check')
    args = parser.parse_args()
    try:
        (finish if args.operation == 'finish' else prepare)(args)
    except (ValueError, KeyError, OSError, TypeError, subprocess.TimeoutExpired, plistlib.InvalidFileException) as error:
        print('REFUSED: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
