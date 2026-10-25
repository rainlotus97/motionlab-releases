#!/usr/bin/env python3
"""Build a self-contained macOS ZIP for one CPU, or a legacy universal bridge."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import zipfile

from studio_update import version

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(record, cache):
    target = cache / record['name']
    if not target.is_file() or sha(target) != record['sha256']:
        temporary = target.with_suffix(target.suffix + '.part')
        subprocess.run(['/usr/bin/curl', '-fL', '--retry', '3', '--connect-timeout', '30',
                        '--max-time', '600', record['url'], '-o', str(temporary)], check=True)
        if sha(temporary) != record['sha256']:
            raise ValueError('Runtime download checksum mismatch: ' + record['name'])
        temporary.replace(target)
    return target


def build(output, architecture):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    release = (REPO / 'VERSION').read_text().strip()
    version(release)
    lock = json.loads((REPO / 'release-runtime.json').read_text())
    selected = ['arm64', 'x86_64'] if architecture == 'universal' else [architecture]
    if any(item not in lock['architectures'] for item in selected):
        raise ValueError('Unsupported architecture')
    lock['architectures'] = {item: lock['architectures'][item] for item in selected}
    package_architecture = architecture
    cache = REPO / 'output/release-cache'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='motionlab-build-') as directory:
        root = Path(directory)
        app = root / 'MotionLab.app'
        resources = app / 'Contents/Resources'
        resources.mkdir(parents=True)
        binary = app / 'Contents/MacOS/MotionLab'
        binary.parent.mkdir(parents=True)
        architectures = []
        for architecture, packages in lock['architectures'].items():
            runtime = resources / 'runtime' / architecture
            runtime.mkdir(parents=True)
            archive = fetch(packages['python'], cache)
            subprocess.run(['/usr/bin/tar', '-xzf', str(archive), '-C', str(runtime)], check=True)
            wheel = fetch(packages['pillow'], cache)
            with zipfile.ZipFile(wheel) as package:
                package.extractall(runtime / 'python/lib/python3.12/site-packages')
            # The package licenses remain with their respective distributions.
            compiled = root / ('native-' + architecture)
            subprocess.run(['xcrun', 'swiftc', str(REPO / 'assets/desktop/Studio.swift'),
                '-target', architecture + '-apple-macosx12.0', '-O', '-o', str(compiled),
                '-framework', 'Cocoa', '-framework', 'WebKit'], check=True)
            architectures.append(compiled)
        if len(architectures) == 1:
            shutil.copy2(architectures[0], binary)
        else:
            subprocess.run(['xcrun', 'lipo', '-create', *map(str, architectures), '-output', str(binary)], check=True)
        source = resources / 'character-motion'
        tree = hashlib.sha256()
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=REPO).decode().split('\0')
        for name in sorted(filter(None, names)):
            if name.startswith('.github/') or name == '.gitignore':
                continue
            original = REPO / name
            if not original.is_file():
                continue
            tree.update(name.encode() + b'\0' + original.read_bytes())
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        iconset = root / 'Studio.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
                subprocess.run(['/usr/bin/sips', '-z', str(size * scale), str(size * scale),
                    str(REPO / 'assets/desktop/studio-icon.png'), '--out', str(iconset / name)], check=True, capture_output=True)
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'Studio.icns')], check=True)
        info = {'CFBundleExecutable': 'MotionLab', 'CFBundleIdentifier': 'local.character-motion.studio',
                'CFBundleName': 'MotionLab', 'CFBundleDisplayName': 'MotionLab', 'CFBundlePackageType': 'APPL',
                'CFBundleShortVersionString': release, 'CFBundleVersion': release, 'CFBundleIconFile': 'Studio.icns',
                'LSMinimumSystemVersion': '12.0', 'NSHighResolutionCapable': True, 'StudioBundled': True,
                'StudioArchitectures': selected}
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        metadata = {'version': release, 'sourceTreeSha256': tree.hexdigest(),
                    'gitRevision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                    'runtime': lock, 'signing': 'ad-hoc; not notarized'}
        (resources / 'release-build.json').write_text(json.dumps(metadata, indent=2))
        # Re-sign all nested Mach-O files before sealing the outer app. Do not mutate after signing.
        mach = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xfe\xed\xfa\xcf'}
        for path in app.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as handle:
                is_mach = handle.read(4) in mach
            if is_mach:
                subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(path)], check=True, capture_output=True)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True, capture_output=True)
        final_app = output / 'MotionLab.app'
        if final_app.exists():
            shutil.rmtree(final_app)
        shutil.copytree(app, final_app, symlinks=True)
        archive = output / f'MotionLab-{release}-{package_architecture}.zip'
        archive.unlink(missing_ok=True)
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(archive)], check=True)
        (output / 'SHA256SUMS.txt').write_text(f'{sha(archive)}  {archive.name}\n')
        record = {'version': release, 'architecture': package_architecture, 'asset': archive.name, 'bytes': archive.stat().st_size, 'sha256': sha(archive)}
        (output / f'package-{package_architecture}.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha(archive)}))
        return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=REPO / 'output/release')
    parser.add_argument('--architecture', choices=('arm64', 'x86_64', 'universal'), required=True)
    args = parser.parse_args()
    build(args.output, args.architecture)
