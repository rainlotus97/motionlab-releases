#!/usr/bin/env python3
"""Build the macOS single-window app. Data lives independently on the Desktop."""
import argparse
import hashlib
import plistlib
from pathlib import Path
import platform
import subprocess
import sys
import shutil
import tempfile
from studio_desktop import default_library, initialize_library, migrate_default_library


def build_hash(repo):
    digest = hashlib.sha256()
    for name in ('assets/desktop/Studio.swift', 'assets/desktop/studio-icon.png', 'scripts/install_desktop.py'):
        digest.update((repo / name).read_bytes())
    return digest.hexdigest()


def install(library=None):
    if platform.system() != 'Darwin':
        raise SystemExit('Native desktop launcher currently requires macOS; the Python HTTP/CLI tools remain portable.')
    from PIL import Image  # Fail before creating a launcher with an unusable interpreter.
    compiler = shutil.which('swiftc')
    if not compiler:
        raise SystemExit('首次安装需要 Xcode Command Line Tools。请运行 xcode-select --install，安装完成后重试。')
    root, _, _ = initialize_library(library or migrate_default_library())
    repo = Path(__file__).resolve().parents[1]
    app = root / 'MotionLab.app'
    contents = app / 'Contents'
    binary = contents / 'MacOS' / 'CharacterMotionStudio'
    binary.parent.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_suffix('.new')
    subprocess.run([compiler, str(repo / 'assets/desktop/Studio.swift'), '-target', platform.machine() + '-apple-macosx12.0', '-o', str(temporary),
                    '-framework', 'Cocoa', '-framework', 'WebKit'], check=True)
    temporary.replace(binary)
    resources = contents / 'Resources'
    resources.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / 'Studio.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                subprocess.run(['sips', '-z', str(size * scale), str(size * scale),
                    str(repo / 'assets/desktop/studio-icon.png'), '--out', str(iconset / name)],
                    check=True, capture_output=True)
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'Studio.icns')], check=True)
    data = {'CFBundleExecutable': binary.name, 'CFBundleIdentifier': 'local.character-motion.studio',
            'CFBundleName': 'MotionLab', 'CFBundleDisplayName': 'MotionLab',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '3', 'CFBundleIconFile': 'Studio.icns', 'LSMinimumSystemVersion': '12.0',
            'NSHighResolutionCapable': True, 'StudioPython': sys.executable,
            'StudioBuildHash': build_hash(repo),
            'StudioScript': str(repo / 'scripts/studio_desktop.py'), 'StudioLibrary': str(root)}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(data))
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
    print(app)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    args = parser.parse_args()
    install(args.library)
