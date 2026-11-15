#!/usr/bin/env python3
"""Acquire only the exact-build native sources used to justify standard SCO.

On this unit's native macOS (no Bluetooth activity required):
  python3 -B prepare-bluetooth-native.py --unit-serial-sha256 LINUX_UNIT_SHA256 \
      --output /private/tmp/j716s-beta-native

For retained read-only APFS extractions pass --system-root SYSTEM_ROOT and
--cryptex-root CRYPTEX_ROOT and an externally anchored --platform-plist from
the same unit's paired-macOS acquisition. Neither filesystem is mounted or
modified here. Native mode verifies live IORegistry, model and System build;
retained mode also requires the independently measured Linux unit identity.
The source bundle contains the Beta launcher/identity, paired SystemVersion,
the t2026-CentauriBeta IPC profile, bluetoothd, the small DriverKit cache and
its symbols, and only Airship's inspected 611944-byte __TEXT segment from the
macOS cache. It does NOT copy macOS's multi-gigabyte caches wholesale.
All protocol source hashes are pinned; mismatched builds/bytes refuse.
No logs, audio, calibration, pairing database or user data are read.

The kernelcollection's AppleCentauriManager and the dext's DummySymbol launcher
do not implement HCI transport. Exact bluetoothd supplies SCO framing and
attribute parsing; the profile supplies rings/stride; Airship supplies descriptor
completion/ownership semantics. These inputs justify the standard-header source
patch, not a microphone qualification. Private four-byte HDR SCO is unsupported.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import platform

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = 'com.apple.driver.AppleCentauriBeta'
BUILD = '26C5035r'
MAX_EXECUTABLE = 128 * 1024 * 1024
PROFILE_PATH = 'System/DriverKit/System/Library/PrivateFrameworks/AirshipDK.framework/t2026-CentauriBeta.plist'
DRIVERKIT_CACHE = 'System/DriverKit/System/Library/dyld/dyld_shared_cache_arm64e'
AIRSHIP_CACHE = 'System/Library/dyld/dyld_shared_cache_arm64e.52'
AIRSHIP_OFFSET, AIRSHIP_SIZE = 0x13e5000, 611944
SOURCE_HASHES = {
    't2026-CentauriBeta.plist': 'b4b0736c3f2151b73d735d03fc1cfcfd1d52295954b6f9df7e0c464407766943',
    'bluetoothd.macho': 'eb0d4a8566365f95df5b6bc42537cef2cc1cec785834e507a55df9930c0dd539',
    'driverkit-cache': '56fce56ce213e521e00cbfaa9e02ac0d9cd171b282a3f23fc7081c827f606b78',
    'driverkit-cache.symbols': '74e52bd3875a054877cbf5bb77c739ec6be8f862b519028b3da2ca838f587fac',
    'Airship-TEXT.bin': '55b8f5a26b661dd04d4d035018a4c84feaaf1fa95b8733961d24c722e9232c29',
}


class Refused(ValueError):
    pass


def bounded_file(path, root, maximum):
    path = path.resolve(strict=True)
    if root != path and root not in path.parents:
        raise Refused('system file resolves outside selected System volume')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise Refused('system input is not a bounded regular file')
        with os.fdopen(fd, 'rb', closefd=False) as file:
            data = file.read(maximum + 1)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
            raise Refused('system input changed during acquisition')
        return data
    finally:
        os.close(fd)


def bounded_span(path, root, offset, size):
    path = path.resolve(strict=True)
    if root not in path.parents:
        raise Refused('cache resolves outside selected Cryptex')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or offset < 0 or size <= 0 or offset + size > before.st_size:
            raise Refused('inspected Airship segment is outside native cache')
        data = os.pread(fd, size, offset)
        after = os.fstat(fd)
        if len(data) != size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Refused('native cache changed during bounded acquisition')
        return data
    finally:
        os.close(fd)


def acquire(root, cryptex=None):
    root = Path(root).resolve(strict=True)
    version = bounded_file(root / 'System/Library/CoreServices/SystemVersion.plist', root, 1024 * 1024)
    if plistlib.loads(version).get('ProductBuildVersion') != BUILD:
        raise Refused('selected System volume is not macOS build 26C5035r')
    directory = root / 'System/Library/DriverExtensions'
    matches = []
    for bundle in sorted(directory.glob('*.dext')):
        contents = bundle / 'Contents'
        info_path = contents / 'Info.plist' if contents.is_dir() else bundle / 'Info.plist'
        info = bounded_file(info_path, root, 4 * 1024 * 1024)
        fields = plistlib.loads(info)
        if fields.get('CFBundleIdentifier') == IDENTIFIER:
            matches.append((bundle, info, fields, contents.is_dir()))
    if len(matches) != 1:
        raise Refused('expected exactly one com.apple.driver.AppleCentauriBeta dext')
    bundle, info, fields, contents_layout = matches[0]
    executable = fields.get('CFBundleExecutable')
    if not isinstance(executable, str) or not executable or executable in ('.', '..') or '/' in executable or '\\' in executable:
        raise Refused('Beta CFBundleExecutable is not a single filename')
    executable_path = bundle / 'Contents/MacOS' / executable if contents_layout else bundle / executable
    image = bounded_file(executable_path, root, MAX_EXECUTABLE)
    if image[:4] not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        raise Refused('Beta executable is not a recognized Mach-O container')
    cryptex = (Path(cryptex) if cryptex is not None else root / 'System/Cryptexes/OS').resolve(strict=True)
    protocol = {
        't2026-CentauriBeta.plist': bounded_file(root / PROFILE_PATH, root, 1024 * 1024),
        'bluetoothd.macho': bounded_file(root / 'usr/sbin/bluetoothd', root, MAX_EXECUTABLE),
        'driverkit-cache': bounded_file(cryptex / DRIVERKIT_CACHE, cryptex, 64 * 1024 * 1024),
        'driverkit-cache.symbols': bounded_file(cryptex / (DRIVERKIT_CACHE + '.symbols'), cryptex, 32 * 1024 * 1024),
        'Airship-TEXT.bin': bounded_span(cryptex / AIRSHIP_CACHE, cryptex, AIRSHIP_OFFSET, AIRSHIP_SIZE),
    }
    if any(hashlib.sha256(data).hexdigest() != SOURCE_HASHES[name] for name, data in protocol.items()):
        raise Refused('native protocol source differs from inspected 26C5035r inputs')
    files = dict(protocol, **{'AppleCentauriBeta.macho': image, 'Info.plist': info, 'SystemVersion.plist': version})
    receipt = {'schema': 1, 'purpose': 'exact native standard-SCO protocol source acquisition',
               'build': BUILD, 'bundle_identifier': IDENTIFIER,
               'bundle_version': fields.get('CFBundleVersion'),
               'source_bundle': str(bundle.relative_to(root)), 'source_executable': executable,
               'source_unit_identity_verified': False,
               'sco_support_qualified': False, 'bluetooth_or_audio_activity_initiated': False,
               'protocol_sources_acquired': True,
               'native_header_mode': 'standard HCI SCO, 3 bytes; private HDR unsupported',
               'airship_text': {'cache_file': AIRSHIP_CACHE, 'file_offset': AIRSHIP_OFFSET,
                                'virtual_address': 0x2617e5000, 'bytes': AIRSHIP_SIZE},
               'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                         for name, data in files.items()}}
    return files, receipt


def bind_unit(platform_plist, unit_sha256):
    """Bind source acquisition to actual platform evidence, never a board override."""
    if (len(unit_sha256) != 64 or any(char not in '0123456789abcdef' for char in unit_sha256)):
        raise Refused('unit identity must be an independently retained lowercase SHA256')
    spec = importlib.util.spec_from_file_location('bluetooth_camera_identity', ROOT / 'scripts/prepare-camera-firmware.py')
    camera = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(camera)
    if platform_plist is None:
        profile = json.loads((ROOT / 'profiles/j716s.json').read_text())
        identity, data = camera.als_tool().macos_identity(profile, unit_sha256)
        identity['identity_source'] = 'live-paired-macos-platform-registry'
    else:
        path, anchor = platform_plist
        data = camera.read_regular(path, private=True)
        if hashlib.sha256(data).hexdigest() != anchor:
            raise Refused('retained platform plist differs from its external SHA256 anchor')
        model, serial = camera.als_tool().platform_identity(data)
        identity = camera.current_identity()
        if (model != identity['model'] or hashlib.sha256(serial.encode('ascii')).hexdigest() != unit_sha256 or
                identity['unit_serial_sha256'] != unit_sha256):
            raise Refused('retained platform evidence is not the current exact J716s unit')
        identity['identity_source'] = 'anchored-paired-macos-platform-matched-to-live-linux'
    return identity, data


def save(directory, files, receipt):
    directory = Path(directory).resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise Refused('native executable must remain private outside repository')
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in dict(files, **{'acquisition.json': (json.dumps(receipt, indent=2) + '\n').encode()}).items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system-root', type=Path, default=Path('/'), help='existing same-unit read-only System volume root')
    parser.add_argument('--cryptex-root', type=Path, help='same-build read-only Cryptex OS root; defaults to System/Cryptexes/OS')
    parser.add_argument('--unit-serial-sha256', required=True, help='independently retained Linux unit hash')
    parser.add_argument('--platform-plist', type=Path, help='private paired-macOS platform evidence for retained mode')
    parser.add_argument('--platform-sha256', help='external SHA256 anchor for --platform-plist')
    parser.add_argument('--output', type=Path, required=True, help='new private directory outside repository')
    args = parser.parse_args()
    try:
        if bool(args.platform_plist) != bool(args.platform_sha256):
            raise Refused('retained platform evidence and its external SHA256 must be supplied together')
        if args.platform_plist is None and (platform.system() != 'Darwin' or args.system_root != Path('/')):
            raise Refused('retained System acquisition requires anchored paired-macOS platform evidence')
        roots = (args.system_root, args.cryptex_root or args.system_root / 'System/Cryptexes/OS')
        if any(not (os.statvfs(root).f_flag & os.ST_RDONLY) for root in roots):
            raise Refused('System and Cryptex sources must already be read-only')
        evidence = (args.platform_plist, args.platform_sha256) if args.platform_plist else None
        identity, platform_data = bind_unit(evidence, args.unit_serial_sha256)
        files, receipt = acquire(args.system_root, args.cryptex_root)
        if identity['macos_build'] != receipt['build']:
            raise Refused('platform and source acquisition builds differ')
        files['platform.plist'] = platform_data
        receipt.update({**identity, 'target_unit_identity_verified': True,
                        'source_unit_identity_verified': args.platform_plist is None})
        receipt['files']['platform.plist'] = {'bytes': len(platform_data),
                                            'sha256': hashlib.sha256(platform_data).hexdigest()}
        directory = save(args.output, files, receipt)
        print(f'Exact native SCO sources acquired privately: {directory}. No radio or audio operation issued.')
        return 0
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException, subprocess.SubprocessError) as error:
        print('Native acquisition refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
