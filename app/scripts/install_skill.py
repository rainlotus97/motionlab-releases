#!/usr/bin/env python3
"""Install one discoverable entry; preserve previous installs outside skill discovery."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil


LEGACY_NAMES = ('character-motion-simple', 'character-motion-detailed',
                'character-motion-skeleton', 'character-motion-studio')


def install(source, target_root):
    source = Path(source).resolve(strict=True)
    if not (source / 'SKILL.md').is_file():
        raise ValueError('Source must contain the character-motion SKILL.md')
    target_root = Path(target_root).expanduser().absolute()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / 'character-motion'
    names = list(LEGACY_NAMES)
    if not target.exists() or target.resolve() != source:
        names.append('character-motion')
    old = [target_root / name for name in names
           if (target_root / name).exists() or (target_root / name).is_symlink()]
    for path in old:
        if not path.is_symlink() and source.is_relative_to(path.resolve()):
            raise ValueError('Clone the source outside the installed skill entries before replacing them')
    backup = None
    if old:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = target_root.parent / 'skill-backups' / ('character-motion-' + stamp)
        backup.mkdir(parents=True)
        for path in old:
            shutil.move(str(path), str(backup / path.name))
    if not target.exists():
        target.symlink_to(source, target_is_directory=True)
    return {'skill': str(target), 'source': str(source),
            'backup': str(backup) if backup else None,
            'archived': [path.name for path in old]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'
    parser.add_argument('--target', type=Path, default=default_root,
                        help='Skills directory; previous character-motion entries are backed up')
    args = parser.parse_args()
    result = install(Path(__file__).resolve().parents[1], args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
