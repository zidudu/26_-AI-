"""V8 beta.2 derivatives and authenticated conditional responses.

No original is replaced. Generated files live under the configured data directory.
Mobile MP4 is a fixed bitrate-capped rendition, NOT adaptive HLS streaming.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi.responses import FileResponse, Response
from .library import Store, now

PROFILES = {
    'mobile720': {'label': '모바일 720p', 'width': 1280, 'height': 720, 'fps': 30,
                  'maxrate_kbps': 1600, 'audio_kbps': 96, 'kind': 'mobile', 'crf': 26},
    'mobile480': {'label': '절약 480p', 'width': 854, 'height': 480, 'fps': 30,
                  'maxrate_kbps': 800, 'audio_kbps': 64, 'kind': 'economy', 'crf': 28},
}
PROFILE_VERSION = 'beta2-mobile-v1'


def etag_matches(header: str, etag: str) -> bool:
    return any(part.strip().removeprefix('W/') in {'*', etag} for part in header.split(','))


def conditional_json(request, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    tag = '"' + hashlib.sha256(raw).hexdigest() + '"'
    # Authentication runs even for 304 responses. Must revalidate after logout.
    request.state.cache_policy = 'private, no-cache, must-revalidate'
    if etag_matches(request.headers.get('if-none-match', ''), tag):
        return Response(status_code=304, headers={'ETag': tag})
    return Response(raw, media_type='application/json', headers={'ETag': tag})


def conditional_file(request, path: Path, content_type: str):
    stat = path.stat()
    tag = '"' + hashlib.sha256(f'{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest() + '"'
    request.state.cache_policy = 'private, no-cache, must-revalidate'
    if etag_matches(request.headers.get('if-none-match', ''), tag):
        return Response(status_code=304, headers={'ETag': tag})
    return FileResponse(path, stat_result=stat, media_type=content_type, headers={'ETag': tag})


class ThumbnailCache:
    """On-demand, 320x180 bounding-box thumbnails. No original-path input from client."""
    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def get(self, source: Path, file_id: str):
        from PIL import Image, ImageOps, UnidentifiedImageError
        stat = source.stat()
        key = hashlib.sha256(f'{file_id}:{stat.st_size}:{stat.st_mtime_ns}:320-v1'.encode()).hexdigest()
        dest = self.folder / f'{file_id}_{key[:16]}.jpg'
        if dest.is_file():
            return dest
        with self._lock:
            if dest.is_file():
                return dest
            try:
                with Image.open(source) as image:
                    # Pixel limit is local to this decoder; do not disable Pillow bomb checks.
                    if image.width * image.height > 40_000_000:
                        raise ValueError('이미지가 너무 큽니다.')
                    image.draft('RGB', (320, 180))
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((320, 180), Image.Resampling.LANCZOS)
                    temp = dest.with_suffix('.tmp')
                    image.save(temp, format='JPEG', quality=74, optimize=True)
                    os.replace(temp, dest)
            except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise ValueError('유효한 썸네일이 아닙니다.') from exc
            # A changed source must not accumulate unlimited generations.
            for previous in self.folder.glob(file_id + '_*.jpg'):
                if previous != dest:
                    previous.unlink(missing_ok=True)
        return dest


def valid_derivative(store: Store, file_id: str):
    """Return false only for tracked derivatives whose original has changed/disappeared."""
    with store.connect() as c:
        row = c.execute('SELECT d.*, f.path AS source_path FROM derivatives d LEFT JOIN files f ON f.id=d.source_id WHERE d.file_id=?', (file_id,)).fetchone()
    if row is None:  # Imported local derivatives have no provenance record.
        return True
    try:
        source = Path(row['source_path'])
        stat = source.stat()
        return (not source.is_symlink() and str(source.resolve()) == str(source)
                and stat.st_size == row['source_size'] and stat.st_mtime_ns == row['source_mtime_ns'])
    except (OSError, TypeError):
        return False


def make_mobile_preview(item_id: str, store: Store, profile: str = 'mobile720') -> dict:
    from .engine import emit, ffmpeg_exe, media_complete
    if profile not in PROFILES:
        raise ValueError('알 수 없는 모바일 프로필')
    item = store.item(item_id)
    source_entry = next((f for f in (item or {}).get('files', []) if f['kind'] == 'video'), None)
    source = store.file(source_entry['id']) if source_entry else None
    if not source:
        raise ValueError('변환할 원본 영상이 없습니다.')
    src = Path(source['path'])
    before = src.stat()
    spec = PROFILES[profile]
    folder = store.data_dir / 'mobile_previews' / item_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{profile}.mp4'
    fid = hashlib.sha256(str(target.resolve()).encode()).hexdigest()[:24]
    fingerprint = PROFILE_VERSION + ':' + profile
    with store.connect() as c:
        old = c.execute('SELECT * FROM derivatives WHERE file_id=?', (fid,)).fetchone()
    if (old and target.is_file() and target.stat().st_size > 64 and old['source_id'] == source['id']
            and old['source_size'] == before.st_size and old['source_mtime_ns'] == before.st_mtime_ns
            and old['profile'] == fingerprint):
        emit('stage', stage=spec['label'] + ' 기존 재생본 재사용')
        return {'item_id': item_id, 'file_id': fid, 'profile': profile, 'reused': True, 'size': target.stat().st_size}
    # Avoid full-size temporary writes when the data volume is already nearly full.
    if shutil.disk_usage(folder).free < 256 * 1024**2:
        raise RuntimeError('재생본 저장 공간이 256MB 미만입니다. 공간을 확보하세요.')
    temp = folder / f'{profile}.tmp.mp4'
    emit('stage', stage=spec['label'] + ' 변환 중 · 원본 유지 / CPU 2스레드')
    vf = (f"fps={spec['fps']},scale=w='min({spec['width']},iw)':h='min({spec['height']},ih)':"
          'force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1')
    command = [ffmpeg_exe(), '-hide_banner', '-nostdin', '-y', '-v', 'error',
               '-threads', '2', '-filter_threads', '2', '-i', str(src),
               '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
               '-vf', vf, '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast',
               '-crf', str(spec['crf']), '-maxrate', f"{spec['maxrate_kbps']}k",
               '-bufsize', f"{spec['maxrate_kbps']*2}k", '-pix_fmt', 'yuv420p',
               '-profile:v', 'high', '-g', '60', '-c:a', 'aac', '-ac', '2',
               '-b:a', f"{spec['audio_kbps']}k", '-movflags', '+faststart', '-max_muxing_queue_size', '1024', str(temp)]
    try:
        # Binary capture fixes the former Windows CP949 reader-thread failure.
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7200,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError('모바일 변환 실패: ' + result.stderr.decode('utf-8', 'replace')[-2400:])
        media_complete(temp)
        after = src.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('변환 중 원본이 변경되었습니다. 다시 생성해 주세요.')
        os.replace(temp, target)
        filename = item['stem'] + ('_mobile720.mp4' if profile == 'mobile720' else '_mobile480.mp4')
        with store.connect() as c:
            c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                      (fid, item_id, spec['kind'], str(target.resolve()), filename, target.stat().st_size, 'video/mp4'))
            c.execute('INSERT OR REPLACE INTO derivatives VALUES (?,?,?,?,?)',
                      (fid, source['id'], before.st_size, before.st_mtime_ns, fingerprint))
        emit('library_changed')
        return {'item_id': item_id, 'file_id': fid, 'profile': profile, 'reused': False, 'size': target.stat().st_size,
                'source_size': before.st_size}
    finally:
        temp.unlink(missing_ok=True)
