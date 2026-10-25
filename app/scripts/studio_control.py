#!/usr/bin/env python3
"""Discover the live desktop service and call its existing project APIs as JSON."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('工作台接口不能重定向到其他服务')


def request(url, data=None):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = Request(url, data=body, headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
        return json.load(response)


def connect(library):
    instance = json.loads((Path(library).expanduser() / '.studio/instance.json').read_text())
    url = instance['url']
    parsed = urlparse(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path != '/' or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('无效的本地工作台地址')
    live = request(url + 'api/desktop/health')
    if (live.get('service') != 'character-motion-studio' or not instance.get('instanceId')
            or any(live.get(key) != instance[key] for key in ('pid', 'instanceId'))):
        raise ValueError('工作台实例已过期，请重新打开应用')
    return instance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path.home() / 'Desktop/MotionLab')
    parser.add_argument('command', choices=('status', 'projects', 'request'))
    parser.add_argument('--project-id', help='Catalog project id returned by projects')
    parser.add_argument('--route', help='Existing /api/ route; GET unless --data-file is provided')
    parser.add_argument('--data-file', type=Path, help='JSON body for POST; never evaluate code')
    args = parser.parse_args()
    try:
        instance = connect(args.library)
        url = instance['url']
        if args.command == 'status':
            result = {'running': True, **instance}
        elif args.command == 'projects':
            result = request(url + 'api/catalog')
        else:
            route = args.route or '/api/project'
            parsed = urlparse(route)
            if not route.startswith('/api/') or parsed.scheme or parsed.netloc or parsed.fragment or '..' in route or '\\' in route:
                raise ValueError('route 必须为工作台 /api/ 路径')
            if args.project_id:
                catalog = request(url + 'api/catalog')
                if args.project_id not in {p['id'] for p in catalog['projects']}:
                    raise ValueError('工程不在当前目录中，请先 projects 查询')
                request(url + 'api/catalog/open', {'id': args.project_id})
                prefix = f'p/{args.project_id}/'
                url += prefix
            data = json.loads(args.data_file.read_text()) if args.data_file else None
            result = request(url + route.lstrip('/'), data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, URLError) as error:
        detail = str(error)
        if isinstance(error, HTTPError):
            detail = error.read().decode(errors='replace')[:2000]
        print(json.dumps({'error': detail, 'hint': '若应用尚未启动，请运行 scripts/setup_studio.py。'}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
