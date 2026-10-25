import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from studio_desktop import initialize_library, migrate_default_library
from motion_studio import load_project
from studio_control import connect
from PIL import Image


class DesktopTests(unittest.TestCase):
    def test_legacy_default_moves_without_overwriting_an_existing_library(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pathlib.Path.home', return_value=Path(tmp).resolve()):
            old = Path(tmp).resolve() / 'Desktop/角色动作工坊'
            _, project, _ = initialize_library(old)
            (old / '.studio/last-project.json').write_text(json.dumps({'path': 'projects/my-characters/studio.project.json', 'assetRoot': str(old / 'projects')}))
            (old / '角色动作工坊.app').mkdir()
            root = migrate_default_library()
            self.assertEqual(root.name, 'MotionLab')
            self.assertFalse(old.exists())
            self.assertTrue((root / 'MotionLab.app').is_dir())
            self.assertTrue((root / project.relative_to(old)).is_file())
            state = json.loads((root / '.studio/last-project.json').read_text())
            self.assertEqual(state['assetRoot'], str(root / 'projects'))
            old.mkdir()
            (old / 'keep.txt').write_text('unmerged')
            self.assertEqual(migrate_default_library(), root)
            self.assertEqual((old / 'keep.txt').read_text(), 'unmerged')

    def test_default_library_is_idempotent_and_keeps_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project, tools = initialize_library(tmp)
            (root / 'projects' / 'notes.txt').write_text('keep')
            before = tools.read_bytes(), project.read_bytes()
            initialize_library(tmp)
            self.assertEqual(before, (tools.read_bytes(), project.read_bytes()))
            self.assertEqual((root / 'projects' / 'notes.txt').read_text(), 'keep')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS app-owned service')
    def test_single_instance_shutdown_and_relaunch_release_listener(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, str(Path(__file__).with_name('studio_desktop.py')), '--library', tmp, '--owner-pid', str(os.getpid())]
            first = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                url = first.stdout.readline().strip()
                self.assertTrue(url.startswith('http://127.0.0.1:'))
                self.assertEqual(json.load(urlopen(url + 'api/project'))['name'], '我的角色')
                live = connect(tmp)
                self.assertEqual(live['pid'], first.pid)
                control = [sys.executable, str(Path(__file__).with_name('studio_control.py')), '--library', tmp]
                listing = subprocess.run(control + ['projects'], capture_output=True, text=True, timeout=5)
                self.assertEqual(listing.returncode, 0, listing.stderr)
                identifier = json.loads(listing.stdout)['projects'][0]['id']
                body = Path(tmp) / 'write.json'
                body.write_text(json.dumps({'path': 'control-test.txt', 'text': 'AI control via shared file API'}))
                edited = subprocess.run(control + ['request', '--project-id', identifier,
                    '--route', '/api/files/write', '--data-file', str(body)], capture_output=True, text=True, timeout=5)
                self.assertEqual(edited.returncode, 0, edited.stderr)
                self.assertEqual((Path(tmp) / 'projects/my-characters/control-test.txt').read_text(), 'AI control via shared file API')
                state = Path(tmp) / '.studio/instance.json'
                stale = {**live, 'instanceId': 'outdated-instance'}
                state.write_text(json.dumps(stale))
                with self.assertRaises(ValueError): connect(tmp)
                state.write_text(json.dumps(live))
                duplicate = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertIn('已在运行', duplicate.stderr)
                first.terminate(); first.wait(timeout=5)
                self.assertFalse((Path(tmp) / '.studio/instance.json').exists())
                from urllib.parse import urlparse
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(('127.0.0.1', urlparse(url).port)), 0)
                second = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                try:
                    self.assertTrue(second.stdout.readline().startswith('http://127.0.0.1:'))
                finally:
                    second.terminate(); second.wait(timeout=5); second.stdout.close()
            finally:
                if first.poll() is None: first.terminate(); first.wait(timeout=5)
                first.stdout.close()

    def test_legacy_sequence_repeats_and_nonuniform_durations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, path, _ = initialize_library(tmp)
            for name in ('a.png', 'b.png'):
                Image.new('RGBA', (8, 8), (200, 20, 30, 255)).save(path.parent / name)
            config = json.loads(path.read_text())
            config['sources'] = [{'type': 'sequence', 'id': 'sweep', 'files': ['a.png','b.png'],
                'canvas': [8,8], 'origin': [4,8], 'steps': [{'frame':0,'durationMs':400},{'frame':1,'durationMs':200},{'frame':0,'durationMs':100}]}]
            path.write_text(json.dumps(config))
            project, _ = load_project(path)
            self.assertEqual([s['frame'] for s in project['clips'][0]['steps']], [0,1,0])
            self.assertEqual(sum(s['durationMs'] for s in project['clips'][0]['steps']), 700)
            config['sources'][0]['steps'][1]['frame'] = 2
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError): load_project(path)


if __name__ == '__main__': unittest.main()
