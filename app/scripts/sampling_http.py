"""Same-origin HTTP adapter for project-local skeleton sampling plans."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sampling_plans import (ConflictError, StaleSourceError, export_plan, get_source,
                            get_state, list_sources, mutate)


def sampling_handler(base_handler, project_path, load_project, tool_project=None):
    project_path = Path(project_path).resolve()

    sampling_path = Path(tool_project).resolve() if tool_project else project_path

    class SamplingHandler(base_handler):
        def do_GET(self):
            route = urlparse(self.path)
            if route.path == '/api/project':
                try:
                    data, _ = load_project(project_path)
                    config = json.loads(sampling_path.read_text(encoding='utf-8'))
                    if config.get('sampling'):
                        data['links'].insert(0, {'name': '骨骼抽样方案', 'url': '/sampling.html'})
                        data['samplingConfigured'] = True
                    self.json_response(200, data)
                except (ValueError, OSError, KeyError) as error:
                    self.json_response(400, {'error': str(error)})
                return
            if not route.path.startswith('/api/sampling/'):
                return super().do_GET()
            if not self.local_request():
                self.json_response(403, {'error': 'Sampling requires a same-origin local studio request'})
                return
            query = parse_qs(route.query)
            value = lambda key: query.get(key, [None])[0]
            try:
                if route.path == '/api/sampling/sources':
                    result = {'sources': list_sources(sampling_path), 'projectPath': str(sampling_path)}
                elif route.path == '/api/sampling/source':
                    result = get_source(sampling_path, value('id'), value('profile'))
                elif route.path == '/api/sampling/state':
                    result = get_state(sampling_path)
                elif route.path == '/api/sampling/export':
                    result = export_plan(sampling_path, value('id'))
                else:
                    self.json_response(404, {'error': 'Unknown sampling API route'})
                    return
                self.json_response(200, result)
            except (ConflictError, StaleSourceError) as error:
                self.json_response(409, {'error': str(error)})
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

        def do_POST(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/sampling/'):
                return super().do_POST()
            if route != '/api/sampling/mutate':
                self.json_response(404, {'error': 'Unknown sampling API route'})
                return
            if not self.local_request():
                self.json_response(403, {'error': 'Plan changes require a same-origin local studio request'})
                return
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 200_000 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Expected a JSON body of at most 200 KB')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Expected application/json')
                data = json.loads(self.rfile.read(int(length)))
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                result = mutate(sampling_path, data.get('operation'), data.get('payload'), data.get('expectedRevision'))
                self.json_response(200, result)
            except (ConflictError, StaleSourceError) as error:
                self.json_response(409, {'error': str(error)})
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

    return SamplingHandler
