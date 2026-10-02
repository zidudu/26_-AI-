#!/usr/bin/env python3
"""Build the pinned Understand Anything source in a disposable cache."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=sys.stderr, stderr=sys.stderr)


def main():
    skill = Path(__file__).resolve().parent.parent
    meta = json.loads((skill / 'references/provenance.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path)
    args = parser.parse_args()
    cache = (args.cache_dir or Path.home() / '.cache/codex-skills/understand-anything' / meta['commit']).resolve()
    if not (cache / 'package.json').exists():
        shutil.copytree(skill / 'runtime', cache, dirs_exist_ok=True)
    version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 22:
        raise RuntimeError('Understand Anything requires Node.js 22 or newer.')
    if not (cache / '.build-ready').exists():
        run(['pnpm', 'install', '--frozen-lockfile'], cache)
        run(['pnpm', '--filter', '@understand-anything/core', 'build'], cache)
        run(['pnpm', '--filter', '@understand-anything/dashboard', 'build'], cache)
        if not (cache / 'packages/core/dist/index.js').exists() or not (cache / 'packages/dashboard/dist/index.html').exists():
            raise RuntimeError('Build completed without expected outputs.')
        (cache / '.build-ready').write_text(meta['commit'])
    print(cache)


if __name__ == '__main__':
    main()
