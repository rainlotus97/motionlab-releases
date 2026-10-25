import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from comparison_store import get_state, save_review
from comparison_http import comparison_handler
from motion_studio import load_project, make_server
from sampling_plans import ConflictError, StaleSourceError, export_plan, mutate


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project.json'
        self.config = {'version': 1, 'id': 'fixture', 'sources': [
            {'id': 'art-walk', 'type': 'registration', 'path': 'art/report.json'}],
            'sampling': {'store': 'plans.json', 'sources': [
                {'id': 'walk', 'name': 'Walk', 'type': '100style', 'path': 'walk.json'}]},
            'comparisons': {'reviewStore': 'reviews.json', 'sources': [
                {'id': 'walk-comparison', 'path': 'comparison.json'}]}}
        self.project.write_text(json.dumps(self.config))
        self.source = self.root / 'walk.json'
        self.source.write_text(json.dumps({'names': ['Hips', 'RightWrist'], 'parents': [-1, 0],
            'dt': 1 / 60, 'frameIds': list(range(80)),
            'inPlace': [[[0, 10, 0], [-1, 7, index / 10]] for index in range(80)]}))
        state = mutate(self.project, 'create', {'sourceId': 'walk', 'frames': [.5, 40.5], 'timing': 'even'}, 0)
        self.contract = export_plan(self.project, state['plans'][0]['id'])
        (self.root / 'contract.json').write_text(json.dumps(self.contract))
        (self.root / 'art').mkdir()
        records = []
        for name in ('a', 'b'):
            Image.new('RGBA', (20, 30), (100, 120, 200, 255)).save(self.root / 'art' / (name + '.png'))
            master = self.root / (name + '-master.png')
            Image.new('RGBA', (40, 60), (120, 140, 180, 255)).save(master)
            records.append({'id': name, 'durationMs': 667, 'markers': {},
                'source': str(master), 'sha256': hashlib.sha256(master.read_bytes()).hexdigest()})
        (self.root / 'art/report.json').write_text(json.dumps({'status': 'registered-not-visual-approval',
            'errors': [], 'canvas': [20, 30], 'origin': [10, 28], 'records': records}))
        self.definition = {'version': 1, 'id': 'walk-comparison', 'name': 'Walk comparison',
            'clipId': 'art-walk', 'planContract': 'contract.json', 'relationship': 'candidate-not-reviewed',
            'alignment': {'canvas': [20, 30], 'origin': [10, 28], 'pixelsPerSourceUnit': 2,
                'sourceOriginX': 0, 'sourceFloorY': -1.599, 'method': 'manual display alignment'},
            'frames': [{'slot': i, 'artFrameId': frame, 'sourceIndex': self.contract['samples'][i]['sourceIndex']}
                for i, frame in enumerate(('a', 'b'))]}
        (self.root / 'comparison.json').write_text(json.dumps(self.definition))

    def tearDown(self):
        self.temp.cleanup()

    def review(self, state, **change):
        comparison = state['comparisons'][0]
        return {'expectedRevision': state['revision'], 'comparisonId': comparison['id'],
            'comparisonFingerprint': comparison['fingerprint'], 'slot': 0,
            'status': 'needs-fix', 'note': 'Near wrist is too far forward', 'tags': ['arms'], **change}

    def test_frozen_pose_links_alignment_and_review_round_trip(self):
        state = get_state(self.project)
        comparison = state['comparisons'][0]
        self.assertFalse(comparison['stale'])
        self.assertEqual(comparison['alignment']['sourceFloorY'], -1.599)
        self.assertEqual(comparison['planContract'], self.contract)
        self.assertEqual(comparison['planContractSha256'], hashlib.sha256((self.root / 'contract.json').read_bytes()).hexdigest())
        self.assertEqual(comparison['source']['frameCount'], 80)
        self.assertEqual(comparison['frames'][0]['artSource']['url'], '/project/art/a.png')
        self.assertEqual(comparison['frames'][0]['masterSource']['url'], '/project/a-master.png')
        result = save_review(self.project, self.review(state))
        self.assertEqual(result['revision'], 1)
        record = result['reviews']['walk-comparison']['slots']['0']
        self.assertEqual(record['tags'], ['arms'])
        self.assertFalse(record['stale'])
        self.assertNotIn('stale', json.loads((self.root / 'reviews.json').read_text())['reviews']['walk-comparison']['slots']['0'])
        # Changing the active editable plan cannot change the frozen comparison.
        plans = json.loads((self.root / 'plans.json').read_text())
        mutate(self.project, 'update', {'id': plans['plans'][0]['id'], 'frames': [10, 50]}, plans['revision'])
        self.assertEqual(get_state(self.project)['comparisons'][0]['planContract'], self.contract)

    def test_assets_revision_and_stale_source_cannot_be_mistaken_for_current(self):
        state = get_state(self.project)
        save_review(self.project, self.review(state))
        with self.assertRaises(ConflictError):
            save_review(self.project, self.review(state))
        latest = get_state(self.project)
        Image.new('RGBA', (20, 30), 'red').save(self.root / 'art/a.png')
        with self.assertRaises(ConflictError):
            save_review(self.project, self.review(latest))
        self.assertTrue(get_state(self.project)['reviews']['walk-comparison']['slots']['0']['stale'])
        self.source.write_text(self.source.read_text() + '\n')
        current = get_state(self.project)
        self.assertTrue(current['comparisons'][0]['stale'])
        self.assertIsNone(current['comparisons'][0]['source'])
        self.assertTrue(current['reviews']['walk-comparison']['slots']['0']['stale'])
        with self.assertRaises(StaleSourceError):
            save_review(self.project, self.review(current, status='accepted'))

    def test_bad_paths_and_snapshot_contributors_are_not_trusted(self):
        self.contract['samples'][0]['contributors'][0]['sourceFrameId'] = 9999
        (self.root / 'contract.json').write_text(json.dumps(self.contract))
        self.assertTrue(get_state(self.project)['comparisons'][0]['stale'])
        self.config['comparisons']['reviewStore'] = '../outside.json'
        self.project.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, 'outside'):
            get_state(self.project)
        self.config['comparisons']['reviewStore'] = 'walk.json'
        self.project.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            get_state(self.project)
        self.config['comparisons']['reviewStore'] = 'contract.json'
        self.project.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            get_state(self.project)

    def test_studio_hook_adds_one_link_with_one_project_load_and_preserves_old_projects(self):
        with patch('motion_studio.load_project', wraps=load_project) as loader:
            server = make_server(self.project, 0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            def get(route):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('GET', route)
                response = connection.getresponse()
                body = response.read()
                connection.close()
                return response.status, json.loads(body) if route.startswith('/api/') else body
            try:
                baseline = loader.call_count
                status, project = get('/api/project')
                self.assertEqual(status, 200)
                self.assertEqual(loader.call_count, baseline + 1)
                self.assertEqual(sum(link['url'] == '/comparison.html' for link in project['links']), 1)
                self.assertTrue(project['comparisonConfigured'])
                self.assertTrue(any(link['url'] == '/sampling.html' for link in project['links']))
                self.assertEqual(get('/api/comparison')[0], 200)
                self.assertEqual(get('/api/sampling/sources')[0], 200)
                self.assertEqual(get('/')[0], 200)
                # Existing projects without a comparison config keep their
                # original studio and skeleton-plan behavior.
                del self.config['comparisons']
                self.project.write_text(json.dumps(self.config))
                status, old_project = get('/api/project')
                self.assertEqual(status, 200)
                self.assertNotIn('comparisonConfigured', old_project)
                self.assertEqual(old_project['links'][0]['url'], '/sampling.html')
                self.assertEqual(get('/api/sampling/state')[0], 200)
                status, empty_comparisons = get('/api/comparison')
                self.assertEqual(status, 200)
                self.assertFalse(empty_comparisons['configured'])
                self.assertEqual(empty_comparisons['comparisons'], [])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_generated_sheet_provenance_rect_path_and_review_fingerprint(self):
        sheet = self.root / 'mother.png'
        Image.new('RGBA', (120, 100), 'blue').save(sheet)
        self.definition['frames'][0].update(sheetSource='mother.png',
            sheetSha256=hashlib.sha256(sheet.read_bytes()).hexdigest(), sheetRect=[10, 20, 50, 60])
        (self.root / 'comparison.json').write_text(json.dumps(self.definition))
        state = get_state(self.project)
        source = state['comparisons'][0]['frames'][0]['sheetSource']
        self.assertEqual(source['url'], '/project/mother.png')
        self.assertEqual(source['rect'], [10, 20, 50, 60])
        self.assertEqual(source['size'], [120, 100])
        self.assertFalse(source['stale'])
        self.assertFalse(state['comparisons'][0]['frames'][1]['sheetSource']['available'])
        save_review(self.project, self.review(state))
        Image.new('RGBA', (120, 100), 'red').save(sheet)
        changed = get_state(self.project)
        self.assertTrue(changed['comparisons'][0]['frames'][0]['sheetSource']['stale'])
        self.assertNotEqual(changed['comparisons'][0]['fingerprint'], state['comparisons'][0]['fingerprint'])
        self.assertTrue(changed['reviews']['walk-comparison']['slots']['0']['stale'])
        with self.assertRaises(ConflictError):
            save_review(self.project, self.review(state, expectedRevision=1))
        self.definition['frames'][0]['sheetSource'] = '../outside.png'
        (self.root / 'comparison.json').write_text(json.dumps(self.definition))
        with self.assertRaisesRegex(ValueError, 'outside'):
            get_state(self.project)
        self.definition['frames'][0]['sheetSource'] = 'mother.png'
        self.definition['frames'][0]['sheetRect'] = [0, 0, 121, 100]
        (self.root / 'comparison.json').write_text(json.dumps(self.definition))
        with self.assertRaisesRegex(ValueError, 'outside the original'):
            get_state(self.project)

    def test_http_same_origin_review_and_conflict(self):
        class Handler(BaseHTTPRequestHandler):
            def local_request(self):
                return self.headers.get('Origin') in (None, f'http://127.0.0.1:{self.server.server_port}')
            def json_response(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), comparison_handler(Handler, self.project, load_project))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(method, route, data=None, origin=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            connection.request(method, route, json.dumps(data) if data is not None else None, headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        try:
            status, state = request('GET', '/api/comparison')
            self.assertEqual(status, 200)
            self.assertEqual(request('POST', '/api/comparison/review', self.review(state))[0], 200)
            self.assertEqual(request('POST', '/api/comparison/review', self.review(state))[0], 409)
            self.assertEqual(request('GET', '/api/comparison', origin='https://example.com')[0], 403)
            self.assertEqual(request('POST', '/api/comparison/review', [], origin=None)[0], 400)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
