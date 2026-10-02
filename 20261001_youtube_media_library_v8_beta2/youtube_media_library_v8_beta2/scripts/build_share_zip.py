"""Build and verify a source-only Windows sharing package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'youtube_media_library_v8_beta2'
DIRECTORIES = {
    'yme': {'.py'},
    'static': {'.js', '.html', '.css', '.svg'},
    'scripts': {'.py', '.bat', '.ps1'},
    'tests': {'.py'},
    'ai_plugin': {'.py', '.txt', '.md'},
    'chrome_extension': {'.js', '.json', '.html', '.css', '.png', '.md'},
}
EXCLUDED = {'.venv', '__pycache__', 'config', 'data', 'output', 'logs',
            'backups', '.pytest_cache', '.playwright-cli', 'dist', '.git'}


def package_files():
    files = [p for p in ROOT.iterdir() if p.is_file()
             and (p.suffix in {'.py', '.bat', '.md'}
                  or p.name in {'.gitignore', 'pytest.ini', 'requirements.txt', 'requirements-dev.txt'})]
    for directory, extensions in DIRECTORIES.items():
        files.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and p.suffix in extensions
                     and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts))
    return sorted(set(files), key=lambda p: p.relative_to(ROOT).as_posix())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT.parent / 'youtube_media_library_v8_beta2_share_20261001.zip')
    args = parser.parse_args()
    files = package_files()
    contents = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in files}
    manifest = {
        'name': 'YouTube Media Library V8 beta.2 Sharing Package',
        'version': '8.0.4-beta.2',
        'build': '2026-10-01',
        'files': [{'path': name, 'bytes': len(data), 'sha256': sha256(data)}
                  for name, data in contents.items()],
        'excluded': sorted(EXCLUDED) + ['credentials', 'API keys', 'media', 'third-party executables'],
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (ROOT / 'PACKAGE_MANIFEST.json').write_bytes(manifest_data)
    contents['PACKAGE_MANIFEST.json'] = manifest_data
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f'Choose a new output path: {args.output}')
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in contents.items():
            archive.writestr(f'{PACKAGE}/{name}', data)
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {f'{PACKAGE}/{name}' for name in contents}
        for entry in manifest['files']:
            data = archive.read(f"{PACKAGE}/{entry['path']}")
            assert len(data) == entry['bytes'] and sha256(data) == entry['sha256']
        assert not any(part in EXCLUDED for name in archive.namelist() for part in Path(name).parts)
    digest = sha256(args.output.read_bytes())
    args.output.with_suffix('.zip.sha256').write_text(f'{digest}  {args.output.name}\n', encoding='ascii')
    print(json.dumps({'path': str(args.output.resolve()), 'files': len(contents),
                      'bytes': args.output.stat().st_size, 'sha256': digest,
                      'zip_crc': 'ok', 'manifest': 'ok'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
