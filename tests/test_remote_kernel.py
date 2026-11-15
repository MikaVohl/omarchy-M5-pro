"""Remote return archives must not escape or substitute the artifact envelope."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('remote_kernel', Path(__file__).resolve().parents[1] / 'scripts/build-kernel-remote.py')
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)


class RemoteReturnTests(unittest.TestCase):
    def archive(self, path, extra=None):
        with tarfile.open(path, 'w:gz') as out:
            for name in sorted(remote.RETURN_FILES):
                content = (name + '\n').encode()
                item = tarfile.TarInfo(name)
                item.size = len(content)
                out.addfile(item, io.BytesIO(content))
            if extra is not None:
                content = b'not an artifact'
                extra.size = len(content) if extra.isfile() else 0
                out.addfile(extra, io.BytesIO(content) if extra.isfile() else None)

    def test_complete_envelope_extracts_original_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.archive(root / 'return.tar.gz')
            output = root / 'out'
            output.mkdir()
            remote.extract_return(root / 'return.tar.gz', output)
            for name in remote.RETURN_FILES:
                self.assertEqual((output / name).read_bytes(), (name + '\n').encode())

    def test_duplicate_artifact_is_refused_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.archive(root / 'return.tar.gz', tarfile.TarInfo('Image.gz'))
            with self.assertRaises(ValueError):
                remote.extract_return(root / 'return.tar.gz', root / 'out')
            self.assertFalse((root / 'out').exists())

    def test_traversal_is_refused_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.archive(root / 'return.tar.gz', tarfile.TarInfo('../escape'))
            with self.assertRaises(ValueError):
                remote.extract_return(root / 'return.tar.gz', root / 'out')
            self.assertFalse((root / 'escape').exists())

    def test_replacing_an_artifact_with_a_link_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(root / 'return.tar.gz', 'w:gz') as out:
                for name in sorted(remote.RETURN_FILES):
                    item = tarfile.TarInfo(name)
                    if name == 'Image.gz':
                        item.type = tarfile.SYMTYPE
                        item.linkname = '/etc/passwd'
                        out.addfile(item)
                    else:
                        item.size = 1
                        out.addfile(item, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                remote.extract_return(root / 'return.tar.gz', root / 'out')
            self.assertFalse((root / 'out').exists())
