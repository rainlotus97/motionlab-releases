#!/usr/bin/env python3
"""Project-local skeleton sampling sources, persistent plans and generation contracts.

Public API: list_sources(project_path), get_source(project_path, source_id,
profile_id=None), get_state(project_path), mutate(project_path, operation,
payload, expected_revision), export_plan(project_path, plan_id=None).

Only configured project-relative skeleton sources and the configured plan store
are accessed. Original continuous/base plans belong to the viewer and are never
stored. A custom plan contains unique source sample indices (fractional indices
are interpolated references with explicit real-frame contributors), their playback
order and positive per-pose hold durations. No mirrored poses, automatic loop
repair, image generation or artwork acceptance is implied.

Export schema version 1 / kind character-motion-sampling-plan contains projectId,
plan, source (joint names, hierarchy, file hashes and original provenance), and
samples (slot, sourceIndex, sourceFrameId, contributors, sourceTimeMs, durationMs,
positions). Fractional samples declare contributors and never invent native frame IDs.
The sum of sample durations is playbackDurationMs; source.durationMs retains the
native source duration. A non-loop final endpoint receives one source cadence
of hold time rather than a zero-duration invisible pose.
"""
import argparse
import bisect
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import sys
from pathlib import Path
import tempfile
import threading
import uuid


class ConflictError(ValueError):
    """The caller's revision no longer matches the stored revision."""


class StaleSourceError(ValueError):
    """A plan refers to a changed or unavailable source."""


_LOCK = threading.RLock()
_DIRECTIONS = {'E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must be a nonempty string of at most {limit} characters')
    return value.strip()


def _number(value, label):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f'{label} must be finite')
    return value


def _inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Expected a project-relative path')
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Path outside configured project')
    return target


def _project(path):
    path = Path(path).resolve()
    config = _read(path)
    if config.get('version') != 1:
        raise ValueError('Expected project version 1')
    _text(config.get('id'), 'project id')
    sampling = config.get('sampling')
    if not isinstance(sampling, dict) or 'store' not in sampling:
        raise ValueError('Project sampling configuration requires an explicit store path')
    root = path.parent
    store = _inside(root, sampling['store'])
    if store == path or store.suffix != '.json':
        raise ValueError('Plan store must be a separate JSON file')
    catalog = []
    sources = sampling.get('sources', [])
    libraries = sampling.get('libraries', [])
    if not isinstance(sources, list) or not isinstance(libraries, list):
        raise ValueError('sampling sources and libraries must be lists')
    for item in sources:
        if not isinstance(item, dict) or item.get('type') not in ('100style', 'libraryClip'):
            raise ValueError('Supported sampling source types: 100style and libraryClip')
        source = dict(item)
        source['id'] = _text(item.get('id'), 'source id')
        source['name'] = _text(item.get('name', source['id']), 'source name')
        source['_path'] = _inside(root, item.get('path'))
        catalog.append(source)
    for relative in libraries:
        library_path = _inside(root, relative)
        library = _read(library_path)
        if library.get('version') != 1 or not isinstance(library.get('clips'), list):
            raise ValueError('Expected version 1 motion library registry')
        profiles = library.get('profiles', {})
        if not isinstance(profiles, dict) or any(not isinstance(value, dict) for value in profiles.values()):
            raise ValueError('Library profile metadata must be objects')
        for clip in library['clips']:
            source = dict(clip)
            source['id'] = _text(clip.get('id'), 'library clip id')
            source['name'] = _text(clip.get('name', source['id']), 'library clip name')
            source['type'] = 'libraryClip'
            file = library_path.parent / clip.get('file', '')
            source['_path'] = _inside(root, file.relative_to(root).as_posix())
            source['_registry'] = library_path
            source['_profileMetadata'] = {key: value for key, value in profiles.items()
                if value.get('family') == source.get('family')}
            source['_sourceMetadata'] = library.get('sources', {}).get(clip.get('pack'), {})
            catalog.append(source)
    ids = [source['id'] for source in catalog]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate sampling source IDs')
    if store in {source['_path'] for source in catalog} or store in {source.get('_registry') for source in catalog}:
        raise ValueError('Plan store cannot overwrite a source or library')
    return config, root, store, catalog


def _entry(project_path, source_id):
    _, root, _, catalog = _project(project_path)
    for source in catalog:
        if source['id'] == source_id:
            return root, source
    raise ValueError('Unknown skeleton source ID')


