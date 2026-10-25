#!/usr/bin/env python3
"""Plan RGBA8 sprite storage; estimates only, no image or runtime mutations."""
import argparse
import json
import math
from pathlib import Path


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def estimate(width, height, frames, directions=5, resident_directions=1,
             actions=1, outfits=1, resident_actions=1, resident_outfits=1,
             columns=4, padding=2, max_texture_size=4096, mipmaps=False,
             cpu_copies=0):
    counts = (width, height, frames, directions, resident_directions, actions,
              outfits, resident_actions, resident_outfits, columns, max_texture_size)
    if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in counts):
        raise ValueError('dimensions and counts must be positive integers')
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in (padding, cpu_copies)):
        raise ValueError('padding and cpu_copies must be nonnegative integers')
    if (resident_directions > directions or resident_actions > actions
            or resident_outfits > outfits):
        raise ValueError('resident counts cannot exceed stored counts')
    groups = directions * actions * outfits
    resident_groups = resident_directions * resident_actions * resident_outfits
    rows = math.ceil(frames / columns)
    page_width = columns * (width + padding * 2)
    page_height = rows * (height + padding * 2)
    page_bytes = page_width * page_height * 4
    frame_bytes = width * height * 4
    resident_base = page_bytes * resident_groups
    mip_extra = math.ceil(resident_base / 3) if mipmaps else 0
    return {
        'uniqueFramesPerDirection': frames,
        'frameBytes': frame_bytes,
        'uniqueFramePixelsBytesPerGroup': frame_bytes * frames,
        'allUniqueFramePixelsBytes': frame_bytes * frames * groups,
        'residentUniqueFramePixelsBytes': frame_bytes * frames * resident_groups,
        'atlas': {
            'layout': 'one regular-grid page per direction/action/outfit',
            'width': page_width, 'height': page_height, 'columns': columns,
            'rows': rows, 'unusedCells': rows * columns - frames,
            'paddingPerCellSide': padding, 'pageBaseBytes': page_bytes,
            'storedPages': groups, 'residentPages': resident_groups,
            'allBaseBytes': page_bytes * groups,
            'residentBaseBytes': resident_base,
            'maxTextureSizeAssumption': max_texture_size,
            'requiresPageSplit': max(page_width, page_height) > max_texture_size
        },
        'residentMipExtraBytesApprox': mip_extra,
        'residentTextureBytesApprox': resident_base + mip_extra,
        'cpuDecodedCopiesAssumption': cpu_copies,
        'residentCpuDecodedBytesEstimate': resident_base * cpu_copies,
        'limitations': 'Planning only; RGBA8, regular cells, no packing/compression/streaming. Page splitting is flagged, not performed. CPU copies exclude mipmaps. Not measured process RAM or GPU usage.'
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('width', 'height'):
        parser.add_argument('--' + name, type=positive, required=True)
    parser.add_argument('--frames', default='8,16,24', help='comma-separated unique frame counts')
    for name, default in [('directions', 5), ('resident-directions', 1), ('actions', 1),
                          ('outfits', 1), ('resident-actions', 1), ('resident-outfits', 1),
                          ('columns', 4), ('max-texture-size', 4096)]:
        parser.add_argument('--' + name, type=positive, default=default)
    parser.add_argument('--padding', type=nonnegative, default=2)
    parser.add_argument('--cpu-copies', type=nonnegative, default=0)
    parser.add_argument('--mipmaps', action='store_true')
    parser.add_argument('--out', type=Path)
    args = vars(parser.parse_args())
    output = args.pop('out')
    try:
        frame_counts = [positive(value.strip()) for value in args.pop('frames').split(',')]
        report = {'schemaVersion': 1, 'kind': 'planning-estimate', 'assumptions': args,
                  'scenarios': [estimate(frames=count, **args) for count in frame_counts]}
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if output:
        output.write_text(serialized, encoding='utf-8')
    print(serialized, end='')


if __name__ == '__main__':
    main()
