"""Pixel and contract checks for real sprite packages; no character artwork is generated."""
import copy
import hashlib
import json
import random
from pathlib import Path
import tempfile
import unittest
import zipfile
from PIL import Image

from motion_studio import load_project, export_manifest
from motion_tool import pack
from sprite_export import export_package, default_draft, options_contract, pack_rectangles, scaled_canvas


class SpriteExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        a = Image.new('RGBA', (24, 32))
        a.paste((150, 100, 70, 255), (7, 10, 14, 24))
        a.putpixel((6, 9), (234, 200, 170, 1))  # A faint detached fur edge must survive.
        a.putpixel((14, 24), (240, 190, 140, 80))
        a.save(self.root / 'a.png')
        a.save(self.root / 'b.png')  # Exact duplicate should share atlas storage only.
        c = a.copy()
        c.putpixel((10, 20), (151, 100, 70, 255))  # Near-pose must not be merged.
        c.save(self.root / 'c.png')
        self.report = self.root / 'registration.json'
        self.report.write_text(json.dumps({'status': 'registered-not-visual-approval', 'errors': [],
            'canvas': [24, 32], 'origin': [12, 29], 'records': [
                {'id': name, 'durationMs': ms, 'markers': {'head': [12, 13]}}
                for name, ms in [('a', 100), ('b', 120), ('c', 200)]]}))
        self.project = self.root / 'studio.project.json'
        self.project.write_text(json.dumps({'version': 1, 'id': 'pet', 'name': 'Test pet',
            'sources': [{'type': 'registration', 'path': 'registration.json', 'id': 'pet/plain/walk-S'}]}))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        self.clip_id = self.data['clips'][0]['id']

    def tearDown(self):
        self.temp.cleanup()

    def run_export(self, name='out', options=None, draft=None):
        out = self.root / name
        result = export_package(self.project, out, self.draft if draft is None else draft,
                                options or {'preset': 'lossless', 'format': 'png'})
        return out, result, json.loads((out / 'package.json').read_text())

    def reconstruct(self, out, frame):
        record = json.loads((out / 'package.json').read_text())['frames'][frame['id']]
        image = Image.open(out / frame['url']).convert('RGBA').crop(tuple(frame['rect']))
        full = Image.new('RGBA', (record['sourceSize']['w'], record['sourceSize']['h']))
        full.paste(image, tuple(frame['drawRect'][:2]))
        return full

    def test_trim_reconstruction_faint_alpha_exact_dedup_and_provenance(self):
        before = {p.name: p.read_bytes() for p in self.root.glob('*') if p.is_file()}
        out, result, package = self.run_export()
        frames = package['clips'][0]['frames']
        self.assertEqual(result['report']['uniqueSpriteCount'], 2)
        self.assertEqual(result['report']['stepCount'], 3)
        self.assertEqual(frames[0]['rect'], frames[1]['rect'])
        self.assertNotEqual(frames[0]['rect'], frames[2]['rect'])
        for frame, source in zip(frames, ('a', 'b', 'c')):
            expected = Image.open(self.root / (source + '.png')).convert('RGBA')
            actual = self.reconstruct(out, frame)
            self.assertEqual(actual.tobytes(), expected.tobytes())
            self.assertEqual(actual.getpixel((6, 9))[3], 1)
        self.assertEqual(package['clips'][0]['id'], self.clip_id)
        self.assertEqual(package['frames'][frames[0]['id']]['sourceSize'], {'w': 24, 'h': 32})
        self.assertEqual(package['frames'][frames[0]['id']]['origin'], [12, 29])
        self.assertEqual(package['frames'][frames[0]['id']]['source']['originalAssetPath'], 'a.png')
        self.assertEqual(package['provenance']['files'][0]['sha256'], hashlib.sha256(before['a.png']).hexdigest())
        self.assertEqual(result['report']['sourceFileBytes'], sum(len(before[n + '.png']) for n in ('a', 'b', 'c')))
        self.assertEqual(result['report']['sourceDecodedRGBABytes'], 24 * 32 * 4 * 3)
        self.assertEqual(result['report']['packageTotalBytes'], sum((out / p).stat().st_size for p in result['report']['includedFiles']))
        self.assertEqual(result['report']['zipBytes'], (out / 'package.zip').stat().st_size)
        for name, data in before.items(): self.assertEqual((self.root / name).read_bytes(), data)
        with zipfile.ZipFile(out / 'package.zip') as archive:
            self.assertEqual(set(archive.namelist()), {'package.json', *[p['file'] for p in package['pages']]})
            self.assertNotIn('preview.html', archive.namelist())
            self.assertNotIn('report.json', archive.namelist())
            self.assertNotIn('package.zip', archive.namelist())
            self.assertFalse(any(name.startswith('/') or '..' in Path(name).parts for name in archive.namelist()))
        self.assertFalse((out / 'preview.html').exists())
        self.assertFalse((out / 'provenance').exists())
        self.assertNotIn('previewProject', package)
        self.assertEqual(result['previewProject']['clips'], package['clips'])

    def test_draft_offset_baked_timing_reorder_and_scaled_markers(self):
        edits = self.draft['clips'][self.clip_id]
        edits['steps'] = [edits['steps'][2], edits['steps'][0], edits['steps'][1]]
        edits['steps'][0].update(durationMs=321, offset=[2, -2])
        edits['loop'] = False
        out, _, package = self.run_export(options={'preset': 'lossless', 'scale': .5, 'includeFrames': True})
        clip = package['clips'][0]
        self.assertEqual(clip['canvas'], [12, 16])
        self.assertEqual(clip['origin'], [6, 14.5])
        self.assertFalse(clip['loop'])
        self.assertEqual([s['durationMs'] for s in clip['steps']], [321, 100, 120])
        self.assertEqual(clip['steps'][0]['offset'], [0, 0])
        self.assertEqual(clip['frames'][0]['markers']['head'], [7, 5.5])
        expected = Image.new('RGBA', (24, 32))
        expected.paste(Image.open(self.root / 'c.png'), (2, -2))
        self.assertEqual(self.reconstruct(out, clip['frames'][0]).tobytes(), scaled_canvas(expected, .5).tobytes())
        self.assertEqual(len(list((out / 'frames').glob('*.png'))), 3)
        with Image.open(next((out / 'frames').glob('*.png'))) as frame:
            self.assertEqual(frame.size, (12, 16))

    def test_lossy_webp_preserves_alpha_and_lossless_preserves_visible_rgb(self):
        out, _, package = self.run_export(options={'preset': 'balanced', 'scale': 1})
        frame = package['clips'][0]['frames'][0]
        actual = self.reconstruct(out, frame)
        expected = Image.open(self.root / 'a.png').convert('RGBA')
        self.assertEqual(actual.getchannel('A').tobytes(), expected.getchannel('A').tobytes())
        out, _, package = self.run_export('lossless-webp', {'preset': 'lossless'})
        actual = self.reconstruct(out, package['clips'][0]['frames'][0])
        self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_packing_deterministic_multipage_no_overlap_or_rotation(self):
        def records():
            return [{'id': f'p{i}', 'image': Image.new('RGBA', size, 'white')}
                    for i, size in enumerate(((700, 600), (700, 600), (300, 200), (100, 800)))]
        first, second = records(), records()
        pages = pack_rectangles(first, 1024)
        pack_rectangles(second, 1024)
        self.assertGreaterEqual(len(pages), 2)
        self.assertEqual([(r['page'], r['position']) for r in first], [(r['page'], r['position']) for r in second])
        for page in pages:
            self.assertLessEqual(page['width'], 1024)
            self.assertLessEqual(page['height'], 1024)
            for i, a in enumerate(page['items']):
                ax, ay = a['position']; aw, ah = a['image'].size
                for b in page['items'][i + 1:]:
                    bx, by = b['position']; bw, bh = b['image'].size
                    self.assertTrue(ax + aw + 4 <= bx or bx + bw + 4 <= ax or ay + ah + 4 <= by or by + bh + 4 <= ay)

    def test_real_export_splits_pages_and_extrudes_without_losing_edges(self):
        for name, color in [('a', 'red'), ('b', 'blue'), ('c', 'green')]:
            Image.new('RGBA', (600, 600), color).save(self.root / (name + '.png'))
        data = json.loads(self.report.read_text())
        data['canvas'], data['origin'] = [600, 600], [300, 590]
        self.report.write_text(json.dumps(data))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        out, result, package = self.run_export(options={'preset': 'lossless', 'format': 'png', 'maxPageSize': 1024})
        self.assertEqual(result['report']['pageCount'], 3)
        for frame in package['clips'][0]['frames']:
            x, y, x2, y2 = frame['rect']
            with Image.open(out / frame['url']) as image:
                self.assertEqual(image.size, (608, 608))
                self.assertEqual(image.getpixel((x, y)), image.getpixel((x - 2, y - 2)))
                self.assertEqual(image.getpixel((x2 - 1, y2 - 1)), image.getpixel((x2 + 1, y2 + 1)))
                self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_random_bounded_packing_no_overlap(self):
        rng = random.Random(20261025)
        for _ in range(20):
            records = [{'id': f'r{i}', 'image': Image.new('RGBA', (rng.randint(1, 44), rng.randint(1, 44)))} for i in range(30)]
            pages = pack_rectangles(records, 64)
            for page in pages:
                for i, a in enumerate(page['items']):
                    ax, ay = a['position']; aw, ah = a['image'].size
                    self.assertGreaterEqual(ax - 4, 0)
                    self.assertGreaterEqual(ay - 4, 0)
                    self.assertLessEqual(ax + aw + 4, page['width'])
                    self.assertLessEqual(ay + ah + 4, page['height'])
                    for b in page['items'][i + 1:]:
                        bx, by = b['position']; bw, bh = b['image'].size
                        self.assertTrue(ax + aw + 8 <= bx or bx + bw + 8 <= ax or ay + ah + 8 <= by or by + bh + 8 <= ay)

    def test_provenance_json_copied_with_path_mapping_and_hash_bound_to_draft(self):
        proof = self.root / 'asset-index.json'
        proof.write_text('{"master":"master.png","sourceRect":[1,2,3,4]}')
        project = json.loads(self.project.read_text())
        project['provenancePaths'] = ['asset-index.json']
        self.project.write_text(json.dumps(project))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        lean_out, _, lean_package = self.run_export('runtime-with-proof')
        self.assertEqual(lean_package['provenance']['proofFiles'], [])
        self.assertFalse((lean_out / 'provenance').exists())
        out, _, package = self.run_export(options={'preset': 'lossless', 'includeProofs': True})
        record = package['provenance']['proofFiles'][0]
        self.assertEqual(record['path'], 'asset-index.json')
        self.assertEqual((out / record['bundledPath']).read_bytes(), proof.read_bytes())
        proof.write_text('{"master":"changed.png"}')
        with self.assertRaisesRegex(ValueError, 'changed source'):
            self.run_export('stale-proof')

    def test_stale_fingerprint_clipping_existing_output_and_invalid_options_rejected(self):
        bad = copy.deepcopy(self.draft)
        bad['fingerprint'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'changed source'):
            self.run_export('stale', draft=bad)
        bad = copy.deepcopy(self.draft)
        bad['clips'][self.clip_id]['steps'][0]['offset'] = [-7, 0]
        with self.assertRaisesRegex(ValueError, 'clip'):
            self.run_export('clipped', draft=bad)
        self.assertFalse((self.root / 'clipped').exists())
        self.run_export()
        with self.assertRaisesRegex(ValueError, 'must be new'):
            self.run_export()
        for opts in ({'quality': 0}, {'scale': .1}, {'scale': True}, {'includeFrames': 1},
                     {'includePreview': 1}, {'includeProofs': 'yes'},
                     {'maxPageSize': 8192}, {'format': 'jpeg'}, {'preset': 'fast'}, {'extra': 1},
                     {'clipIds': []}, {'clipIds': [self.clip_id, self.clip_id]}):
            with self.subTest(opts=opts), self.assertRaises(ValueError): options_contract(opts)
        with self.assertRaisesRegex(ValueError, 'existing clips'):
            self.run_export('unknown', {'clipIds': ['unknown']})

    def test_runtime_root_events_exclusion_and_unique_atlas_bytes(self):
        draft_path = self.root / 'draft.json'
        draft_path.write_text(json.dumps(self.draft))
        manifest = self.root / 'motion.json'
        data = export_manifest(self.project, draft_path, manifest)
        self.assertEqual(data['studioProvenance']['clipMap']['default/clip0']['sourceClipId'], self.clip_id)
        self.assertTrue(data['studioProvenance']['authoringOnly'])
        action = data['variants']['default']['actions']['clip0']
        action['root_motion'] = [[0, 0], [2, 4], [6, 8]]
        action['events'] = [{'step': 1, 'name': 'step', 'payload': {'foot': 'left'}}]
        data['variants']['default']['actions']['history'] = copy.deepcopy(action)
        manifest.write_text(json.dumps(data))
        runtime = pack(manifest, self.root / 'runtime')
        self.project.write_text(json.dumps({'version': 1, 'id': 'pet', 'sources': [
            {'type': 'runtime', 'id': 'r', 'path': str(runtime.relative_to(self.root)), 'excludeActions': ['history']}]}))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        self.assertEqual(len(self.data['clips']), 1)
        out, result, package = self.run_export(options={'preset': 'compact'})
        clip = package['clips'][0]
        self.assertEqual(clip['id'], 'r/default/clip0')
        self.assertEqual(clip['steps'][1]['root'], [.5, 1])
        self.assertEqual(clip['steps'][1]['events'], [{'step': 1, 'name': 'step', 'payload': {'foot': 'left'}}])
        self.assertFalse(clip['canReorder'])
        self.assertEqual(result['report']['sourceFileCount'], 1)
        self.assertEqual(result['report']['sourceFileBytes'], Path(self.data['clips'][0]['frames'][0]['file']).stat().st_size)
        self.assertNotIn(str(self.root), (out / 'package.json').read_text())
        self.assertEqual(clip['characterId'], 'studio-character')
        self.assertEqual(clip['variantId'], 'default')

    def test_runtime_explicit_null_root_motion_is_stationary(self):
        draft_path = self.root / 'draft.json'
        draft_path.write_text(json.dumps(self.draft))
        manifest = self.root / 'motion.json'
        export_manifest(self.project, draft_path, manifest)
        runtime = pack(manifest, self.root / 'runtime')
        data = json.loads(runtime.read_text())
        data['variants']['default']['actions']['clip0']['root_motion'] = None
        runtime.write_text(json.dumps(data))
        self.project.write_text(json.dumps({'version': 1, 'id': 'pet', 'sources': [
            {'type': 'runtime', 'id': 'r', 'path': str(runtime.relative_to(self.root))}]}))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        self.assertTrue(self.data['clips'][0]['canReorder'])
        _, _, package = self.run_export()
        self.assertEqual([s['root'] for s in package['clips'][0]['steps']], [[0, 0]] * 3)

    def test_portable_package_preserves_marker_meanings_with_action_override(self):
        draft_path = self.root / 'draft.json'
        draft_path.write_text(json.dumps(self.draft))
        manifest = self.root / 'motion.json'
        data = export_manifest(self.project, draft_path, manifest)
        data['variants']['default']['actions']['clip1'] = copy.deepcopy(data['variants']['default']['actions']['clip0'])
        manifest.write_text(json.dumps(data))
        runtime = pack(manifest, self.root / 'runtime')
        self.project.write_text(json.dumps({'version': 1, 'id': 'pet', 'sources': [
            {'type': 'runtime', 'id': 'r', 'path': str(runtime.relative_to(self.root)),
             'markerLabels': {'head': '基础配准参考'}, 'originLabel': '逻辑导出原点',
             'markerLabelsByAction': {'clip0': {'head': '双瞳中点（配准参考）'}}}]}))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        out, result, package = self.run_export(options={'preset': 'lossless', 'scale': .5, 'format': 'png', 'includePreview': True})
        for actual, expected in zip(package['clips'], self.data['clips']):
            self.assertEqual(actual['markerLabels'], expected['markerLabels'])
            self.assertEqual(actual['originLabel'], expected['originLabel'])
            self.assertEqual(actual['origin'], [n * .5 for n in expected['origin']])
            self.assertEqual(actual['frames'][0]['markers']['head'], [6, 6.5])
            self.assertEqual([s['durationMs'] for s in actual['steps']], [100, 120, 200])
        self.assertEqual(result['previewProject']['clips'], package['clips'])
        self.assertEqual(package['clips'][0]['markerLabels']['head'], '双瞳中点（配准参考）')
        self.assertEqual(package['clips'][1]['markerLabels']['head'], '基础配准参考')
        html = (out / 'preview.html').read_text()
        self.assertIn('window.__MOTION_STUDIO_PROJECT__=', html)
        self.assertNotIn('src="studio.mjs"', html)
        self.assertNotIn('href="studio.css"', html)
        self.assertIn('markerLabels', html)
        self.assertIn('逻辑导出原点', html)

    def test_authored_library_metadata_survives_export_and_uses_stable_clip_id(self):
        self.assertEqual(self.data['clips'][0]['actionId'], 'walk')
        self.assertEqual(self.data['clips'][0]['direction'], 'S')
        config = json.loads(self.project.read_text())
        config['clipMetadata'] = {self.clip_id: {
            'characterId': 'mountain-spirit', 'characterName': '山灵',
            'variantId': 'plain', 'variantName': '日常衣装',
            'actionId': 'walk', 'actionName': '跑动', 'direction': 'S'}}
        self.project.write_text(json.dumps(config))
        self.data, _ = load_project(self.project)
        self.draft = default_draft(self.data)
        _, _, package = self.run_export()
        clip = package['clips'][0]
        self.assertEqual(clip['id'], self.clip_id)
        for key, value in config['clipMetadata'][self.clip_id].items():
            self.assertEqual(clip[key], value)
        self.assertEqual([s['durationMs'] for s in clip['steps']], [100, 120, 200])


if __name__ == '__main__':
    unittest.main()
