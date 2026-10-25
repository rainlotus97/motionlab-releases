import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from render_sampling_guide import render
from sampling_plans import mutate


class SamplingGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project.json'
        source = {'names': ['Hips', 'Head', 'RightWrist', 'LeftWrist', 'RightAnkle', 'LeftAnkle'],
            'parents': [-1, 0, 0, 0, 0, 0], 'dt': 1 / 16, 'frameIds': list(range(100, 116)),
            'inPlace': [[[0, 5, 0], [0, 10, 0], [-1, 4, frame / 2], [1, 4, -frame / 2],
                [-1, 0, 1], [1, 0, -1]] for frame in range(16)]}
        # Unselected source outlier must still influence every pose's shared fit.
        source['inPlace'][-1][2][2] = 25
        self.source = self.root / 'source.json'
        self.source.write_text(json.dumps(source))
        self.project.write_text(json.dumps({'version': 1, 'id': 'guide-fixture', 'sampling': {
            'store': 'plans.json', 'sources': [{'id': 'walk', 'name': 'Walk',
                'type': '100style', 'path': 'source.json'}], 'libraries': []}}))
        self.state = mutate(self.project, 'create', {'sourceId': 'walk', 'frames': [i * 2 + .5 for i in range(8)],
            'timing': 'even', 'camera': {'direction': 'E', 'degrees': 0}}, 0)
        self.pid = self.state['plans'][0]['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_selected_guide_records_true_hash_rects_and_one_source_fit(self):
        mutate(self.project, 'select', {'id': self.pid}, self.state['revision'])
        result = render(self.project, self.root / 'guide')
        saved = json.loads((self.root / 'guide/guide.json').read_text())
        self.assertEqual(saved, result)
        self.assertEqual(saved['status'], 'selected-plan-guide')
        self.assertEqual(saved['sourceHash'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(saved['sheetSize'], [1728, 1536])
        self.assertEqual(saved['samples'][0]['rect'], [0, 0, 432, 768])
        self.assertEqual(saved['samples'][4]['rect'], [0, 768, 432, 768])
        self.assertEqual(saved['samples'][-1]['rect'], [1296, 768, 432, 768])
        self.assertEqual(len({sample['scale'] for sample in saved['samples']}), 1)
        self.assertEqual(len({sample['floorY'] for sample in saved['samples']}), 1)
        self.assertEqual(len({sample['bodyAxisX'] for sample in saved['samples']}), 1)
        self.assertEqual(saved['fit']['sourceFrameCountUsed'], 16)
        self.assertLess(saved['fit']['scale'], 8)  # The 25-unit outlier controls horizontal fit.
        self.assertEqual(saved['samples'][0]['sourceFrameIds'], [100, 101])
        self.assertEqual([c['weight'] for c in saved['samples'][0]['contributors']], [.5, .5])
        with Image.open(self.root / 'guide/guide.png') as sheet:
            self.assertEqual(list(sheet.size), saved['sheetSize'])
        self.assertEqual(len(list((self.root / 'guide').glob('pose-*.png'))), 8)

    def test_explicit_unselected_plan_is_draft_and_existing_output_is_refused(self):
        output = self.root / 'draft'
        result = render(self.project, output, self.pid)
        self.assertEqual(result['status'], 'draft-plan-guide')
        self.assertFalse(result['selectedForGeneration'])
        before = (output / 'guide.png').read_bytes()
        with self.assertRaisesRegex(ValueError, 'must be new'):
            render(self.project, output, self.pid)
        self.assertEqual((output / 'guide.png').read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'Select a saved'):
            render(self.project, self.root / 'no-selected')
        self.assertFalse((self.root / 'no-selected').exists())


if __name__ == '__main__':
    unittest.main()
