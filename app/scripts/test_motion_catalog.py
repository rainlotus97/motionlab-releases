import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlparse

from motion_catalog import browse_directories, discover_projects, project_id
from motion_studio import make_server


def write_project(root, name, config_id='same-config-id'):
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'studio.project.json'
    path.write_text(json.dumps({'version': 1, 'id': config_id, 'name': name, 'sources': []}), encoding='utf-8')
    (root / 'identity.txt').write_text(name, encoding='utf-8')
    return path


class CatalogDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_named_supported_projects_only_and_external_links_not_followed(self):
        first = write_project(self.root / 'art' / 'one', 'One')
        second = write_project(self.root / 'evidence' / 'two', 'Two')
        write_project(self.root / 'node_modules' / 'ignored', 'Ignored')
        bad = self.root / 'unsupported'
        bad.mkdir()
        (bad / 'studio.project.json').write_text('{"version":2,"id":"bad"}')
        (self.root / 'arbitrary.json').write_text('not inspected')
        with tempfile.TemporaryDirectory() as external:
            write_project(Path(external) / 'outside', 'Outside')
            (self.root / 'external-link').symlink_to(external, target_is_directory=True)
            state = discover_projects(self.root)
        self.assertEqual({item['path'] for item in state['projects']}, {str(first), str(second)})
        self.assertNotEqual(project_id(first), project_id(second))
        self.assertEqual(state['skippedCount'], 1)
        self.assertFalse(state['truncated'])

    def test_bounded_scan_and_large_or_symlinked_configuration(self):
        for name in ('a', 'b', 'c'):
            write_project(self.root / name, name)
        with patch('motion_catalog.MAX_PROJECTS', 2):
            state = discover_projects(self.root)
        self.assertEqual(len(state['projects']), 2)
        self.assertTrue(state['truncated'])
        with patch('motion_catalog.MAX_SCAN_DEPTH', 0):
            state = discover_projects(self.root)
        self.assertEqual(state['projects'], [])
        self.assertTrue(state['truncated'])
        large = self.root / 'a' / 'studio.project.json'
        large.write_text(' ' * 65_537)
        (self.root / 'b' / 'studio.project.json').unlink()
        (self.root / 'b' / 'studio.project.json').symlink_to(self.root / 'c' / 'studio.project.json')
        state = discover_projects(self.root)
        self.assertEqual([item['name'] for item in state['projects']], ['c'])
        self.assertEqual(state['skippedCount'], 1)

    def test_directory_browser_and_invalid_paths(self):
        write_project(self.root / 'visible', 'Visible')
        (self.root / '.hidden').mkdir()
        (self.root / 'linked').symlink_to(self.root / 'visible', target_is_directory=True)
        data = browse_directories(self.root)
        self.assertEqual(data['path'], str(self.root))
        self.assertEqual(data['parent'], str(self.root.parent))
        self.assertEqual([item['name'] for item in data['directories']], ['visible'])
        for bad in ('relative', self.root / 'missing', self.root / 'visible' / 'studio.project.json', '', None):
            with self.subTest(path=bad):
                with self.assertRaises((ValueError, OSError)):
                    discover_projects(bad)


class CatalogHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.first = write_project(self.root / 'first', 'First')
        self.second = write_project(self.root / 'second', 'Second')
        self.before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.server = make_server(self.first, 0, asset_root=self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, route, data=None, headers=None, port=None, raw=False):
        connection = http.client.HTTPConnection('127.0.0.1', port or self.server.server_port, timeout=5)
        connection.request(method, route, json.dumps(data) if data is not None else None,
                           {'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, content.decode() if raw else json.loads(content)

    def test_open_reuses_listener_and_old_project_and_assets_remain_bound(self):
        status, state = self.request('GET', '/api/catalog')
        self.assertEqual(status, 200)
        self.assertEqual(len(state['projects']), 2)
        self.assertEqual(state['currentProjectId'], project_id(self.first))
        status, child = self.request('POST', '/api/catalog/open', {'id': project_id(self.second)})
        self.assertEqual(status, 200, child)
        child_port = urlparse(child['url']).port
        self.assertEqual(child_port, self.server.server_port)
        prefix = urlparse(child['url']).path.rstrip('/')
        self.assertEqual(self.request('POST', '/api/catalog/open', {'id': project_id(self.second)})[1]['url'], child['url'])
        self.assertEqual(self.request('GET', prefix + '/api/project', port=child_port)[1]['name'], 'Second')
        self.assertEqual(self.request('GET', '/api/project')[1]['name'], 'First')
        self.assertEqual(self.request('GET', prefix + '/project/identity.txt', port=child_port, raw=True)[1], 'Second')
        self.assertEqual(self.request('GET', '/project/identity.txt', raw=True)[1], 'First')
        self.assertEqual(self.request('GET', prefix + '/api/catalog', port=child_port)[1]['currentProjectId'], project_id(self.second))
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, self.before)

    def test_root_selection_changes_catalog_only_and_failed_root_keeps_state(self):
        status, state = self.request('POST', '/api/catalog/root', {'path': str(self.second.parent)})
        self.assertEqual(status, 200)
        self.assertEqual([item['name'] for item in state['projects']], ['Second'])
        self.assertEqual(state['currentProjectId'], project_id(self.first))
        self.assertEqual(self.request('GET', '/api/project')[1]['name'], 'First')
        self.assertEqual(self.request('POST', '/api/catalog/root', {'path': str(self.root / 'missing')})[0], 400)
        self.assertEqual(self.request('GET', '/api/catalog')[1]['root'], str(self.second.parent))
        self.assertEqual(self.request('POST', '/api/catalog/open', {'id': project_id(self.first)})[0], 400)
        status, data = self.request('GET', '/api/catalog/directories?path=' + quote(str(self.root)))
        self.assertEqual(status, 200)
        self.assertEqual([item['name'] for item in data['directories']], ['first', 'second'])

    def test_create_in_empty_directory_preserves_assets_and_rejects_overwrite(self):
        root = self.root / 'new-project'
        root.mkdir()
        (root / 'notes.md').write_text('keep')
        status, opened = self.request('POST', '/api/catalog/create', {'path': str(root), 'name': 'New'})
        self.assertEqual(status, 200, opened)
        child = urlparse(opened['url']).port
        self.assertEqual(self.request('GET', urlparse(opened['url']).path + 'api/project', port=child)[1]['clips'], [])
        before = (root / 'studio.project.json').read_bytes()
        self.assertEqual(self.request('POST', '/api/catalog/create', {'path': str(root), 'name': 'Again'})[0], 400)
        self.assertEqual((root / 'studio.project.json').read_bytes(), before)
        self.assertEqual((root / 'notes.md').read_text(), 'keep')

    def test_catalog_routes_enforce_same_origin_and_reject_invalid_bodies(self):
        original = self.request('GET', '/api/catalog')[1]
        for headers in ({'Origin': 'https://attacker.example'}, {'Host': 'attacker.example'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('GET', '/api/catalog', headers=headers)[0], 403)
                self.assertEqual(self.request('GET', '/api/catalog/directories', headers=headers)[0], 403)
                self.assertEqual(self.request('POST', '/api/catalog/root', {'path': str(self.root)}, headers)[0], 403)
                self.assertEqual(self.request('POST', '/api/catalog/open', {'id': project_id(self.second)}, headers)[0], 403)
        for body in ({'path': 'relative'}, {'path': str(self.root), 'extra': True}, [], {'path': None}):
            self.assertEqual(self.request('POST', '/api/catalog/root', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/catalog/open', {'id': 'missing'})[0], 400)
        self.assertEqual(self.request('POST', '/api/catalog/open', {'id': project_id(self.second)}, {'Content-Length': '16385'})[0], 400)
        self.assertEqual(self.request('POST', '/api/catalog/root', {'path': str(self.root)}, {'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('POST', '/api/catalog/unknown', {})[0], 404)
        self.assertEqual(self.request('GET', '/api/catalog')[1], original)
        self.assertEqual(len(self.server.catalog.handlers), 1)

    def test_invalid_discovered_asset_cannot_open_server(self):
        config = json.loads(self.second.read_text())
        config['sources'] = [{'id': 'missing', 'type': 'registration', 'path': 'missing.json'}]
        self.second.write_text(json.dumps(config))
        self.request('POST', '/api/catalog/root', {'path': str(self.root)})
        status, result = self.request('POST', '/api/catalog/open', {'id': project_id(self.second)})
        self.assertEqual(status, 400, result)
        self.assertEqual(len(self.server.catalog.handlers), 1)
        self.assertEqual(self.request('GET', '/api/project')[1]['name'], 'First')


if __name__ == '__main__':
    unittest.main()
