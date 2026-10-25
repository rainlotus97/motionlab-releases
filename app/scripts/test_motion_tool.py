#!/usr/bin/env python3
"""Technical geometry fixtures only: these are not finished character artwork."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

SPEC = importlib.util.spec_from_file_location('motion_tool', Path(__file__).with_name('motion_tool.py'))
motion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(motion)


class MotionToolTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.root = Path(self.temporary.name)
    image = Image.new('RGBA', (18, 10))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 3, 5, 8), fill=(80, 120, 180, 255))
    draw.point((0, 0), fill=(220, 160, 40, 255))  # Detached floating prop must survive.
    draw.rectangle((11, 3, 14, 8), fill=(80, 120, 180, 255))
    image.save(self.root / 'poses.png')
    image.resize((36, 20), Image.Resampling.NEAREST).save(self.root / 'costume.png')
    frames = [dict(source='poses', rect=[0, 0, 8, 10], anchor=[4, 9], markers=dict(hand=[5, 4])),
         dict(source='poses', rect=[8, 0, 18, 10], anchor=[5, 9], markers=dict(hand=[6, 4]))]
    action = dict(loop=True, frames=frames, sequence=[0, 1, 0], durations_ms=[120, 80, 200],
           events=[dict(step=1, name='footstep', payload=dict(note='visual cue only'))],
           root_motion=[[0, 0], [2, 0], [0, 0]])
    other = copy.deepcopy(action)
    action['name'] = '招招手'
    for frame in other['frames']:
      frame['source'] = 'costume'
      frame['rect'] = [value * 2 for value in frame['rect']]
      frame['anchor'] = [value * 2 for value in frame['anchor']]
      frame['markers'] = {key: [value * 2 for value in values] for key, values in frame['markers'].items()}
    self.data = dict(version=1, character=dict(id='test', name='Geometry fixture'), canvas=[20, 20],
            origin=[10, 16], scale=1, sources=dict(poses=dict(path='poses.png'),
            costume=dict(path='costume.png', pixel_ratio=2)),
                         variants=dict(blue=dict(name='扫庭装', actions=dict(wave=action)), gray=dict(actions=dict(wave=other))))
    self.manifest = self.root / 'manifest.json'
    self.save()

  def tearDown(self):
    self.temporary.cleanup()

  def save(self):
    motion.write_json(self.manifest, self.data)

  def test_uneven_rects_anchor_pixel_ratio_detached_prop_and_lossless_source(self):
    before = (self.root / 'poses.png').read_bytes()
    _, _, prepared, report = motion.prepare(self.manifest)
    self.assertTrue(report['valid'])
    self.assertEqual(prepared[0]['frames'][0]['placement'], [6, 7])
    self.assertEqual(prepared[0]['frames'][1]['placement'], [5, 7])
    self.assertEqual(prepared[0]['images'][0].getpixel((6, 7)), (220, 160, 40, 255))
    self.assertEqual(prepared[0]['frames'][0]['markers']['hand'], [11, 11])
    self.assertEqual(prepared[0]['frames'][0]['markers'], prepared[1]['frames'][0]['markers'])
    self.assertEqual(prepared[0]['images'][0].size, prepared[1]['images'][0].size)
    self.assertEqual(prepared[0]['frames'][0]['placement'], prepared[1]['frames'][0]['placement'])
    runtime_path = motion.pack(self.manifest, self.root / 'packed', columns=2)
    self.assertEqual(motion.verify(runtime_path), dict(valid=True, atlases=2))
    runtime = motion.read_json(runtime_path)
    action = runtime['variants']['blue']['actions']['wave']
    self.assertEqual(runtime['variants']['blue']['name'], '扫庭装')
    self.assertEqual(action['name'], '招招手')
    self.assertNotIn('name', runtime['variants']['gray'])
    self.assertNotIn('name', runtime['variants']['gray']['actions']['wave'])
    self.assertEqual(action['atlasSize'], [40, 20])
    self.assertEqual(len(action['frames']), 2)  # Repeated playback is not an extra painted pose.
    self.assertEqual(action['sequence'], [0, 1, 0])
    self.assertEqual(action['durations_ms'], [120, 80, 200])
    self.assertEqual(action['atlas'], 'test--blue--wave.webp')
    self.assertEqual(action['events'][0]['step'], 1)
    self.assertEqual(action['root_motion'], [[0, 0], [2, 0], [0, 0]])
    with Image.open(runtime_path.parent / action['atlas']) as atlas:
      self.assertEqual(atlas.convert('RGBA').crop((0, 0, 20, 20)).tobytes(), prepared[0]['images'][0].tobytes())
    self.assertEqual(before, (self.root / 'poses.png').read_bytes())

  def test_clipping_refused_and_recommendation_is_usable(self):
    self.data['origin'] = [0, 0]
    self.save()
    _, _, _, report = motion.prepare(self.manifest)
    self.assertFalse(report['valid'])
    self.assertGreater(report['actions'][0]['frames'][0]['clippedPixels'], 0)
    with self.assertRaisesRegex(motion.ContractError, 'clipped'):
      motion.pack(self.manifest, self.root / 'packed')
    self.assertFalse((self.root / 'packed').exists())
    self.data['canvas'] = report['recommendedCanvas']
    self.data['origin'] = report['recommendedOrigin']
    self.save()
    self.assertTrue(motion.prepare(self.manifest)[3]['valid'])

  def test_anchor_can_be_outside_source_for_projected_airborne_origin(self):
    self.data['canvas'] = [30, 30]
    self.data['origin'] = [10, 20]
    self.data['variants'] = dict(blue=self.data['variants']['blue'])
    for frame in self.data['variants']['blue']['actions']['wave']['frames']:
      frame['anchor'] = [4, 15]  # Ground projection below the 10-pixel source rectangle.
    self.save()
    _, _, prepared, report = motion.prepare(self.manifest)
    self.assertTrue(report['valid'])
    self.assertEqual(prepared[0]['frames'][0]['placement'], [6, 5])

  def test_invalid_timing_rect_source_and_background_contracts(self):
    action = self.data['variants']['blue']['actions']['wave']
    mutations = [lambda: action.update(durations_ms=[10]),
                 lambda: action.update(name='  '),
                 lambda: self.data['variants']['blue'].update(name=False),
          lambda: action.update(sequence=[]),
          lambda: action.update(sequence=[99]),
          lambda: action.update(events=[dict(step=8, name='impact')]),
          lambda: action.update(root_motion=[[0, 0]]),
          lambda: action['frames'][0].update(rect=[0, 0, 19, 10]),
          lambda: action['frames'][0].update(anchor=[float('inf'), 0]),
          lambda: self.data['character'].update(id='../escape')]
    for mutation in mutations:
      with self.subTest(mutation=mutation):
        original = copy.deepcopy(self.data)
        mutation()
        # The JSON writer correctly rejects infinity before a file can be emitted.
        try:
          self.save()
          with self.assertRaises(motion.ContractError):
            motion.prepare(self.manifest)
        except ValueError:
          pass
        self.data = original
        action = self.data['variants']['blue']['actions']['wave']
    Image.new('RGB', (18, 10), 'red').save(self.root / 'poses.png')
    self.save()
    with self.assertRaisesRegex(motion.ContractError, 'alpha source'):
      motion.prepare(self.manifest)
    self.data['background'] = 'opaque'
    self.save()
    self.assertTrue(motion.prepare(self.manifest)[3]['valid'])
    Image.new('RGBA', (18, 10)).save(self.root / 'poses.png')
    with self.assertRaisesRegex(motion.ContractError, 'empty alpha frame'):
      motion.prepare(self.manifest)

  def test_source_overwrite_and_nonfinite_json_are_rejected(self):
    out = self.root / 'packed'
    out.mkdir()
    collision = out / 'test--blue--wave.webp'
    with Image.open(self.root / 'poses.png') as source:
      source.save(collision, lossless=True, exact=True)
    self.data['sources']['poses']['path'] = str(collision)
    self.data['extension_for_future'] = {'enabled': True}  # Forward-compatible metadata is permitted.
    self.save()
    before = collision.read_bytes()
    with self.assertRaisesRegex(motion.ContractError, 'overwrite'):
      motion.pack(self.manifest, out)
    self.assertEqual(collision.read_bytes(), before)
    self.manifest.write_text('{"scale":NaN}', encoding='utf-8')
    with self.assertRaisesRegex(motion.ContractError, 'non-finite'):
      motion.prepare(self.manifest)

  def test_verify_detects_tampered_atlas(self):
    path = motion.pack(self.manifest, self.root / 'packed')
    data = motion.read_json(path)
    atlas = path.parent / data['variants']['blue']['actions']['wave']['atlas']
    atlas.write_bytes(atlas.read_bytes() + b'tampered')
    with self.assertRaisesRegex(motion.ContractError, 'hash mismatch'):
      motion.verify(path)

  def test_verify_rejects_unsafe_paths_dimensions_and_frame_rectangles(self):
    path = motion.pack(self.manifest, self.root / 'packed')
    original = motion.read_json(path)
    mutations = [lambda a: a.update(atlas='../outside.webp'),
                 lambda a: a.update(atlasSize=[1000000,1000000]),
                 lambda a: a.update(grid=[1,1]),
                 lambda a: a['frames'][0].update(rect=[0,0,999,20]),
                 lambda a: a['frames'][0].update(rect=[20,0,40,20]),
                 lambda a: a['frames'][0].update(bounds=[0,0,1,1])]
    for mutation in mutations:
      with self.subTest(mutation=mutation):
        data = copy.deepcopy(original)
        mutation(data['variants']['blue']['actions']['wave'])
        motion.write_json(path, data)
        with self.assertRaises(motion.ContractError):
          motion.verify(path)

  def test_animated_webp_preserves_variable_timeline_loops_and_constant_actions(self):
    action = self.data['variants']['blue']['actions']['wave']
    action.update(sequence=[0,0,1,1,0], durations_ms=[30,70,120,80,150], root_motion=[[0,0]] * 5)
    for loop in [True, False]:
      action['loop'] = loop
      self.save()
      runtime = motion.pack(self.manifest, self.root / 'packed')
      out = self.root / f'animated-{loop}.webp'
      result = motion.animate(runtime, 'blue', 'wave', out)
      metadata = motion.read_json(out.with_suffix('.metadata.json'))
      self.assertEqual((result['sourceFrames'], result['playbackSteps'], result['totalDurationMs']), (2,5,450))
      self.assertLess(result['encodedFrames'], 5)  # Encoder can merge identical adjacent poses.
      self.assertEqual(metadata['durations_ms'], [30,70,120,80,150])
      self.assertEqual(metadata['root_motion'], [[0,0]] * 5)
      self.assertFalse(metadata['rootMotionBaked'])
      with Image.open(out) as decoded:
        self.assertEqual(decoded.info['loop'], 0 if loop else 1)
      with self.assertRaisesRegex(motion.ContractError, 'overwrite'):
        motion.animate(runtime, 'blue', 'wave', runtime.parent / 'test--blue--wave.webp')
      with self.assertRaisesRegex(motion.ContractError, 'extension'):
        motion.animate(runtime, 'blue', 'wave', self.root / 'unsupported.gif')
    action.update(sequence=[0,0], durations_ms=[90,110], root_motion=None)
    self.save()
    runtime = motion.pack(self.manifest, self.root / 'packed')
    out = self.root / 'constant.webp'
    result = motion.animate(runtime, 'blue', 'wave', out)
    self.assertEqual((result['encodedFrames'], result['totalDurationMs']), (1,200))
    self.assertIn(b'ANIM', out.read_bytes())
    with Image.open(out) as decoded:
      decoded.load()
      self.assertEqual((decoded.info['duration'], decoded.info['loop']), (200,1))

  def test_preview_is_data_safe_relative_and_cannot_overwrite_sources(self):
    self.data['character']['name'] = '</script><script>alert(1)</script>'
    self.data['variants']['blue']['actions']['wave']['events'][0]['payload'] = '</script>'
    self.save()
    path = motion.pack(self.manifest, self.root / 'packed assets')
    template_root = self.root / 'fake-tool'
    (template_root / 'assets').mkdir(parents=True)
    (template_root / 'assets' / 'preview.html').write_text(
      '<script id="data" type="application/json">__MOTION_DATA__</script>', encoding='utf-8')
    with patch.object(motion, '__file__', str(template_root / 'scripts' / 'motion_tool.py')):
      html_path = motion.preview(path, self.root / 'review' / 'view.html')
      html = html_path.read_text(encoding='utf-8')
      self.assertNotIn('</script><script>', html)
      self.assertIn('\\u003c/script>', html)
      encoded = html.split('>', 1)[1].rsplit('</script>', 1)[0]
      payload = json.loads(encoded)
      atlas_url = payload['variants']['blue']['actions']['wave']['atlas']
      self.assertTrue(atlas_url.startswith('../packed%20assets/'))
      self.assertEqual(payload['character']['name'], self.data['character']['name'])
      with self.assertRaisesRegex(motion.ContractError, 'overwrite'):
        motion.preview(path, self.root / 'poses.png')
    _, sources, _, _ = motion.prepare(self.manifest)
    with self.assertRaisesRegex(motion.ContractError, 'overwrite'):
      motion.protect([self.root / 'poses.png'], sources)


if __name__ == '__main__':
  unittest.main()
