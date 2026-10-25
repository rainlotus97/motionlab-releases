#!/usr/bin/env python3
"""Prepare an isolated Python runtime, install the skill and open the Mac app."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys
import venv


def setup(library=None, no_open=False, no_examples=False, skill_target=None):
    if platform.system() != 'Darwin' or sys.version_info < (3, 10):
        raise SystemExit('桌面工作台需要 macOS 12+ 和 Python 3.10+。')
    compiler = subprocess.run(['xcrun', '--find', 'swiftc'], capture_output=True)
    if compiler.returncode:
        raise SystemExit('首次安装需要 Xcode Command Line Tools：运行 xcode-select --install，安装完成后再次打开。')
    repo = Path(__file__).resolve().parents[1]
    runtime = repo / '.venv'
    python = runtime / 'bin/python3'
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(runtime)
    ready = subprocess.run([str(python), '-c', 'from PIL import Image, features; assert features.check("webp")'], capture_output=True)
    if ready.returncode:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(repo / 'requirements.txt')], check=True)
    skill_command = [str(python), str(repo / 'scripts/install_skill.py')]
    if skill_target:
        skill_command.extend(['--target', str(skill_target)])
    subprocess.run(skill_command, check=True)
    library_args = ['--library', str(library)] if library else []
    if not no_examples:
        subprocess.run([str(python), str(repo / 'scripts/install_examples.py'), *library_args], check=True)
    entry = 'install_desktop.py' if no_open else 'launch_studio.py'
    command = [str(python), str(repo / 'scripts' / entry)]
    if library:
        command.extend(['--library', str(library)])
    subprocess.run(command, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    parser.add_argument('--no-open', action='store_true', help='Build and install without opening a window')
    parser.add_argument('--no-examples', action='store_true', help='Skip bundled example projects')
    parser.add_argument('--skill-target', type=Path, help='Override the skill discovery directory')
    args = parser.parse_args()
    setup(args.library, args.no_open, args.no_examples, args.skill_target)
