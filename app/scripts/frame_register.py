#!/usr/bin/env python3
"""Register complete character masters from explicit, auditable landmarks.
No pose detection, camera-yaw correction, limb warping, or bbox fitting.
"""
import argparse
import os
from urllib.parse import quote
import hashlib
import html
import json
import math
import re
from pathlib import Path
from PIL import Image, ImageDraw


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite number')
    return float(value)


def point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Expected [x,y] point')
    return [finite(v) for v in value]


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plan(path):
    data = json.loads(path.read_text())
    if data['version'] != 1 or not data['frames']:
        raise ValueError('Expected version1 and nonempty frames')
    width, height = data['canvas']
    if any(type(v) is not int or v < 1 for v in (width, height)) or width * height > 16_000_000:
        raise ValueError('Invalid canvas')
    root_x, ground_y = point(data['origin'])
    # Motion-root origin and projected anatomical pelvis need not coincide.
    body_target_x = finite(data.get('targetBodyAxisX', root_x))
    if not 0 <= body_target_x <= width:
        raise ValueError('targetBodyAxisX must lie inside the common canvas')
    horizontal_anchor = data.get('horizontalAnchor', dict(marker='bodyAxis', targetX=body_target_x))
    if not isinstance(horizontal_anchor, dict):
        raise ValueError('horizontalAnchor must be an object')
    anchor_marker = horizontal_anchor.get('marker')
    if not isinstance(anchor_marker, str) or not anchor_marker.strip():
        raise ValueError('horizontalAnchor.marker must be a nonempty landmark name')
    anchor_target_x = finite(horizontal_anchor.get('targetX'))
    if not 0 <= anchor_target_x <= width:
        raise ValueError('horizontalAnchor.targetX must lie inside the common canvas')
    horizontal_anchor = dict(marker=anchor_marker, targetX=anchor_target_x)
    head_key = data['headMarker']
    ids = set()
    records = []
    for frame in data['frames']:
        name = frame['id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name) or name in ids:
            raise ValueError('Invalid or duplicate frame id')
        ids.add(name)
        source = (path.parent / frame['source']).resolve()
        if digest(source) != frame['sha256']:
            raise ValueError(f'{name}: source changed; re-annotate landmarks')
        markers = {key: point(value) for key, value in frame['markers'].items()}
        if anchor_marker not in markers:
            raise ValueError(f'{name}: horizontalAnchor marker {anchor_marker!r} is missing')
        support = frame['support']
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', v) for v in (support, frame['swing'])) or support == frame['swing']:
            raise ValueError(f'{name}: inconsistent anatomical roles')
        sole = markers[f'{support}Contact']
        head = markers[head_key]
        source_span = sole[1] - head[1]
        target_head = finite(frame['targetHeadY'])
        if source_span <= 0 or ground_y <= target_head:
            raise ValueError(f'{name}: nonpositive head/contact span')
        # Explicit height calibration only; motion bob is supplied per phase.
        scale = (ground_y - target_head) / source_span
        pixel_ratio = finite(frame.get('pixelRatio', 1))
        if pixel_ratio <= 0:
            raise ValueError(f'{name}: pixelRatio must be positive')
        # Compare correction in canonical source units, not raw source pixels.
        # This metadata never alters the landmark-derived geometry transform.
        effective_scale = scale * pixel_ratio
        translation = [anchor_target_x - markers[anchor_marker][0] * scale, ground_y - sole[1] * scale]
        transformed = {key: [p[0] * scale + translation[0], p[1] * scale + translation[1]] for key, p in markers.items()}
        duration = frame['durationMs']
        if type(duration) is not int or duration <= 0:
            raise ValueError('Invalid duration')
        records.append(dict(id=name, source=str(source), sha256=frame['sha256'], scale=scale,
                            pixelRatio=pixel_ratio, effectiveScaleCanonical=effective_scale,
                            translation=translation, markers=transformed, sourceMarkers=markers,
                            targetHeadY=target_head, horizontalAnchor=horizontal_anchor.copy(),
                            targetBodyAxisX=anchor_target_x if anchor_marker == 'bodyAxis' else None,
                            bodyAxisX=transformed['bodyAxis'][0],
                            support=support, swing=frame['swing'],
                            camera=frame['camera'], durationMs=duration))
    by_id = {r['id']: r for r in records}
    reference = by_id[data['reference']]
    errors = []
    tolerances = data['tolerances']
    for record in records:
        name = record['id']
        correction = record['effectiveScaleCanonical'] / reference['effectiveScaleCanonical'] - 1
        record['relativeScaleCorrection'] = correction
        if abs(correction) > finite(tolerances['scaleFraction']):
            errors.append(f'{name}: scale correction exceeds limit; redraw or re-annotate')
        if record['camera'] != data['camera']:
            errors.append(f'{name}: camera/facing/near-side mismatch; cannot repair yaw with 2D alignment')
        checks = {}
        for pair in data['proportionPairs']:
            a, b = pair
            reference_length = distance(reference['markers'][a], reference['markers'][b])
            if reference_length <= 0:
                raise ValueError('Zero reference landmark span')
            drift = distance(record['markers'][a], record['markers'][b]) / reference_length - 1
            checks[f'{a}/{b}'] = drift
            if abs(drift) > finite(tolerances['proportionFraction']):
                errors.append(f'{name}: {a}/{b} proportion drift; do not stretch body parts')
        # The 2D face vector detects pitch/profile drift, not full 3D camera yaw.
        a, b = [record['markers'][k] for k in data['anglePair']]
        c, d = [reference['markers'][k] for k in data['anglePair']]
        if distance(a, b) == 0 or distance(c, d) == 0:
            raise ValueError('Zero angle vector')
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        ref_angle = math.degrees(math.atan2(d[1] - c[1], d[0] - c[0]))
        delta = (angle - ref_angle + 180) % 360 - 180
        record.update(proportionDrift=checks, faceVectorDegrees=angle, faceAngleDeltaDegrees=delta)
        if abs(delta) > finite(tolerances['angleDegrees']):
            errors.append(f'{name}: face-vector angle drift; camera must be reviewed')
    for a, b in data.get('phasePairs', []):
        if abs(by_id[a]['targetHeadY'] - by_id[b]['targetHeadY']) > finite(tolerances['pairedHeadPixels']):
            errors.append(f'{a}/{b}: asymmetric paired-phase head trajectory')
    return data, records, errors


def render_record(record, canvas):
    with Image.open(record['source']) as im:
        if 'A' not in im.getbands():
            raise ValueError('Source must carry alpha')
        source = im.convert('RGBA')
    scale = record['scale']
    dx, dy = record['translation']
    bounds = source.getchannel('A').getbbox()
    if not bounds:
        raise ValueError('Empty source')
    record['sourceSize'] = list(source.size)
    # Check original nonzero-alpha extent, including detached hair/accessories.
    x0, y0, x1, y1 = bounds
    if x0 * scale + dx < 0 or y0 * scale + dy < 0 or x1 * scale + dx > canvas[0] or y1 * scale + dy > canvas[1]:
        raise ValueError(f'{record["id"]}: alpha would clip; enlarge common canvas')
    # Fractional uniform affine avoids independent integer-rounding height jitter.
    return source.transform(tuple(canvas), Image.Transform.AFFINE,
                            (1 / scale, 0, -dx / scale, 0, 1 / scale, -dy / scale), Image.Resampling.BICUBIC)


def publish(manifest, out):
    data, records, errors = load_plan(manifest)
    out = out.resolve()
    protected = {manifest.resolve(), *(Path(r['source']) for r in records)}
    targets = [out / 'report.json', out / 'walk.webp', out / 'contact-sheet.png', out / 'preview.html'] + [out / f'{r["id"]}.png' for r in records]
    if protected.intersection(targets):
        raise ValueError('Output would overwrite input')
    images = []
    for record in records:
        try:
            images.append(render_record(record, data['canvas']))
        except ValueError as error:
            errors.append(str(error))
    report = dict(version=1, status='blocked' if errors else 'registered-not-visual-approval',
                  limitations='Manually annotated landmarks. No hidden anatomy detection; camera tags require visual confirmation. Uniform whole-image scale/translation only.',
                  canvas=data['canvas'], origin=data['origin'],
                  horizontalAnchor=records[0]['horizontalAnchor'], records=records, errors=errors)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if errors:
        message = html.escape('\n'.join(errors))
        (out / 'preview.html').write_text('<!doctype html><meta charset="utf-8"><title>Registration blocked</title><h1>Registration blocked</h1><p>Previous image exports are retained but are not the current accepted output.</p><pre>' + message + '</pre>')
        return 2
    for record, im in zip(records, images):
        im.save(out / f'{record["id"]}.png')
    images[0].save(out / 'walk.webp', save_all=True, append_images=images[1:],
                   duration=[r['durationMs'] for r in records], loop=0, lossless=True)
    w, h = data['canvas']
    sheet = Image.new('RGB', (w * len(images), h + 40), '#edf0ec')
    for i, (record, im) in enumerate(zip(records, images)):
        tile = Image.new('RGBA', (w, h), '#edf0ec')
        tile.alpha_composite(im)
        draw = ImageDraw.Draw(tile)
        draw.line((data['origin'][0], 0, data['origin'][0], h), fill='#81968a')
        draw.line((0, data['origin'][1], w, data['origin'][1]), fill='#81968a')
        draw.line((0, record['targetHeadY'], w, record['targetHeadY']), fill='#a58f70')
        sheet.paste(tile.convert('RGB'), (i * w, 40))
        ImageDraw.Draw(sheet).text((i * w + 12, 12), f'{record["id"]} support:{record["support"]} {record["durationMs"]}ms', fill='#23383d')
    sheet.save(out / 'contact-sheet.png')
    template = Path(__file__).with_name('frame_register_preview.html').read_text()
    payload = dict(title=data.get('title', 'Registered frames'), canvas=data['canvas'], origin=data['origin'], headMarker=data['headMarker'], frames=records)
    (out / 'preview.html').write_text(template.replace('__PAYLOAD__', json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')))
    return 0


def annotation_page(manifest, output):
    data = json.loads(manifest.read_text())
    if output.resolve() == manifest.resolve():
        raise ValueError('Cannot overwrite manifest')
    protected = {(manifest.parent / f['source']).resolve() for f in data['frames']}
    if output.resolve() in protected:
        raise ValueError('Cannot overwrite source')
    images = []
    for f in data['frames']:
        source = (manifest.parent / f['source']).resolve()
        images.append(quote(os.path.relpath(source, output.resolve().parent).replace(os.sep, '/'), safe='/'))
    template = Path(__file__).with_name('frame_register_annotator.html').read_text()
    payload = json.dumps(dict(manifest=data, images=images), ensure_ascii=False).replace('<', '\\u003c')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace('__PAYLOAD__', payload))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['register', 'annotate'])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'annotate':
            annotation_page(args.manifest, args.out)
            return 0
        return publish(args.manifest, args.out)
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(f'ERROR: {error}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
