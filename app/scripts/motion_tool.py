#!/usr/bin/env python3
"""Inspect, pack, verify, preview and animate whole-pose assets. Python 3.10+, Pillow."""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

from PIL import Image, ImageChops, ImageStat

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
MAX_PIXELS = 64_000_000


class ContractError(ValueError):
  pass


def require(ok, message):
  if not ok:
    raise ContractError(message)


def number(value):
  try:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
  except OverflowError:
    return False


def integer(value):
  return isinstance(value, int) and not isinstance(value, bool)


def point(value, label):
  require(isinstance(value, list) and len(value) == 2 and all(map(number, value)),
      f'{label}: expected two finite numbers')
  return value


def size(value, label):
  point(value, label)
  require(all(integer(v) and v > 0 for v in value), f'{label}: expected positive integers')
  require(value[0] * value[1] <= MAX_PIXELS, f'{label}: exceeds {MAX_PIXELS} pixel safety limit')
  return value


def ident(value, label):
  require(isinstance(value, str) and ID.fullmatch(value), f'{label}: unsafe ID; use ASCII letters, digits, _ or -')
  return value


def obj(value, label):
  require(isinstance(value, dict), f'{label}: expected object')
  return value


def read_json(path):
  def reject(value):
    raise ContractError(f'non-finite JSON value: {value}')
  try:
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=reject)
  except (OSError, json.JSONDecodeError) as error:
    raise ContractError(f'{path}: {error}') from error


