"""V7.1 수집 기능을 GUI 작업 프로세스에서 호출하도록 분리한 코어.

외부 네트워크 의존성은 함수 안에서 import합니다. 라이브러리 열람은
YouTube 접속이나 다운로드 패키지 없이도 가능합니다.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .library import AUDIO, VIDEO, Store, atomic_text, now, safe_json

ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'}
MODES = {
    'subtitles': ('subtitle',), 'video_subtitles': ('subtitle', 'video'),
    'audio_subtitles': ('subtitle', 'audio'), 'all': ('subtitle', 'video', 'audio'),
    'video': ('video',), 'audio': ('audio',),
}
DEFAULTS = {'mode': 'all', 'quality': '1080', 'audio_format': 'm4a', 'subtitle_policy': 'auto',
            'thumbnail': True, 'playlist_limit': 100, 'tags': [], 'mobile_preview': True}


def emit(event: str, **data):
    print('@YME@' + json.dumps({'event': event, **data}, ensure_ascii=False), flush=True)


def normalize_url(value: str) -> tuple[str, str, str]:
    """영상 URL과 재생목록 URL을 구분하고 허용한 호스트만 처리합니다."""
    value = value.strip().strip('<>"\'')
    if ID_RE.fullmatch(value):
        return f'https://www.youtube.com/watch?v={value}', 'video', value
    p = urlparse(value if '://' in value else 'https://' + value)
    if p.scheme not in {'https', 'http'} or p.hostname not in HOSTS or p.username or p.password:
        raise ValueError('YouTube 영상 또는 재생목록 URL이 아닙니다.')
    if p.port not in (None, 80, 443):
        raise ValueError('URL 포트는 지정하지 마세요.')
    query, parts = parse_qs(p.query), p.path.strip('/').split('/')
    if p.path.rstrip('/') == '/playlist':
        pid = query.get('list', [''])[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', pid):
            raise ValueError('재생목록 ID를 찾지 못했습니다.')
        if pid.startswith('RD') or pid in {'LL', 'WL'}:
            raise ValueError('자동 믹스/비공개 특수 목록 대신 일반 공개 재생목록을 사용해 주세요.')
        return f'https://www.youtube.com/playlist?list={pid}', 'playlist', pid
    if p.hostname.endswith('youtu.be'):
        vid = parts[0]
    elif p.path.rstrip('/') == '/watch':
        vid = query.get('v', [''])[0]
    elif len(parts) >= 2 and parts[0] in {'shorts', 'live', 'embed'}:
        vid = parts[1]
    else:
        vid = ''
    if not ID_RE.fullmatch(vid):
        raise ValueError('11자리 Video ID를 찾지 못했습니다.')
    return f'https://www.youtube.com/watch?v={vid}', 'video', vid


def parse_sources(text: str) -> dict:
    sources, errors, duplicates, seen = [], [], [], set()
    for raw in re.split(r'\s+', text.strip()):
        if not raw:
            continue
        try:
            url, kind, ident = normalize_url(raw)
            key = kind + ':' + ident
            if key in seen:
                duplicates.append(raw)
                continue
            seen.add(key)
            sources.append({'url': url, 'kind': kind, 'id': ident, 'input': raw})
        except (ValueError, TypeError) as exc:
            errors.append({'input': raw[:250], 'error': str(exc)})
    return {'sources': sources, 'errors': errors, 'duplicates': duplicates}


def validate_settings(settings: dict) -> dict:
    result = {**DEFAULTS, **settings}
    if result['mode'] not in MODES:
        raise ValueError('지원하지 않는 다운로드 모드입니다.')
    if str(result['quality']) not in {'720', '1080', '1440', '2160', 'best'}:
        raise ValueError('지원하지 않는 영상 품질입니다.')
    if result['audio_format'] not in {'m4a', 'mp3', 'wav', 'flac', 'opus'}:
        raise ValueError('지원하지 않는 음원 형식입니다.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{2,25}', str(result['subtitle_policy'])):
        raise ValueError('올바른 자막 언어 코드를 입력해 주세요.')
    result['playlist_limit'] = int(result['playlist_limit'])
    if not 1 <= result['playlist_limit'] <= 1000:
        raise ValueError('재생목록 상한은 1~1,000개입니다.')
    result['tags'] = [Store.tag_name(t)[0] for t in result.get('tags', [])][:30]
    result['thumbnail'] = bool(result['thumbnail'])
    if not isinstance(result['mobile_preview'], bool):
        raise ValueError('모바일 재생본 옵션은 true/false여야 합니다.')
    return result


def ffmpeg_exe() -> str:
    value = shutil.which('ffmpeg')
    if value:
        return value
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], timeout: int = 7200) -> None:
    """V7.1의 binary 로그 처리를 유지합니다. shell=True를 사용하지 않습니다."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    proc = subprocess.run([ffmpeg_exe(), '-hide_banner', '-nostdin', '-y', '-threads', '2', '-filter_threads', '2', *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, creationflags=flags)
    if proc.returncode:
        detail = proc.stderr.decode('utf-8', errors='replace')[-2400:]
        raise RuntimeError('FFmpeg 실패: ' + detail)


