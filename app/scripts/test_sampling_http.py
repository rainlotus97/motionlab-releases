import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from motion_studio import make_server


class SamplingHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / 'studio.project.json'
        self.project.write_text(json.dumps({'version': 1, 'id': 'fixture', 'sources': [],
            'sampling': {'store': 'plans.json', 'sources': [{'id': 'walk', 'type': '100style',
                'name': 'Walk', 'path': 'walk.json'}]}}))
        (root / 'walk.json').write_text(json.dumps({'names': ['Hips', 'RightWrist'],
            'parents': [-1, 0], 'frameIds': list(range(80)), 'dt': 1 / 60,
            'inPlace': [[[0, 10, 0], [1, 7, 2]] for _ in range(80)]}))
        self.server = make_server(self.project, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload)

    def test_sources_plan_save_select_export_and_conflict(self):
        status, project = self.request('GET', '/api/project')
        self.assertEqual(status, 200)
        self.assertEqual(project['links'][0]['url'], '/sampling.html')
        self.assertEqual(self.request('GET', '/api/sampling/sources')[1]['sources'][0]['id'], 'walk')
        self.assertEqual(self.request('GET', '/api/sampling/source?id=walk')[1]['frameCount'], 80)
        status, state = self.request('POST', '/api/sampling/mutate', {'operation': 'create',
            'expectedRevision': 0, 'payload': {'name': 'Eight', 'sourceId': 'walk',
                'frames': list(range(0, 80, 10)), 'timing': 'source'}})
        self.assertEqual(status, 200)
        plan_id = state['plans'][0]['id']
        self.assertTrue(state['plans'][0]['phaseAudit']['paired'])
        self.assertEqual(self.request('GET', '/api/sampling/export')[0], 400)
        self.assertEqual(self.request('POST', '/api/sampling/mutate', {'operation': 'select',
            'expectedRevision': 0, 'payload': {'id': plan_id}})[0], 409)
        self.assertEqual(self.request('POST', '/api/sampling/mutate', {'operation': 'select',
            'expectedRevision': 1, 'payload': {'id': plan_id}})[0], 200)
        status, exported = self.request('GET', '/api/sampling/export')
        self.assertEqual(status, 200)
        self.assertEqual(exported['plan']['id'], plan_id)
        self.assertEqual(len(exported['samples']), 8)

    def test_cross_origin_and_invalid_input_cannot_write(self):
        status, _ = self.request('POST', '/api/sampling/mutate', {}, {'Origin': 'https://example.com'})
        self.assertEqual(status, 403)
        self.assertEqual(self.request('GET', '/api/sampling/state', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('POST', '/api/sampling/mutate', [1])[0], 400)
        self.assertEqual(self.request('GET', '/api/sampling/unknown')[0], 404)
        self.assertFalse((self.project.parent / 'plans.json').exists())


if __name__ == '__main__':
    unittest.main()
