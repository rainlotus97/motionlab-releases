#!/usr/bin/env python3
"""Start a relocatable Release without pip, a compiler or a system Python."""
import argparse
import json
import os
from pathlib import Path
import sys

from install_examples import install_examples
from install_skill import install
from studio_desktop import run


def prepare(library, skill_root=None):
    source = Path(__file__).resolve().parents[1]
    result = install_examples(library)
    target = skill_root or Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'
    skill = install(source, target)
    print(json.dumps({'examples': result, 'skill': skill}, ensure_ascii=False), file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--owner-pid', type=int, required=True)
    args = parser.parse_args()
    prepare(args.library)
    run(args.library, args.owner_pid)
