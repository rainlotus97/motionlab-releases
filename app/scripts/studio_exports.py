"""Observable export jobs for the shared studio; the CLI/synchronous export stays compatible."""
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse
from datetime import datetime, timezone
from sprite_export import export_package


def export_job_handler(base_handler, project_path, export_gate=None):
    jobs, lock = {}, threading.Lock()
    gate = export_gate or threading.BoundedSemaphore(1)
    root = Path(project_path).resolve().parent

    def run(identifier, request):
        with lock: jobs[identifier].update(status='running', phase='正在生成图集')
        try:
            exports = root / 'exports'
            if exports.is_symlink(): raise ValueError('导出目录不能是符号链接')
            exports.mkdir(exist_ok=True)
            output = exports / identifier
            result = export_package(project_path, output, request['draft'], request.get('options'))
            base = '/project/exports/' + identifier + '/'
            result.update(exportId=identifier, downloadUrl=base + 'package.zip', manifestUrl=base + 'package.json')
            if (output / 'preview.html').is_file(): result['previewUrl'] = base + 'preview.html'
            result.pop('packagePath', None)
            with lock: jobs[identifier].update(status='complete', phase='导出完成', result=result)
        except Exception as error:
            # Always settle the job; exceptions must not leave the button spinning indefinitely.
            with lock: jobs[identifier].update(status='failed', phase='导出失败', error=str(error) or type(error).__name__)
        finally:
            gate.release()

    class ExportJobHandler(base_handler):
        def do_GET(self):
            route = urlparse(self.path).path
            if not route.startswith('/api/exports/'): return super().do_GET()
            if not self.local_request(): return self.json_response(403, {'error': '需要同源工作台请求'})
            with lock:
                result = jobs.get(route.rsplit('/', 1)[-1])
                self.json_response(200 if result else 404, result or {'error': '导出任务不存在，请重新导出'})

        def do_POST(self):
            if urlparse(self.path).path != '/api/exports': return super().do_POST()
            if not self.local_request(): return self.json_response(403, {'error': '需要同源工作台请求'})
            try:
                length = self.headers.get('Content-Length', '')
                if not length.isdigit() or not 0 < int(length) <= 2_000_000 or self.headers.get('Transfer-Encoding'): raise ValueError('导出请求过大或无效')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise ValueError('需要 JSON 请求')
                raw = self.rfile.read(int(length))
                if len(raw) != int(length): raise ValueError('导出请求不完整')
                data = json.loads(raw)
                if not isinstance(data, dict) or set(data) - {'draft', 'options'} or 'draft' not in data: raise ValueError('缺少当前调整')
                with lock:
                    if not gate.acquire(blocking=False):
                        return self.json_response(429, {'error': '已有导出任务进行中'})
                    for key in list(jobs)[:-15]: del jobs[key]
                    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
                    jobs[identifier] = {'id': identifier, 'status': 'queued', 'phase': '准备导出', 'startedAt': time.time()}
                    initial = dict(jobs[identifier])
                threading.Thread(target=run, args=(identifier, data), daemon=True, name='studio-export-' + identifier).start()
                self.json_response(202, initial)
            except (ValueError, OSError, KeyError, TypeError) as error: self.json_response(400, {'error': str(error)})
    return ExportJobHandler
