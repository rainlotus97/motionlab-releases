import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from assemble_release import assemble


class ReleaseTests(unittest.TestCase):
    def fixture(self, root, release='0.1.3', universal=True):
        for arch in ('arm64', 'x86_64', *(['universal'] if universal else [])):
            archive = root / f'MotionLab-{release}-{arch}.zip'
            archive.write_bytes(arch.encode())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (root / f'package-{arch}.json').write_text(json.dumps({'version': release, 'architecture': arch,
                'asset': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest}))
            (root / f'verification-{arch}.json').write_text(json.dumps({'version': release, 'archiveSha256': digest,
                'hostArchitecture': arch, 'runtimes': {arch: {'execution': 'native', 'startup': True,
                'restartPreservesData': True, 'export': True, 'clips': 9, 'skeletonSources': 43}}}))

    def test_bridge_remains_available_for_old_clients_after_next_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            bridge = assemble(root, '0.1.3')
            self.assertEqual(bridge['version'], '0.1.3')
            (root / 'package-universal.json').unlink()
            self.fixture(root, '0.1.4', False)
            result = assemble(root, '0.1.4', bridge)
            self.assertEqual(result['version'], '0.1.3')
            self.assertEqual(result['latestVersion'], '0.1.4')
            self.assertEqual(result['platforms']['arm64']['asset'], 'MotionLab-0.1.4-arm64.zip')

    def test_rejects_corrupt_package_and_rosetta_only_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'MotionLab-0.1.3-arm64.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'metadata mismatch'):
                assemble(root, '0.1.3')
            self.fixture(root)
            path = root / 'verification-x86_64.json'
            report = json.loads(path.read_text())
            report['hostArchitecture'] = 'arm64'
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'Native verification'):
                assemble(root, '0.1.3')

    def test_requires_legacy_bridge_and_both_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, '0.1.4', False)
            with self.assertRaisesRegex(ValueError, 'legacy upgrade bridge'):
                assemble(root, '0.1.4')
            (root / 'package-x86_64.json').unlink()
            with self.assertRaises(FileNotFoundError):
                assemble(root, '0.1.4')


if __name__ == '__main__':
    unittest.main()