def js_runtimes() -> dict:
    result = {}
    for name, candidates in [('deno', ['deno']), ('node', ['node']), ('quickjs', ['qjs', 'quickjs'])]:
        for candidate in candidates:
            path = shutil.which(candidate)
            if path:
                result[name] = {'path': path}
                break
    return result


class Log:
    def debug(self, msg):
        if not msg.startswith('[debug]'):
            print(msg, flush=True)
    def info(self, msg):
        print(msg, flush=True)
    def warning(self, msg):
        print('[경고] ' + msg, flush=True)
    def error(self, msg):
        print('[오류] ' + msg, flush=True)


def ydl_options() -> dict:
    opts = {'quiet': True, 'no_warnings': False, 'logger': Log(), 'noplaylist': True,
            'socket_timeout': 25, 'retries': 2, 'fragment_retries': 2, 'noprogress': True,
            'windowsfilenames': True, 'restrictfilenames': False}
    runtimes = js_runtimes()
    if runtimes:
        opts['js_runtimes'] = runtimes
    return opts


def format_selector(quality: str) -> str:
    # 상한을 넘는 /b 폴백은 두지 않습니다. 최고화질은 코덱으로 제한하지 않습니다.
    cap = '' if quality == 'best' else f'[height<={int(quality)}]'
    return f'bv{cap}+ba/b{cap}'


def safe_name(value: str, max_len: int = 54) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value))
    value = re.sub(r'\s+', ' ', value).strip().rstrip('. ') or 'untitled'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    return value[:max_len].rstrip('. ') or 'untitled'


def choose_transcript(items, policy: str):
    items = list(items)
    if not items:
        raise RuntimeError('사용 가능한 자막이 없습니다.')
    if policy == 'first':
        return items[0]
    priorities = ['ko', 'en', 'ja'] if policy == 'auto' else [policy]
    for lang in priorities:
        matches = [t for t in items if t.language_code.lower().replace('_', '-') == lang.lower().replace('_', '-')
                   or t.language_code.lower().startswith(lang.lower() + '-')]
        if matches:
            return next((t for t in matches if not t.is_generated), matches[0])
    if policy == 'auto':
        selected = next((t for t in items if not t.is_generated), items[0])
        print(f'[알림] 한국어/영어/일본어가 없어 {selected.language_code} 자막을 선택했습니다.', flush=True)
        return selected
    raise RuntimeError(f'{policy} 자막이 없습니다. 가능 언어: ' + ', '.join(t.language_code for t in items))


def fetch_subtitles(vid: str, policy: str):
    import requests
    from youtube_transcript_api import YouTubeTranscriptApi
    class TimedSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs.setdefault('timeout', 25)
            return super().request(method, url, **kwargs)
    with TimedSession() as session:
        api = YouTubeTranscriptApi(http_client=session)
        selected = choose_transcript(api.list(vid), policy)
        fetched = selected.fetch()
        snippets = [{'start': float(s.start), 'duration': float(s.duration), 'text': s.text} for s in fetched]
    if not snippets:
        raise RuntimeError('자막 응답에 문장이 없습니다.')
    return selected, snippets


