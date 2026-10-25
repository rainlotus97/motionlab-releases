#!/usr/bin/env python3
"""Open the desktop app, installing its local launcher when necessary."""
import argparse
from pathlib import Path
import plistlib
import subprocess
from studio_desktop import default_library, migrate_default_library
from install_desktop import install, build_hash
import sys


def launch(library=None):
    root = Path(library or migrate_default_library()).expanduser().resolve()
    app = root / 'MotionLab.app'
    info_path = app / 'Contents/Info.plist'
    current = {}
    if info_path.is_file():
        try:
            current = plistlib.loads(info_path.read_bytes())
        except (ValueError, plistlib.InvalidFileException):
            pass
    repo = Path(__file__).resolve().parents[1]
    digest = build_hash(repo)
    if (current.get('StudioBuildHash') != digest
            or current.get('StudioScript') != str(repo / 'scripts/studio_desktop.py')
            or current.get('StudioPython') != sys.executable
            or not (app / 'Contents/MacOS/CharacterMotionStudio').is_file()):
        app = install(root)
    subprocess.run(['open', str(app)], check=True)
    print(f'已打开：{app}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    launch(parser.parse_args().library)