def _profiles(source, actual=None):
    metadata = source.get('_profileMetadata', {})
    ids = actual if actual is not None else metadata
    return [{'id': key, 'name': metadata.get(key, {}).get('name', key)} for key in ids]


def list_sources(project_path):
    """List registry metadata without loading every library's joint arrays.

    Frame counts and exact profile IDs are returned by get_source on demand.
    Registry profile choices are family-filtered advertised profiles.
    """
    _, root, _, catalog = _project(project_path)
    result = []
    for source in catalog:
        item = {key: source[key] for key in ('id', 'name', 'family', 'group', 'pack') if key in source}
        item.update(format=source['type'], path=source['_path'].relative_to(root).as_posix(),
                    profiles=_profiles(source) if source['type'] == 'libraryClip' else [{'id': 'original', 'name': '来源比例'}],
                    loop=source.get('loop', True), available=source['_path'].is_file())
        if 'duration' in source:
            item['durationMs'] = _number(source['duration'], 'duration') * 1000
        result.append(item)
    return result


def _validate_joints(names, parents):
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Expected distinct joint names')
    if not isinstance(parents, list) or len(parents) != len(names):
        raise ValueError('Expected joint parents matching names')
    if any(type(parent) is not int or parent < -1 or parent >= len(names) or parent == index
           for index, parent in enumerate(parents)):
        raise ValueError('Invalid joint parent')
    for index, parent in enumerate(parents):
        seen = {index}
        while parent != -1:
            if parent in seen:
                raise ValueError('Cyclic joint hierarchy')
            seen.add(parent)
            parent = parents[parent]
    if not any(p == -1 for p in parents):
        raise ValueError('Joint hierarchy needs a root')


def _positions(frames, joint_count, root_path=None):
    if not isinstance(frames, list) or not frames:
        raise ValueError('Expected nonempty joint positions')
    if root_path is not None and (not isinstance(root_path, list) or len(root_path) != len(frames)):
        raise ValueError('Root path count differs from source frames')
    clean = []
    for index, frame in enumerate(frames):
        if not isinstance(frame, list) or len(frame) != joint_count:
            raise ValueError('Source joint count mismatch')
        route = [0, 0]
        if root_path is not None:
            route = root_path[index]
            if not isinstance(route, list) or len(route) != 2:
                raise ValueError('Root path must contain X/Z coordinate pairs')
            route = [_number(v, 'root path') for v in route]
        pose = []
        for point in frame:
            if not isinstance(point, list) or len(point) != 3:
                raise ValueError('Expected 3D joint coordinates')
            x, y, z = [_number(v, 'joint coordinate') for v in point]
            pose.append([x - route[0], y, z - route[1]])
        clean.append(pose)
    return clean


