import json
import http.client
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from motion_studio import load_project, export_manifest, validate_edit, inside, make_server
from motion_tool import pack


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('a', 'b'):
            image = Image.new('RGBA', (20, 30))
            image.putpixel((10, 20), (200, 100, 50, 255))
            image.save(self.root / (name + '.png'))
        self.report = self.root / 'report.json'
        self.report.write_text(json.dumps({'status': 'registered-not-visual-approval', 'errors': [],
            'canvas': [20, 30], 'origin': [10, 28], 'records': [
                {'id': name, 'durationMs': ms, 'markers': {}} for name, ms in [('a', 100), ('b', 200)]]}))
        self.project = self.root / 'studio.json'
        self.project.write_text(json.dumps({'version': 1, 'id': 'fixture', 'sources': [
            {'type': 'registration', 'path': 'report.json', 'id': 'walk'}]}))
        self.data, _ = load_project(self.project)
        self.draft = {'version': 1, 'projectId': 'fixture', 'fingerprint': self.data['fingerprint'],
            'clips': {'walk': {'loop': True, 'steps': [
                {'slot': 1, 'durationMs': 220, 'offset': [1, -1]},
                {'slot': 0, 'durationMs': 80, 'offset': [0, 0]}]}}}
        self.draft_path = self.root / 'draft.json'
        self.draft_path.write_text(json.dumps(self.draft))

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_to_existing_pack_and_runtime_adapter(self):
        out = self.root / 'edited.json'
        result = export_manifest(self.project, self.draft_path, out)
        action = result['variants']['default']['actions']['clip0']
        self.assertEqual(action['durations_ms'], [220, 80])
        self.assertEqual(action['frames'][0]['anchor'], [9, 29])
        pack(out, self.root / 'runtime')
        self.project.write_text(json.dumps({'version': 1, 'id': 'runtime-fixture', 'sources': [
            {'type': 'runtime', 'path': 'runtime/motion.runtime.json', 'id': 'r'}]}))
        loaded, _ = load_project(self.project)
        self.assertEqual([s['durationMs'] for s in loaded['clips'][0]['steps']], [220, 80])

    def test_source_changes_reject_old_draft(self):
        image = Image.open(self.root / 'a.png')
        image.putpixel((11,20), (100,200,30,255))
        image.save(self.root / 'a.png')
        with self.assertRaisesRegex(ValueError, 'changed source'):
            export_manifest(self.project, self.draft_path, self.root / 'edited.json')

    def test_output_and_blocked_registration(self):
        with self.assertRaisesRegex(ValueError, 'not overwritten'):
            export_manifest(self.project, self.draft_path, self.project)
        report = json.loads(self.report.read_text())
        report['errors'] = ['angle mismatch']
        self.report.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'blocked'):
            load_project(self.project)

    def test_reorder_guard_and_bad_duration(self):
        clip = self.data['clips'][0]
        clip['canReorder'] = False
        with self.assertRaisesRegex(ValueError, 'event'):
            validate_edit(clip, self.draft['clips']['walk'])
        clip['canReorder'] = True
        self.draft['clips']['walk']['steps'][0]['durationMs'] = 0
        with self.assertRaisesRegex(ValueError, 'duration'):
            validate_edit(clip, self.draft['clips']['walk'])

    def test_directory_boundary(self):
        with self.assertRaises(ValueError):
            inside(self.root, '../outside')

    def test_registration_labels_are_optional_and_use_source_defaults_only(self):
        self.assertEqual(self.data['clips'][0]['markerLabels'], {})
        self.assertEqual(self.data['clips'][0]['originLabel'], '导出原点')
        config = json.loads(self.project.read_text())
        config['sources'][0].update(markerLabels={'head': '头部配准参考'}, originLabel='画布导出原点',
            markerLabelsByAction={'walk': {'head': '不适用于配准报告的动作覆盖'}})
        self.project.write_text(json.dumps(config))
        loaded, _ = load_project(self.project)
        self.assertEqual(loaded['clips'][0]['markerLabels'], {'head': '头部配准参考'})
        self.assertEqual(loaded['clips'][0]['originLabel'], '画布导出原点')
        self.assertEqual(loaded['clips'][0]['origin'], self.data['clips'][0]['origin'])
        self.assertEqual(loaded['clips'][0]['frames'], self.data['clips'][0]['frames'])

    def test_runtime_labels_merge_by_exact_action_without_changing_coordinates(self):
        manifest = self.root / 'edited.json'
        data = export_manifest(self.project, self.draft_path, manifest)
        action = data['variants']['default']['actions']['clip0']
        for frame in action['frames']:
            frame['markers'] = {'head': [10, 13], 'body': [10, 20]}
        data['variants']['default']['actions']['clip1'] = copy.deepcopy(action)
        manifest.write_text(json.dumps(data))
        runtime = pack(manifest, self.root / 'runtime')
        before = {p.name: p.read_bytes() for p in runtime.parent.iterdir() if p.is_file()}
        source = {'type': 'runtime', 'path': 'runtime/motion.runtime.json', 'id': 'r'}
        config = {'version': 1, 'id': 'runtime-labels', 'sources': [source]}
        self.project.write_text(json.dumps(config))
        original, _ = load_project(self.project)
        source.update(markerLabels={'head': '基础头部参考', 'body': '身体参考'},
            originLabel='逻辑导出原点', markerLabelsByAction={'clip0': {'head': '双瞳配准参考'}})
        self.project.write_text(json.dumps(config))
        loaded, _ = load_project(self.project)
        self.assertEqual(loaded['clips'][0]['markerLabels'], {'head': '双瞳配准参考', 'body': '身体参考'})
        self.assertEqual(loaded['clips'][1]['markerLabels'], {'head': '基础头部参考', 'body': '身体参考'})
        for actual, expected in zip(loaded['clips'], original['clips']):
            self.assertEqual(actual['originLabel'], '逻辑导出原点')
            for key in ('canvas', 'origin', 'frames', 'steps'):
                self.assertEqual(actual[key], expected[key])
        for name, content in before.items():
            self.assertEqual((runtime.parent / name).read_bytes(), content)
        source['markerLabelsByAction'] = {'r/default/clip0': {'head': '不是动作ID'}}
        self.project.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'exact action ID'):
            load_project(self.project)

    def test_invalid_semantic_labels_are_rejected(self):
        config = json.loads(self.project.read_text())
        for field, value in [('markerLabels', []), ('markerLabels', {'head': 1}),
                ('markerLabels', {'head': ''}), ('markerLabels', {'head': 'x' * 101}),
                ('markerLabelsByAction', []), ('markerLabelsByAction', {'walk': []}),
                ('originLabel', None), ('originLabel', 'x' * 101)]:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(config)
                changed['sources'][0][field] = value
                self.project.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_project(self.project)

    def test_export_api_local_origin_new_output_and_rejections(self):
        server = make_server(self.project, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        def request(body, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            connection.request('POST', '/api/export', json.dumps(body),
                headers={'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            status, result = response.status, json.loads(response.read())
            connection.close()
            return status, result
        try:
            payload = {'draft': self.draft, 'options': {'preset': 'lossless'}}
            status, result = request(payload, {'Origin': 'https://attacker.example'})
            self.assertEqual(status, 403)
            status, result = request(payload, {'Host': 'attacker.example'})
            self.assertEqual(status, 403)
            status, result = request(payload, {'Sec-Fetch-Site': 'cross-site'})
            self.assertEqual(status, 403)
            status, result = request(payload, {'Origin': f'http://127.0.0.1:{port}'})
            self.assertEqual(status, 201, result)
            self.assertTrue(Path(result['outputDirectory']).is_relative_to(self.project.resolve().parent / 'exports'))
            self.assertTrue(result['downloadUrl'].endswith('/package.zip'))
            self.assertGreater(result['report']['zipBytes'], 0)
            status, result = request({'draft': {**self.draft, 'fingerprint': 'old'}})
            self.assertEqual(status, 400)
            self.assertIn('changed source', result['error'])
            status, result = request({'draft': self.draft, 'options': {'out': '../escape'}})
            self.assertEqual(status, 400)
            status, result = request(payload, {'Content-Length': '2000001'})
            self.assertEqual(status, 400)
            self.assertIn('2000000', result['error'])
            status, result = request(payload, {'Content-Type': 'text/plain'})
            self.assertEqual(status, 400)
            self.assertEqual(len(list((self.project.parent / 'exports').glob('*'))), 1)
            from sprite_export import export_package
            entered, release, done = threading.Event(), threading.Event(), []
            def blocked_export(*args, **kwargs):
                entered.set()
                if not release.wait(5): raise ValueError('Test timed out waiting for release')
                return export_package(*args, **kwargs)
            with patch('sprite_export.export_package', side_effect=blocked_export):
                first = threading.Thread(target=lambda: done.append(request(payload)), daemon=True)
                first.start()
                try:
                    self.assertTrue(entered.wait(2))
                    status, result = request(payload)
                    self.assertEqual(status, 429)
                finally:
                    release.set()
                    first.join(timeout=8)
                self.assertEqual(done[0][0], 201, done)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
