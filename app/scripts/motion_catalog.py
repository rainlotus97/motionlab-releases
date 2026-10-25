"""Bounded local project discovery; projects share one HTTP listener and keep isolated URL namespaces."""
import hashlib
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse


PROJECT_FILE = 'studio.project.json'
MAX_CONFIG_BYTES = 65_536
MAX_SCAN_DEPTH = 8
MAX_SCAN_DIRECTORIES = 2_000
MAX_SCAN_ENTRIES = 20_000
MAX_PROJECTS = 100
MAX_DIRECTORY_ENTRIES = 2_000
MAX_OPEN_PROJECTS = 100
IGNORED_DIRECTORIES = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'exports'}


def directory_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip() or len(str(value)) > 4096:
        raise ValueError('Directory path must be a nonempty absolute path')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('Directory path must be absolute')
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Choose an existing directory')
    return path


def project_id(path):
    return 'project-' + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]


def project_metadata(path, root):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError('Project configuration must be a regular JSON file under 64 KB')
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('id'), str) or not config['id'].strip():
        raise ValueError('Unsupported project configuration')
    sources = config.get('sources', [])
    if not isinstance(sources, list) or any(not isinstance(source, dict) or source.get('type') not in ('registration', 'runtime', 'sequence') for source in sources):
        raise ValueError('Unsupported project sources')
    name = config.get('name', config['id'])
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise ValueError('Invalid project name')
    return {'id': project_id(path), 'configId': config['id'], 'name': name,
            'path': str(path), 'relativePath': path.relative_to(root).as_posix()}


def discover_projects(root):
    """Read only small named configurations; skip links and bound all traversal."""
    root = directory_path(root)
    pending = [(root, 0)]
    projects, skipped, examined, visited, truncated = [], 0, 0, 0, False
    while pending:
        if visited >= MAX_SCAN_DIRECTORIES or examined >= MAX_SCAN_ENTRIES or len(projects) >= MAX_PROJECTS:
            truncated = True
            break
        directory, depth = pending.pop()
        visited += 1
        entries = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    examined += 1
                    if examined > MAX_SCAN_ENTRIES:
                        truncated = True
                        break
                    entries.append(entry)
        except OSError:
            skipped += 1
            continue
        children = []
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            try:
                if entry.is_symlink():
                    continue
                if entry.name == PROJECT_FILE and entry.is_file(follow_symlinks=False):
                    try:
                        projects.append(project_metadata(Path(entry.path), root))
                    except (ValueError, OSError, TypeError):
                        skipped += 1
                    if len(projects) >= MAX_PROJECTS:
                        truncated = True
                        break
                elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.') and entry.name not in IGNORED_DIRECTORIES:
                    if depth < MAX_SCAN_DEPTH:
                        children.append((Path(entry.path), depth + 1))
                    else:
                        truncated = True
            except OSError:
                skipped += 1
        pending.extend(reversed(children))
    return {'root': str(root), 'projects': sorted(projects, key=lambda item: (item['name'].casefold(), item['relativePath'])),
            'truncated': truncated, 'skippedCount': skipped}


def browse_directories(value):
    path = directory_path(value)
    result, truncated = [], False
    with os.scandir(path) as iterator:
        for index, entry in enumerate(iterator):
            if index >= MAX_DIRECTORY_ENTRIES:
                truncated = True
                break
            try:
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink() and not entry.name.startswith('.'):
                    result.append({'name': entry.name, 'path': str(Path(entry.path))})
            except OSError:
                continue
    return {'path': str(path), 'parent': str(path.parent) if path != path.parent else None,
            'directories': sorted(result, key=lambda item: item['name'].casefold()), 'truncated': truncated}


class ProjectCatalog:
    def __init__(self, asset_root, handler_factory):
        self.lock = threading.RLock()
        self.handler_factory = handler_factory
        self.handlers = {}
        self.state = discover_projects(asset_root)
        self.server = None

    def snapshot(self, current_path):
        with self.lock:
            return {**self.state, 'currentProjectId': project_id(current_path),
                    'projects': [{**item, 'url': self.url(item['id'])} for item in self.state['projects']]}

    def choose_root(self, value, current_path):
        state = discover_projects(value)
        with self.lock:
            self.state = state
            return self.snapshot(current_path)

    def create_project(self, value, name, current_path):
        root = directory_path(value)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('请输入工程名称')
        path = root / PROJECT_FILE
        with path.open('x', encoding='utf-8') as file:
            json.dump({'version': 1, 'id': project_id(path), 'name': name.strip(), 'sources': []}, file, ensure_ascii=False, indent=2)
        with self.lock:
            self.state = discover_projects(root)
        return self.open_project(project_id(path))

    def url(self, identifier):
        return f'http://127.0.0.1:{self.server.server_port}/p/{identifier}/'

    def open_project(self, identifier):
        with self.lock:
            item = next((item for item in self.state['projects'] if item['id'] == identifier), None)
            if item is None:
                raise ValueError('Project is not in the selected asset directory; scan it again')
            if identifier not in self.handlers:
                if len(self.handlers) >= MAX_OPEN_PROJECTS:
                    raise ValueError('Too many open projects; restart the workbench')
                candidate = Path(item['path'])
                if candidate.is_symlink() or not candidate.resolve().is_relative_to(Path(self.state['root'])):
                    raise ValueError('Project path changed outside the selected asset directory; scan it again')
                self.handlers[identifier] = self.handler_factory(candidate, self)
            return {'id': item['id'], 'name': item['name'], 'path': item['path'], 'url': self.url(identifier)}


def catalog_handler(base_handler, catalog, project_path):
    class CatalogHandler(base_handler):
        def do_GET(self):
            route = urlparse(self.path)
            if route.path not in ('/api/catalog', '/api/catalog/directories'):
                return super().do_GET()
            if not self.local_request():
                self.json_response(403, {'error': 'Asset discovery requires a same-origin local studio request'})
                return
            try:
                if route.path == '/api/catalog':
                    result = catalog.snapshot(project_path)
                else:
                    query = parse_qs(route.query)
                    result = browse_directories(query.get('path', [catalog.snapshot(project_path)['root']])[0])
                self.json_response(200, result)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

        def do_POST(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/catalog/'):
                return super().do_POST()
            if route not in ('/api/catalog/root', '/api/catalog/open', '/api/catalog/create'):
                self.json_response(404, {'error': 'Unknown catalog API route'})
                return
            if not self.local_request():
                self.json_response(403, {'error': 'Asset selection requires a same-origin local studio request'})
                return
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 16_384 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Expected a JSON body of at most 16 KB')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Expected application/json')
                raw = self.rfile.read(int(length))
                if len(raw) != int(length):
                    raise ValueError('Incomplete catalog request')
                data = json.loads(raw)
                if route.endswith('/create'):
                    if not isinstance(data, dict) or set(data) != {'path', 'name'}:
                        raise ValueError('Expected directory path and project name')
                    self.json_response(200, catalog.create_project(data['path'], data['name'], project_path))
                    return
                field = 'path' if route.endswith('/root') else 'id'
                if not isinstance(data, dict) or set(data) != {field} or not isinstance(data[field], str):
                    raise ValueError(f'Expected an object containing only {field}')
                result = catalog.choose_root(data['path'], project_path) if field == 'path' else catalog.open_project(data['id'])
                self.json_response(200, result)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})

    return CatalogHandler
