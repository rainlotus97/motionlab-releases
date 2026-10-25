#!/usr/bin/env python3
"""Render a saved skeleton sampling plan into a reusable multi-cell pose guide.

Technical skeleton rendering only: source joint arrays are never character art.
All selected poses use one fit computed from the whole source, retaining root
sway and vertical motion. The declared camera is orthographic; E at 0 degrees
projects Z horizontally and Y vertically. No support-foot or cloth inference.
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont

from sampling_plans import export_plan, get_source, get_state, StaleSourceError


CELL = [432, 768]
COLUMNS = 4
COLORS = {'right': '#2c83b2', 'left': '#bf6552', 'center': '#405560'}


def _side(name):
    lower = name.lower()
    if lower.startswith('right') or lower.endswith('_r') or lower.endswith('.r'):
        return 'right'
    if lower.startswith('left') or lower.endswith('_l') or lower.endswith('.l'):
        return 'left'
    return 'center'


def _finger(name):
    return bool(re.search(r'finger|thumb|index_|middle_|pinky_|ring_', name.lower()))


def _project(point, degrees):
    angle = math.radians(degrees)
    x, y, z = point
    return [z * math.cos(angle) + x * math.sin(angle), y,
            -x * math.cos(angle) + z * math.sin(angle)]


def _font():
    for name in ('DejaVuSans.ttf', '/System/Library/Fonts/Supplemental/Arial.ttf'):
        try:
            return ImageFont.truetype(name, 15)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(source, degrees):
    """Fit the whole source once; selected poses do not control their own scale."""
    names = source['names']
    visible = [i for i, name in enumerate(names) if not _finger(name)]
    points = [_project(frame[index], degrees) for frame in source['positions'] for index in visible]
    terminal_feet = [i for i, name in enumerate(names)
        if re.search(r'toeend|toe_end|ball_leaf|toes_end|paw_end', name.lower())]
    feet = terminal_feet or [i for i, name in enumerate(names)
        if re.search(r'ankle|toe|foot|ball_|paw', name.lower()) and not _finger(name)]
    floor = min(frame[i][1] for frame in source['positions'] for i in (feet or visible))
    root_x = [_project(frame[source['rootIndex']], degrees)[0] for frame in source['positions']]
    anchor_x = sum(root_x) / len(root_x)
    height = max(point[1] for point in points) - floor
    head_radius = max(height * .04, .01)
    heads = [i for i, name in enumerate(names) if name.lower() in ('head', 'head_01')]
    top = max(point[1] for point in points)
    if heads:
        top = max(top, max(frame[heads[0]][1] + head_radius for frame in source['positions']))
    low = min(point[1] for point in points)
    extent = max(abs(point[0] - anchor_x) for point in points)
    floor_pixel = 710
    axis_pixel = CELL[0] / 2
    top_room = floor_pixel - 64
    below_room = CELL[1] - 24 - floor_pixel
    scale = min(top_room / max(top - floor, .01),
                (CELL[0] / 2 - 32) / max(extent + head_radius, .01))
    if low < floor:
        scale = min(scale, below_room / (floor - low))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Cannot compute a finite positive source fit')
    return {'scale': scale, 'sourceFloorY': floor, 'sourceAxisX': anchor_x,
            'bodyAxisX': axis_pixel, 'floorY': floor_pixel,
            'sourceHeadRadius': head_radius, 'headIndex': heads[0] if heads else None,
            'fitPolicy': 'One orthographic fit over every captured source pose; no per-sample scale or recentering',
            'sourceFrameCountUsed': source['frameCount']}


def _draw_pose(source, sample, camera, fit):
    image = Image.new('RGB', CELL, '#f2f5f2')
    draw = ImageDraw.Draw(image)
    draw.line([(16, fit['floorY']), (CELL[0] - 16, fit['floorY'])], fill='#acbeb6', width=2)
    draw.line([(fit['bodyAxisX'], 48), (fit['bodyAxisX'], CELL[1] - 20)], fill='#d6e0da', width=1)
    projected = [_project(point, camera['degrees']) for point in sample['positions']]
    def pixel(index):
        horizontal, vertical, _ = projected[index]
        return (fit['bodyAxisX'] + (horizontal - fit['sourceAxisX']) * fit['scale'],
                fit['floorY'] - (vertical - fit['sourceFloorY']) * fit['scale'])
    bones = []
    for index, parent in enumerate(source['parents']):
        if parent < 0 or _finger(source['names'][index]) or _finger(source['names'][parent]):
            continue
        if parent == source['rootIndex'] and source['names'][parent].lower() in ('root', 'rootmotion', 'root_motion'):
            continue  # Ground-motion helper is not an extra anatomical limb.
        if index == fit['headIndex'] or 'headend' in source['names'][index].lower():
            continue
        side = _side(source['names'][index])
        bones.append(((projected[index][2] + projected[parent][2]) / 2, index, parent, side))
    # Actual source depth decides occlusion. Anatomical identities never change.
    bones.sort(key=lambda bone: (bone[0], {'left': 0, 'center': 1, 'right': 2}[bone[3]]))
    for _, index, parent, side in bones:
        color = COLORS[side]
        a, b = pixel(parent), pixel(index)
        draw.line([a, b], fill=color, width=4)
        for x, y in (a, b):
            draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=color)
    if fit['headIndex'] is not None:
        head = fit['headIndex']
        parent = source['parents'][head]
        if parent >= 0:
            draw.line([pixel(parent), pixel(head)], fill=COLORS['center'], width=4)
        x, y = pixel(head)
        radius = fit['sourceHeadRadius'] * fit['scale']
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     outline=COLORS['center'], width=3)
        # A small forward marker makes the camera heading readable.
        angle = math.radians(camera['degrees'])
        draw.line([(x, y), (x + radius * 1.2 * math.cos(angle), y)], fill=COLORS['center'], width=2)
    label = f'{sample["slot"] + 1:02d} / idx {sample["sourceIndex"]:.6g} / phase {sample["phase"]:.5f}'
    draw.text((18, 16), label, fill='#263e48', font=_font())
    return image


def render(project_path, output, plan_id=None):
    """Render to a new directory and return the guide metadata dictionary."""
    output = Path(output).expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError('Guide output directory must be new; existing outputs are never overwritten')
    contract = export_plan(project_path, plan_id)
    source = get_source(project_path, contract['plan']['sourceId'], contract['plan']['profileId'])
    if source['fingerprint'] != contract['source']['fingerprint']:
        raise StaleSourceError('Source changed during guide export; reload the plan')
    state = get_state(project_path)
    selected = state['selectedPlanId'] == contract['plan']['id']
    camera = contract['plan']['camera']
    fit = _fit(source, camera['degrees'])
    rows = math.ceil(len(contract['samples']) / COLUMNS)
    size = [CELL[0] * COLUMNS, CELL[1] * rows]
    if size[0] * size[1] > 40_000_000:
        raise ValueError('Guide sheet exceeds 40 million pixels; split the sampling plan into smaller batches')
    sheet = Image.new('RGB', size, '#e1e8e2')
    # Exclusive mkdir prevents concurrent runs or symlinks replacing an output.
    output.mkdir()
    records = []
    for sample in contract['samples']:
        image = _draw_pose(source, sample, camera, fit)
        column, row = sample['slot'] % COLUMNS, sample['slot'] // COLUMNS
        x, y = column * CELL[0], row * CELL[1]
        filename = f'pose-{sample["slot"] + 1:02d}.png'
        image.save(output / filename)
        sheet.paste(image, (x, y))
        records.append({key: sample[key] for key in ('slot', 'sourceIndex', 'sourceFrameId',
            'sourceFrameIds', 'sourceTimeMs', 'phase', 'durationMs', 'contributors', 'interpolated')})
        records[-1].update(file=filename, rect=[x, y, *CELL],
            scale=fit['scale'], bodyAxisX=fit['bodyAxisX'], floorY=fit['floorY'],
            sheetBodyAxisX=x + fit['bodyAxisX'], sheetFloorY=y + fit['floorY'])
    sheet.save(output / 'guide.png')
    metadata = {'version': 1, 'kind': 'character-motion-sampling-guide',
        'status': 'selected-plan-guide' if selected else 'draft-plan-guide',
        'projectId': contract['projectId'], 'planId': contract['plan']['id'],
        'planName': contract['plan']['name'], 'selectedForGeneration': selected,
        'sourceHash': source['fingerprint'], 'sourceProvenance': source['provenance'],
        'camera': camera, 'projection': {'type': 'orthographic', 'sourceAxes': 'Y up; Z forward',
            'horizontal': 'Z*cos(degrees) + X*sin(degrees)', 'vertical': 'Y',
            'nearDepth': '-X*cos(degrees) + Z*sin(degrees)', 'depthOrder': 'far to near'},
        'colors': {'anatomicalRightNearAtE': COLORS['right'], 'anatomicalLeftFarAtE': COLORS['left'],
                   'center': COLORS['center']},
        'cell': CELL, 'columns': COLUMNS, 'rows': rows, 'sheetSize': size,
        'sheet': 'guide.png', 'fit': fit, 'samples': records,
        'phaseAudit': contract['phaseAudit'], 'playbackDurationMs': contract['playbackDurationMs'],
        'limitations': ['Technical reference skeletons only; no generated artwork or artistic approval.',
            'All poses retain one whole-source fit; source sway, bob and asymmetry are retained.',
            'Interpolated references identify original frame contributors; XYZ interpolation is not a native captured pose.',
            'No support-foot, hidden anatomy, hair, sleeves or skirt inference is performed.',
            'An explicit draft plan can be inspected; select it before using the default generation workflow.']}
    (output / 'guide.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--plan', help='Saved custom plan ID; defaults only to the selected plan')
    parser.add_argument('--out', type=Path, required=True, help='New output directory; its parent must exist')
    args = parser.parse_args(argv)
    try:
        result = render(args.project, args.out, args.plan)
        print(f'Rendered {len(result["samples"])} reference poses ({result["status"]}): {args.out.resolve() / "guide.png"}')
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
