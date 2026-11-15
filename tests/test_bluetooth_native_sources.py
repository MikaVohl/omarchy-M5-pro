"""Offline exact native Bluetooth protocol-source acquisition guards; no radio."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('bluetooth_prepare', 'prepare-bluetooth-native.py')


class ExecutableAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {
            't2026-CentauriBeta.plist': plistlib.dumps({'ring_size': 9}),
            'bluetoothd.macho': b'fixture daemon code',
            'driverkit-cache': b'fixture DriverKit cache',
            'driverkit-cache.symbols': b'fixture symbol table',
            'Airship-TEXT.bin': b'fixture Airship text',
        }
        # Synthetic source fixtures exercise hash refusal without proprietary
        # bytes. The production constants are restored after every test.
        for name, value in (
                ('SOURCE_HASHES', {n: hashlib.sha256(d).hexdigest() for n, d in self.protocol.items()}),
                ('AIRSHIP_OFFSET', 8), ('AIRSHIP_SIZE', len(self.protocol['Airship-TEXT.bin']))):
            patcher = patch.object(prepare, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def system(self, root, build='26C5035r', executable='Beta'):
        version = root / 'System/Library/CoreServices/SystemVersion.plist'
        version.parent.mkdir(parents=True)
        version.write_bytes(plistlib.dumps({'ProductBuildVersion': build}))
        bundle = root / 'System/Library/DriverExtensions/NotGuessedFilename.dext/Contents'
        (bundle / 'MacOS').mkdir(parents=True)
        (bundle / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': prepare.IDENTIFIER,
                                                        'CFBundleExecutable': executable,
                                                        'CFBundleVersion': '129'}))
        if executable == 'Beta':
            (bundle / 'MacOS/Beta').write_bytes(b'\xcf\xfa\xed\xfe' + bytes(28))
        cryptex = root / 'System/Cryptexes/OS'
        paths = {
            't2026-CentauriBeta.plist': root / prepare.PROFILE_PATH,
            'bluetoothd.macho': root / 'usr/sbin/bluetoothd',
            'driverkit-cache': cryptex / prepare.DRIVERKIT_CACHE,
            'driverkit-cache.symbols': cryptex / (prepare.DRIVERKIT_CACHE + '.symbols'),
            'Airship-TEXT.bin': cryptex / prepare.AIRSHIP_CACHE,
        }
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self.protocol[name]
            path.write_bytes(bytes(prepare.AIRSHIP_OFFSET) + data + b'unrelated cache bytes'
                             if name == 'Airship-TEXT.bin' else data)
        return bundle

    def test_exact_build_identifier_and_private_files_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.system(root)
            files, report = prepare.acquire(root)
            self.assertEqual(set(files), set(self.protocol) | {'AppleCentauriBeta.macho', 'Info.plist', 'SystemVersion.plist'})
            destination = prepare.save(root / 'private', files, report)
            self.assertFalse(report['sco_support_qualified'])
            self.assertEqual(report['bundle_identifier'], prepare.IDENTIFIER)
            self.assertTrue(report['protocol_sources_acquired'])
            self.assertEqual(report['airship_text']['bytes'], len(self.protocol['Airship-TEXT.bin']))
            saved = json.loads((destination / 'acquisition.json').read_text())
            self.assertEqual(saved['files']['Airship-TEXT.bin']['sha256'],
                             hashlib.sha256(self.protocol['Airship-TEXT.bin']).hexdigest())
            for file in destination.iterdir():
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                prepare.save(destination, files, report)

    def test_flat_driverkit_layout_is_acquired_without_guessed_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = self.system(root)
            bundle = contents.parent
            (contents / 'Info.plist').rename(bundle / 'Info.plist')
            (contents / 'MacOS/Beta').rename(bundle / 'Beta')
            (contents / 'MacOS').rmdir()
            contents.rmdir()
            files, report = prepare.acquire(root)
            self.assertEqual(files['AppleCentauriBeta.macho'][:4], b'\xcf\xfa\xed\xfe')
            self.assertTrue(report['protocol_sources_acquired'])
            self.assertEqual(report['source_executable'], 'Beta')

    def test_changed_protocol_sources_and_truncated_cache_refuse(self):
        for filename in ('profile', 'cache'):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.system(root)
                path = root / prepare.PROFILE_PATH if filename == 'profile' else root / 'System/Cryptexes/OS' / prepare.AIRSHIP_CACHE
                path.write_bytes(b'invalid')
                with self.assertRaises(prepare.Refused):
                    prepare.acquire(root)

    def test_wrong_build_and_executable_traversal_are_refused(self):
        for build, executable in (('26B123', 'Beta'), ('26C5035r', '../private')):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.system(root, build, executable)
                with self.assertRaises(prepare.Refused):
                    prepare.acquire(root)

    def test_outside_system_symlink_and_repository_output_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.system(root)
            external = root.parent / (root.name + '-external')
            external.write_bytes(b'\xcf\xfa\xed\xfe')
            self.addCleanup(external.unlink)
            image = root / 'System/Library/DriverExtensions/NotGuessedFilename.dext/Contents/MacOS/Beta'
            image.unlink()
            image.symlink_to(external)
            with self.assertRaises(prepare.Refused):
                prepare.acquire(root)
            with self.assertRaises(prepare.Refused):
                prepare.save(ROOT / 'work/private-beta', {}, {})


if __name__ == '__main__':
    unittest.main()
