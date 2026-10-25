#!/usr/bin/env python3
"""Uniformly sample and audit the canonical 100STYLE motion-data.json format.

No pose mirroring, cycle correction, retargeting, or artwork validation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def _positive_int(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f'{label} must be a positive integer')
    return value


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite numeric coordinates')
    return float(value)


def uniform_indices(frame_count, count, includes_endpoint=False):
    """Return count phase indices in [0, period), excluding phase 1.0.

    With an explicitly included endpoint, period is frame_count - 1. Otherwise
    the indexing period is frame_count; that policy does not certify a loop.
    """
    _positive_int(frame_count, 'frame_count')
    _positive_int(count, 'count')
    if type(includes_endpoint) is not bool:
        raise ValueError('includes_endpoint must be boolean')
    period = frame_count - int(includes_endpoint)
    if period < 1:
        raise ValueError('An included endpoint needs at least two source frames')
    return [i * period / count for i in range(count)]


def allocate_ms(total_ms, count):
    """Round phase boundaries once, preserving the exact positive cycle total."""
    _positive_int(total_ms, 'total_ms')
    _positive_int(count, 'count')
    if total_ms < count:
        raise ValueError('Cycle cannot allocate at least one millisecond per sample')
    # Integer arithmetic gives nearest rounding without floating-point drift.
    boundaries = [(2 * i * total_ms + count) // (2 * count) for i in range(count + 1)]
    return [b - a for a, b in zip(boundaries, boundaries[1:])]


def audit_trajectory(points, reference=None):
    """Audit 2D/3D point steps, including last-to-first, optionally relative.

    Reference subtraction occurs per frame, so common root translation has no
    influence. A small wrapped step alone cannot prove velocity continuity.
    """
    if not points:
        raise ValueError('Expected a nonempty trajectory')
    dimensions = len(points[0])
    if dimensions not in (2, 3):
        raise ValueError('Expected 2D or 3D trajectory points')
    clean = []
    for p in points:
        if len(p) != dimensions:
            raise ValueError('Inconsistent trajectory dimensions')
        clean.append([_finite(v) for v in p])
    if reference is not None:
        if len(reference) != len(clean):
            raise ValueError('Reference trajectory length mismatch')
        relative = []
        for p, origin in zip(clean, reference):
            if len(origin) != dimensions:
                raise ValueError('Reference trajectory dimensions mismatch')
            relative.append([v - _finite(o) for v, o in zip(p, origin)])
        clean = relative
    steps = [math.dist(clean[i], clean[(i + 1) % len(clean)]) for i in range(len(clean))]
    largest = max(range(len(steps)), key=steps.__getitem__)
    return {
        'positions': clean,
        'stepDistancesIncludingWrap': steps,
        'maximumStep': max(steps),
        'meanStepIncludingWrap': sum(steps) / len(steps),
        'maximumInternalStep': max(steps[:-1], default=0.0),
        'wrappedSeamStep': steps[-1],
        'largestTransition': [largest, (largest + 1) % len(clean)],
        'limitations': 'Position differences only; no velocity, foot-contact, anatomical, or seamless-loop certification.'
    }


def _validate_source(data):
    required = ['inPlace', 'frameIds', 'names', 'parents', 'dt']
    if not isinstance(data, dict) or any(key not in data for key in required):
        raise ValueError('Expected canonical 100STYLE inPlace/frameIds/names/parents/dt data')
    names, parents, frames = data['names'], data['parents'], data['inPlace']
    if not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Expected unique joint names')
    if 'Hips' not in names or len(parents) != len(names):
        raise ValueError('Expected canonical Hips joint and matching parents')
    if any(type(p) is not int or p < -1 or p >= len(names) for p in parents):
        raise ValueError('Invalid joint parent')
    if not frames or len(data['frameIds']) != len(frames):
        raise ValueError('Source frame count mismatch')
    if any(type(i) is not int for i in data['frameIds']):
        raise ValueError('Expected integer source frame IDs')
    if _finite(data['dt']) <= 0:
        raise ValueError('Source dt must be positive')
    for frame in frames:
        if len(frame) != len(names):
            raise ValueError('Joint count mismatch')
        for point in frame:
            if len(point) != 3:
                raise ValueError('Expected 3D joint coordinates')
            for value in point:
                _finite(value)


def _sample_at(data, index, includes_endpoint):
    frames = data['inPlace']
    a = int(math.floor(index))
    weight = index - a
    if a < 0 or a >= len(frames) or (includes_endpoint and index >= len(frames) - 1):
        raise ValueError('Sample index outside declared cycle')
    if weight == 0:
        return {
            'sampleIndex': index, 'sourceIndices': [a],
            'sourceFrameIds': [data['frameIds'][a]], 'interpolationWeight': 0.0,
            'wrappedInterpolation': False,
            'inPlace': [[float(v) for v in point] for point in frames[a]]
        }
    b = a + 1
    wrapped = b == len(frames)
    if wrapped:
        if includes_endpoint:
            raise ValueError('Included endpoint interpolation cannot wrap')
        b = 0
    points = [[x + (y - x) * weight for x, y in zip(p, q)] for p, q in zip(frames[a], frames[b])]
    return {
        'sampleIndex': index, 'sourceIndices': [a, b],
        'sourceFrameIds': [data['frameIds'][a], data['frameIds'][b]],
        'interpolationWeight': weight, 'wrappedInterpolation': wrapped, 'inPlace': points
    }


def relative_trajectories(frames, names):
    """Audit canonical wrists relative to shoulder/hips and feet relative to hips."""
    by_name = {name: i for i, name in enumerate(names)}
    pairs = []
    for side in ['Right', 'Left']:
        for origin in [side + 'Shoulder', 'Hips']:
            pairs.append((side + 'Wrist', origin))
        for tip in ['Ankle', 'Toe', 'ToeEnd']:
            pairs.append((side + tip, 'Hips'))
    result = {}
    for joint, origin in pairs:
        if joint not in by_name or origin not in by_name:
            continue
        positions = [frame[by_name[joint]] for frame in frames]
        reference = [frame[by_name[origin]] for frame in frames]
        stats = audit_trajectory(positions, reference)
        projected = [[p[2], -p[1]] for p in stats['positions']]
        result[f'{joint}@{origin}'] = {'source3d': stats, 'strictE2d': audit_trajectory(projected)}
    return result


def build_report(data, count, includes_endpoint=False):
    _validate_source(data)
    indices = uniform_indices(len(data['inPlace']), count, includes_endpoint)
    period = len(data['inPlace']) - int(includes_endpoint)
    total_ms = int(math.floor(period * data['dt'] * 1000 + 0.5))
    durations = allocate_ms(total_ms, count)
    samples = [_sample_at(data, index, includes_endpoint) for index in indices]
    elapsed = 0
    for i, (sample, duration) in enumerate(zip(samples, durations)):
        sample.update(phase=i / count, timeMs=elapsed, durationMs=duration)
        elapsed += duration
    unique_source = data['inPlace'][:-1] if includes_endpoint else data['inPlace']
    result = {
        'version': 1, 'status': 'sampled-source-not-artwork-or-loop-approval',
        'sourceFormat': 'canonical 100STYLE motion-data.json',
        'sourceMetadata': {key: data[key] for key in ['credit', 'license', 'source', 'sha256'] if key in data},
        'sourceFrameCount': len(data['inPlace']), 'sourceDtSeconds': data['dt'],
        'includesEndpoint': includes_endpoint, 'periodInSourceSamples': period,
        'cycleSeconds': period * data['dt'], 'totalMs': total_ms,
        'count': count, 'names': data['names'], 'parents': data['parents'],
        'samples': samples,
        'sourceTrajectories': relative_trajectories(unique_source, data['names']),
        'sampledTrajectories': relative_trajectories([s['inPlace'] for s in samples], data['names']),
        'limitations': [
            'Uniform time phases do not force anatomical left/right symmetry or equal travel distances.',
            'The declared indexing period does not establish that a captured source is seamless.',
            'Fractional samples interpolate existing source positions; they are not additional captured BVH poses.',
            'Wrapped interpolation is permitted only for endpoint-exclusive input and is explicitly reported.',
            'No mirroring, automatic cycle correction, pose normalization, or artwork limb validation is performed.',
            'Image wrist/foot markers, occlusion and cloth must be audited separately against these source trajectories.'
        ]
    }
    if includes_endpoint:
        result['declaredEndpointErrors'] = {
            'maximumHipsRelativeJointDistance': max(
                math.dist(
                    [v - o for v, o in zip(data['inPlace'][0][j], data['inPlace'][0][data['names'].index('Hips')])],
                    [v - o for v, o in zip(data['inPlace'][-1][j], data['inPlace'][-1][data['names'].index('Hips')])]
                ) for j in range(len(data['names']))
            ),
            'note': 'Compared declared first and final endpoints. No automatic correction or equality assumption.'
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--count', type=int, default=8)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--includes-endpoint', action='store_true', help='Declare that the final source sample is the cycle endpoint; do not infer this automatically')
    args = parser.parse_args()
    try:
        if args.source.resolve() == args.out.resolve():
            raise ValueError('Output cannot overwrite source')
        raw = args.source.read_bytes()
        data = json.loads(raw)
        report = build_report(data, args.count, args.includes_endpoint)
        report['sourceFile'] = str(args.source.resolve())
        report['sourceFileSha256'] = hashlib.sha256(raw).hexdigest()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(f'Sampled {report["count"]} phases / {report["totalMs"]} ms; source, seam and image correspondence require review: {args.out}')
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f'ERROR: {error}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
