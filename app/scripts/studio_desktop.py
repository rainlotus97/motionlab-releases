#!/usr/bin/env python3
"""Desktop library initialization and an app-owned single-listener service."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
import secrets

from motion_studio import make_server


def default_library():
    return Path.home() / 'Desktop' / 'MotionLab'


def migrate_default_library():
    """Move the legacy default only when the destination is absent and no app owns it."""
    root = default_library()
    legacy = root.with_name('角色动作工坊')
    if root.exists() or not legacy.is_dir():
        return root
    import fcntl
    handles = []
    try:
        (legacy / '.studio').mkdir(exist_ok=True)
        for name in ('window.lock', 'service.lock'):
            handle = (legacy / '.studio' / name).open('a')
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('请先退出旧工作台，再迁移到 Desktop/MotionLab。')
        legacy.rename(root)
        old_app = root / '角色动作工坊.app'
        if old_app.is_dir():
            old_app.rename(root / 'MotionLab.app')
        # The last project path is relative when inside the library; discovery root was absolute.
        state = root / '.studio/last-project.json'
        if state.is_file():
            data = json.loads(state.read_text())
            for key in ('path', 'assetRoot'):
                value = data.get(key)
                if isinstance(value, str) and (value == str(legacy) or value.startswith(str(legacy) + '/')):
                    data[key] = str(root) + value[len(str(legacy)):]
            state.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return root
    finally:
        for handle in handles:
            handle.close()


def initialize_library(root):
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for folder in ('projects', 'references', '.studio'):
        (root / folder).mkdir(exist_ok=True)
    guide = root / 'README.md'
    if not guide.exists():
        guide.write_bytes((Path(__file__).resolve().parents[1] / 'assets/desktop/library-readme.md').read_bytes())
    tools = root / 'studio.tools.json'
    if not tools.exists():
        tools.write_text(json.dumps({'version': 1, 'id': 'motion-tools', 'name': '全局工具', 'sources': [],
            'sampling': {'store': 'references/sampling-plans.json', 'sources': [], 'libraries': []}}, ensure_ascii=False, indent=2))
    from motion_catalog import discover_projects
    projects = discover_projects(root / 'projects')['projects']
    if not projects:
        blank = root / 'projects' / 'my-characters'
        blank.mkdir(exist_ok=True)
        project = blank / 'studio.project.json'
        project.write_text(json.dumps({'version': 1, 'id': 'my-characters', 'name': '我的角色', 'sources': []}, ensure_ascii=False, indent=2))
    else:
        project = Path(projects[0]['path'])
    state = root / '.studio' / 'last-project.json'
    if state.is_file():
        try:
            candidate = root / json.loads(state.read_text())['path']
            if candidate.is_file() and candidate.name == 'studio.project.json':
                project = candidate
        except (ValueError, KeyError, OSError):
            pass
    return root, project, tools


def run(root, owner_pid):
    # OS lock, not PID-file guesses: stale files cannot block a later launch.
    import fcntl
    root, project, tools = initialize_library(root)
    with (root / '.studio' / 'service.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('工作台已在运行，请打开已有窗口。')
        catalog_root = root / 'projects'
        state_file = root / '.studio' / 'last-project.json'
        try:
            saved_root = Path(json.loads(state_file.read_text()).get('assetRoot', str(catalog_root)))
            if saved_root.is_dir(): catalog_root = saved_root
        except (ValueError, OSError):
            pass
        server = make_server(project, 0, asset_root=catalog_root, tool_project=tools)
        server.instance_identity = {'pid': os.getpid(), 'instanceId': secrets.token_hex(16)}
        instance = root / '.studio' / 'instance.json'
        instance.write_text(json.dumps({**server.instance_identity, 'ownerPid': owner_pid,
            'url': f'http://127.0.0.1:{server.server_port}/'}))
        original_open = server.catalog.open_project
        def open_project(identifier):
            result = original_open(identifier)
            candidate = Path(result['path'])
            state = root / '.studio' / 'last-project.json'
            state.write_text(json.dumps({'path': candidate.relative_to(root).as_posix() if candidate.is_relative_to(root) else str(candidate), 'assetRoot': server.catalog.state['root']}))
            return result
        server.catalog.open_project = open_project
        stop = threading.Event()
        def shutdown(*_):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        def monitor():
            while not stop.wait(1):
                if owner_pid and os.getppid() != owner_pid:
                    shutdown()
                    return
        threading.Thread(target=monitor, daemon=True).start()
        print(f'http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever(poll_interval=.2)
        finally:
            stop.set()
            server.server_close()
            instance.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=default_library())
    parser.add_argument('--owner-pid', type=int, default=0)
    parser.add_argument('--initialize', action='store_true')
    args = parser.parse_args()
    if args.initialize:
        print(initialize_library(args.library)[0])
    else:
        run(args.library, args.owner_pid)


if __name__ == '__main__':
    main()
