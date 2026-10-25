import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from motion_studio import load_project
from sprite_export import export_package
from studio_files import AssetFiles, sha


class AssetFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'studio.project.json'
        self.project.write_text(json.dumps({'version': 1, 'id': 'fixture', 'name': 'Fixture', 'sources': []}))
        self.files = AssetFiles(self.project, load_project)

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, color):
        buffer = io.BytesIO()
        image = Image.new('RGBA', (32, 32))
        image.paste(color, (8, 8, 22, 29))
        image.save(buffer, format='PNG')
        self.files.mutate('upload', {'path': name, 'content': base64.b64encode(buffer.getvalue()).decode()})

    def test_real_file_lifecycle_and_old_revision_rejection(self):
        self.files.mutate('mkdir', {'path': 'notes'})
        first = self.files.mutate('write', {'path': 'notes/a.md', 'text': 'first'})
        updated = self.files.mutate('write', {'path': 'notes/a.md', 'text': 'second', 'sha256': first['sha256']})
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.files.mutate('write', {'path': 'notes/a.md', 'text': 'lost update', 'sha256': first['sha256']})
        self.files.mutate('rename', {'path': 'notes/a.md', 'target': 'notes/b.md', 'sha256': updated['sha256']})
        result = self.files.mutate('trash', {'path': 'notes/b.md', 'sha256': updated['sha256']})
        self.assertFalse((self.root / 'notes/b.md').exists())
        self.files.mutate('restore', {'id': result['id']})
        self.assertEqual((self.root / 'notes/b.md').read_text(), 'second')
        backup = self.files.trash()['entries'][0]
        self.assertEqual(backup['operation'], 'replace')
        self.files.mutate('restore', {'id': backup['id']})
        self.assertEqual((self.root / 'notes/a.md').read_text(), 'first')
        old = self.files.mutate('trash', {'path': 'notes/a.md', 'sha256': sha(self.root / 'notes/a.md')})
        self.files.mutate('write', {'path': 'notes/a.md', 'text': 'new'})
        with self.assertRaises(ValueError): self.files.mutate('restore', {'id': old['id']})
        self.files.mutate('restore', {'id': old['id'], 'target': 'notes/recovered.md'})
        self.assertEqual((self.root / 'notes/recovered.md').read_text(), 'first')
        self.assertEqual((self.root / 'notes/a.md').read_text(), 'new')

    def test_traversal_symlink_and_collision_never_touch_outside(self):
        with tempfile.TemporaryDirectory() as other:
            target = Path(other) / 'private.txt'; target.write_text('private')
            (self.root / 'linked').symlink_to(other, target_is_directory=True)
            for value in ('../private.txt', str(target), 'linked/private.txt', '.motion-studio/a', ''):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError): self.files.mutate('write', {'path': value, 'text': 'bad'})
            self.assertEqual(target.read_text(), 'private')
        self.files.mutate('write', {'path': 'a.txt', 'text': 'a'})
        with self.assertRaises(ValueError): self.files.mutate('write', {'path': 'a.txt', 'text': 'overwrite'})
        self.assertEqual((self.root / 'a.txt').read_text(), 'a')

    def test_sequence_authoring_roundtrip_export_and_referenced_files(self):
        self.files.mutate('mkdir', {'path': 'walk'})
        self.image('walk/01.png', '#c08d63')
        self.image('walk/02.png', '#517568')
        before = {p: p.read_bytes() for p in (self.root / 'walk').iterdir()}
        project, _ = load_project(self.project)
        result = self.files.add_sequence({'name': '小步', 'files': ['walk/02.png', 'walk/01.png'], 'durationMs': 135, 'fingerprint': project['fingerprint']})
        loaded, _ = load_project(self.project)
        self.assertEqual(loaded['clips'][0]['id'], result['clipId'])
        self.assertTrue(loaded['clips'][0]['frames'][0]['file'].endswith('02.png'))
        self.assertEqual([s['durationMs'] for s in loaded['clips'][0]['steps']], [135, 135])
        with self.assertRaisesRegex(ValueError, '引用'):
            self.files.mutate('trash', {'path': 'walk'})
        with self.assertRaisesRegex(ValueError, '引用'):
            self.files.mutate('rename', {'path': 'walk/01.png', 'target': 'x.png', 'sha256': sha(self.root / 'walk/01.png')})
        export = self.root / 'exports' / 'test'
        export.parent.mkdir()
        export_package(self.project, export, options={'preset': 'balanced', 'includeFrames': True})
        package = json.loads((export / 'package.json').read_text())
        self.assertEqual(len(package['clips']), 1)
        self.assertEqual(package['clips'][0]['origin'], [8, 16])
        self.assertEqual([s['durationMs'] for s in package['clips'][0]['steps']], [135, 135])
        for path, content in before.items(): self.assertEqual(path.read_bytes(), content)
        self.files.edit_action({'clipId': result['clipId'], 'operation': 'rename', 'name': '步行', 'fingerprint': loaded['fingerprint']})
        renamed, _ = load_project(self.project)
        self.assertEqual(renamed['clips'][0]['name'], '步行')
        self.assertEqual(renamed['clips'][0]['id'], result['clipId'])
        self.files.edit_action({'clipId': result['clipId'], 'operation': 'remove', 'fingerprint': renamed['fingerprint']})
        self.assertEqual(load_project(self.project)[0]['clips'], [])
        self.files.mutate('trash', {'path': 'walk'})
        self.assertFalse((self.root / 'walk').exists())

    def test_bad_sequence_input_does_not_modify_project(self):
        self.image('a.png', '#fff')
        Image.new('RGBA', (16,16), '#fff').save(self.root / 'b.png')
        original = self.project.read_bytes()
        project, _ = load_project(self.project)
        for files in (['a.png','a.png'], ['a.png','b.png'], [], ['../a.png']):
            with self.assertRaises((ValueError, OSError)):
                self.files.add_sequence({'name': 'Bad', 'files': files, 'fingerprint': project['fingerprint']})
            self.assertEqual(self.project.read_bytes(), original)
        with self.assertRaises((ValueError, OSError)): self.files.mutate('upload', {'path': 'fake.png', 'content': base64.b64encode(b'not an image').decode()})

    def test_classified_sequence_keeps_identity_and_rejects_duplicate_or_invalid_ids(self):
        self.image('a.png', '#fff')
        metadata = {'characterId': 'pet', 'characterName': '小山灵', 'variantId': 'everyday',
                    'variantName': '日常短褂', 'actionId': 'run', 'actionName': '跑动', 'direction': 'NE'}
        project, _ = load_project(self.project)
        request = {'name': '跑动', 'files': ['a.png'], 'durationMs': 160, 'loop': False,
                   'fingerprint': project['fingerprint'], 'metadata': metadata}
        result = self.files.add_sequence(request)
        self.assertEqual(result['clipId'], 'pet--everyday--run--ne')
        loaded, _ = load_project(self.project)
        clip = loaded['clips'][0]
        for key, value in metadata.items():
            self.assertEqual(clip[key], value)
        self.assertFalse(clip['loop'])
        request['fingerprint'] = loaded['fingerprint']
        original = self.project.read_bytes()
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.files.add_sequence(request)
        for bad in ('../pet', 'Pet', '小山灵', '', 'a' * 65):
            with self.subTest(identifier=bad):
                with self.assertRaisesRegex(ValueError, 'ID'):
                    self.files.add_sequence({**request, 'metadata': {**metadata, 'characterId': bad}})
                self.assertEqual(self.project.read_bytes(), original)

    def test_reference_guard_covers_linked_metadata_and_parents(self):
        (self.root / 'refs').mkdir()
        (self.root / 'refs/data.txt').write_text('data')
        (self.root / 'proof.json').write_text(json.dumps({'path': 'refs/data.txt'}))
        config = json.loads(self.project.read_text()); config['provenancePaths'] = ['proof.json']; self.project.write_text(json.dumps(config))
        for path in ('proof.json','refs/data.txt','refs','studio.project.json'):
            with self.assertRaisesRegex(ValueError, '引用'):
                self.files.mutate('trash', {'path': path})
        self.assertTrue(self.files.listing('refs')['entries'][0]['referenced'])

    def test_rename_family_updates_directions_in_one_outfit_and_remove_cleans_metadata(self):
        self.image('a.png', '#fff')
        identifiers = []
        for outfit, direction in [('everyday', 'E'), ('everyday', 'W'), ('deluxe', 'E')]:
            project, _ = load_project(self.project)
            result = self.files.add_sequence({'name': '跑动', 'files': ['a.png'], 'fingerprint': project['fingerprint'],
                'metadata': {'characterId': 'pet', 'variantId': outfit, 'actionId': 'run', 'direction': direction}})
            identifiers.append(result['clipId'])
        project, _ = load_project(self.project)
        self.files.edit_action({'clipId': identifiers[0], 'operation': 'rename', 'scope': 'family',
                                'name': '小步快跑', 'fingerprint': project['fingerprint']})
        renamed, _ = load_project(self.project)
        self.assertEqual([clip['id'] for clip in renamed['clips']], identifiers)
        self.assertEqual([clip['actionName'] for clip in renamed['clips']], ['小步快跑', '小步快跑', 'run'])
        self.files.edit_action({'clipId': identifiers[0], 'operation': 'remove', 'fingerprint': renamed['fingerprint']})
        remaining, _ = load_project(self.project)
        self.assertEqual([clip['id'] for clip in remaining['clips']], identifiers[1:])
        self.assertNotIn(identifiers[0], json.loads(self.project.read_text())['clipMetadata'])
        self.assertTrue((self.root / 'a.png').is_file())


if __name__ == '__main__': unittest.main()
