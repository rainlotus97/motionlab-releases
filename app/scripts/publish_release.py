#!/usr/bin/env python3
"""Publish a reviewed build snapshot; GitHub Actions builds and releases both Macs."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
PUBLIC = 'rainlotus97/motionlab-releases'
PUBLIC_REMOTES = {f'git@github.com:{PUBLIC}.git', f'https://github.com/{PUBLIC}.git', f'https://github.com/{PUBLIC}'}


def run(*args, cwd=REPO, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)


def output(*args, cwd=REPO):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution', type=Path, default=REPO / 'output/distribution')
    args = parser.parse_args()
    if output('git', 'status', '--porcelain'):
        raise SystemExit('Commit intended changes before publishing.')
    if output('git', 'branch', '--show-current') != 'main':
        raise SystemExit('Publish only from main.')
    release = (REPO / 'VERSION').read_text().strip()
    from studio_update import version
    version(release)
    tag = 'v' + release
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', PUBLIC, '--json', 'isDraft'], capture_output=True, text=True)
    if existing.returncode == 0 and not json.loads(existing.stdout)['isDraft']:
        raise SystemExit('Published versions cannot be overwritten; bump VERSION.')
    destination = args.distribution.resolve()
    if destination == REPO or destination in REPO.parents:
        raise SystemExit('Distribution must be a separate checkout.')
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run('gh', 'repo', 'clone', PUBLIC, str(destination))
    if output('git', 'remote', 'get-url', 'origin', cwd=destination) not in PUBLIC_REMOTES:
        raise SystemExit('Unexpected distribution remote; refusing to publish.')
    if output('git', 'branch', '--show-current', cwd=destination) != 'main' or output('git', 'status', '--porcelain', cwd=destination):
        raise SystemExit('Distribution checkout must be clean on main.')
    run('git', 'pull', '--ff-only', cwd=destination)
    revision = output('git', 'rev-parse', 'HEAD')
    # Match the tracked, readable content already distributed inside the app.
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=REPO).decode().split('\0')
    names = [name for name in names if name and not name.startswith('.github/') and name != '.gitignore']
    if any((REPO / name).is_symlink() or not (REPO / name).is_file() for name in names):
        raise SystemExit('Snapshot must contain only regular tracked files.')
    app = destination / 'app'
    old = subprocess.check_output(['git', 'ls-files', '-z', 'app/'], cwd=destination).decode().split('\0')
    allowed = {'app/' + name for name in names}
    for name in filter(None, old):
        if name not in allowed:
            (destination / name).unlink(missing_ok=True)
    for name in names:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    workflow = destination / '.github/workflows/release.yml'
    workflow.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / '.github/workflows/release.yml', workflow)
    (destination / '.gitignore').write_text('/app/output/\n/app/.venv/\n**/__pycache__/\n.DS_Store\n')
    (destination / 'BUILD_SOURCE.json').write_text(json.dumps({'version': release, 'sourceCommit': revision,
        'scope': 'Build snapshot of shipped app content; no development history or private user library.'}, indent=2) + '\n')
    run('git', 'add', 'app', '.github/workflows/release.yml', '.gitignore', 'BUILD_SOURCE.json', cwd=destination)
    # Preserve upstream license text verbatim, including its original whitespace.
    run('git', 'diff', '--cached', '--check', '--', '.', ':(exclude)app/assets/licenses/', cwd=destination)
    if output('git', 'diff', '--cached', '--name-only', cwd=destination):
        run('git', 'commit', '-m', 'Build snapshot for MotionLab ' + release, cwd=destination)
    snapshot = output('git', 'rev-parse', 'HEAD', cwd=destination)
    existing_tag = subprocess.run(['git', 'rev-parse', tag + '^{}'], cwd=destination, capture_output=True, text=True)
    if existing_tag.returncode == 0 and existing_tag.stdout.strip() != snapshot:
        raise SystemExit('Existing version tag points elsewhere; never move published version tags.')
    if existing_tag.returncode:
        run('git', 'tag', '-a', tag, '-m', 'MotionLab ' + release, cwd=destination)
    run('git', 'push', 'origin', 'main')
    run('git', 'push', 'origin', 'main', tag, cwd=destination)
    print(f'Cloud build and publication: https://github.com/{PUBLIC}/actions/workflows/release.yml')
    print(f'Release appears only after verification: https://github.com/{PUBLIC}/releases/tag/{tag}')


if __name__ == '__main__':
    main()
