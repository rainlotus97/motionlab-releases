import hashlib
import io
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from studio_update import BUNDLE_ID, API, SafeRedirect, check, prepare, replace_app, validate_zip, version, select_release, hardware_architecture


def app(path, release):
    (path / 'Contents').mkdir(parents=True)
    (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': BUNDLE_ID, 'CFBundleShortVersionString': release, 'StudioBundled': True}))
    (path / 'Contents/version.txt').write_text(release)
    return path


class UpdateTests(unittest.TestCase):
    def test_version_and_published_release_selection(self):
        self.assertGreater(version('0.10.0'), version('0.9.9'))
        for invalid in ('0.1', '../0.1.0', '1.0.0-beta', 'latest'):
            with self.assertRaises(ValueError):
                version(invalid)
        release = {'version': '0.2.0', 'asset': 'MotionLab-0.2.0-universal.zip', 'sha256': 'a' * 64, 'bytes': 123}
        with patch('studio_update.request', return_value=io.BytesIO(json.dumps(release).encode())):
            self.assertTrue(check('0.1.0')['available'])
        with patch('studio_update.request', return_value=io.BytesIO(json.dumps(release).encode())):
            self.assertFalse(check('0.3.0')['available'])
        release.pop('sha256')
        with patch('studio_update.request', return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaises(ValueError):
                check('0.1.0')

    def test_arch_specific_updates_and_legacy_bridge(self):
        release = {'version': '0.1.3', 'asset': 'MotionLab-0.1.3-universal.zip',
                   'sha256': 'a' * 64, 'bytes': 123, 'latestVersion': '0.2.0',
                   'platforms': {arch: {'asset': f'MotionLab-0.2.0-{arch}.zip', 'sha256': 'b' * 64, 'bytes': 99}
                                 for arch in ('arm64', 'x86_64')}}
        for arch in ('arm64', 'x86_64'):
            result = select_release(release, '0.1.3', arch)
            self.assertTrue(result['available'])
            self.assertEqual(result['version'], '0.2.0')
            self.assertTrue(result['asset']['url'].endswith(f'-{arch}.zip'))
        release['platforms'].pop('arm64')
        with self.assertRaises(ValueError):
            select_release(release, '0.1.3', 'arm64')
        release['platforms']['x86_64']['asset'] = 'MotionLab-0.2.0-arm64.zip'
        with self.assertRaises(ValueError):
            select_release(release, '0.1.3', 'x86_64')
        release.pop('platforms')
        self.assertTrue(select_release(release, '0.1.2', 'arm64')['asset']['url'].endswith('-universal.zip'))

    def test_rosetta_app_selects_actual_arm_hardware(self):
        from subprocess import CompletedProcess
        with patch('studio_update.sys.platform', 'darwin'), patch('studio_update.platform.machine', return_value='x86_64'):
            with patch('studio_update.subprocess.run', return_value=CompletedProcess([], 0, '1\n')):
                self.assertEqual(hardware_architecture(), 'arm64')
            with patch('studio_update.subprocess.run', return_value=CompletedProcess([], 0, '0\n')):
                self.assertEqual(hardware_architecture(), 'x86_64')

    def test_redirect_strips_token_and_rejects_other_hosts(self):
        request = urllib.request.Request(API + '/download/v0.2.0/MotionLab-0.2.0-universal.zip', headers={'Authorization': 'Bearer secret'})
        result = SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://release-assets.githubusercontent.com/blob')
        self.assertFalse(result.has_header('Authorization'))
        for url in ('http://api.github.com/blob', 'https://example.com/blob'):
            with self.assertRaises(ValueError):
                SafeRedirect().redirect_request(request, None, 302, '', {}, url)

    def test_archive_rejects_traversal_and_external_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'update.zip'
            for name in ('../escape', '/absolute', 'other.app/payload'):
                with zipfile.ZipFile(archive, 'w') as package:
                    package.writestr(name, b'x')
                with self.assertRaises(ValueError):
                    validate_zip(archive)
            with zipfile.ZipFile(archive, 'w') as package:
                entry = zipfile.ZipInfo('MotionLab.app/link')
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                package.writestr(entry, '../../outside')
            with self.assertRaises(ValueError):
                validate_zip(archive)
            with zipfile.ZipFile(archive, 'w') as package:
                entry.filename = 'MotionLab.app/Contents/runtime/bin/python3'
                package.writestr(entry, 'python3.12')
            validate_zip(archive)

    def test_replace_retains_backup_and_never_touches_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = app(root / 'MotionLab.app', '0.1.0')
            candidate = app(root / 'stage/MotionLab.app', '0.2.0')
            data = root / 'Desktop/MotionLab/projects/note.txt'
            data.parent.mkdir(parents=True); data.write_text('my edits')
            backup = replace_app(candidate, target)
            self.assertEqual((target / 'Contents/version.txt').read_text(), '0.2.0')
            self.assertEqual((backup / 'Contents/version.txt').read_text(), '0.1.0')
            self.assertEqual(data.read_text(), 'my edits')

    def test_failed_launch_restores_old_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = app(root / 'MotionLab.app', '0.1.0')
            candidate = app(root / 'stage/MotionLab.app', '0.2.0')
            def fail(_):
                raise OSError('cannot open app')
            with self.assertRaises(OSError):
                replace_app(candidate, target, fail)
            self.assertEqual((target / 'Contents/version.txt').read_text(), '0.1.0')
            self.assertEqual((candidate / 'Contents/version.txt').read_text(), '0.2.0')

    def test_bad_download_hash_leaves_current_app_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = app(root / 'MotionLab.app', '0.1.0')
            release = {'version': '0.2.0', 'available': True, 'asset': {
                'url': API + '/download/v0.2.0/MotionLab-0.2.0-universal.zip', 'size': 3, 'digest': 'sha256:' + hashlib.sha256(b'new').hexdigest()}}
            with patch('studio_update.check', return_value=release), patch('studio_update.request', return_value=io.BytesIO(b'bad')):
                with self.assertRaisesRegex(ValueError, '校验失败'):
                    prepare(target, '0.2.0', 123)
            self.assertEqual((target / 'Contents/version.txt').read_text(), '0.1.0')
            self.assertFalse(list(root.glob('.motionlab-update-*')))


if __name__ == '__main__':
    unittest.main()
