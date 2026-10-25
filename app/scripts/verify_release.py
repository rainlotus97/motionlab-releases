#!/usr/bin/env python3
"""Exercise the downloaded app with only its bundled runtime and an isolated home."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import platform
import subprocess
import tempfile
import urllib.request


def verify(archive, require_native=False):
    with tempfile.TemporaryDirectory(prefix='motionlab-release-test-') as directory:
        root = Path(directory).resolve() / 'Moved app with spaces'
        root.mkdir()
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(Path(archive).resolve()), str(root)], check=True)
        app = root / 'MotionLab.app'
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        resources = app / 'Contents/Resources'
        script = resources / 'character-motion/scripts/release_bootstrap.py'
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        architectures = info['StudioArchitectures']
        if require_native and architectures != [platform.machine()]:
            raise ValueError('Package must contain only this runner architecture')
        actual = sorted(path.name for path in (resources / 'runtime').iterdir())
        assert actual == sorted(architectures), 'Unexpected extra runtime in app'
        native_slices = subprocess.check_output(['lipo', '-archs', str(app / 'Contents/MacOS/MotionLab')], text=True).split()
        assert sorted(native_slices) == sorted(architectures), 'Unexpected native executable architecture'
        report = {'hostArchitecture': platform.machine(), 'hostMacOS': platform.mac_ver()[0], 'version': info['CFBundleShortVersionString'], 'runtimes': {}}
        for architecture in architectures:
            python = resources / f'runtime/{architecture}/python/bin/python3'
            library = root / ('library-' + architecture)
            home = root / ('home-' + architecture)
            home.mkdir()
            environment = {**os.environ, 'HOME': str(home), 'CODEX_HOME': str(home / '.codex'),
                           'PATH': '/usr/bin:/bin', 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
            environment.pop('PYTHONPATH', None); environment.pop('PYTHONHOME', None)
            try:
                smoke = subprocess.run([str(python), '-B', '-c', 'from PIL import features; assert features.check("webp")'], env=environment, capture_output=True)
            except OSError:
                if require_native:
                    raise
                report['runtimes'][architecture] = 'cannot execute on this build host; needs target hardware'
                continue
            if smoke.returncode:
                raise RuntimeError(smoke.stderr.decode())
            for attempt in range(2):
                process = subprocess.Popen([str(python), '-B', str(script), '--library', str(library), '--owner-pid', str(os.getpid())],
                                           env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    url = process.stdout.readline().strip()
                    if not url.startswith('http://127.0.0.1:'):
                        raise RuntimeError(process.stderr.read())
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    project = json.load(opener.open(url + 'api/project', timeout=5))
                    sources = json.load(opener.open(url + 'api/sampling/sources', timeout=5))
                    assert len(project['clips']) == 9 and len(sources['sources']) == 43
                    assert (home / '.codex/skills/character-motion').resolve() == resources / 'character-motion'
                    note = library / 'projects/songtuan/upgrade-note.txt'
                    if attempt:
                        assert note.read_text() == 'preserve my project'
                    else:
                        note.write_text('preserve my project')
                finally:
                    process.terminate(); process.wait(timeout=15)
                assert not (library / '.studio/instance.json').exists()
            output = library / 'export'
            subprocess.run([str(python), '-B', str(resources / 'character-motion/scripts/motion_studio.py'), 'export',
                            '--project', str(library / 'projects/songtuan/studio.project.json'), '--out', str(output)],
                           env=environment, check=True, capture_output=True)
            assert len(json.loads((output / 'package.json').read_text())['clips']) == 9
            report['runtimes'][architecture] = {'execution': 'native' if architecture == platform.machine() else 'Rosetta', 'startup': True, 'restartPreservesData': True, 'clips': 9, 'skeletonSources': 43, 'export': True}
        report['archiveSha256'] = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
        print(json.dumps(report, indent=2))
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--require-native', action='store_true')
    args = parser.parse_args()
    result = verify(args.archive, args.require_native)
    architecture = args.archive.stem.rsplit('-', 1)[1]
    (args.archive.parent / f'verification-{architecture}.json').write_text(json.dumps(result, indent=2) + '\n')
