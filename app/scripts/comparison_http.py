"""Same-origin HTTP adapter for frozen plan / artwork comparisons."""
import json
from pathlib import Path
from urllib.parse import urlparse

from comparison_store import get_state, save_review
from sampling_plans import ConflictError, StaleSourceError


def comparison_handler(base_handler, project_path, load_project, tool_project=None):
    project_path = Path(project_path).resolve()

    class ComparisonHandler(base_handler):
        def json_response(self, status, data):
            # Enrich the existing sampling/project response without loading its
            # art assets a second time or copying its route implementation.
            if status == 200 and self.command == 'GET' and urlparse(self.path).path == '/api/project':
                config = json.loads(project_path.read_text(encoding='utf-8'))
                if config.get('comparisons'):
                    data = dict(data)
                    data['links'] = [link for link in data.get('links', [])
                                     if link.get('url') != '/comparison.html']
                    data['links'].insert(0, {'name': '骨骼/人物逐帧对比', 'url': '/comparison.html'})
                    data['comparisonConfigured'] = True
            return super().json_response(status, data)

        def do_GET(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/comparison'):
                return super().do_GET()
            if route != '/api/comparison':
                self.json_response(404, {'error': 'Unknown comparison API route'})
                return
            if not self.local_request():
                self.json_response(403, {'error': 'Comparison requires a same-origin local studio request'})
                return
            try:
                self.json_response(200, get_state(project_path, load_project, tool_project))
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

        def do_POST(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/comparison'):
                return super().do_POST()
            if route != '/api/comparison/review':
                self.json_response(404, {'error': 'Unknown comparison API route'})
                return
            if not self.local_request():
                self.json_response(403, {'error': 'Comparison reviews require a same-origin local studio request'})
                return
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 200_000 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Expected a JSON body of at most 200 KB')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Expected application/json')
                data = json.loads(self.rfile.read(int(length)))
                self.json_response(200, save_review(project_path, data, load_project, tool_project))
            except (ConflictError, StaleSourceError) as error:
                self.json_response(409, {'error': str(error)})
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

    return ComparisonHandler
