"""Fetch the pinned official Windows portable Real-ESRGAN package. No installs."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

URL = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip'
SHA256 = 'abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--archive', type=Path, help='Reuse a locally downloaded official ZIP')
    args = parser.parse_args()
    dest = args.destination.resolve()
    if dest.exists():
        raise SystemExit('Destination exists. Reuse its engine or choose a new directory.')
    archive = args.archive.resolve() if args.archive else dest.with_suffix('.zip')
    if not args.archive and not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open('xb') as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != SHA256:
        raise SystemExit('Archive SHA256 mismatch. Keep it for inspection; do not run or extract it.')
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            resolved = (dest / item.filename).resolve()
            if not resolved.is_relative_to(dest):
                raise SystemExit('Unexpected archive path')
        package.extractall(dest)
    engine = dest / 'realesrgan-ncnn-vulkan.exe'
    required = [engine]
    for scale in (2, 3, 4):
        required.extend(dest / 'models' / f'realesr-animevideov3-x{scale}.{ext}' for ext in ('bin', 'param'))
    if not all(p.is_file() for p in required):
        raise SystemExit('Missing expected engine or model files')
    print(json.dumps({'engine_dir': str(dest), 'archive_sha256': actual, 'source': URL}, indent=2))


if __name__ == '__main__':
    main()
