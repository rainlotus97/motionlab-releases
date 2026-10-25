#!/usr/bin/env python3
"""Non-destructive, trimmed sprite packages for the shared character motion studio."""
import argparse
import re
import hashlib
import json
import math
import tempfile
import zipfile
from pathlib import Path
from pathlib import PureWindowsPath
from PIL import Image


PRESETS = {
    'lossless': {'scale': 1, 'format': 'webp', 'quality': 100, 'lossless': True},
    'balanced': {'scale': .5, 'format': 'webp', 'quality': 82, 'lossless': False},
    'compact': {'scale': .25, 'format': 'webp', 'quality': 65, 'lossless': False},
}
MAX_FRAMES = 4096
MAX_STEPS = 20000
MAX_RENDER_PIXELS = 64_000_000
MAX_PAGE_PIXELS = 128_000_000
MAX_SOURCE_BYTES = 512_000_000
MARGIN = 4  # Two extruded texels and two transparent padding texels per side.


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def options_contract(options=None):
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError('options must be an object')
    allowed = {'preset', 'clipIds', 'scale', 'format', 'quality', 'maxPageSize',
               'includeFrames', 'includePreview', 'includeProofs'}
    if set(options) - allowed:
        raise ValueError('Unknown export options: ' + ', '.join(sorted(set(options) - allowed)))
    preset = options.get('preset', 'balanced')
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError('preset must be lossless, balanced or compact')
    result = {**PRESETS[preset], 'preset': preset, 'maxPageSize': 2048,
              'includeFrames': False, 'includePreview': False, 'includeProofs': False}
    for key in ('scale', 'format', 'quality', 'maxPageSize', 'includeFrames', 'includePreview', 'includeProofs'):
        if key in options:
            result[key] = options[key]
    if type(result['scale']) not in (int, float) or result['scale'] not in (1, .75, .5, .25):
        raise ValueError('scale must be 1, 0.75, 0.5 or 0.25')
    if result['format'] not in ('webp', 'png'):
        raise ValueError('format must be webp or png')
    if type(result['quality']) is not int or not 1 <= result['quality'] <= 100:
        raise ValueError('quality must be an integer 1..100')
    if type(result['maxPageSize']) is not int or result['maxPageSize'] not in (1024, 2048, 4096):
        raise ValueError('maxPageSize must be 1024, 2048 or 4096')
    for key in ('includeFrames', 'includePreview', 'includeProofs'):
        if type(result[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    if 'clipIds' in options:
        ids = options['clipIds']
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError('clipIds must be a nonempty list of distinct clip IDs')
        result['clipIds'] = ids
    # PNG is always lossless. WebP lossless is selected by the explicit preset.
    result['lossless'] = result['format'] == 'png' or result['lossless']
    return result


def default_draft(project):
    return {'version': 1, 'projectId': project['id'], 'fingerprint': project['fingerprint'],
            'clips': {c['id']: {'loop': c['loop'], 'steps': [
                {'slot': s['slot'], 'durationMs': s['durationMs'], 'offset': list(s['offset'])}
                for s in c['steps']]} for c in project['clips']}}


def validate_draft(project, draft):
    from motion_studio import validate_edit
    if not isinstance(draft, dict) or draft.get('version') != 1 or draft.get('projectId') != project['id'] or draft.get('fingerprint') != project['fingerprint']:
        raise ValueError('Draft belongs to a different project or changed source assets')
    edits = draft.get('clips')
    if not isinstance(edits, dict) or set(edits) != {c['id'] for c in project['clips']}:
        raise ValueError('Draft clip set differs from project')
    for clip in project['clips']:
        if not isinstance(edits[clip['id']], dict):
            raise ValueError('Invalid clip edit')
        validate_edit(clip, edits[clip['id']])
    return edits


def scaled_canvas(image, scale):
    if scale == 1:
        return image.copy()
    denominator = 2 if scale == .5 else 4
    width, height = image.size
    padded = Image.new('RGBA', (math.ceil(width / denominator) * denominator,
                                math.ceil(height / denominator) * denominator))
    padded.paste(image, (0, 0))
    resized = padded.resize((int(padded.width * scale), int(padded.height * scale)), Image.Resampling.LANCZOS)
    return resized.crop((0, 0, math.ceil(width * scale), math.ceil(height * scale)))


def visible_hash(image):
    normalized = image.copy()
    normalized.paste((0, 0, 0, 0), mask=image.getchannel('A').point(lambda a: 255 if a == 0 else 0))
    return hashlib.sha256(f'{image.width}x{image.height}:'.encode() + normalized.tobytes()).hexdigest()


def portable_source_path(value, root):
    """Keep meaningful project-relative provenance without publishing a workstation path."""
    if not isinstance(value, str) or not value:
        return None
    # A source authored on a different OS is evidence, never a runtime path dependency.
    if PureWindowsPath(value).is_absolute() and not Path(value).is_absolute():
        return None
    candidate = Path(value)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        return resolved.relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def compose_frame(clip, frame, offset, cache):
    source = Path(frame['file']).resolve()
    if source not in cache:
        with Image.open(source) as opened:
            if getattr(opened, 'n_frames', 1) != 1 or opened.width * opened.height > MAX_RENDER_PIXELS:
                raise ValueError('Source must be a bounded static image')
            cache[source] = opened.convert('RGBA')
    image = cache[source]
    rect = frame['rect']
    if not isinstance(rect, list) or len(rect) != 4 or any(type(x) is not int for x in rect):
        raise ValueError('Invalid source rectangle')
    x, y, x2, y2 = rect
    if not 0 <= x < x2 <= image.width or not 0 <= y < y2 <= image.height:
        raise ValueError('Source rectangle outside image')
    part = image.crop(tuple(rect))
    draw = frame.get('drawRect', [0, 0, *clip['canvas']])
    if len(draw) != 4 or any(type(n) is not int for n in draw) or draw[2:] != list(part.size):
        raise ValueError('Frame dimensions must match canvas or an explicit unscaled drawRect')
    full = Image.new('RGBA', tuple(clip['canvas']))
    bounds = part.getchannel('A').getbbox()
    if not bounds:
        raise ValueError('Empty source frame')
    dx, dy = offset
    left, top = draw[0] + dx, draw[1] + dy
    if bounds[0] + left < 0 or bounds[1] + top < 0 or bounds[2] + left > full.width or bounds[3] + top > full.height:
        raise ValueError(f'{clip["id"]}: frame offset would clip nontransparent pixels')
    # Do not use an alpha mask here: masking would square alpha at translucent fur edges.
    if float(left).is_integer() and float(top).is_integer():
        full.paste(part, (int(left), int(top)))
    else:
        full = part.transform(full.size, Image.Transform.AFFINE, (1, 0, -left, 0, 1, -top),
                              resample=Image.Resampling.BICUBIC)
    return full


def pack_rectangles(records, limit):
    """Deterministic, no-rotation MaxRects placement, with tightly cropped pages."""
    pages = []
    for record in sorted(records, key=lambda r: (-r['image'].height, -r['image'].width, r['id'])):
        width, height = record['image'].width + MARGIN * 2, record['image'].height + MARGIN * 2
        if width > limit or height > limit:
            raise ValueError('A trimmed sprite exceeds maxPageSize including extrusion/padding')
        choices = []
        for pi, page in enumerate(pages):
            for free in page['free']:
                fx, fy, fw, fh = free
                if width <= fw and height <= fh:
                    choices.append((min(fw - width, fh - height), max(fw - width, fh - height), pi, fy, fx))
        if not choices:
            if len(pages) >= 128:
                raise ValueError('Export exceeds page count limit')
            pages.append({'free': [(0, 0, limit, limit)], 'items': [], 'width': 0, 'height': 0})
            choices.append((0, 0, len(pages) - 1, 0, 0))
        _, _, pi, y, x = min(choices)
        page = pages[pi]
        free_next = []
        for fx, fy, fw, fh in page['free']:
            if x >= fx + fw or x + width <= fx or y >= fy + fh or y + height <= fy:
                free_next.append((fx, fy, fw, fh))
                continue
            if x > fx: free_next.append((fx, fy, x - fx, fh))
            if x + width < fx + fw: free_next.append((x + width, fy, fx + fw - x - width, fh))
            if y > fy: free_next.append((fx, fy, fw, y - fy))
            if y + height < fy + fh: free_next.append((fx, y + height, fw, fy + fh - y - height))
        unique = sorted(set(free_next))
        page['free'] = [r for i, r in enumerate(unique) if not any(i != j and
            r[0] >= s[0] and r[1] >= s[1] and r[0] + r[2] <= s[0] + s[2] and r[1] + r[3] <= s[1] + s[3]
            for j, s in enumerate(unique))]
        record['page'], record['position'] = pi, (x + MARGIN, y + MARGIN)
        page['items'].append(record)
        page['width'], page['height'] = max(page['width'], x + width), max(page['height'], y + height)
    if sum(p['width'] * p['height'] for p in pages) > MAX_PAGE_PIXELS:
        raise ValueError('Export exceeds decoded atlas pixel budget')
    return pages


def extrude(page, sprite, x, y):
    width, height = sprite.size
    page.paste(sprite, (x, y))
    for delta in (1, 2):
        page.paste(sprite.crop((0, 0, width, 1)), (x, y - delta))
        page.paste(sprite.crop((0, height - 1, width, height)), (x, y + height - 1 + delta))
        page.paste(sprite.crop((0, 0, 1, height)), (x - delta, y))
        page.paste(sprite.crop((width - 1, 0, width, height)), (x + width - 1 + delta, y))
    for sx, sy, px, py in ((0, 0, x - 2, y - 2), (width - 1, 0, x + width, y - 2),
                           (0, height - 1, x - 2, y + height), (width - 1, height - 1, x + width, y + height)):
        page.paste(sprite.getpixel((sx, sy)), (px, py, px + 2, py + 2))


def portable_preview(path, project):
    assets = Path(__file__).resolve().parents[1] / 'assets' / 'studio'
    html = (assets / 'index.html').read_text(encoding='utf-8')
    css = (assets / 'studio.css').read_text(encoding='utf-8')
    javascript = (assets / 'studio.mjs').read_text(encoding='utf-8')
    controls = (assets / 'controls.mjs').read_text(encoding='utf-8').replace('export function ', 'function ')
    javascript = re.sub(r"import \{[^}]+\} from './controls\.mjs'", lambda _: controls, javascript, count=1)
    if html.count('href="studio.css"') != 1 or html.count('src="studio.mjs"') != 1:
        raise ValueError('Shared studio asset contract changed')
    bootstrap = json.dumps(project, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    html = html.replace('<link rel="stylesheet" href="studio.css">', '<style>' + css + '</style>')
    html = html.replace('<script type="module" src="studio.mjs"></script>',
        '<script>window.__MOTION_STUDIO_PROJECT__=' + bootstrap + '</script>\n<script type="module">' +
        javascript.replace('</script', '<\\/script') + '</script>')
    path.write_text(html, encoding='utf-8')


def export_package(project_path, output, draft=None, options=None):
    """Validate fresh sources and write one new directory; never overwrite project assets."""
    from motion_studio import load_project, CLIP_LIBRARY_FIELDS
    project_path = Path(project_path).resolve()
    project, root = load_project(project_path)
    settings = options_contract(options)
    edits = validate_draft(project, default_draft(project) if draft is None else draft)
    ids = settings.get('clipIds', [c['id'] for c in project['clips']])
    known = {c['id'] for c in project['clips']}
    if not ids or set(ids) - known:
        raise ValueError('Select existing clips to export')
    selected = [c for c in project['clips'] if c['id'] in ids]
    if sum(len(c['frames']) for c in selected) > MAX_FRAMES or sum(len(c['steps']) for c in selected) > MAX_STEPS:
        raise ValueError('Export exceeds frame/step count limit')
    if sum(c['canvas'][0] * c['canvas'][1] * len(c['steps']) for c in selected) > MAX_RENDER_PIXELS:
        raise ValueError('Export exceeds logical frame pixel budget; select fewer clips')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Export directory must be new; source files are not overwritten')
    if not output.parent.is_dir():
        raise ValueError('Export parent directory does not exist')
    if output in (root, project_path) or output.is_relative_to(project_path):
        raise ValueError('Unsafe export path')
    source_files = sorted({Path(f['file']).resolve() for c in selected for f in c['frames']})
    if sum(p.stat().st_size for p in source_files) > MAX_SOURCE_BYTES:
        raise ValueError('Selected source files exceed byte budget')
    provenance = [{'path': p.relative_to(root).as_posix(), 'sha256': sha(p), 'bytes': p.stat().st_size}
                  for p in source_files]
    source_rgba = 0
    for source in source_files:
        try:
            with Image.open(source) as image:
                if image.width * image.height > MAX_RENDER_PIXELS:
                    raise ValueError('Source image exceeds decoded pixel budget')
                source_rgba += image.width * image.height * 4
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError(f'Cannot decode source image: {source.name}') from error
    if source_rgba > MAX_PAGE_PIXELS * 4:
        raise ValueError('Selected source images exceed decoded pixel budget')
    scale = settings['scale']
    unique, cache, pending_clips, frame_records = {}, {}, [], {}
    # Temporary output lives on the same filesystem for an atomic final rename.
    with tempfile.TemporaryDirectory(prefix='.sprite-export-', dir=output.parent) as temporary:
        work = Path(temporary) / 'package'
        (work / 'atlases').mkdir(parents=True)
        if settings['includeFrames']: (work / 'frames').mkdir()
        for clip in selected:
            canvas = [math.ceil(n * scale) for n in clip['canvas']]
            origin = [n * scale for n in clip['origin']]
            outclip = {'id': clip['id'], 'name': clip['name'], 'canvas': canvas, 'origin': origin,
                       'loop': edits[clip['id']]['loop'], 'canReorder': clip['canReorder'],
                       'markerLabels': dict(clip['markerLabels']), 'originLabel': clip['originLabel'],
                       'frames': [], 'steps': []}
            outclip.update({key: clip[key] for key in CLIP_LIBRARY_FIELDS if key in clip})
            for index, edit in enumerate(edits[clip['id']]['steps']):
                original = clip['steps'][edit['slot']]
                frame = clip['frames'][original['frame']]
                rendered = scaled_canvas(compose_frame(clip, frame, edit['offset'], cache), scale)
                bounds = rendered.getchannel('A').getbbox()
                if not bounds: raise ValueError('Scale would erase a complete frame')
                trimmed = rendered.crop(bounds)
                content_hash = visible_hash(trimmed)
                if content_hash not in unique:
                    unique[content_hash] = {'id': f'sprite-{len(unique):04d}', 'image': trimmed, 'sha256': content_hash}
                record = unique[content_hash]
                key = f'clip-{len(pending_clips):04d}-step-{index:04d}'
                markers = {name: [(p[axis] + edit['offset'][axis]) * scale for axis in range(2)]
                           for name, p in frame.get('markers', {}).items()}
                draw = [bounds[0], bounds[1], trimmed.width, trimmed.height]
                frame_records[key] = {'sprite': record['id'], 'sourceSize': {'w': canvas[0], 'h': canvas[1]},
                    'spriteSourceSize': dict(zip(('x', 'y', 'w', 'h'), draw)), 'trimmed': list(bounds) != [0, 0, *canvas],
                    'rotated': False, 'pivot': {'x': origin[0] / canvas[0], 'y': origin[1] / canvas[1]},
                    'origin': origin, 'markers': markers,
                    'source': {'file': Path(frame['file']).resolve().relative_to(root).as_posix(),
                               'rect': frame['rect'], 'frameId': frame['id'], 'slot': original['slot'],
                               'offset': edit['offset'], 'originalAssetPath': portable_source_path(frame.get('sourcePath'), root),
                               'originalAssetSha256': frame.get('sourceSha256'),
                               'originalSourceRect': frame.get('sourceRect'), 'originalSourceId': frame.get('sourceId')}}
                outclip['frames'].append({'id': key, 'drawRect': draw, 'markers': markers, '_sprite': content_hash})
                outclip['steps'].append({'slot': index, 'frame': index, 'durationMs': edit['durationMs'],
                    'offset': [0, 0], 'root': [n * scale for n in original['root']],
                    'events': [{**event, **({'step': index} if 'step' in event else {})} for event in original['events']]})
                if settings['includeFrames']:
                    rendered.save(work / 'frames' / (key + '.png'), optimize=True)
            pending_clips.append(outclip)
        pages = pack_rectangles(list(unique.values()), settings['maxPageSize'])
        page_records = []
        for index, page in enumerate(pages):
            image = Image.new('RGBA', (page['width'], page['height']))
            for record in page['items']: extrude(image, record['image'], *record['position'])
            relative = f'atlases/page-{index:03d}.{settings["format"]}'
            file = work / relative
            if settings['format'] == 'webp':
                image.save(file, lossless=settings['lossless'], quality=settings['quality'], method=6, exact=True)
            else:
                image.save(file, optimize=True, compress_level=9)
            page_records.append({'id': index, 'file': relative, 'width': image.width, 'height': image.height,
                                 'bytes': file.stat().st_size, 'sha256': sha(file)})
        for c in pending_clips:
            for frame in c['frames']:
                record = unique[frame.pop('_sprite')]
                x, y = record['position']; width, height = record['image'].size
                frame['url'] = page_records[record['page']]['file']
                frame['rect'] = [x, y, x + width, y + height]
                frame_records[frame['id']].update({'page': record['page'], 'frame': {'x': x, 'y': y, 'w': width, 'h': height}})
        atlas_bytes = sum(p['bytes'] for p in page_records)
        source_bytes = sum(p['bytes'] for p in provenance)
        frame_bytes = sum(p.stat().st_size for p in (work / 'frames').glob('*.png')) if settings['includeFrames'] else 0
        rgba = sum(p['width'] * p['height'] * 4 for p in page_records)
        full_rgba = sum(c['canvas'][0] * c['canvas'][1] * len(c['frames']) * 4 for c in pending_clips)
        report = {'sourceFileBytes': source_bytes, 'sourceFileCount': len(provenance),
            'sourceDecodedRGBABytes': source_rgba,
            'outputAtlasBytes': atlas_bytes, 'optionalFrameBytes': frame_bytes,
            'estimatedDecodedAtlasRGBABytes': rgba, 'untrimmedScaledFrameRGBABytes': full_rgba,
            'atlasFileReductionFraction': 1 - atlas_bytes / source_bytes if source_bytes else 0,
            'decodedTrimDedupReductionFraction': 1 - rgba / full_rgba if full_rgba else 0,
            'pageCount': len(page_records), 'pages': page_records, 'uniqueSpriteCount': len(unique),
            'sourceFrameCount': sum(len(c['frames']) for c in selected),
            'stepCount': sum(len(c['steps']) for c in pending_clips), 'clipCount': len(selected),
            'alphaPreserved': True, 'padding': 2, 'extrude': 2,
            'notes': ['WebP/PNG file bytes are transfer/storage sizes; decoded RGBA is a separate estimate.',
                      'Lossy WebP changes RGB only; alpha remains lossless. Scaling changes source sampling.',
                      'GPU block compression and mipmaps are not produced by this exporter.']}
        fingerprint = hashlib.sha256(json.dumps({'pages': page_records, 'clips': pending_clips}, sort_keys=True).encode()).hexdigest()
        preview_project = {'version': 1, 'id': project['id'] + '-export', 'name': project['name'],
            'fingerprint': fingerprint, 'clips': pending_clips, 'links': [], 'exportReadOnly': True}
        proof_files = [{**proof, 'bundledPath': f'provenance/{index:03d}-{Path(proof["path"]).name}'}
                       for index, proof in enumerate(project.get('provenanceFiles', []))] if settings['includeProofs'] else []
        package = {'version': 1, 'format': 'character-motion-sprite-package', 'projectId': project['id'],
            'projectName': project['name'], 'settings': settings, 'pages': page_records, 'frames': frame_records,
            'clips': pending_clips, 'provenance': {'projectPath': project_path.name,
                'projectSha256': sha(project_path), 'sourceFingerprint': project['fingerprint'], 'files': provenance,
                'proofFiles': proof_files},
            'meta': {'app': 'character-motion', 'scale': scale, 'format': 'RGBA8888',
                     'rotation': False, 'padding': 2, 'extrude': 2},
            'consumer': {
                'select': 'Select clips by stable id, or characterId + variantId + actionId + direction; use authored labels only for display.',
                'time': 'steps are ordered playback segments; durationMs is milliseconds. steps[].frame indexes clip.frames. Loop by total duration; stop non-loop clips at the last segment.',
                'image': 'Load frame.url relative to package.json. clip.frames[].rect is [left,top,right,bottom] in atlas pixels. frames[id].frame is the equivalent {x,y,w,h}.',
                'placement': 'Draw the atlas rect into drawRect [x,y,width,height] within clip.canvas; place clip.origin at the world anchor. The pivot/origin is an export convention, not an inferred body root.',
                'scale': 'All canvas, origin, drawRect, markers and step root coordinates are already scaled logical pixels. meta.scale describes source conversion; do not apply it again.',
                'ownership': 'The host owns world movement and gameplay effects. Root coordinates and events are data; consume them at most once per playback instance under the host authority. Pause/release presentation when hidden or disposed.',
            }}
        json_write(work / 'package.json', package)
        if settings['includePreview']:
            portable_preview(work / 'preview.html', preview_project)
        for proof in proof_files:
            (work / 'provenance').mkdir(exist_ok=True)
            proof_bytes = (root / proof['path']).read_bytes()
            if hashlib.sha256(proof_bytes).hexdigest() != proof['sha256']:
                raise ValueError('Provenance proof changed during export')
            (work / proof['bundledPath']).write_bytes(proof_bytes)
        # Runtime ZIP contents are deliberately separate from local encoding diagnostics.
        deliverables = sorted(p for p in work.rglob('*') if p.is_file())
        report['packageTotalBytes'] = sum(p.stat().st_size for p in deliverables)
        report['includedFiles'] = [p.relative_to(work).as_posix() for p in deliverables]
        # Re-read source identity immediately before publishing; a mid-export change invalidates the package.
        fresh, _ = load_project(project_path)
        if fresh['fingerprint'] != project['fingerprint']:
            raise ValueError('Source assets changed during export; retry from the current project')
        with zipfile.ZipFile(work / 'package.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for file in deliverables:
                info = zipfile.ZipInfo(file.relative_to(work).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, file.read_bytes(), compresslevel=9)
        report['zipBytes'] = (work / 'package.zip').stat().st_size
        json_write(work / 'report.json', report)
        # Reserve the new name atomically. An existing source/output directory is never replaced.
        output.mkdir()
        work.rename(output)
    return {'outputDirectory': str(output), 'report': {**report, 'zipBytes': (output / 'package.zip').stat().st_size}, 'previewProject': preview_project,
            'packagePath': str(output / 'package.zip')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--draft', type=Path)
    parser.add_argument('--preset', choices=PRESETS, default='balanced')
    parser.add_argument('--clip', action='append', dest='clipIds')
    parser.add_argument('--scale', type=float, choices=(1, .75, .5, .25))
    parser.add_argument('--format', choices=('webp', 'png'))
    parser.add_argument('--quality', type=int)
    parser.add_argument('--max-page-size', type=int, choices=(1024, 2048, 4096), default=2048)
    parser.add_argument('--include-frames', action='store_true')
    parser.add_argument('--include-preview', action='store_true')
    parser.add_argument('--include-proofs', action='store_true')
    args = parser.parse_args()
    options = {k: v for k, v in vars(args).items() if k in ('preset', 'clipIds', 'scale', 'format', 'quality') and v is not None}
    options.update({'maxPageSize': args.max_page_size, 'includeFrames': args.include_frames,
                    'includePreview': args.include_preview, 'includeProofs': args.include_proofs})
    draft = json.loads(args.draft.read_text(encoding='utf-8')) if args.draft else None
    print(json.dumps(export_package(args.project, args.out, draft, options), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