def timestamp(value: float) -> str:
    sec = max(0, int(value))
    return f'{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def save_subtitles(job, info, selected, snippets, folder: Path, stem: str) -> list[Path]:
    dt = datetime.now().astimezone()
    header = '\n'.join(['=' * 70, 'YouTube 자막 추출 결과', '=' * 70,
        f"유튜브 제목      : {info['title']}", f"채널명           : {info.get('channel', '')}",
        f"채널 URL         : {info.get('channel_url', '')}", f"Video ID         : {job['id']}",
        f"원본 입력        : {job['input']}", f"유튜브 원본 링크 : {job['url']}",
        f"가져온 날짜      : {dt:%Y-%m-%d}", f"가져온 시간      : {dt:%H:%M:%S}",
        f"시간대           : {dt:%z}", f"자막 언어        : {selected.language} ({selected.language_code})",
        f"자막 종류        : {'자동 생성' if selected.is_generated else '수동 작성'}",
        f"자막 조각 수     : {len(snippets)}", '=' * 70])
    normal = folder / f'{stem}_transcript.txt'
    timed = folder / f'{stem}_timestamp.txt'
    body = '\n'.join(s['text'] for s in snippets)
    timed_body = '\n'.join(f"[{timestamp(s['start'])}] " + s['text'].replace('\r', ' ').replace('\n', ' ') for s in snippets)
    atomic_text(normal, header + '\n\n[자막 본문]\n\n' + body + '\n\n' + '=' * 70 + '\nEND\n', 'utf-8-sig')
    atomic_text(timed, header + '\n\n[타임스탬프 자막]\n\n' + timed_body + '\n\n' + '=' * 70 + '\nEND\n', 'utf-8-sig')
    atomic_text(folder / f'{stem}_segments.json', json.dumps(snippets, ensure_ascii=False))
    return [normal, timed]


