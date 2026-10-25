#!/usr/bin/env python3
"""Shared read-only motion workbench server and explicit draft-to-manifest export."""
import argparse
import hashlib
import json
import math
import mimetypes
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Path outside configured workspace')
    return path


def label_text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError(f'{name} must be a nonempty string of at most 100 characters')
    return value


def marker_labels(value, name):
    if type(value) is not dict:
        raise ValueError(f'{name} must be an object of marker IDs and string labels')
    for marker, label in value.items():
        label_text(marker, f'{name} marker ID')
        label_text(label, f'{name}.{marker}')
    return dict(value)


CLIP_LIBRARY_FIELDS = ('characterId', 'characterName', 'sourceId', 'sourceName',
                       'variantId', 'variantName', 'actionId', 'actionName', 'direction')
DIRECTIONS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


def clip_library(config, source, character=None, variant_id='default', variant_name='默认外观',
                 action_id=None, action_name=None, action=None):
    """Authored metadata wins; only the documented -<compass> suffix is inferred."""
    character = character or {}
    action = action or {}
    full_action = action_id or source['id'].rsplit('/', 1)[-1]
    base_action, _, suffix = full_action.rpartition('-')
    direction = suffix if base_action and suffix in DIRECTIONS else None
    action_group = base_action if direction else full_action
    defaults = {
        'characterId': character.get('id', config['id']),
        'characterName': character.get('name', config.get('name', config['id'])),
        'sourceId': source['id'], 'sourceName': source.get('name', source['id']),
        'variantId': variant_id, 'variantName': variant_name,
        'actionId': action_group,
        'actionName': action.get('groupName', action_group if direction else action_name or source.get('name', full_action)),
        'direction': action.get('direction', direction),
    }
    metadata = {**defaults, **{key: source[key] for key in CLIP_LIBRARY_FIELDS if key in source}}
    for key, value in metadata.items():
        if key == 'direction' and value is None:
            continue
        label_text(value, key)
    if metadata['direction'] is not None and metadata['direction'] not in DIRECTIONS:
        raise ValueError('direction must be N, NE, E, SE, S, SW, W or NW')
    return metadata