def write_json(path, value):
  Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def sha(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rect(value, dimensions, label):
  require(isinstance(value, list) and len(value) == 4 and all(map(integer, value)),
      f'{label}: expected four integer pixel coordinates')
  x0, y0, x1, y1 = value
  require(0 <= x0 < x1 <= dimensions[0] and 0 <= y0 < y1 <= dimensions[1],
      f'{label}: must be a nonempty rectangle inside {dimensions}')
  return value


def timeline(action, frame_count, label):
  require(isinstance(action.get('loop'), bool), f'{label}.loop: expected boolean')
  sequence = action.get('sequence', list(range(frame_count)))
  require(isinstance(sequence, list) and sequence and all(integer(v) and 0 <= v < frame_count for v in sequence),
      f'{label}.sequence: expected valid frame indices')
  durations = action.get('durations_ms')
  require(isinstance(durations, list) and len(durations) == len(sequence)
      and all(integer(v) and v > 0 for v in durations),
      f'{label}.durations_ms: positive integer per playback step required')
  events = action.get('events', [])
  require(isinstance(events, list), f'{label}.events: expected array')
  for i, event in enumerate(events):
    obj(event, f'{label}.events[{i}]')
    require(integer(event.get('step')) and 0 <= event['step'] < len(sequence), f'{label}: invalid event step')
    require(isinstance(event.get('name'), str) and event['name'].strip(), f'{label}: event name required')
    try:
      json.dumps(event.get('payload'), allow_nan=False)
    except (ValueError, TypeError) as error:
      raise ContractError(f'{label}: event payload must be finite JSON') from error
  root = action.get('root_motion')
  if root is not None:
    require(isinstance(root, list) and len(root) == len(sequence), f'{label}.root_motion: one position per step required')
    for i, value in enumerate(root):
      point(value, f'{label}.root_motion[{i}]')
    require(root[0] == [0, 0], f'{label}.root_motion: first position must be [0, 0]')
  return dict(loop=action['loop'], sequence=sequence, durations_ms=durations, events=events, root_motion=root)


def base_contract(data):
  obj(data, 'manifest')
  require(integer(data.get('version')) and data['version'] == 1, 'version must be integer 1')
  character = obj(data.get('character'), 'character')
  ident(character.get('id'), 'character.id')
  require(isinstance(character.get('name'), str) and character['name'].strip(), 'character.name required')
  size(data.get('canvas'), 'canvas')
  point(data.get('origin'), 'origin')
  require(number(data.get('scale')) and data['scale'] > 0, 'scale must be a finite positive number')
  require(data.get('background', 'transparent') in ('transparent', 'opaque'), 'background must be transparent or opaque')
  variants = obj(data.get('variants'), 'variants')
  require(variants, 'at least one variant required')
  for variant_id, variant in variants.items():
    ident(variant_id, 'variant ID')
    actions = obj(obj(variant, variant_id).get('actions'), f'{variant_id}.actions')
    if 'name' in variant:
      require(isinstance(variant['name'], str) and variant['name'].strip(), f'{variant_id}.name: nonempty string required')
    require(actions, f'{variant_id}: at least one action required')
    for action_id, action in actions.items():
      ident(action_id, 'action ID')
      obj(action, f'{variant_id}.{action_id}')
      if 'name' in action:
        require(isinstance(action['name'], str) and action['name'].strip(), f'{variant_id}.{action_id}.name: nonempty string required')
  return variants


def load_sources(data, manifest_path):
  sources = obj(data.get('sources'), 'sources')
  require(sources, 'at least one source required')
  result = {}
  for source_id, source in sources.items():
    ident(source_id, 'source ID')
    obj(source, source_id)
    require(isinstance(source.get('path'), str) and source['path'] and '\0' not in source['path'], f'{source_id}: valid source path required')
    path = (manifest_path.parent / source['path']).resolve()
    ratio = source.get('pixel_ratio', 1)
    require(number(ratio) and ratio > 0, f'{source_id}.pixel_ratio must be positive')
    try:
      with Image.open(path) as opened:
        require(opened.width * opened.height <= MAX_PIXELS, f'{source_id}: source exceeds pixel safety limit')
        has_alpha = 'A' in opened.getbands() or 'transparency' in opened.info
        rgba = opened.convert('RGBA')
    except (OSError, Image.DecompressionBombError) as error:
      raise ContractError(f'{source_id}: cannot read {path}: {error}') from error
    if data.get('background', 'transparent') == 'transparent':
      require(has_alpha and rgba.getchannel('A').getextrema()[0] < 255,
          f'{source_id}: transparent mode requires an alpha source with transparent pixels')
    result[source_id] = dict(path=path, image=rgba, pixel_ratio=ratio,
                record=dict(path=str(path), inputPath=source['path'], sha256=sha(path), bytes=path.stat().st_size,
                      size=list(rgba.size), pixel_ratio=ratio))
  return result


def alpha_count(alpha):
  return sum(alpha.histogram()[1:])


def render_frame(frame, source, data, label):
  obj(frame, label)
  box = rect(frame.get('rect', [0, 0, *source['image'].size]), source['image'].size, f'{label}.rect')
  anchor = point(frame.get('anchor'), f'{label}.anchor')
  markers = obj(frame.get('markers', {}), f'{label}.markers')
  for key, value in markers.items():
    ident(key, f'{label} marker ID')
    point(value, f'{label}.markers.{key}')
  effective = data['scale'] / source['pixel_ratio']
  require(number(effective) and effective > 0, f'{label}: effective scale must be finite and positive')
  crop = source['image'].crop(box)
  scaled = [v * effective for v in crop.size]
  require(all(number(v) and 0 < v <= MAX_PIXELS for v in scaled), f'{label}: resized dimensions exceed safety limit')
  dimensions = [round(v) for v in scaled]
  size(dimensions, f'{label} resized frame')
  resized = crop if tuple(dimensions) == crop.size else crop.resize(dimensions, Image.Resampling.LANCZOS)
  alpha = resized.getchannel('A')
  bounds = alpha.getbbox()
  require(bounds is not None, f'{label}: empty alpha frame; explicit fade/empty-frame playback is unsupported')
  placement = [data['origin'][i] - anchor[i] * effective for i in range(2)]
  require(all(map(number, placement)), f'{label}: transformed anchor is not finite')
  xy = [round(v) for v in placement]
  translated = [bounds[0] + xy[0], bounds[1] + xy[1], bounds[2] + xy[0], bounds[3] + xy[1]]
  width, height = data['canvas']
  visible = [max(0, -xy[0]), max(0, -xy[1]), min(dimensions[0], width - xy[0]), min(dimensions[1], height - xy[1])]
  inside = alpha_count(alpha.crop(visible)) if visible[2] > visible[0] and visible[3] > visible[1] else 0
  clipped = alpha_count(alpha) - inside
  canvas = Image.new('RGBA', (width, height))
  canvas.paste(resized, tuple(xy))
  transformed = {key: [xy[i] + value[i] * effective for i in range(2)] for key, value in markers.items()}
  require(all(number(v) for values in transformed.values() for v in values), f'{label}: transformed marker is not finite')
  return canvas, dict(source=frame['source'], sourceRect=box, sourceHash=source['record']['sha256'],
            alphaBounds=translated, clippedPixels=clipped, placement=xy,
            effectiveScale=effective, markers=transformed)


def difference(first, second):
  union = ImageChops.lighter(first.getchannel('A'), second.getchannel('A')).point(lambda value: 255 if value else 0)
  means = ImageStat.Stat(ImageChops.difference(first, second), mask=union).mean
  return round(sum(means) / 4, 6)


def prepare(manifest):
  manifest = Path(manifest).resolve()
  data = read_json(manifest)
  variants = base_contract(data)
  sources = load_sources(data, manifest)
  prepared = []
  all_bounds = []
  for variant_id, variant in variants.items():
    for action_id, action in variant['actions'].items():
      label = f'{variant_id}.{action_id}'
      frames = action.get('frames')
      require(isinstance(frames, list) and frames, f'{label}.frames: nonempty array required')
      timing = timeline(action, len(frames), label)
      images, reports = [], []
      for index, frame in enumerate(frames):
        obj(frame, f'{label}.frames[{index}]')
        require(isinstance(frame.get('source'), str) and frame['source'] in sources,
            f'{label}.frames[{index}]: unknown source')
        image, report = render_frame(frame, sources[frame['source']], data, f'{label}.frames[{index}]')
        images.append(image)
        reports.append(report)
        all_bounds.append(report['alphaBounds'])
      diffs = []
      for step in range(len(timing['sequence']) - (0 if timing['loop'] else 1)):
        next_step = (step + 1) % len(timing['sequence'])
        diffs.append(dict(fromStep=step, toStep=next_step, maskedUnionMeanAbsRGBA=difference(
          images[timing['sequence'][step]], images[timing['sequence'][next_step]])))
      prepared.append(dict(variant=variant_id, action=action_id, images=images, frames=reports,
                timeline=timing, adjacentDifferences=diffs))
  left = max(0, -min(box[0] for box in all_bounds))
  top = max(0, -min(box[1] for box in all_bounds))
  right = max(0, max(box[2] for box in all_bounds) - data['canvas'][0])
  bottom = max(0, max(box[3] for box in all_bounds) - data['canvas'][1])
  report = dict(version=1, character=data['character'], valid=not any(
    f['clippedPixels'] for a in prepared for f in a['frames']), canvas=data['canvas'], origin=data['origin'],
    recommendedCanvas=[data['canvas'][0] + left + right, data['canvas'][1] + top + bottom],
    recommendedOrigin=[data['origin'][0] + left, data['origin'][1] + top],
    sourceHashes={key: value['record'] for key, value in sources.items()},
    actions=[{key: value for key, value in item.items() if key != 'images'} for item in prepared],
    diagnostic='Pixel differences are diagnostic only; near-duplicates are not removed automatically.')
  return data, sources, prepared, report


def protect(paths, sources, manifest=None):
  protected = {value['path'] for value in sources.values()}
  if manifest:
    protected.add(Path(manifest).resolve())
  for path in paths:
    path = Path(path).resolve()
    same = path in protected or any(path.exists() and old.exists() and path.samefile(old) for old in protected)
    require(not same, f'output would overwrite a source or manifest: {path}')


def pack(manifest, out, columns=4):
  require(integer(columns) and 1 <= columns <= 256, 'columns must be an integer from 1 to 256')
  data, sources, prepared, report = prepare(manifest)
  require(report['valid'], 'nontransparent pixels would be clipped; run inspect for bounds and recommended canvas/origin')
  out = Path(out).resolve()
  names = [f"{data['character']['id']}--{a['variant']}--{a['action']}.webp" for a in prepared]
  require(len(set(names)) == len(names), 'IDs produce colliding atlas filenames; rename IDs containing repeated -- separators')
  protect([out / name for name in [*names, 'motion.runtime.json']], sources, manifest)
  runtime = {key: data[key] for key in ('version', 'character', 'canvas', 'origin', 'scale')}
  runtime.update(background=data.get('background', 'transparent'), sourceHashes=report['sourceHashes'], variants={})
  out.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.motion-', dir=out.parent) as temporary:
    temporary = Path(temporary)
    for item, name in zip(prepared, names):
      width, height = data['canvas']
      cols = min(columns, len(item['images']))
      rows = math.ceil(len(item['images']) / cols)
      dimensions = [width * cols, height * rows]
      size(dimensions, 'atlas size')
      require(max(dimensions) <= 16383, 'atlas exceeds WebP 16383-pixel side limit; choose fewer columns or revise shared canvas')
      atlas = Image.new('RGBA', dimensions)
      frames = []
      for index, (image, report_frame) in enumerate(zip(item['images'], item['frames'])):
        x, y = index % cols * width, index // cols * height
        atlas.paste(image, (x, y))
        frames.append(dict(rect=[x, y, x + width, y + height], bounds=report_frame['alphaBounds'],
                 origin=data['origin'], markers=report_frame['markers'], source=report_frame['source'],
                 sourceRect=report_frame['sourceRect']))
      path = temporary / name
      atlas.save(path, format='WEBP', lossless=True, exact=True, method=6)
      with Image.open(path) as decoded:
        require(decoded.convert('RGBA').tobytes() == atlas.tobytes(), f'{name}: lossless RGBA round trip failed')
      action = dict(atlas=name, atlasSize=dimensions, grid=[cols, rows], cellSize=data['canvas'], frames=frames,
             sha256=sha(path), bytes=path.stat().st_size, **item['timeline'])
      source_variant = data['variants'][item['variant']]
      source_action = source_variant['actions'][item['action']]
      if 'name' in source_action:
        action['name'] = source_action['name']
      target_variant = runtime['variants'].setdefault(item['variant'], dict(actions={}))
      if 'name' in source_variant:
        target_variant['name'] = source_variant['name']
      target_variant['actions'][item['action']] = action
    write_json(temporary / 'motion.runtime.json', runtime)
    verify(temporary / 'motion.runtime.json')
    out.mkdir(parents=True, exist_ok=True)
    for name in [*names, 'motion.runtime.json']:
      os.replace(temporary / name, out / name)
  return out / 'motion.runtime.json'


def verify(path):
  path = Path(path).resolve()
  data = read_json(path)
  variants = base_contract(data)
  source_hashes = obj(data.get('sourceHashes'), 'sourceHashes')
  require(source_hashes, 'sourceHashes must not be empty')
  for source_id, record in source_hashes.items():
    ident(source_id, 'source ID')
    obj(record, source_id)
    require(isinstance(record.get('path'), str) and record['path'] and '\0' not in record['path'], f'{source_id}: valid source path required')
    require(isinstance(record.get('sha256'), str) and HASH.fullmatch(record['sha256']), f'{source_id}: invalid source hash')
    size(record.get('size'), f'{source_id}.size')
    require(integer(record.get('bytes')) and record['bytes'] > 0, f'{source_id}: invalid source bytes')
    require(number(record.get('pixel_ratio')) and record['pixel_ratio'] > 0, f'{source_id}: invalid pixel_ratio')
  checked = 0
  atlas_names = set()
  for variant_id, variant in variants.items():
    for action_id, action in variant['actions'].items():
      label = f'{variant_id}.{action_id}'
      name = action.get('atlas')
      expected = f"{data['character']['id']}--{variant_id}--{action_id}.webp"
      require(name == expected, f'{label}: unsafe or mismatched atlas filename')
      require(name not in atlas_names, f'{label}: colliding atlas filename')
      atlas_names.add(name)
      atlas_path = path.parent / name
      require(isinstance(action.get('sha256'), str) and HASH.fullmatch(action['sha256']), f'{label}: invalid hash')
      require(atlas_path.is_file() and sha(atlas_path) == action['sha256'], f'{label}: atlas missing or hash mismatch')
      require(integer(action.get('bytes')) and atlas_path.stat().st_size == action['bytes'], f'{label}: bytes mismatch')
      dimensions = size(action.get('atlasSize'), f'{label}.atlasSize')
      grid = size(action.get('grid'), f'{label}.grid')
      require(action.get('cellSize') == data['canvas'], f'{label}: cellSize must match canvas')
      width, height = data['canvas']
      require(dimensions == [grid[0] * width, grid[1] * height], f'{label}: grid dimensions mismatch')
      frames = action.get('frames')
      require(isinstance(frames, list) and frames and len(frames) <= grid[0] * grid[1], f'{label}: invalid frames')
      timeline(action, len(frames), label)
      try:
        with Image.open(atlas_path) as opened:
          require(list(opened.size) == dimensions and opened.format == 'WEBP', f'{label}: atlas dimensions/format mismatch')
          require(getattr(opened, 'n_frames', 1) == 1, f'{label}: atlas must be one static image')
          atlas = opened.convert('RGBA')
      except (OSError, Image.DecompressionBombError) as error:
        raise ContractError(f'{label}: cannot read atlas: {error}') from error
      for index, frame in enumerate(frames):
        obj(frame, f'{label}.frames[{index}]')
        x, y = index % grid[0] * width, index // grid[0] * height
        box = rect(frame.get('rect'), dimensions, label)
        require(box == [x, y, x + width, y + height], f'{label}: frame grid/order mismatch')
        bounds = atlas.crop(box).getchannel('A').getbbox()
        require(bounds and frame.get('bounds') == list(bounds), f'{label}: empty frame or alpha bounds mismatch')
        require(frame.get('origin') == data['origin'], f'{label}: frame origin mismatch')
        for marker, value in obj(frame.get('markers'), f'{label}.markers').items():
          ident(marker, 'marker ID')
          point(value, f'{label}.{marker}')
        source_id = frame.get('source')
        require(isinstance(source_id, str) and source_id in source_hashes, f'{label}: unknown frame source')
        rect(frame.get('sourceRect'), source_hashes[source_id]['size'], f'{label}.sourceRect')
      checked += 1
  return dict(valid=True, atlases=checked)


def preview(runtime_path, out=None):
  runtime_path = Path(runtime_path).resolve()
  verify(runtime_path)
  data = read_json(runtime_path)
  out = Path(out).resolve() if out else runtime_path.parent / 'preview.html'
  protected = {runtime_path}
  for record in data['sourceHashes'].values():
    if isinstance(record.get('path'), str) and Path(record['path']).is_absolute():
      protected.add(Path(record['path']).resolve())
  for variant in data['variants'].values():
    for action in variant['actions'].values():
      atlas_path = runtime_path.parent / action['atlas']
      protected.add(atlas_path.resolve())
      action['atlas'] = quote(Path(os.path.relpath(atlas_path, out.parent)).as_posix(), safe='/')
  same = out in protected or any(out.exists() and old.exists() and out.samefile(old) for old in protected)
  require(not same, f'preview output would overwrite a source, runtime or atlas: {out}')
  template = Path(__file__).resolve().parents[1] / 'assets' / 'preview.html'
  require(template.is_file(), f'preview template missing: {template}')
  html = template.read_text(encoding='utf-8')
  require(html.count('__MOTION_DATA__') == 1, 'preview template must contain exactly one __MOTION_DATA__ placeholder')
  safe_json = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(html.replace('__MOTION_DATA__', safe_json), encoding='utf-8')
  return out


def runtime_outputs_safe(runtime_path, data, outputs):
  protected = {runtime_path.resolve()}
  protected.update((runtime_path.parent / action['atlas']).resolve()
                   for variant in data['variants'].values() for action in variant['actions'].values())
  protected.update(Path(record['path']).resolve() for record in data['sourceHashes'].values()
                   if Path(record['path']).is_absolute())
  for output in outputs:
    same = output in protected or any(output.exists() and old.exists() and output.samefile(old) for old in protected)
    require(not same, f'animation output would overwrite a source, runtime or atlas: {output}')


def static_animation(path, image, duration, loops):
  """Retain timing when libwebp optimizes a constant animation into an untimed still."""
  require(duration <= 0xffffff, 'constant animation exceeds WebP single-frame duration limit (16777215 ms)')
  buffer = BytesIO()
  image.save(buffer, format='WEBP', lossless=True, exact=True)
  raw, position, payload = buffer.getvalue(), 12, b''
  while position + 8 <= len(raw):
    length = int.from_bytes(raw[position + 4:position + 8], 'little')
    end = position + 8 + length + length % 2
    if raw[position:position + 4] in (b'VP8L', b'ALPH', b'VP8 '):
      payload += raw[position:end]
    position = end
  require(payload, 'cannot extract lossless image payload for timed constant animation')
  def uint24(value):
    return value.to_bytes(3, 'little')
  def chunk(name, content):
    return name + len(content).to_bytes(4, 'little') + content + b'\0' * (len(content) % 2)
  width, height = image.size
  extended = b'\x12\0\0\0' + uint24(width - 1) + uint24(height - 1)
  frame = b'\0' * 6 + uint24(width - 1) + uint24(height - 1) + uint24(duration) + b'\x02' + payload
  body = chunk(b'VP8X', extended) + chunk(b'ANIM', b'\0' * 4 + loops.to_bytes(2, 'little')) + chunk(b'ANMF', frame)
  path.write_bytes(b'RIFF' + (len(body) + 4).to_bytes(4, 'little') + b'WEBP' + body)


def visible_rgba_equal(first, second):
  alpha = first.getchannel('A')
  if alpha.tobytes() != second.getchannel('A').tobytes():
    return False
  mask = alpha.point(lambda value: 255 if value else 0).convert('RGB')
  difference = ImageChops.difference(first.convert('RGB'), second.convert('RGB'))
  return ImageChops.multiply(difference, mask).getbbox() is None


def animate(runtime_path, variant_id, action_id, out):
  runtime_path, out = Path(runtime_path).resolve(), Path(out).resolve()
  require(out.suffix.lower() == '.webp', 'animate output extension must be .webp')
  ident(variant_id, 'variant ID')
  ident(action_id, 'action ID')
  verify(runtime_path)
  data = read_json(runtime_path)
  require(variant_id in data['variants'], 'unknown variant ID')
  actions = data['variants'][variant_id]['actions']
  require(action_id in actions, 'unknown action ID')
  action = actions[action_id]
  metadata_path = out.with_suffix('.metadata.json')
  runtime_outputs_safe(runtime_path, data, [out, metadata_path])
  durations, sequence = action['durations_ms'], action['sequence']
  total, loops = sum(durations), 0 if action['loop'] else 1
  require(max(durations) <= 0xffffff and total <= 0x7fffffff, 'animation timing exceeds WebP codec limits')
  with Image.open(runtime_path.parent / action['atlas']) as atlas:
    rgba = atlas.convert('RGBA')
    frames = [rgba.crop(frame['rect']) for frame in action['frames']]
  played = [frames[index] for index in sequence]
  out.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.motion-animation-', dir=out.parent) as temporary:
    encoded = Path(temporary) / 'animation.webp'
    played[0].save(encoded, format='WEBP', save_all=True, append_images=played[1:], duration=durations,
                   loop=loops, lossless=True, exact=True, method=6, allow_mixed=False)
    with Image.open(encoded) as probe:
      probe.load()
      constant = getattr(probe, 'n_frames', 1) == 1 and probe.info.get('duration', 0) == 0
    if constant:
      static_animation(encoded, played[0], total, loops)
    elapsed, source_step, remaining, encoded_count = 0, 0, durations[0], 0
    with Image.open(encoded) as decoded:
      require(list(decoded.size) == data['canvas'] and decoded.info.get('loop') == loops, 'animation size/loop round trip failed')
      encoded_count = decoded.n_frames
      for index in range(encoded_count):
        decoded.seek(index)
        actual = decoded.convert('RGBA')
        duration = decoded.info.get('duration', 0)
        require(integer(duration) and duration > 0, 'encoded animation frame has invalid duration')
        elapsed += duration
        while duration:
          require(source_step < len(played), 'encoded timeline exceeds source playback')
          require(visible_rgba_equal(played[source_step], actual), 'animation visible RGBA/timeline round trip failed')
          consumed = min(duration, remaining)
          duration, remaining = duration - consumed, remaining - consumed
          if remaining == 0:
            source_step += 1
            remaining = durations[source_step] if source_step < len(durations) else 0
    require(elapsed == total and source_step == len(sequence), 'encoded total duration differs from source timeline')
    metadata = dict(version=1, runtimeSha256=sha(runtime_path), atlasSha256=action['sha256'],
                    character=data['character'], variant=variant_id, action=action_id,
                    sourceFrames=len(frames), playbackSteps=len(sequence), encodedFrames=encoded_count,
                    totalDurationMs=total, sequence=sequence, durations_ms=durations, loop=action['loop'], webpLoopCount=loops,
                    events=action.get('events', []), root_motion=action.get('root_motion'), rootMotionBaked=False,
                    cues='Metadata only; no damage, movement, reward or save operations are executed.',
                    terminalFrame='For non-looping files, terminal-frame retention depends on the reader.',
                    verification='Decoded complete timeline: visible RGBA, durations, canvas and loop matched.',
                    sha256=sha(encoded), bytes=encoded.stat().st_size)
    write_json(Path(temporary) / 'metadata.json', metadata)
    os.replace(encoded, out)
    os.replace(Path(temporary) / 'metadata.json', metadata_path)
  return dict(animation=str(out), metadata=str(metadata_path), encodedFrames=encoded_count,
              sourceFrames=len(frames), playbackSteps=len(sequence), totalDurationMs=total)


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  inspect = commands.add_parser('inspect', help='report source/frame bounds and playback differences')
  inspect.add_argument('--manifest', required=True)
  inspect.add_argument('--out')
  packing = commands.add_parser('pack', help='pack complete anchored poses into lossless RGBA atlases')
  packing.add_argument('--manifest', required=True)
  packing.add_argument('--out', required=True)
  packing.add_argument('--columns', type=int, default=4)
  checking = commands.add_parser('verify', help='verify runtime contract, atlas hashes and alpha bounds')
  checking.add_argument('--runtime', required=True)
  viewing = commands.add_parser('preview', help='write a self-contained player UI referencing atlas files')
  viewing.add_argument('--runtime', required=True)
  viewing.add_argument('--out')
  animated = commands.add_parser('animate', help='export a timed lossless animated WebP and metadata')
  animated.add_argument('--runtime', required=True)
  animated.add_argument('--variant', required=True)
  animated.add_argument('--action', required=True)
  animated.add_argument('--out', required=True)
  args = parser.parse_args(argv)
  try:
    if args.command == 'inspect':
      _, sources, _, report = prepare(args.manifest)
      if args.out:
        protect([args.out], sources, args.manifest)
        write_json(args.out, report)
      else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
      require(report['valid'], 'clipping detected; inspect report includes recommendedCanvas and recommendedOrigin')
    elif args.command == 'pack':
      print(pack(args.manifest, args.out, args.columns))
    elif args.command == 'verify':
      print(json.dumps(verify(args.runtime)))
    elif args.command == 'animate':
      print(json.dumps(animate(args.runtime, args.variant, args.action, args.out)))
    else:
      print(preview(args.runtime, args.out))
  except (ContractError, OSError) as error:
    print(f'error: {error}', file=sys.stderr)
    return 2
  return 0


if __name__ == '__main__':
  sys.exit(main())
