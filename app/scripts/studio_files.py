"""Project-scoped asset operations. Mutations are versioned and removals are recoverable."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from PIL import Image

IMAGE_TYPES = {'.png', '.webp', '.jpg', '.jpeg', '.gif'}
TEXT_TYPES = {'.json', '.md', '.txt'}
MAX_FILE = 24 * 1024 * 1024
MACHINE_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
DIRECTIONS = {'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'}


def sequence_identity(value):
    if value is None:
        return {}, 'sequence-' + secrets.token_hex(5)
    if not isinstance(value, dict):
        raise ValueError('动作分类无效')
    allowed = {'characterId', 'characterName', 'variantId', 'variantName', 'actionId', 'actionName', 'direction'}
    if set(value) - allowed:
        raise ValueError('动作分类包含未知字段')
    metadata = {}
    for kind in ('character', 'variant', 'action'):
        identifier = value.get(kind + 'Id')
        if not isinstance(identifier, str) or len(identifier) > 64 or not MACHINE_ID.fullmatch(identifier):
            raise ValueError('角色、衣装和动作 ID 使用小写字母、数字及连字符，最长 64 位')
        name = value.get(kind + 'Name', identifier)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('角色、衣装和动作名称不能为空，最长 100 字')
        metadata[kind + 'Id'] = identifier
        metadata[kind + 'Name'] = name.strip()
    direction = value.get('direction') or None
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError('方向必须是 N、NE、E、SE、S、SW、W、NW 或空')
    metadata['direction'] = direction
    identifier = '--'.join([metadata['characterId'], metadata['variantId'], metadata['actionId'], *([direction.lower()] if direction else [])])
    if len(identifier) > 100:
        raise ValueError('角色、衣装、动作与方向的组合 ID 最长 100 位，请缩短分类 ID')
    return metadata, identifier


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


class AssetFiles:
    def __init__(self, project_path, loader):
        self.project_path = Path(project_path).resolve()
        self.root = self.project_path.parent
        self.loader = loader
        self.lock = threading.RLock()

    def path(self, relative, allow_root=False):
        if not isinstance(relative, str) or len(relative) > 2048:
            raise ValueError('路径无效')
        value = Path(relative)
        if value.is_absolute() or '..' in value.parts or any(part.startswith('.') for part in value.parts):
            raise ValueError('请选择工程内的普通文件或目录')
        target = self.root / value
        for part in [target, *target.parents]:
            if part == self.root: break
            if part.is_symlink(): raise ValueError('不支持符号链接')
        if not target.resolve().is_relative_to(self.root) or (target == self.root and not allow_root):
            raise ValueError('不能修改工程根目录')
        return target

    def protected(self):
        found = {self.project_path}
        queue = [self.project_path]
        # Follow existing local JSON references so renaming a source cannot silently break a runtime.
        while queue:
            file = queue.pop()
            if len(found) > 3000: raise ValueError('引用过多，请先缩小工程范围')
            if file.suffix != '.json' or file.stat().st_size > 5_000_000: continue
            try: value = json.loads(file.read_text())
            except (ValueError, UnicodeError): continue
            def walk(node):
                if isinstance(node, dict):
                    for item in node.values(): yield from walk(item)
                elif isinstance(node, list):
                    for item in node: yield from walk(item)
                elif isinstance(node, str) and len(node) < 2048: yield node
            for value in walk(value):
                for candidate in (file.parent / value, self.root / value):
                    try:
                        candidate = candidate.resolve()
                        if candidate.is_relative_to(self.root) and candidate.is_file() and candidate not in found:
                            found.add(candidate)
                            if candidate.suffix == '.json': queue.append(candidate)
                    except (ValueError, OSError): pass
        return found

    def assert_mutable(self, path):
        blocked = [p for p in self.protected() if p == path or p.is_relative_to(path)]
        if blocked:
            names = ', '.join(str(p.relative_to(self.root)) for p in sorted(blocked)[:3])
            raise ValueError('仍被工程引用：' + names)

    def listing(self, relative=''):
        path = self.path(relative, True)
        if not path.is_dir(): raise ValueError('目录不存在')
        protected = self.protected()
        entries = []
        for index, child in enumerate(sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))):
            if index >= 2000: raise ValueError('目录超过 2000 项，请选择更小的目录')
            if child.name.startswith('.') or child.is_symlink(): continue
            stat = child.stat()
            entries.append({'name': child.name, 'path': child.relative_to(self.root).as_posix(),
                'kind': 'directory' if child.is_dir() else 'image' if child.suffix.lower() in IMAGE_TYPES else 'text' if child.suffix.lower() in TEXT_TYPES else 'file',
                'bytes': stat.st_size if child.is_file() else 0, 'modified': stat.st_mtime,
                'referenced': child in protected or any(p.is_relative_to(child) for p in protected) if child.is_dir() else child in protected})
        return {'root': str(self.root), 'path': path.relative_to(self.root).as_posix(), 'entries': entries}

    def inspect(self, relative):
        path = self.path(relative)
        if not path.is_file(): raise ValueError('文件不存在')
        result = {'path': relative, 'sha256': sha(path), 'bytes': path.stat().st_size, 'referenced': path in self.protected()}
        if path.suffix.lower() in TEXT_TYPES and path.stat().st_size <= 1_000_000:
            result['text'] = path.read_text(encoding='utf-8')
        elif path.suffix.lower() in IMAGE_TYPES:
            with Image.open(path) as image: result['size'] = list(image.size)
        return result

    def backup(self, path, operation):
        base = self.root / '.motion-studio-trash'
        if base.is_symlink(): raise ValueError('回收站路径无效')
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(5)
        directory = base / identifier
        directory.mkdir(parents=True)
        record = {'id': identifier, 'path': path.relative_to(self.root).as_posix(), 'operation': operation,
                  'time': datetime.now(timezone.utc).isoformat()}
        (directory / 'record.json').write_text(json.dumps(record, ensure_ascii=False))
        return directory, record

    def write(self, path, data, expected=None):
        if len(data) > MAX_FILE: raise ValueError('文件超过 24 MiB')
        if not path.parent.is_dir(): raise ValueError('目标目录不存在')
        if path.exists():
            if not path.is_file() or expected != sha(path): raise ValueError('文件已变化，请刷新后重试')
            directory, _ = self.backup(path, 'replace')
            (directory / 'content').write_bytes(path.read_bytes())
        elif expected is not None: raise ValueError('文件已被移除，请刷新')
        temporary = path.parent / ('.studio-write-' + secrets.token_hex(6))
        try:
            temporary.write_bytes(data)
            if not path.exists():
                os.link(temporary, path)  # exclusive creation; do not replace a concurrent file
            else:
                if sha(path) != expected: raise ValueError('文件已变化，请刷新后重试')
                os.replace(temporary, path)
        finally: temporary.unlink(missing_ok=True)
        return {'path': path.relative_to(self.root).as_posix(), 'sha256': sha(path)}

    def mutate(self, operation, data):
        with self.lock:
            if operation == 'restore':
                identifier = data.get('id', '')
                if not isinstance(identifier, str) or not identifier.replace('-', '').isalnum(): raise ValueError('回收记录无效')
                directory = self.root / '.motion-studio-trash' / identifier
                if directory.parent.is_symlink() or directory.is_symlink() or (directory / 'content').is_symlink(): raise ValueError('回收记录无效')
                record = json.loads((directory / 'record.json').read_text())
                target = self.path(data.get('target', record['path']))
                if target.exists(): raise ValueError('原位置已有同名文件')
                if not target.parent.is_dir(): raise ValueError('原目录不存在')
                os.rename(directory / 'content', target)
                (directory / 'record.json').unlink(); directory.rmdir()
                return {'path': target.relative_to(self.root).as_posix()}
            path = self.path(data.get('path', ''))
            if operation == 'mkdir':
                path.mkdir(); return {'path': data['path']}
            if operation == 'upload':
                if path.exists(): raise ValueError('文件已存在，请改名后导入')
                if path.suffix.lower() not in IMAGE_TYPES | TEXT_TYPES: raise ValueError('支持 PNG、WebP、JPEG、GIF、JSON、MD、TXT')
                content = base64.b64decode(data.get('content', ''), validate=True)
                if path.suffix.lower() in IMAGE_TYPES:
                    with Image.open(io.BytesIO(content)) as image:
                        if image.width * image.height > 32_000_000: raise ValueError('图片超过 3200 万像素')
                        image.verify()
                else:
                    content.decode('utf-8')
                    if path.suffix.lower() == '.json': json.loads(content)
                return self.write(path, content)
            if operation == 'write':
                self.assert_mutable(path)
                if path.suffix.lower() not in TEXT_TYPES: raise ValueError('此处仅编辑 JSON、MD、TXT')
                content = data.get('text')
                if not isinstance(content, str) or len(content.encode()) > 1_000_000: raise ValueError('文本超过 1 MB')
                if path.suffix.lower() == '.json': json.loads(content)
                return self.write(path, content.encode(), data.get('sha256'))
            if operation in ('rename', 'trash'):
                if not path.exists(): raise ValueError('文件已不存在')
                self.assert_mutable(path)
                if path.is_file() and data.get('sha256') != sha(path): raise ValueError('文件已变化，请刷新后重试')
                if operation == 'rename':
                    target = self.path(data.get('target', ''))
                    if target.exists(): raise ValueError('目标名称已存在')
                    if not target.parent.is_dir(): raise ValueError('目标目录不存在')
                    path.rename(target); return {'path': target.relative_to(self.root).as_posix()}
                directory, record = self.backup(path, 'delete')
                path.rename(directory / 'content')
                return record
            raise ValueError('未知文件操作')

    def trash(self):
        root = self.root / '.motion-studio-trash'
        if root.is_symlink(): raise ValueError('回收站路径无效')
        entries = []
        if root.exists():
            for directory in sorted(root.iterdir(), reverse=True)[:500]:
                if not directory.is_symlink() and (directory / 'record.json').is_file():
                    entries.append(json.loads((directory / 'record.json').read_text()))
        return {'entries': entries}

    def add_sequence(self, data):
        with self.lock:
            expected = sha(self.project_path)
            project, _ = self.loader(self.project_path)
            if data.get('fingerprint') != project['fingerprint']: raise ValueError('工程已变化，请刷新')
            files = data.get('files')
            if not isinstance(files, list) or not 1 <= len(files) <= 256 or len(set(files)) != len(files): raise ValueError('请选择 1–256 张不同的序列图片')
            dimensions = set()
            for name in files:
                path = self.path(name)
                if path.suffix.lower() not in IMAGE_TYPES: raise ValueError('序列必须是图片')
                with Image.open(path) as image:
                    if getattr(image, 'n_frames', 1) != 1: raise ValueError('请选择静态序列帧')
                    dimensions.add(image.size)
            if len(dimensions) != 1: raise ValueError('序列画布尺寸不一致')
            canvas = list(dimensions.pop())
            duration = data.get('durationMs', 120)
            if type(duration) is not int or not 1 <= duration <= 60000: raise ValueError('帧时长无效')
            name = data.get('name', '').strip()
            if not name or len(name) > 100: raise ValueError('请输入动作名称')
            config = json.loads(self.project_path.read_text())
            metadata, identifier = sequence_identity(data.get('metadata'))
            if identifier in {source['id'] for source in config.get('sources', [])} or identifier in {clip['id'] for clip in project['clips']}:
                raise ValueError('此角色、衣装、动作与方向已存在；请调整原动作或使用新的动作 ID')
            loop = data.get('loop', True)
            if type(loop) is not bool:
                raise ValueError('循环设置无效')
            config.setdefault('sources', []).append({'type': 'sequence', 'id': identifier, 'name': name,
                'files': files, 'canvas': canvas, 'origin': [canvas[0] / 2, canvas[1]], 'durationMs': duration, 'loop': loop, **metadata})
            self.write(self.project_path, (json.dumps(config, ensure_ascii=False, indent=2)+'\n').encode(), expected)
            return {'clipId': identifier}

    def edit_action(self, data):
        with self.lock:
            expected = sha(self.project_path)
            project, _ = self.loader(self.project_path)
            if data.get('fingerprint') != project['fingerprint']: raise ValueError('工程已变化，请刷新')
            clip_id = data.get('clipId')
            if clip_id not in {clip['id'] for clip in project['clips']}: raise ValueError('动作不存在')
            config = json.loads(self.project_path.read_text())
            if data.get('operation') == 'rename':
                name = data.get('name')
                if not isinstance(name, str) or not name.strip() or len(name) > 100: raise ValueError('名称无效')
                scope = data.get('scope', 'current')
                if scope not in ('current', 'family'):
                    raise ValueError('重命名范围无效')
                selected = next(clip for clip in project['clips'] if clip['id'] == clip_id)
                keys = ('characterId', 'variantId', 'actionId')
                targets = [clip for clip in project['clips']
                           if clip['id'] == clip_id or scope == 'family' and
                           all(clip.get(key) == selected.get(key) for key in keys)]
                for clip in targets:
                    config.setdefault('clipNames', {})[clip['id']] = name.strip()
                    config.setdefault('clipMetadata', {}).setdefault(clip['id'], {})['actionName'] = name.strip()
            elif data.get('operation') == 'remove':
                source = next((source for source in config['sources'] if source['type'] in ('sequence', 'registration') and source['id'] == clip_id), None)
                if source:
                    config['sources'].remove(source)
                else:
                    config.setdefault('excludedClips', []).append(clip_id)
                config.get('clipNames', {}).pop(clip_id, None)
                config.get('clipMetadata', {}).pop(clip_id, None)
            else: raise ValueError('动作操作无效')
            self.write(self.project_path, (json.dumps(config, ensure_ascii=False, indent=2)+'\n').encode(), expected)
            return {'clipId': clip_id}


def file_handler(base_handler, project_path, loader):
    assets = AssetFiles(project_path, loader)
    class FileHandler(base_handler):
        def do_GET(self):
            route = urlparse(self.path)
            if not route.path.startswith('/api/files'): return super().do_GET()
            if not self.local_request(): return self.json_response(403, {'error': '需要同源工作台请求'})
            try:
                query = parse_qs(route.query)
                if route.path == '/api/files': value = assets.listing(query.get('path', [''])[0])
                elif route.path == '/api/files/inspect': value = assets.inspect(query.get('path', [''])[0])
                elif route.path == '/api/files/trash': value = assets.trash()
                elif route.path == '/api/files/draft':
                    file = assets.root / '.motion-studio' / 'draft.json'
                    if file.parent.is_symlink() or file.is_symlink(): raise ValueError('草稿路径无效')
                    value = json.loads(file.read_text()) if file.exists() else None
                else: return self.json_response(404, {'error': '未知文件接口'})
                self.json_response(200, value)
            except (ValueError, OSError, KeyError, TypeError) as error: self.json_response(400, {'error': str(error)})

        def do_POST(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/files/'): return super().do_POST()
            if not self.local_request(): return self.json_response(403, {'error': '需要同源工作台请求'})
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 34_000_000 or self.headers.get('Transfer-Encoding'): raise ValueError('请求过大或无效')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise ValueError('需要 JSON 请求')
                raw = self.rfile.read(int(length))
                if len(raw) != int(length): raise ValueError('请求不完整')
                data = json.loads(raw)
                if not isinstance(data, dict): raise ValueError('请求无效')
                operation = route.rsplit('/', 1)[-1]
                if operation == 'sequence': result = assets.add_sequence(data)
                elif operation == 'action': result = assets.edit_action(data)
                elif operation == 'draft':
                    from motion_studio import validate_edit
                    current, _ = loader(project_path)
                    if data.get('fingerprint') != current['fingerprint'] or data.get('projectId') != current['id']: raise ValueError('素材已变化，请重新打开工程')
                    if set(data.get('clips', {})) != {c['id'] for c in current['clips']}: raise ValueError('动作列表已变化')
                    for clip in current['clips']: validate_edit(clip, data['clips'][clip['id']])
                    folder = assets.root / '.motion-studio'
                    if folder.is_symlink(): raise ValueError('草稿目录无效')
                    folder.mkdir(exist_ok=True)
                    file = folder / 'draft.json'
                    if file.is_symlink(): raise ValueError('草稿文件无效')
                    with assets.lock:
                        temp = folder / ('.draft-' + secrets.token_hex(5))
                        temp.write_text(json.dumps(data, ensure_ascii=False)); os.replace(temp, file)
                    result = {'saved': True}
                else: result = assets.mutate(operation, data)
                self.json_response(200, result)
            except (ValueError, OSError, KeyError, TypeError) as error: self.json_response(400, {'error': str(error)})
    return FileHandler