def info_for(job) -> dict:
    import requests
    try:
        import yt_dlp
        opts = ydl_options()
        # 자막만 있는 영상의 메타데이터를 포맷 선택 실패로 막지 않습니다.
        opts.update({'skip_download': True, 'ignore_no_formats_error': True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            raw = ydl.extract_info(job['url'], download=False)
        if not raw:
            raise RuntimeError('빈 영상 정보')
        if raw.get('is_live') or raw.get('live_status') in {'is_live', 'is_upcoming'}:
            raise ValueError('진행 중/예정 라이브는 지원하지 않습니다. 종료된 일반 영상을 사용해 주세요.')
        return {'title': raw.get('title') or job['id'], 'channel': raw.get('channel') or raw.get('uploader') or '',
                'channel_url': raw.get('channel_url') or raw.get('uploader_url') or '',
                'thumbnail': raw.get('thumbnail'), 'duration_seconds': raw.get('duration'), 'upload_date': raw.get('upload_date')}
    except ValueError:
        raise
    except Exception as exc:
        print(f'[경고] 상세 메타데이터 조회 실패, oEmbed로 다시 확인: {exc}', flush=True)
        try:
            r = requests.get('https://www.youtube.com/oembed', params={'url': job['url'], 'format': 'json'}, timeout=20)
            r.raise_for_status()
            x = r.json()
            return {'title': x.get('title') or job['id'], 'channel': x.get('author_name') or '',
                    'channel_url': x.get('author_url') or '', 'thumbnail': x.get('thumbnail_url')}
        except Exception:
            return {'title': 'YouTube_' + job['id'], 'channel': '', 'metadata_warning': str(exc)[:700]}


def expand_sources(sources: list[dict], limit: int) -> tuple[list, list]:
    jobs, errors, seen = [], [], set()
    for source in sources:
        if source['kind'] == 'video':
            entries = [source]
        else:
            try:
                import yt_dlp
                emit('stage', stage='재생목록 확인 중: ' + source['id'])
                opts = ydl_options()
                opts.update({'noplaylist': False, 'extract_flat': 'in_playlist', 'skip_download': True,
                             'ignoreerrors': True, 'playlistend': limit, 'lazy_playlist': False})
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(source['url'], download=False)
                if not info:
                    raise RuntimeError('재생목록을 읽지 못했습니다.')
                entries = []
                for i, entry in enumerate(info.get('entries') or [], 1):
                    if not entry or not ID_RE.fullmatch(str(entry.get('id', ''))):
                        errors.append({'input': source['url'], 'error': f'{i}번 항목을 읽지 못했습니다.'})
                        continue
                    vid = entry['id']
                    entries.append({'id': vid, 'url': f'https://www.youtube.com/watch?v={vid}', 'input': source['input'],
                                    'playlist': {'id': source['id'], 'title': info.get('title', '재생목록'), 'index': entry.get('playlist_index') or i}})
                if len(entries) >= limit:
                    print(f'[알림] 재생목록은 설정한 상한 {limit}개까지만 처리합니다.', flush=True)
            except Exception as exc:
                errors.append({'input': source['url'], 'error': str(exc)})
                continue
        for entry in entries:
            if entry['id'] not in seen:
                seen.add(entry['id'])
                jobs.append(entry)
            else:
                print('[중복 제외] ' + entry['id'], flush=True)
    return jobs, errors


def output_location(root: Path, job: dict, info: dict) -> tuple[Path, str]:
    date = datetime.now().strftime('%Y%m%d')
    stem = f"{date}_{safe_name(info['title'])}"
    parent = root
    prefix = ''
    if job.get('playlist'):
        pl = job['playlist']
        parent = root / f"{date}_{safe_name(pl['title'], 32)}_{pl['id'][-8:]}"
        prefix = f"{int(pl['index']):03d}_"
    folder = parent / (prefix + stem)
    existing = list(folder.glob('*_metadata.json')) if folder.exists() else []
    old = safe_json(existing[0]) if existing else {}
    if folder.exists() and (old.get('video_id') != job['id']):
        folder = parent / (prefix + stem + '_' + job['id'])
    folder.mkdir(parents=True, exist_ok=True)
    return folder, stem


def media_complete(path: Path):
    if not path.exists() or path.stat().st_size < 64:
        raise RuntimeError('미디어 파일이 없거나 비어 있습니다.')
    # 처음 0.1초 디코딩 점검. 전체 길이 재생 검증을 의미하지는 않습니다.
    run_ffmpeg(['-v', 'error', '-i', str(path), '-t', '0.1', '-f', 'null', '-'], timeout=60)


def download_media(job: dict, work: Path, video: bool, quality: str, hook) -> Path:
    import yt_dlp
    options = ydl_options()
    options.update({'format': format_selector(quality) if video else 'ba[ext=m4a]/ba/b',
                    'outtmpl': str(work / ('video.%(ext)s' if video else 'audio.%(ext)s')),
                    'ffmpeg_location': ffmpeg_exe(), 'merge_output_format': 'mp4/mkv',
                    'progress_hooks': [hook], 'continuedl': True})
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(job['url'], download=True)
    name = 'video' if video else 'audio'
    allowed = VIDEO if video else AUDIO | VIDEO
    matches = [p for p in work.iterdir() if p.stem == name and p.suffix.lower() in allowed and p.stat().st_size > 0]
    if not matches:
        raise RuntimeError('완성된 미디어 파일을 찾지 못했습니다. 부분 파일은 성공 처리하지 않습니다.')
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    media_complete(matches[0])
    return matches[0]


def convert_audio(source: Path, target: Path, fmt: str) -> str:
    temp = target.with_name(target.stem + '.converting' + target.suffix)
    method = 'transcode'
    args = ['-v', 'error', '-i', str(source), '-map', '0:a:0', '-vn']
    try:
        if fmt == 'm4a':
            try:
                run_ffmpeg(args + ['-c:a', 'copy', str(temp)])
                method = 'stream_copy'
            except RuntimeError:
                run_ffmpeg(args + ['-c:a', 'aac', '-b:a', '192k', str(temp)])
        else:
            codecs = {'mp3': ['-c:a', 'libmp3lame', '-q:a', '2'], 'wav': ['-c:a', 'pcm_s16le'],
                      'flac': ['-c:a', 'flac'], 'opus': ['-c:a', 'libopus', '-b:a', '160k']}
            run_ffmpeg(args + codecs[fmt] + [str(temp)])
        media_complete(temp)
        os.replace(temp, target)
        return method
    finally:
        temp.unlink(missing_ok=True)


def save_thumbnail(url: str, folder: Path, stem: str) -> Path:
    import requests
    with requests.get(url, timeout=20, stream=True) as r:
        r.raise_for_status()
        content = bytearray()
        for chunk in r.iter_content(65536):
            content.extend(chunk)
            if len(content) > 10_000_000:
                raise RuntimeError('썸네일 파일이 10MB를 초과합니다.')
    raw = bytes(content)
    ext = '.jpg' if raw.startswith(b'\xff\xd8\xff') else '.png' if raw.startswith(b'\x89PNG') else '.webp' if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' else None
    if not ext:
        raise RuntimeError('썸네일 응답이 JPG/PNG/WebP 이미지가 아닙니다.')
    path = folder / f'{stem}_thumbnail{ext}'
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(raw)
    os.replace(temp, path)
    return path


def extract_batch(payload: dict, store: Store) -> dict:
    settings = validate_settings(payload['settings'])
    parsed = parse_sources(payload['text'])
    jobs, errors = expand_sources(parsed['sources'], settings['playlist_limit'])
    errors += parsed['errors']
    root = Path(payload['output']).resolve()
    results = []
    for index, job in enumerate(jobs, 1):
        emit('stage', stage=f"[{index}/{len(jobs)}] 영상 정보 확인", progress=(index-1)/len(jobs)*100)
        folder = None
        try:
            info = info_for(job)
            folder, stem = output_location(root, job, info)
            old = safe_json(folder / f'{stem}_metadata.json')
            work = folder / '.work' / uuid.uuid4().hex[:10]
            work.mkdir(parents=True, exist_ok=True)
            state = {}
            video_path = None
            def stage(name):
                emit('stage', stage=f"[{index}/{len(jobs)}] {info['title']} · {name}")
            def hook(data):
                total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
                pct = min(99, data.get('downloaded_bytes', 0) / total * 100) if total else 0
                emit('progress', progress=((index-1)+pct/100) / len(jobs)*100)
            for part in MODES[settings['mode']]:
                try:
                    if part == 'subtitle':
                        stage('자막 가져오는 중')
                        selected, snippets = fetch_subtitles(job['id'], settings['subtitle_policy'])
                        paths = save_subtitles(job, info, selected, snippets, folder, stem)
                        state[part] = {'status': 'success', 'language': selected.language_code,
                                       'kind': 'auto' if selected.is_generated else 'manual', 'count': len(snippets),
                                       'files': [p.name for p in paths]}
                    elif part == 'video':
                        stage('영상 다운로드 / 병합 중')
                        previous = old.get('result', {}).get('video', {})
                        candidate = folder / Path(previous.get('file', 'NONE')).name
                        reusable = previous.get('status') == 'success' and old.get('settings', {}).get('quality') == settings['quality'] and candidate.is_file() and candidate.stat().st_size > 0
                        if reusable:
                            video_path = candidate
                            print('[재사용] ' + candidate.name, flush=True)
                        else:
                            downloaded = download_media(job, work, True, settings['quality'], hook)
                            video_path = folder / (stem + downloaded.suffix)
                            os.replace(downloaded, video_path)
                        state[part] = {'status': 'success', 'file': video_path.name, 'reused': reusable}
                    else:
                        stage('음원 분리 중')
                        target = folder / f"{stem}.{settings['audio_format']}"
                        previous = old.get('result', {}).get('audio', {})
                        reusable = previous.get('status') == 'success' and previous.get('file') == target.name and target.is_file() and target.stat().st_size > 0
                        if reusable:
                            method = previous.get('method', 'reused')
                        else:
                            source = video_path or download_media(job, work, False, settings['quality'], hook)
                            method = convert_audio(source, target, settings['audio_format'])
                        state[part] = {'status': 'success', 'file': target.name, 'reused': reusable, 'method': method}
                except Exception as exc:
                    state[part] = {'status': 'failed', 'error': str(exc)[-3000:]}
                    print(f'[{part} 실패] {exc}', flush=True)
            if settings['thumbnail'] and info.get('thumbnail'):
                try:
                    thumb = save_thumbnail(info['thumbnail'], folder, stem)
                    state['thumbnail'] = {'status': 'success', 'file': thumb.name}
                except Exception as exc:
                    state['thumbnail'] = {'status': 'failed', 'error': str(exc)}
            success = sum(state[k]['status'] == 'success' for k in MODES[settings['mode']])
            status = 'success' if success == len(MODES[settings['mode']]) else 'partial' if success else 'failed'
            metadata = {**info, 'program_version': 'V8', 'video_id': job['id'], 'webpage_url': job['url'],
                        'original_input': job['input'], 'saved_at': now(), 'settings': settings,
                        'playlist': job.get('playlist'), 'result': state, 'status': status}
            atomic_text(folder / f'{stem}_metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
            item_ids = store.register_folder(folder, settings['tags'])
            if settings['mobile_preview'] and video_path:
                from .performance import make_mobile_preview
                for ident in item_ids:
                    try:
                        stage('모바일 720p 재생본 변환 중 · 원본 보존')
                        make_mobile_preview(ident, store, 'mobile720')
                    except Exception as exc:
                        print('[모바일 재생본 실패] 원본은 보존됩니다: ' + str(exc), flush=True)
                        state['mobile_preview'] = {'status': 'failed', 'error': str(exc)[-2000:]}
                        metadata['result'] = state
                        atomic_text(folder / f'{stem}_metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
            results.append({'video_id': job['id'], 'title': info['title'], 'status': status, 'result': state})
            emit('library_changed')
            # 실패한 부분 파일은 재사용하지 않습니다. 완료 후 임시 공간만 정리합니다.
            shutil.rmtree(work, ignore_errors=True)
        except Exception as exc:
            errors.append({'input': job['url'], 'error': str(exc)[-3000:]})
            results.append({'video_id': job['id'], 'status': 'failed', 'error': str(exc)[-3000:]})
            if folder:
                store.register_folder(folder, settings['tags'])
        emit('progress', progress=index / max(1, len(jobs)) * 100)
    summary = {'items': results, 'errors': errors, 'duplicates': parsed['duplicates'],
               'success': sum(r['status'] == 'success' for r in results),
               'partial': sum(r['status'] == 'partial' for r in results),
               'failed': sum(r['status'] == 'failed' for r in results)}
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    atomic_text(root / f'{stamp}_V8_batch_result.txt', json.dumps(summary, ensure_ascii=False, indent=2), 'utf-8-sig')
    return summary


def make_preview(item_id: str, store: Store) -> dict:
    """브라우저가 원본 코덱을 지원하지 않을 때 별도 H.264/AAC 재생본 생성."""
    item = store.item(item_id)
    if not item:
        raise ValueError('항목을 찾지 못했습니다.')
    videos = [f for f in item['files'] if f['kind'] == 'video']
    if not videos:
        raise ValueError('영상 파일이 없습니다.')
    f = store.file(videos[0]['id'])
    if not f:
        raise ValueError('원본 영상이 이동되었거나 삭제되었습니다.')
    target = store.data_dir / 'previews' / f'{item_id}.mp4'
    target.parent.mkdir(exist_ok=True)
    temp = target.with_name(item_id + '.tmp.mp4')
    emit('stage', stage='브라우저 재생본 만드는 중 · 원본은 유지됩니다')
    try:
        # 720p 범위 내 비율 유지, 홀수 해상도 보정, 낮은 해상도를 확대하지 않음.
        run_ffmpeg(['-v', 'error', '-i', f['path'], '-map', '0:v:0', '-map', '0:a:0?',
                    '-vf', "fps=30,scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(temp)])
        media_complete(temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    import hashlib
    fid = hashlib.sha256(str(target.resolve()).encode()).hexdigest()[:24]
    with store.connect() as c:
        c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                  (fid, item_id, 'preview', str(target.resolve()), item['stem'] + '_browser_preview.mp4', target.stat().st_size, 'video/mp4'))
    return {'item_id': item_id, 'file_id': fid}
