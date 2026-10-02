#!/usr/bin/env python3
"""Reconstruct the pinned renderer in a disposable writable cache."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=sys.stderr, stderr=sys.stderr)


def main():
    skill = Path(__file__).resolve().parent.parent
    meta = json.loads((skill / 'references/provenance.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path)
    args = parser.parse_args()
    cache = (args.cache_dir or Path.home() / '.cache/codex-skills/visual-skills' / meta['commit']).resolve()
    shutil.copytree(skill / 'runtime', cache, dirs_exist_ok=True)
    version = subprocess.check_output(['node', '--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 20:
        raise RuntimeError('Visual Skills requires Node.js 20 or newer.')
    if not (cache / '.dependencies-ready').exists():
        run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cache)
        (cache / '.dependencies-ready').write_text(meta['commit'])
    binary = cache / '.bin/d2'
    if not binary.exists():
        installed = shutil.which('d2')
        binary.parent.mkdir(exist_ok=True)
        if installed:
            shutil.copy2(installed, binary)
        else:
            if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
                raise RuntimeError('Install D2 for this platform, then rerun setup.')
            archive = cache / '.d2.tar.gz'
            run(['curl', '-fsSL', '--retry', '2', '--max-time', '60', meta['d2']['url'], '-o', str(archive)], cache)
            data = archive.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta['d2']['sha256']:
                raise RuntimeError('D2 archive checksum mismatch.')
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                member = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith('/bin/d2')), None)
                if member is None:
                    raise RuntimeError('D2 binary missing from release archive.')
                binary.write_bytes(tar.extractfile(member).read())
            archive.unlink()
        binary.chmod(0o755)
    run([str(binary), '--version'], cache)
    # The tsx CLI creates an IPC listener which some hosted shells disallow.
    # Its import loader runs the same TypeScript without that listener.
    run(['node', '--import', 'tsx', '-e', "console.log('TypeScript loader ready')"], cache)
    print(cache)


if __name__ == '__main__':
    main()
