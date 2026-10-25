"""Frozen skeleton-plan / rendered-frame comparisons and local review annotations.

Comparison definitions are project data, never the current editable sampling plan.
The source is read only to verify the frozen hash and obtain a whole-source fit.
Annotations refer to the exact comparison + snapshot + artwork fingerprint.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import quote

from sampling_plans import ConflictError, StaleSourceError, get_source, _sample


_LOCK = threading.RLock()
_STATUSES = {'unreviewed', 'accepted', 'needs-fix'}
_TAGS = {'angle', 'proportion', 'arms', 'legs', 'cloth', 'alignment', 'loop', 'other'}


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root, value, base=None, absolute=False):
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a project-relative path')
    path = Path(value)
    if path.is_absolute() and not absolute:
        raise ValueError('Expected a project-relative path')
    path = ((base or root) / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Comparison path outside configured project')
    return path


def _text(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must be a nonempty string up to {limit} characters')
    return value.strip()


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _config(project_path):
    path = Path(project_path).resolve()
    config = _read(path)
    root = path.parent
    configured = config.get('comparisons')
    if configured is None:
        return config, root, None, []
    if not isinstance(configured, dict) or not isinstance(configured.get('sources', []), list):
        raise ValueError('comparisons requires a sources list and reviewStore')
    store = _inside(root, configured.get('reviewStore'))
    if store.suffix != '.json':
        raise ValueError('Comparison review store must be JSON')
    definitions = []
    protected = {path}
    for source in config.get('sources', []):
        protected.add(_inside(root, source.get('path')))
    sampling = config.get('sampling', {})
    if sampling.get('store'):
        protected.add(_inside(root, sampling['store']))
    for source in sampling.get('sources', []):
        protected.add(_inside(root, source.get('path')))
    for relative in sampling.get('libraries', []):
        library_path = _inside(root, relative)
        protected.add(library_path)
        for clip in _read(library_path).get('clips', []):
            protected.add(_inside(root, clip.get('file'), library_path.parent))
    for item in configured.get('sources', []):
        if not isinstance(item, dict):
            raise ValueError('Comparison source must be an object')
        source_path = _inside(root, item.get('path'))
        protected.add(source_path)
        definitions.append({**item, '_path': source_path})
    if store in protected:
        raise ValueError('Review store cannot overwrite project, source, or plan definitions')
    return config, root, store, definitions


def _asset(root, path, expected=None):
    try:
        path = _inside(root, str(path), absolute=True)
        if not path.is_file():
            raise ValueError('Missing source file')
        digest = _hash(path)
        relative = path.relative_to(root).as_posix()
        result = {'path': relative, 'url': '/project/' + quote(relative),
            'sha256': digest, 'available': True}
        if expected:
            result.update(expectedSha256=expected, stale=digest != expected)
        return result
    except (ValueError, OSError):
        return {'available': False, 'reason': 'Source file is missing or outside this project'}


def _sheet_asset(root, base, record):
    fields = ('sheetSource', 'sheetSha256', 'sheetRect')
    if not any(key in record for key in fields):
        return {'available': False, 'reason': 'No original generated-sheet provenance is recorded'}, None
    path = _inside(root, record.get('sheetSource'), base)
    expected = record.get('sheetSha256')
    rect = record.get('sheetRect')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('sheetSha256 must be a full lowercase SHA-256 hash')
    if not isinstance(rect, list) or len(rect) != 4 or any(type(value) is not int for value in rect) or min(rect[:2]) < 0 or min(rect[2:]) <= 0:
        raise ValueError('sheetRect must contain nonnegative x/y and positive integer width/height')
    asset = _asset(root, path, expected)
    if asset['available']:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
        if rect[0] + rect[2] > width or rect[1] + rect[3] > height:
            raise ValueError('sheetRect extends outside the original generated sheet')
        asset.update(size=[width, height], rect=list(rect))
    return asset, path


def _alignment(value, clip):
    if not isinstance(value, dict):
        raise ValueError('Comparison requires explicit display alignment')
    if value.get('canvas') != clip['canvas'] or value.get('origin') != clip['origin']:
        raise ValueError('Comparison alignment canvas/origin must match the rendered clip')
    for key in ('pixelsPerSourceUnit', 'sourceOriginX', 'sourceFloorY'):
        if not _finite(value.get(key)):
            raise ValueError(f'Alignment {key} must be finite')
    if value['pixelsPerSourceUnit'] <= 0:
        raise ValueError('Comparison display scale must be positive')
    result = dict(value)
    result.setdefault('method', 'manual display alignment')
    return result


def _snapshot(path):
    contract = _read(path)
    if contract.get('version') != 1 or contract.get('kind') != 'character-motion-sampling-plan':
        raise ValueError('Comparison requires a frozen sampling-plan export snapshot')
    samples = contract.get('samples')
    if not isinstance(samples, list) or not 1 <= len(samples) <= 1024:
        raise ValueError('Comparison snapshot needs 1..1024 selected samples')
    if any(not isinstance(sample, dict) or type(sample.get('slot')) is not int for sample in samples) or [sample.get('slot') for sample in samples] != list(range(len(samples))):
        raise ValueError('Snapshot sample slots must be in explicit playback order')
    source = contract.get('source', {})
    names, parents = source.get('names'), source.get('parents')
    if not isinstance(names, list) or not names or not isinstance(parents, list) or len(parents) != len(names):
        raise ValueError('Snapshot needs joint names and hierarchy')
    for sample in samples:
        if not _finite(sample.get('sourceIndex')) or type(sample.get('durationMs')) is not int or sample['durationMs'] < 1:
            raise ValueError('Snapshot sample index and duration are invalid')
        positions = sample.get('positions')
        if not isinstance(positions, list) or len(positions) != len(names) or any(
                not isinstance(point, list) or len(point) != 3 or any(not _finite(v) for v in point) for point in positions):
            raise ValueError('Snapshot has invalid joint coordinates')
    return contract


def _load_comparisons(project_path, load_project, tool_project=None):
    config, root, store, definitions = _config(project_path)
    project, _ = load_project(project_path)
    clips = {clip['id']: clip for clip in project['clips']}
    registration = {}
    for source in config.get('sources', []):
        if source.get('type') == 'registration':
            report = _read(_inside(root, source['path']))
            registration[source['id']] = {record['id']: record for record in report['records']}
    result = []
    protected = set()
    for item in definitions:
        path = item['_path']
        data = _read(path)
        if data.get('version') != 1:
            raise ValueError('Comparison definition requires version 1')
        cid = _text(data.get('id', item.get('id')), 'comparison id')
        if item.get('id') is not None and item['id'] != cid:
            raise ValueError('Comparison definition ID differs from registry')
        clip = clips.get(data.get('clipId'))
        if clip is None:
            raise ValueError('Comparison references an unknown rendered clip')
        contract_path = _inside(root, data.get('planContract'), path.parent)
        if store == contract_path:
            raise ValueError('Review store cannot overwrite a plan snapshot')
        contract = _snapshot(contract_path)
        if contract.get('projectId') != config['id']:
            raise ValueError('Plan snapshot belongs to a different project')
        if contract.get('plan', {}).get('sourceFingerprint') != contract.get('source', {}).get('fingerprint'):
            raise ValueError('Plan snapshot source hashes disagree')
        source = None
        current_source_hash = None
        stale, reason = False, None
        try:
            source = get_source(tool_project or project_path, contract['plan']['sourceId'], contract['plan']['profileId'])
            current_source_hash = source['fingerprint']
            if source['fingerprint'] != contract['source']['fingerprint']:
                stale, reason, source = True, 'Skeleton source hash changed after the frozen snapshot', None
            elif source['names'] != contract['source']['names'] or source['parents'] != contract['source']['parents']:
                stale, reason, source = True, 'Frozen snapshot hierarchy differs from the verified source', None
            else:
                for sample in contract['samples']:
                    actual = _sample(source, sample['sourceIndex'])
                    contributors = sample.get('contributors')
                    native = actual['contributors']
                    contributor_match = isinstance(contributors, list) and len(contributors) == len(native) and all(
                        isinstance(a, dict) and a.get('sourceIndex') == b['sourceIndex'] and
                        a.get('sourceFrameId') == b['sourceFrameId'] and _finite(a.get('weight')) and abs(a['weight'] - b['weight']) <= 1e-9
                        for a, b in zip(contributors, native))
                    if not contributor_match or any(math.dist(a, b) > 1e-6 for a, b in zip(actual['positions'], sample['positions'])):
                        stale, reason, source = True, 'Frozen snapshot poses differ from their claimed source indices', None
                        break
        except (ValueError, OSError, KeyError, TypeError):
            stale, reason, source = True, 'Skeleton source is missing or no longer validates', None
        frames = data.get('frames')
        if not isinstance(frames, list) or len(frames) != len(contract['samples']):
            raise ValueError('Comparison must map every snapshot slot to an artwork frame')
        if any(not isinstance(frame, dict) or type(frame.get('slot')) is not int for frame in frames) or sorted(frame.get('slot', -1) for frame in frames) != list(range(len(frames))):
            raise ValueError('Comparison artwork slots must occur exactly once')
        by_id = {frame['id']: frame for frame in clip['frames']}
        mapped = []
        for record in sorted(frames, key=lambda frame: frame['slot']):
            slot = record['slot']
            step_slot = record.get('artStepSlot', slot)
            if type(step_slot) is not int or not 0 <= step_slot < len(clip['steps']):
                raise ValueError('Invalid artwork step slot')
            art = by_id.get(record.get('artFrameId'))
            if art is None:
                raise ValueError('Comparison references an unknown artwork frame ID')
            if clip['frames'][clip['steps'][step_slot]['frame']]['id'] != art['id']:
                raise ValueError('Artwork frame ID differs from its declared step slot')
            if 'sourceIndex' in record and (not _finite(record['sourceIndex']) or abs(record['sourceIndex'] - contract['samples'][slot]['sourceIndex']) > 1e-8):
                raise ValueError('Comparison source index differs from the frozen snapshot')
            normalized = {**record, 'artStepSlot': step_slot, 'artSource': _asset(root, art['file'])}
            raw = registration.get(clip['id'], {}).get(art['id'])
            normalized['masterSource'] = _asset(root, raw['source'], raw.get('sha256')) if raw else {
                'available': False, 'reason': 'No original master provenance is available for this clip'}
            normalized['sheetSource'], sheet_path = _sheet_asset(root, path.parent, record)
            if sheet_path is not None:
                protected.add(sheet_path)
            if not normalized['artSource']['available']:
                stale, reason = True, 'A normalized artwork frame is missing'
            else:
                protected.add(_inside(root, normalized['artSource']['path']))
            if normalized['masterSource']['available']:
                protected.add(_inside(root, normalized['masterSource']['path']))
            mapped.append(normalized)
        alignment = _alignment(data.get('alignment', data.get('artToSkeletonFit')), clip)
        proof = {'comparison': _hash(path), 'planContract': _hash(contract_path),
                 'artwork': [frame['artSource'].get('sha256') for frame in mapped],
                 'masters': [frame['masterSource'].get('sha256') for frame in mapped],
                 'sheets': [frame['sheetSource'].get('sha256') for frame in mapped],
                 'verifiedSourceHash': current_source_hash, 'sourceRelationshipValid': not stale}
        fingerprint = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
        result.append({**{k: v for k, v in data.items() if k not in ('planContract', 'frames')},
            'id': cid, 'name': data.get('name', item.get('name', cid)), 'clipId': clip['id'],
            'planContract': contract, 'planContractPath': contract_path.relative_to(root).as_posix(),
            'planContractSha256': proof['planContract'], 'comparisonSha256': proof['comparison'],
            'fingerprint': fingerprint, 'alignment': alignment, 'frames': mapped,
            'source': source, 'stale': stale, 'staleReason': reason})
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('Duplicate comparison IDs')
    if store in protected:
        raise ValueError('Review store cannot overwrite artwork')
    return config, root, store, project, result


def _reviews(config, store, comparisons):
    state = _read(store) if store is not None and store.exists() else {
        'version': 1, 'projectId': config['id'], 'revision': 0, 'reviews': {}}
    if state.get('version') != 1 or state.get('projectId') != config['id'] or type(state.get('revision')) is not int or state['revision'] < 0 or not isinstance(state.get('reviews'), dict):
        raise ValueError('Invalid comparison review store')
    clean = copy.deepcopy(state)
    fingerprints = {item['id']: item['fingerprint'] for item in comparisons}
    for cid, review in clean['reviews'].items():
        for record in review.get('slots', {}).values():
            record['stale'] = record.get('comparisonFingerprint') != fingerprints.get(cid)
    return clean


def get_state(project_path, load_project=None, tool_project=None):
    if load_project is None:
        from motion_studio import load_project
    with _LOCK:
        config, _, store, project, comparisons = _load_comparisons(project_path, load_project, tool_project)
        state = _reviews(config, store, comparisons)
        return {**state, 'configured': store is not None, 'projectFingerprint': project['fingerprint'],
                'clips': project['clips'], 'comparisons': comparisons}


def _write(store, state):
    store.parent.mkdir(parents=True, exist_ok=True)
    if store.resolve() != store:
        raise ValueError('Review store changed through a symlink')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=store.parent,
                prefix='.comparison-', suffix='.tmp', delete=False) as output:
            temporary = output.name
            json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, store)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def save_review(project_path, payload, load_project=None, tool_project=None):
    if load_project is None:
        from motion_studio import load_project
    if not isinstance(payload, dict):
        raise ValueError('Review payload must be an object')
    revision = payload.get('expectedRevision')
    if type(revision) is not int or revision < 0:
        raise ValueError('expectedRevision must be a nonnegative integer')
    with _LOCK:
        config, _, store, _, comparisons = _load_comparisons(project_path, load_project, tool_project)
        if store is None:
            raise ValueError('Project comparison review store is not configured')
        state = _reviews(config, store, comparisons)
        if state['revision'] != revision:
            raise ConflictError('Comparison review revision changed; reload before saving')
        comparison = next((item for item in comparisons if item['id'] == payload.get('comparisonId')), None)
        if comparison is None:
            raise ValueError('Unknown comparison ID')
        if payload.get('comparisonFingerprint') != comparison['fingerprint']:
            raise ConflictError('Comparison assets changed; reload before saving the review')
        slot = payload.get('slot')
        if type(slot) is not int or not 0 <= slot < len(comparison['frames']):
            raise ValueError('Invalid comparison slot')
        status, note, tags = payload.get('status'), payload.get('note', ''), payload.get('tags', [])
        if not isinstance(status, str) or status not in _STATUSES or not isinstance(note, str) or len(note) > 4000:
            raise ValueError('Review needs a known status and note up to 4000 characters')
        if not isinstance(tags, list) or len(tags) > len(_TAGS) or any(not isinstance(tag, str) or tag not in _TAGS for tag in tags) or len(set(tags)) != len(tags):
            raise ValueError('Review tags must contain distinct known problem tags')
        if comparison['stale'] and status == 'accepted':
            raise StaleSourceError('Cannot accept a comparison with an invalid source relationship')
        # Remove derived stale flags before persistence.
        for review in state['reviews'].values():
            for record in review.get('slots', {}).values():
                record.pop('stale', None)
        item = state['reviews'].setdefault(comparison['id'], {'slots': {}})
        item['slots'][str(slot)] = {'status': status, 'note': note, 'tags': list(tags),
            'updatedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'comparisonFingerprint': comparison['fingerprint']}
        state['revision'] += 1
        _write(store, state)
        return _reviews(config, store, comparisons)
