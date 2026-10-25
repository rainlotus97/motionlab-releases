import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from install_examples import install_examples
from motion_studio import load_project
from sampling_plans import list_sources, get_source
from sprite_export import export_package


class ExampleInstallTests(unittest.TestCase):
    def test_fresh_library_is_self_contained_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = install_examples(root)
            self.assertEqual(result['installed'], ['songtuan', 'ual1'])
            self.assertEqual(result['registered'], ['ual1'])
            path = root / 'projects/songtuan/studio.project.json'
            project, _ = load_project(path)
            self.assertEqual(len(project['clips']), 9)
            self.assertEqual(sum(len(clip['steps']) for clip in project['clips']), 40)
            self.assertEqual({clip['variantId'] for clip in project['clips']}, {'everyday'})
            for clip in project['clips']:
                for frame in clip['frames']:
                    self.assertTrue(Path(frame['file']).is_relative_to(root))
                    self.assertFalse(Path(frame['sourcePath']).is_absolute())
            tools = root / 'studio.tools.json'
            sources = list_sources(tools)
            self.assertEqual(len(sources), 43)
            for source in sources:
                for profile in source['profiles']:
                    data = get_source(tools, source['id'], profile['id'])
                    self.assertTrue(data['positions'])
            output = root / 'export'
            export_package(path, output, options={'preset': 'balanced'})
            package = json.loads((output / 'package.json').read_text())
            self.assertEqual(len(package['clips']), 9)
            self.assertNotIn('/Users/', (output / 'package.json').read_text())

    def test_repeat_preserves_edits_and_existing_source_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            install_examples(root)
            project = root / 'projects/songtuan/studio.project.json'
            data = json.loads(project.read_text())
            data['name'] = 'My edited character'
            project.write_text(json.dumps(data))
            tools = root / 'studio.tools.json'
            config = json.loads(tools.read_text())
            # Simulate an earlier full library using the same source IDs.
            config['sampling']['libraries'] = ['references/ual1/./library.json']
            tools.write_text(json.dumps(config))
            before = project.read_bytes(), tools.read_bytes()
            result = install_examples(root)
            self.assertEqual(result['installed'], [])
            self.assertEqual(result['existingSources'], ['ual1'])
            self.assertEqual(before, (project.read_bytes(), tools.read_bytes()))
            self.assertEqual(len(list_sources(tools)), 43)

    def test_invalid_sample_never_leaves_partial_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            examples = root / 'examples'
            (examples / 'broken').mkdir(parents=True)
            (examples / 'broken/frame.png').write_bytes(b'damaged')
            manifest = {'version': 1, 'samples': [{'id': 'broken', 'files': {
                'frame.png': {'bytes': 7, 'sha256': hashlib.sha256(b'correct').hexdigest()}}}]}
            (examples / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                install_examples(root / 'library', examples)
            self.assertEqual(list((root / 'library/projects').iterdir()), [])

    def test_unrelated_same_name_directories_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for folder in ('projects/songtuan', 'references/ual1'):
                target = root / folder
                target.mkdir(parents=True)
                (target / 'keep.txt').write_text('owned locally')
            result = install_examples(root)
            self.assertEqual(result['installed'], [])
            self.assertEqual(result['registered'], [])
            self.assertEqual((root / 'references/ual1/keep.txt').read_text(), 'owned locally')


if __name__ == '__main__':
    unittest.main()
