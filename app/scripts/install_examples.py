#!/usr/bin/env python3
"""Install curated editable samples without replacing existing projects."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile


def install_examples(library, examples=None):
    examples = Path(examples or Path(__file__).resolve().parents[1] / 'examples').resolve()
    root = Path(library).expanduser().resolve()
    (root / '.studio').mkdir(parents=True, exist_ok=True)
    import fcntl
    with (root / '.studio/install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _install(root, examples)


def _install(root, examples):
    manifest = json.loads((examples / 'manifest.json').read_text())
    result = {'installed': [], 'preserved': [], 'registered': [], 'existingSources': []}
    for sample in manifest['samples']:
        identifier = sample['id']
        if not identifier or Path(identifier).name != identifier or identifier in ('.', '..'):
            raise ValueError('Invalid sample ID')
        destination = sample.get('destination', 'projects')
        if destination not in ('projects', 'references'):
            raise ValueError('Invalid sample destination')
        projects = root / destination
        projects.mkdir(parents=True, exist_ok=True)
        target = projects / identifier
        if target.exists() or target.is_symlink():
            result['preserved'].append(identifier)
            continue
        source = examples / identifier
        with tempfile.TemporaryDirectory(prefix='.sample-', dir=projects) as temporary:
            staging = Path(temporary) / identifier
            staging.mkdir()
            for name, record in sample['files'].items():
                relative = Path(name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Invalid sample file path')
                original = source / relative
                if original.is_symlink() or not original.resolve().is_relative_to(source.resolve()):
                    raise ValueError('Sample file escapes source')
                data = original.read_bytes()
                if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                    raise ValueError(f'Sample checksum mismatch: {identifier}/{name}')
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            # Copy only verified manifest members. Never merge into an edited project.
            if target.exists() or target.is_symlink():
                result['preserved'].append(identifier)
                continue
            staging.rename(target)
            result['installed'].append(identifier)
    from studio_desktop import initialize_library
    from sampling_plans import list_sources
    _, _, tools = initialize_library(root)
    config = json.loads(tools.read_text())
    known_ids = {source['id'] for source in list_sources(tools)}
    for sample in manifest['samples']:
        if 'library' not in sample:
            continue
        registry = root / sample['destination'] / sample['id'] / sample['library']
        if not registry.resolve().is_relative_to(root):
            raise ValueError('Invalid sample registry path')
        relative = registry.relative_to(root).as_posix()
        if relative in config['sampling'].get('libraries', []):
            continue
        if sample['id'] in result['preserved']:
            base = registry.parent
            # An unrelated or edited directory with the same name stays untouched.
            if any(not (base / name).is_file() or hashlib.sha256((base / name).read_bytes()).hexdigest() != record['sha256']
                   for name, record in sample['files'].items()):
                continue
        candidate_ids = {clip['id'] for clip in json.loads(registry.read_text())['clips']}
        if candidate_ids & known_ids:
            result['existingSources'].append(sample['id'])
            continue
        config['sampling'].setdefault('libraries', []).append(relative)
        known_ids.update(candidate_ids)
        result['registered'].append(sample['id'])
    if result['registered']:
        with tempfile.NamedTemporaryFile(mode='w', dir=root, suffix='.json', delete=False) as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        temporary.replace(tools)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    args = parser.parse_args()
    from studio_desktop import migrate_default_library
    print(json.dumps(install_examples(args.library or migrate_default_library()), ensure_ascii=False))