def load_project(path):
    path = Path(path).resolve()
    root = path.parent
    config = read(path)
    if config.get('version') != 1 or not config.get('id'):
        raise ValueError('Project requires version 1 and stable id')
    clips = []
    provenance = {str(path): digest(path)}
    for source in config.get('sources', []):
        labels = marker_labels(source.get('markerLabels', {}), 'markerLabels')
        origin_label = label_text(source.get('originLabel', '导出原点'), 'originLabel')
        action_labels = source.get('markerLabelsByAction', {})
        if type(action_labels) is not dict:
            raise ValueError('markerLabelsByAction must be an object keyed by exact action IDs')
        action_labels = {label_text(action, 'markerLabelsByAction action ID'):
                         marker_labels(value, f'markerLabelsByAction.{action}')
                         for action, value in action_labels.items()}
        if source['type'] == 'sequence':
            from PIL import Image
            names = source.get('files', [])
            canvas, origin = source.get('canvas'), source.get('origin')
            duration = source.get('durationMs', 120)
            if not isinstance(names, list) or not 1 <= len(names) <= 256 or len(set(names)) != len(names):
                raise ValueError('Sequence requires 1..256 distinct image paths')
            if not isinstance(canvas, list) or len(canvas) != 2 or any(type(n) is not int or not 1 <= n <= 8192 for n in canvas):
                raise ValueError('Invalid sequence canvas')
            if not isinstance(origin, list) or len(origin) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in origin):
                raise ValueError('Invalid sequence origin')
            if type(duration) is not int or not 1 <= duration <= 60000 or type(source.get('loop', True)) is not bool:
                raise ValueError('Invalid sequence timing')
            clip = {'id': source['id'], 'name': source.get('name', source['id']), 'canvas': canvas,
                    'origin': origin, 'loop': source.get('loop', True), 'markerLabels': labels,
                    'originLabel': origin_label, 'frames': [], 'steps': []}
            clip.update(clip_library(config, source))
            for index, name in enumerate(names):
                image = inside(root, name)
                with Image.open(image) as bitmap:
                    if list(bitmap.size) != canvas or getattr(bitmap, 'n_frames', 1) != 1:
                        raise ValueError('Sequence images must be static and share a canvas')
                clip['frames'].append({'id': str(index), 'file': str(image), 'rect': [0, 0, *canvas], 'markers': {}})
                clip['steps'].append({'slot': index, 'frame': index, 'durationMs': duration, 'offset': [0, 0], 'root': [0, 0], 'events': []})
            if 'steps' in source:
                steps = source['steps']
                if not isinstance(steps, list) or not 1 <= len(steps) <= 1024:
                    raise ValueError('Sequence steps must contain 1..1024 playback segments')
                clip['steps'] = []
                for slot, step in enumerate(steps):
                    if (not isinstance(step, dict) or set(step) != {'frame', 'durationMs'} or
                            type(step['frame']) is not int or not 0 <= step['frame'] < len(names) or
                            type(step['durationMs']) is not int or not 1 <= step['durationMs'] <= 60000):
                        raise ValueError('Invalid sequence step frame or timing')
                    clip['steps'].append({'slot': slot, **step, 'offset': [0, 0], 'root': [0, 0], 'events': []})
            clips.append(clip)
            continue
        src = inside(root, source['path'])
        provenance[str(src)] = digest(src)
        data = read(src)
        if source['type'] == 'registration':
            if data.get('errors') or data.get('status') != 'registered-not-visual-approval':
                raise ValueError('Registration blocked or unsupported report')
            clip = {'id': source['id'], 'name': source.get('name', source['id']),
                    'canvas': data['canvas'], 'origin': data['origin'], 'loop': True,
                    'markerLabels': dict(labels), 'originLabel': origin_label, 'frames': [], 'steps': []}
            clip.update(clip_library(config, source))
            for index, record in enumerate(data['records']):
                image = inside(root, str(src.parent.relative_to(root) / (record['id'] + '.png')))
                clip['frames'].append({'id': record['id'], 'file': str(image),
                                       'rect': [0, 0, *data['canvas']], 'markers': record['markers']})
                clip['steps'].append({'slot': index, 'frame': index, 'durationMs': record['durationMs'],
                                      'offset': [0, 0], 'root': [0, 0], 'events': []})
            clips.append(clip)
        elif source['type'] == 'runtime':
            from motion_tool import verify
            verify(src)
            excluded = source.get('excludeActions', [])
            if not isinstance(excluded, list) or any(not isinstance(name, str) for name in excluded) or len(set(excluded)) != len(excluded):
                raise ValueError('excludeActions must contain distinct exact action IDs')
            known_actions = {name for value in data['variants'].values() for name in value['actions']}
            if set(excluded) - known_actions:
                raise ValueError('excludeActions contains an unknown action ID')
            if set(action_labels) - known_actions:
                raise ValueError('markerLabelsByAction contains an unknown exact action ID')
            for variant, value in data['variants'].items():
                for action_id, action in value['actions'].items():
                    if action_id in excluded:
                        continue
                    image = inside(root, str(src.parent.relative_to(root) / action['atlas']))
                    clip = {'id': f'{source["id"]}/{variant}/{action_id}',
                            'name': f'{value.get("name", variant)} · {action.get("name", action_id)}',
                            'canvas': data['canvas'], 'origin': data['origin'], 'loop': action['loop'],
                            'markerLabels': {**labels, **action_labels.get(action_id, {})},
                            'originLabel': origin_label,
                            'frames': [], 'steps': []}
                    clip.update(clip_library(config, source, data.get('character'), variant,
                                             value.get('name', variant), action_id,
                                             action.get('name', action_id), action))
                    for index, frame in enumerate(action['frames']):
                        original = data['sourceHashes'][frame['source']]
                        clip['frames'].append({'id': str(index), 'file': str(image),
                            'rect': frame['rect'], 'markers': frame.get('markers', {}),
                            'sourcePath': original['path'], 'sourceSha256': original['sha256'],
                            'sourceRect': frame['sourceRect'], 'sourceId': frame['source']})
                    for index, frame_index in enumerate(action['sequence']):
                        root_motion = action.get('root_motion')
                        clip['steps'].append({'slot': index, 'frame': frame_index,
                            'durationMs': action['durations_ms'][index], 'offset': [0, 0],
                            'root': [0, 0] if root_motion is None else root_motion[index],
                            'events': [event for event in action.get('events', []) if event['step'] == index]})
                    clips.append(clip)
        else:
            raise ValueError('Supported sources: registration or motion_tool runtime')
    if len({clip['id'] for clip in clips}) != len(clips):
        raise ValueError('Duplicate clip IDs')
    known_clips = {clip['id'] for clip in clips}
    excluded_clips = config.get('excludedClips', [])
    names = config.get('clipNames', {})
    if not isinstance(excluded_clips, list) or any(not isinstance(identifier, str) or identifier not in known_clips for identifier in excluded_clips):
        raise ValueError('excludedClips requires existing exact clip IDs')
    if not isinstance(names, dict) or any(key not in known_clips or not isinstance(value, str) or not value.strip() or len(value) > 100 for key, value in names.items()):
        raise ValueError('Invalid clipNames')
    clips = [clip for clip in clips if clip['id'] not in excluded_clips]
    metadata = config.get('clipMetadata', {})
    if not isinstance(metadata, dict) or set(metadata) - known_clips:
        raise ValueError('clipMetadata requires existing exact clip IDs')
    for identifier, values in metadata.items():
        if not isinstance(values, dict) or set(values) - set(CLIP_LIBRARY_FIELDS):
            raise ValueError(f'Invalid clipMetadata fields for {identifier}')
        for key, value in values.items():
            if key == 'direction' and value is None:
                continue
            label_text(value, f'clipMetadata.{identifier}.{key}')
        if values.get('direction') is not None and values['direction'] not in DIRECTIONS:
            raise ValueError('clipMetadata direction must be a compass direction or null')
    for clip in clips:
        clip['name'] = names.get(clip['id'], clip['name'])
        clip.update(metadata.get(clip['id'], {}))
    for clip in clips:
        if not clip['frames'] or not clip['steps']:
            raise ValueError('Empty animation')
        clip['canReorder'] = not any(step['events'] or step['root'] != [0, 0] for step in clip['steps'])
        clip['sourcePath'] = next((frame.get('sourcePath', frame['file']) for frame in clip['frames']), '')
        for frame in clip['frames']:
            image = Path(frame['file'])
            provenance[str(image)] = digest(image)
            frame['sourceFilePath'] = image.relative_to(root).as_posix()
            frame.setdefault('sourcePath', frame['sourceFilePath'])
            frame.setdefault('sourceSha256', provenance[str(image)])
            frame.setdefault('sourceRect', frame['rect'])
            frame.setdefault('sourceId', frame['id'])
            frame['url'] = '/project/' + quote(image.relative_to(root).as_posix())
    proof = []
    paths = config.get('provenancePaths', [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError('provenancePaths must be distinct project-relative JSON paths')
    for relative in paths:
        file = inside(root, relative)
        if file.suffix != '.json' or not file.is_file() or file.stat().st_size > 5_000_000:
            raise ValueError('Provenance proof must be an existing JSON file under 5 MB')
        read(file)
        provenance[str(file)] = digest(file)
        proof.append({'path': file.relative_to(root).as_posix(), 'sha256': provenance[str(file)], 'bytes': file.stat().st_size})
    if sum(p['bytes'] for p in proof) > 10_000_000:
        raise ValueError('Provenance proof exceeds 10 MB')
    fingerprint = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    links = []
    for item in config.get('links', []):
        target = inside(root, item['path'])
        if not target.is_file():
            raise ValueError(f'Missing workbench link: {target}')
        links.append({'name': item['name'], 'url': '/project/' + quote(target.relative_to(root).as_posix())})
    return {'version': 1, 'id': config['id'], 'name': config.get('name', config['id']),
            'fingerprint': fingerprint, 'clips': clips, 'links': links, 'provenanceFiles': proof}, root


def validate_edit(clip, edit):
    if not isinstance(edit, dict):
        raise ValueError('Clip edit must be an object')
    if type(edit.get('loop')) is not bool:
        raise ValueError('loop must be boolean')
    steps = edit.get('steps', [])
    if not isinstance(steps, list) or len(steps) != len(clip['steps']) or any(not isinstance(s, dict) for s in steps):
        raise ValueError('This editor reorders existing steps; it does not add or remove poses')
    slots = [step.get('slot') for step in steps]
    if any(type(slot) is not int for slot in slots) or sorted(slots) != list(range(len(steps))):
        raise ValueError('Each original slot must occur once')
    if not clip['canReorder'] and slots != list(range(len(steps))):
        raise ValueError('Reordering event/root-motion clips requires explicit event redesign')
    for step in steps:
        ms = step.get('durationMs')
        offset = step.get('offset')
        if type(ms) is not int or not 1 <= ms <= 60000:
            raise ValueError('durationMs must be integer 1..60000')
        if not isinstance(offset, list) or len(offset) != 2 or any(
                type(x) not in (int, float) or not math.isfinite(x) or not -max(clip['canvas']) <= x <= max(clip['canvas']) for x in offset):
            raise ValueError('Invalid frame offset')
    return steps


def export_manifest(project_path, draft_path, output):
    project, _ = load_project(project_path)
    draft = read(draft_path)
    if draft.get('version') != 1 or draft.get('projectId') != project['id'] or draft.get('fingerprint') != project['fingerprint']:
        raise ValueError('Draft belongs to a different project or changed source assets')
    edits = draft.get('clips', {})
    if set(edits) != {clip['id'] for clip in project['clips']}:
        raise ValueError('Draft clip set differs from project')
    if not project['clips']:
        raise ValueError('No rendered clips to export')
    canvas, origin = project['clips'][0]['canvas'], project['clips'][0]['origin']
    result = {'version': 1, 'character': {'id': 'studio-character', 'name': project['name']},
              'canvas': canvas, 'origin': origin, 'scale': 1, 'background': 'transparent',
              'sources': {}, 'variants': {'default': {'actions': {}}},
              'studioProvenance': {'projectId': project['id'], 'fingerprint': project['fingerprint'],
                                   'authoringOnly': True, 'clipMap': {}}}
    for ci, clip in enumerate(project['clips']):
        if clip['canvas'] != canvas or clip['origin'] != origin:
            raise ValueError('Export each character with a shared logical canvas/origin')
        edit = edits[clip['id']]
        steps = validate_edit(clip, edit)
        action = {'name': clip['name'], 'loop': edit['loop'], 'frames': [], 'durations_ms': [], 'events': [], 'root_motion': []}
        for index, step in enumerate(steps):
            original = clip['steps'][step['slot']]
            frame = clip['frames'][original['frame']]
            sid = f'c{ci}f{original["frame"]}'
            result['sources'][sid] = {'path': frame['file'], 'pixel_ratio': 1}
            action['frames'].append({'source': sid, 'rect': frame['rect'],
                'anchor': [origin[axis] - step['offset'][axis] for axis in range(2)], 'markers': frame['markers']})
            action['durations_ms'].append(step['durationMs'])
            action['root_motion'].append(original['root'])
            action['events'].extend([{**event, 'step': index} for event in original['events']])
        result['variants']['default']['actions'][f'clip{ci}'] = action
        result['studioProvenance']['clipMap'][f'default/clip{ci}'] = {
            'sourceClipId': clip['id'],
            **{key: clip[key] for key in CLIP_LIBRARY_FIELDS if key in clip}}
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Export to a new manifest path; original files are not overwritten')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def make_handler(path, catalog, tool_project=None):
    path = Path(path).resolve()
    project, root = load_project(path)
    from motion_catalog import catalog_handler
    assets = Path(__file__).resolve().parents[1] / 'assets' / 'studio'
    export_lock = threading.BoundedSemaphore(1)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def json_response(self, status, data):
            body = json.dumps(scope_urls(data, getattr(self, 'route_prefix', '')), ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origin = self.headers.get('Origin')
            return host in allowed and (origin is None or origin == 'http://' + host) and self.headers.get('Sec-Fetch-Site') not in ('cross-site', 'same-site')

        def do_OPTIONS(self):
            self.json_response(403, {'error': 'Cross-origin export is disabled'})

        def do_POST(self):
            if urlparse(self.path).path != '/api/export':
                self.json_response(404, {'error': 'Unknown API route'})
                return
            if not self.local_request():
                self.json_response(403, {'error': 'Export requires a same-origin local studio request'})
                return
            if not export_lock.acquire(blocking=False):
                self.json_response(429, {'error': 'An export is already running; wait for it to finish'})
                return
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 2_000_000 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Export body must be 1..2000000 bytes with Content-Length')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('Export requires application/json')
                raw = self.rfile.read(int(length))
                if len(raw) != int(length): raise ValueError('Incomplete export request')
                request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
                if not isinstance(request, dict) or set(request) - {'draft', 'options'}:
                    raise ValueError('Request must contain only draft and options')
                if 'draft' not in request: raise ValueError('Export requires the current matching draft')
                from sprite_export import export_package
                exports = root / 'exports'
                if exports.is_symlink(): raise ValueError('Export directory must not be a symlink')
                exports.mkdir(exist_ok=True)
                export_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
                output = inside(exports, export_id)
                result = export_package(path, output, request['draft'], request.get('options'))
                base = '/project/exports/' + export_id + '/'
                result.update({'exportId': export_id, 'downloadUrl': base + 'package.zip',
                               'manifestUrl': base + 'package.json'})
                if (output / 'preview.html').is_file(): result['previewUrl'] = base + 'preview.html'
                result['report']['zipBytes'] = (output / 'package.zip').stat().st_size
                result.pop('packagePath', None)
                self.json_response(201, result)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_response(400, {'error': str(error)})
            finally:
                export_lock.release()

        def do_GET(self):
            route = unquote(urlparse(self.path).path)
            try:
                if route == '/api/project':
                    # Reload for each page opening; changed assets invalidate old adjustments.
                    current, _ = load_project(path)
                    body = json.dumps(current, ensure_ascii=False).encode()
                    kind = 'application/json; charset=utf-8'
                else:
                    file = inside(root, route[len('/project/'):]) if route.startswith('/project/') else inside(assets, 'index.html' if route == '/' else route.lstrip('/'))
                    body = file.read_bytes()
                    kind = 'text/javascript' if file.suffix == '.mjs' else mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, OSError, KeyError) as error:
                self.send_error(404, str(error))
    from sampling_http import sampling_handler
    from comparison_http import comparison_handler
    from studio_files import file_handler
    from studio_exports import export_job_handler
    handler = export_job_handler(catalog_handler(file_handler(comparison_handler(sampling_handler(Handler, path, load_project, tool_project), path, load_project, tool_project), path, load_project), catalog, path), path, export_lock)
    return handler


def scope_urls(value, prefix):
    if isinstance(value, dict):
        return {key: scope_urls(item, prefix) for key, item in value.items()}
    if isinstance(value, list):
        return [scope_urls(item, prefix) for item in value]
    if isinstance(value, str) and value.startswith(('/project/', '/sampling.html', '/comparison.html')):
        return prefix + value
    return value


def make_server(path, port=8911, asset_root=None, tool_project=None):
    from motion_catalog import ProjectCatalog, project_id
    path = Path(path).resolve()
    catalog = ProjectCatalog(asset_root or path.parent,
        lambda child, shared: make_handler(child, shared, tool_project))
    default = make_handler(path, catalog, tool_project)
    catalog.handlers[project_id(path)] = default

    class Router(default):
        def dispatch(self, method):
            route = urlparse(self.path).path
            self.route_prefix = ''
            if route == '/api/desktop/health' and method == 'do_GET':
                if not self.local_request():
                    self.json_response(403, {'error': 'Local request required'})
                elif hasattr(self.server, 'instance_identity'):
                    self.json_response(200, {'service': 'character-motion-studio', **self.server.instance_identity})
                else:
                    self.json_response(404, {'error': 'Not an app-owned service'})
                return
            handler = default
            if route.startswith('/p/'):
                parts = self.path.split('/', 3)
                identifier = parts[2]
                handler = catalog.handlers.get(identifier)
                if handler is None:
                    try:
                        catalog.open_project(identifier)
                        handler = catalog.handlers[identifier]
                    except (ValueError, OSError):
                        self.send_error(404, 'Unknown project')
                        return
                self.route_prefix = '/p/' + identifier
                self.path = '/' + (parts[3] if len(parts) > 3 else '')
            # Each request binds its own handler; no mutable global current project.
            self.__class__ = type('ScopedRequest', (handler,), {})
            getattr(handler, method)(self)

        def do_GET(self):
            self.dispatch('do_GET')

        def do_POST(self):
            self.dispatch('do_POST')

    server = ThreadingHTTPServer(('127.0.0.1', port), Router)
    server.daemon_threads = True
    server.catalog = catalog
    catalog.server = server
    return server


def serve(path, port, asset_root=None):
    project, _ = load_project(path)
    server = make_server(path, port, asset_root=asset_root)
    print(f'Motion studio: http://127.0.0.1:{server.server_port}/ · {project["name"]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('serve', 'inspect', 'apply', 'export'):
        command = sub.add_parser(cmd)
        command.add_argument('--project', type=Path, required=True)
        if cmd == 'serve':
            command.add_argument('--port', type=int, default=8911)
            command.add_argument('--asset-root', type=Path, help='Discovery directory for studio.project.json projects (default: current project directory)')
        if cmd == 'apply':
            command.add_argument('--draft', type=Path, required=True)
            command.add_argument('--out', type=Path, required=True)
        if cmd == 'export':
            command.add_argument('--out', type=Path, required=True)
            command.add_argument('--draft', type=Path)
            command.add_argument('--preset', choices=('lossless', 'balanced', 'compact'), default='balanced')
            command.add_argument('--clip', action='append', dest='clipIds')
            command.add_argument('--scale', type=float, choices=(1, .75, .5, .25))
            command.add_argument('--format', choices=('webp', 'png'))
            command.add_argument('--quality', type=int)
            command.add_argument('--max-page-size', type=int, choices=(1024, 2048, 4096), default=2048)
            command.add_argument('--include-frames', action='store_true')
            command.add_argument('--include-preview', action='store_true')
            command.add_argument('--include-proofs', action='store_true')
    args = parser.parse_args()
    if args.command == 'serve': serve(args.project, args.port, args.asset_root)
    elif args.command == 'inspect': print(json.dumps(load_project(args.project)[0], ensure_ascii=False, indent=2))
    elif args.command == 'apply': export_manifest(args.project, args.draft, args.out)
    else:
        from sprite_export import export_package
        options = {k: v for k, v in vars(args).items() if k in ('preset', 'clipIds', 'scale', 'format', 'quality') and v is not None}
        options.update({'maxPageSize': args.max_page_size, 'includeFrames': args.include_frames,
                        'includePreview': args.include_preview, 'includeProofs': args.include_proofs})
        result = export_package(args.project, args.out, read(args.draft) if args.draft else None, options)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