def get_source(project_path, source_id, profile_id=None):
    """Load a source/profile into canonical in-place XYZ joint frames."""
    root, source = _entry(project_path, source_id)
    path = source['_path']
    raw = path.read_bytes()
    data = json.loads(raw)
    names, parents = data.get('names'), data.get('parents')
    _validate_joints(names, parents)
    provenance = {'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()}
    if source['type'] == '100style':
        profile_id = profile_id or 'original'
        if profile_id != 'original':
            raise ValueError('100STYLE source supports only original profile')
        positions = _positions(data.get('inPlace'), len(names))
        frame_ids = data.get('frameIds')
        if not isinstance(frame_ids, list) or len(frame_ids) != len(positions) or any(type(i) is not int for i in frame_ids) or len(set(frame_ids)) != len(frame_ids):
            raise ValueError('Expected distinct integer source frameIds')
        dt = _number(data.get('dt'), 'source dt')
        if dt <= 0:
            raise ValueError('Source dt must be positive')
        endpoint = source.get('includesEndpoint', False)
        if type(endpoint) is not bool:
            raise ValueError('includesEndpoint must be boolean')
        times = [i * dt * 1000 for i in range(len(positions))]
        duration = (len(positions) - int(endpoint)) * dt * 1000
        root_index = names.index('Hips') if 'Hips' in names else parents.index(-1)
        profiles = [{'id': 'original', 'name': '来源比例'}]
        provenance['metadata'] = {k: data[k] for k in ('credit', 'license', 'source', 'sha256', 'modifications', 'metrics') if k in data}
        provenance['inPlacePolicy'] = 'Canonical inPlace positions retained without additional root removal'
    else:
        available = data.get('profiles', {})
        if not isinstance(available, dict) or not available:
            raise ValueError('Library clip requires profiles')
        profile_id = profile_id or source.get('profileId') or next(iter(available))
        if profile_id not in available:
            raise ValueError('Unknown source body profile')
        profile = available[profile_id]
        positions = _positions(profile.get('positions'), len(names), profile.get('rootPath'))
        times = data.get('times')
        if not isinstance(times, list) or len(times) != len(positions):
            raise ValueError('Source time count differs from frames')
        times = [_number(v, 'source time') * 1000 for v in times]
        duration = _number(data.get('duration'), 'source duration') * 1000
        endpoint = len(times) > 1 and abs(times[-1] - duration) <= 0.01
        frame_ids = list(range(len(positions)))
        root_index = data.get('root', parents.index(-1))
        profiles = _profiles(source, available)
        provenance['metadata'] = {**source.get('_sourceMetadata', {}), **{k: data[k] for k in ('sourceClip', 'sourceSha256', 'pack', 'family', 'group') if k in data}}
        if '_registry' in source:
            registry = source['_registry']
            provenance['registry'] = {'path': registry.relative_to(root).as_posix(), 'sha256': _hash(registry)}
        provenance['inPlacePolicy'] = 'Per-frame profile rootPath X/Z removed; vertical motion retained' if profile.get('rootPath') is not None else 'No rootPath supplied; positions retained as supplied'
    _number(duration, 'source duration milliseconds')
    for value in times:
        _number(value, 'source time milliseconds')
    if duration <= 0 or not times or abs(times[0]) > 0.01 or any(b <= a for a, b in zip(times, times[1:])) or times[-1] > duration + 0.01:
        raise ValueError('Source times must increase from zero within positive duration')
    if type(root_index) is not int or not 0 <= root_index < len(names):
        raise ValueError('Invalid source root joint')
    loop = source.get('loop', data.get('loop', True))
    if type(loop) is not bool:
        raise ValueError('Source loop must be boolean')
    selectable = len(positions) - int(endpoint and loop)
    if selectable < 1:
        raise ValueError('Source has no selectable frames')
    return {'id': source_id, 'name': source['name'], 'fingerprint': provenance['sha256'],
            'format': source['type'], 'frameCount': len(positions), 'selectableFrameCount': selectable,
            'includesEndpoint': endpoint, 'frameIds': frame_ids, 'timesMs': times,
            'durationMs': duration, 'loop': loop, 'names': names, 'parents': parents,
            'profiles': profiles, 'profileId': profile_id, 'positions': positions,
            'rootIndex': root_index, 'provenance': provenance}


def _empty(config):
    return {'version': 1, 'projectId': config['id'], 'revision': 0, 'plans': [], 'selectedPlanId': None}


def _read_state(config, store):
    state = _read(store) if store.exists() else _empty(config)
    if state.get('version') != 1 or state.get('projectId') != config['id'] or type(state.get('revision')) is not int or state['revision'] < 0:
        raise ValueError('Invalid sampling plan store identity or revision')
    plans = state.get('plans')
    if not isinstance(plans, list) or len(plans) > 1000 or any(not isinstance(p, dict) for p in plans):
        raise ValueError('Invalid sampling plan list')
    ids = [p.get('id') for p in plans]
    if any(not isinstance(i, str) or not i.startswith('plan-') for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('Invalid or reserved plan ID')
    if state.get('selectedPlanId') is not None and state['selectedPlanId'] not in ids:
        raise ValueError('Selected sampling plan is missing')
    return state


def _stale_state(state, catalog, project_path):
    clean = copy.deepcopy(state)
    lookup = {source['id']: source for source in catalog}
    hashes = {}
    loaded_sources = {}
    for plan in clean['plans']:
        sid = plan.get('sourceId')
        if sid not in hashes:
            source = lookup.get(sid)
            try:
                hashes[sid] = _hash(source['_path']) if source else None
            except OSError:
                hashes[sid] = None
        plan['stale'] = hashes[sid] != plan.get('sourceFingerprint')
        if not plan['stale']:
            key = (sid, plan.get('profileId'))
            try:
                if key not in loaded_sources:
                    loaded_sources[key] = get_source(project_path, sid, plan.get('profileId'))
                plan['phaseAudit'] = _validated_plan(loaded_sources[key], plan, plan)['phaseAudit']
            except (OSError, ValueError, KeyError, TypeError):
                plan['stale'] = True
        if plan['stale']:
            plan['staleReason'] = 'Source is missing, changed, or no longer validates; create a new reviewed plan'
    return clean


def get_state(project_path):
    """Read persisted custom plans, marking stale sources without rewriting them."""
    with _LOCK:
        config, _, store, catalog = _project(project_path)
        return _stale_state(_read_state(config, store), catalog, project_path)


def _camera(value):
    if not isinstance(value, dict) or value.get('direction', 'E') not in _DIRECTIONS:
        raise ValueError('Camera direction must be one of eight direction IDs')
    degrees = _number(value.get('degrees', 0), 'camera degrees')
    if not -360 <= degrees <= 360:
        raise ValueError('Camera degrees outside -360..360')
    return {'direction': value.get('direction', 'E'), 'degrees': degrees}


def _sample(source, index):
    """Sample captured XYZ arrays without claiming an interpolated pose is native."""
    value = _number(index, 'source frame index')
    last = source['frameCount'] - 1
    limit = source['selectableFrameCount']
    if value < 0 or (source['loop'] and value >= limit) or (not source['loop'] and value > last):
        raise ValueError('Source frame indices must be unique selectable finite numbers')
    nearest = round(value)
    if abs(value - nearest) < 1e-10 and (nearest < limit if source['loop'] else nearest <= last):
        value = int(nearest)
    a = int(math.floor(value))
    weight = value - a
    time_a = source['timesMs'][a]
    if weight == 0:
        return {'sourceIndex': value, 'sourceTimeMs': time_a,
            'phase': time_a / source['durationMs'], 'sourceFrameId': source['frameIds'][a],
            'sourceFrameIds': [source['frameIds'][a]], 'interpolated': False,
            'contributors': [{'sourceIndex': a, 'sourceFrameId': source['frameIds'][a], 'weight': 1.0}],
            'positions': copy.deepcopy(source['positions'][a])}
    b = a + 1
    if b == source['frameCount']:
        if not source['loop'] or source['includesEndpoint']:
            raise ValueError('Fractional source frame needs a following captured frame')
        b, time_b = 0, source['durationMs']
    else:
        time_b = source['timesMs'][b]
    source_time = time_a + (time_b - time_a) * weight
    return {'sourceIndex': value, 'sourceTimeMs': source_time,
        'phase': source_time / source['durationMs'], 'sourceFrameId': None,
        'sourceFrameIds': [source['frameIds'][a], source['frameIds'][b]], 'interpolated': True,
        'contributors': [{'sourceIndex': a, 'sourceFrameId': source['frameIds'][a], 'weight': 1 - weight},
                         {'sourceIndex': b, 'sourceFrameId': source['frameIds'][b], 'weight': weight}],
        'positions': [[x + (y - x) * weight for x, y in zip(p, q)]
                      for p, q in zip(source['positions'][a], source['positions'][b])]}


def uniform_frames(source, count, phase_offset=0):
    """Return time-uniform sample indices; even loop counts have exact half pairs.

    The inverse map uses actual timestamps, so noninteger frame periods such as
    an 81-interval loop do not silently round samples to captured indices.
    """
    if type(count) is not int or not 1 <= count <= 1024:
        raise ValueError('count must be an integer 1..1024')
    offset = _number(phase_offset, 'phase_offset')
    if not 0 <= offset < 1:
        raise ValueError('phase_offset must be within [0,1)')
    if not source['loop'] and offset != 0:
        raise ValueError('Non-loop uniform sampling does not wrap a phase offset')
    times = source['timesMs']
    result = []
    for slot in range(count):
        if source['loop']:
            time = ((offset + slot / count) % 1) * source['durationMs']
        else:
            time = (slot / (count - 1) if count > 1 else 0) * times[-1]
        a = max(0, bisect.bisect_right(times, time) - 1)
        if a == len(times) - 1:
            if source['loop'] and time > times[-1]:
                index = a + (time - times[a]) / (source['durationMs'] - times[a])
            else:
                index = a
        else:
            index = a + (time - times[a]) / (times[a + 1] - times[a])
        nearest = round(index)
        legal_native = nearest < source['selectableFrameCount'] if source['loop'] else nearest <= source['frameCount'] - 1
        result.append(int(nearest) if abs(index - nearest) < 1e-10 and legal_native else index)
    return result


def phase_audit(source, frames):
    """Check time pairing and chronological cycle order without modifying poses."""
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1024:
        raise ValueError('Audit needs 1..1024 source sample indices')
    samples = [_sample(source, frame) for frame in frames]
    phases = [sample['phase'] for sample in samples]
    count = len(phases)
    pairs = []
    if count % 2 == 0:
        for slot in range(count // 2):
            other = slot + count // 2
            distance = (phases[other] - phases[slot]) % 1
            error = abs(distance - .5)
            pairs.append({'slots': [slot, other], 'phases': [phases[slot], phases[other]],
                'halfCycleError': error, 'halfCycleErrorMs': error * source['durationMs']})
    steps = [((phases[(i + 1) % count] - phase) % 1) * source['durationMs']
             for i, phase in enumerate(phases)]
    if count == 1:
        steps = [source['durationMs']]
    max_error = max((pair['halfCycleError'] for pair in pairs), default=None)
    one_cycle = abs(sum(steps) - source['durationMs']) <= 1e-7
    paired = source['loop'] and count % 2 == 0 and one_cycle and max_error is not None and max_error <= 1e-9
    names = {name: i for i, name in enumerate(source['names'])}
    joint_pairs = []
    for name, index in names.items():
        opposite = name.replace('Right', 'Left', 1) if name.startswith('Right') else name[:-2] + '_l' if name.endswith('_r') else None
        if opposite in names:
            joint_pairs.append((name, index, names[opposite]))
    residuals = []
    root = source['rootIndex']
    for pair in pairs:
        a, b = (samples[slot]['positions'] for slot in pair['slots'])
        values = []
        for name, right, left in joint_pairs:
            for first, second in ((right, left), (left, right)):
                # Strict E only: compare forward Z / height Y after root removal.
                pa = [a[first][axis] - a[root][axis] for axis in (2, 1)]
                pb = [b[second][axis] - b[root][axis] for axis in (2, 1)]
                values.append(math.dist(pa, pb))
        residuals.extend(values)
        pair['maximumOppositeJointDifference'] = max(values, default=None)
    return {'applicable': source['loop'], 'paired': paired, 'pairCount': len(pairs),
        'phases': phases, 'pairs': pairs, 'maxHalfCycleError': max_error,
        'maxHalfCycleErrorMs': max_error * source['durationMs'] if max_error is not None else None,
        'oneForwardCycle': one_cycle, 'internalAndWrapStepsMs': steps,
        'wrappedStepMs': steps[-1],
        'anatomicalHalfCycleAudit': {'projection': 'strict E: root-relative [Z,Y]',
            'units': 'source coordinate units', 'matchedJointPairs': len(joint_pairs),
            'maximumJointDifference': max(residuals, default=None),
            'rmsJointDifference': math.sqrt(sum(v * v for v in residuals) / len(residuals)) if residuals else None,
            'posesModified': False},
        'limitations': ['Half-cycle pairing verifies selected times, not equal anatomical amplitudes or seamless velocity.',
            'Joint residuals retain source asymmetry and use linear XYZ reference interpolation; no pose symmetrization.',
            'Source loop seam and generated artwork still require playback review.']}


def _durations(source, frames, timing, supplied, loop):
    total = int(math.floor(source['durationMs'] + 0.5))
    if total < 1 and timing != 'custom':
        raise ValueError('Source duration is below one integer millisecond')
    if timing == 'even':
        if total < len(frames):
            raise ValueError('Source duration cannot provide a positive millisecond per frame')
        bounds = [(2 * i * total + len(frames)) // (2 * len(frames)) for i in range(len(frames) + 1)]
        return [b - a for a, b in zip(bounds, bounds[1:])]
    if timing == 'custom':
        if not isinstance(supplied, list) or len(supplied) != len(frames):
            raise ValueError('Custom durationsMs must match selected frames')
        if any(type(v) is not int or not 1 <= v <= 60000 for v in supplied):
            raise ValueError('Each custom duration must be an integer 1..60000 ms')
        return list(supplied)
    if timing != 'source':
        raise ValueError('timing must be source, even or custom')
    if not loop and any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError('Non-loop source timing needs chronological frame order')
    times = [_sample(source, frame)['sourceTimeMs'] for frame in frames]
    boundaries = [int(math.floor(time + 0.5)) for time in times]
    if loop:
        durations = [(boundaries[(i + 1) % len(frames)] - boundary) % total for i, boundary in enumerate(boundaries)]
        if len(frames) == 1:
            durations = [total]
    else:
        durations = [b - a for a, b in zip(boundaries, boundaries[1:])]
        # Showing the final captured endpoint still requires a positive hold.
        last = total - boundaries[-1]
        source_times = source['timesMs']
        cadence = max(1, round(source_times[-1] - source_times[-2])) if len(source_times) > 1 else total
        durations.append(last if last > 0 else cadence)
    if any(ms <= 0 for ms in durations):
        raise ValueError('Selected source frames are too close for positive integer millisecond durations')
    return durations


def _validated_plan(source, values, existing=None):
    frames = values.get('frames', list(range(source['selectableFrameCount'])))
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1024:
        raise ValueError('frames must contain 1..1024 source indices')
    for frame in frames:
        _number(frame, 'source frame index')
    frames = [_sample(source, frame)['sourceIndex'] for frame in frames]
    if len(frames) != len(set(frames)):
        raise ValueError('Source frame indices must be unique selectable finite numbers')
    loop = values.get('loop', source['loop'])
    if type(loop) is not bool:
        raise ValueError('Plan loop must be boolean')
    timing = values.get('timing', 'source')
    durations = _durations(source, frames, timing, values.get('durationsMs'), loop)
    audit = phase_audit(source, frames)
    half = len(frames) // 2
    audit['playbackTimingAudit'] = {'durationMs': sum(durations),
        'halfDurationsMs': [sum(durations[:half]), sum(durations[half:])] if len(frames) % 2 == 0 else None,
        'maximumOppositeHoldDifferenceMs': max((abs(durations[i] - durations[i + half]) for i in range(half)), default=None) if len(frames) % 2 == 0 else None,
        'note': 'Pose-time pairing and authored hold-time symmetry are separate; integer millisecond rounding can differ by 1 ms.'}
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    return {'id': existing['id'] if existing else 'plan-' + uuid.uuid4().hex,
        'name': _text(values.get('name', source['name'] + ' · 抽帧方案'), 'plan name'),
        'sourceId': source['id'], 'profileId': source['profileId'],
        'sourceFingerprint': source['fingerprint'], 'frames': list(frames), 'timing': timing,
        'durationsMs': durations,
        'camera': _camera(values.get('camera', {'direction': 'E', 'degrees': 0})), 'loop': loop,
        'phaseAudit': audit,
        'createdAt': existing['createdAt'] if existing else now, 'updatedAt': now}


def _find(state, plan_id):
    for plan in state['plans']:
        if plan['id'] == plan_id:
            return plan
    raise ValueError('Unknown or immutable base plan ID')


def _fresh_source(project_path, plan):
    try:
        source = get_source(project_path, plan['sourceId'], plan['profileId'])
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise StaleSourceError('Plan source is unavailable; review a new source plan') from error
    if source['fingerprint'] != plan['sourceFingerprint']:
        raise StaleSourceError('Plan source file changed; create a new reviewed plan')
    return source


def _atomic_write(store, state):
    store.parent.mkdir(parents=True, exist_ok=True)
    # Resolve again after parent creation; reject a symlinked parent escape.
    if store.resolve() != store:
        raise ValueError('Plan store changed through a symlink')
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='.sampling-', suffix='.tmp', dir=store.parent, delete=False) as output:
            name = output.name
            json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, store)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def mutate(project_path, operation, payload, expected_revision):
    """Apply one custom-plan mutation with optimistic concurrency and atomic save."""
    if not isinstance(payload, dict):
        raise ValueError('Mutation payload must be an object')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision must be a nonnegative integer')
    with _LOCK:
        config, _, store, catalog = _project(project_path)
        state = _read_state(config, store)
        if state['revision'] != expected_revision:
            raise ConflictError(f'Plan revision changed: expected {expected_revision}, current {state["revision"]}')
        if operation == 'create':
            source = get_source(project_path, payload.get('sourceId'), payload.get('profileId'))
            if payload.get('sourceFingerprint', source['fingerprint']) != source['fingerprint']:
                raise StaleSourceError('Source changed after preview; reload it before creating a plan')
            plan = _validated_plan(source, payload)
            state['plans'].append(plan)
        elif operation in ('update', 'duplicate'):
            original = _find(state, payload.get('id'))
            source = _fresh_source(project_path, original)
            if payload.get('sourceId', original['sourceId']) != original['sourceId'] or payload.get('profileId', original['profileId']) != original['profileId']:
                raise ValueError('Create a new plan to change source or body profile')
            values = {**original, **payload}
            if operation == 'duplicate':
                values['name'] = payload.get('name', original['name'] + ' · 副本')
            plan = _validated_plan(source, values, original if operation == 'update' else None)
            if operation == 'update':
                state['plans'][state['plans'].index(original)] = plan
            else:
                state['plans'].append(plan)
        elif operation == 'delete':
            plan = _find(state, payload.get('id'))
            state['plans'].remove(plan)
            if state['selectedPlanId'] == plan['id']:
                state['selectedPlanId'] = None
        elif operation == 'select':
            pid = payload.get('id')
            if pid is not None:
                _fresh_source(project_path, _find(state, pid))
            state['selectedPlanId'] = pid
        else:
            raise ValueError('Unknown plan operation; use create, update, duplicate, delete or select')
        if len(state['plans']) > 1000:
            raise ValueError('Too many sampling plans')
        state['revision'] += 1
        _atomic_write(store, state)
        return _stale_state(state, catalog, project_path)


def export_plan(project_path, plan_id=None):
    """Return selected captured poses and source provenance for later generation."""
    with _LOCK:
        config, _, store, _ = _project(project_path)
        state = _read_state(config, store)
        pid = plan_id if plan_id is not None else state['selectedPlanId']
        if pid is None:
            raise ValueError('Select a saved custom plan before export')
        plan = _find(state, pid)
        source = _fresh_source(project_path, plan)
        validated = _validated_plan(source, plan, plan)
        validated['updatedAt'] = plan['updatedAt']
        samples = [{**_sample(source, index), 'slot': slot, 'durationMs': validated['durationsMs'][slot]}
            for slot, index in enumerate(plan['frames'])]
        source_metadata = {key: source[key] for key in source if key != 'positions'}
        return {'version': 1, 'kind': 'character-motion-sampling-plan', 'projectId': config['id'],
            'storeRevision': state['revision'], 'plan': validated, 'source': source_metadata,
            'playbackDurationMs': sum(validated['durationsMs']), 'samples': samples, 'phaseAudit': validated['phaseAudit'],
            'generationConstraints': {'sampleOrder': 'Use samples in listed order, including last-to-first loop review',
                'camera': plan['camera'], 'mirrored': False,
                'interpolated': any(sample['interpolated'] for sample in samples),
                'interpolation': 'Linear source XYZ positions; contributors identify real captured frames and weights',
                'jointSides': 'Source anatomical left/right identity is retained; do not infer from screen side',
                'acceptance': 'Sampling plan only; generated pose, cloth, registration and motion require separate review'}}


def main(argv=None):
    """Small inspection/export CLI; every write requires an explicit new path."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('sources', 'inspect', 'source', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--project', type=Path, required=True)
        if name in ('inspect', 'export'):
            command.add_argument('--plan', help='Saved custom plan ID; export defaults only to the selected plan')
        if name == 'source':
            command.add_argument('--source', required=True)
            command.add_argument('--profile')
        if name == 'export':
            command.add_argument('--out', type=Path, required=True, help='New JSON path; existing files are never overwritten')
    args = parser.parse_args(argv)
    try:
        if args.command == 'sources':
            result = list_sources(args.project)
        elif args.command == 'source':
            result = get_source(args.project, args.source, args.profile)
        elif args.command == 'inspect':
            state = get_state(args.project)
            result = _find(state, args.plan) if args.plan is not None else state
        else:
            result = export_plan(args.project, args.plan)
            encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
            # Exclusive creation also rejects existing and dangling symlinks.
            with args.out.open('x', encoding='utf-8') as output:
                output.write(encoded)
            print(f'Exported {len(result["samples"])} skeleton references: {args.out.resolve()}')
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
