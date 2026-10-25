import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from motion_studio import make_server, load_project
from sprite_export import default_draft


class ExportJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        Image.new('RGBA', (16,16), '#48705b').save(self.root / 'a.png')
        self.project = self.root / 'studio.project.json'
        self.project.write_text(json.dumps({'version':1, 'id':'test', 'sources':[{'type':'sequence','id':'walk','files':['a.png'],'canvas':[16,16],'origin':[8,16],'durationMs':120}]}))
        self.server = make_server(self.project, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request(method,path,json.dumps(data) if data is not None else None,{'Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse(); value=json.loads(response.read()); connection.close()
        return response.status,value

    def wait(self, identifier):
        for _ in range(100):
            status,value=self.request('GET','/api/exports/'+identifier)
            if value['status'] not in ('queued','running'): return value
            time.sleep(.02)
        self.fail('Export never settled')

    def test_success_and_failure_both_settle_with_visible_status(self):
        draft=default_draft(load_project(self.project)[0])
        status,job=self.request('POST','/api/exports',{'draft':draft,'options':{'preset':'balanced'}})
        self.assertEqual(status,202)
        result=self.wait(job['id'])
        self.assertEqual(result['status'],'complete',result)
        self.assertTrue((self.root / result['result']['downloadUrl'].removeprefix('/project/')).is_file())
        self.assertEqual(result['result']['previewProject']['clips'][0]['steps'][0]['durationMs'],120)
        draft['fingerprint']='stale'
        status,job=self.request('POST','/api/exports',{'draft':draft})
        self.assertEqual(status,202)
        failed=self.wait(job['id'])
        self.assertEqual(failed['status'],'failed')
        self.assertTrue(failed['error'])
        self.assertFalse((self.root / 'exports' / job['id']).exists())

    def test_concurrency_same_origin_and_file_mutation_api(self):
        started=threading.Event(); release=threading.Event()
        def blocked(*args,**kwargs):
            started.set(); release.wait(3); raise ValueError('test failure')
        draft=default_draft(load_project(self.project)[0])
        with patch('studio_exports.export_package',blocked):
            status,job=self.request('POST','/api/exports',{'draft':draft})
            self.assertEqual(status,202); self.assertTrue(started.wait(1))
            self.assertEqual(self.request('POST','/api/exports',{'draft':draft})[0],429)
            self.assertEqual(self.request('POST','/api/export',{'draft':draft})[0],429)
            release.set(); self.assertEqual(self.wait(job['id'])['status'],'failed')
        for path in ('/api/exports','/api/files/write'):
            self.assertEqual(self.request('POST',path,{},headers={'Origin':'https://outside.example'})[0],403)
        status,saved=self.request('POST','/api/files/write',{'path':'memo.md','text':'hello'})
        self.assertEqual(status,200)
        status,data=self.request('GET','/api/files/inspect?path=memo.md')
        self.assertEqual(data['text'],'hello')
        self.assertEqual(self.request('POST','/api/files/write',{'path':'memo.md','text':'overwrite'})[0],400)
        self.assertEqual(self.request('POST','/api/files/draft',draft)[0],200)
        self.assertEqual(self.request('GET','/api/files/draft')[1],draft)


if __name__ == '__main__': unittest.main()
