"""An article-free, hashed one-slide PowerPoint template; failure falls back to build.

Uses PowerPoint SaveCopyAs and Slides.InsertFromFile, not clipboard operations.
The caller holds the existing export lock for this entire operation.
"""
from pathlib import Path
import hashlib
import json
import os
import re
from uuid import uuid4
import zipfile
from xml.etree import ElementTree as ET

from v754.core import V7Error, digest, read_json, write_json
from v754 import powerpoint as legacy

# PowerPoint canonicalizes a bare HTTPS origin to a trailing-slash URL.
# Use that exact form for the internal template; article verification stays strict.
NEUTRAL = {'id': '0', 'title': '', 'vehicle': '-', 'specs': '-', 'complaint': '-',
           'cafe_name': '-', 'same_count': '-', 'views': '0', 'comments': '0',
           'url': 'https://cafe.naver.com/', 'written_at': '2000-01-01T00:00:00+09:00'}
TEMPLATE_NAME = 'V93_INTERNAL_TEMPLATE'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_paths(cfg):
    # Layout/font/code changes invalidate the template, never the analysis cache.
    key = digest({'version': 1, 'font': cfg['font_name'], 'width': legacy.SLIDE_W,
                  'height': legacy.SLIDE_H, 'columns': legacy.COLS, 'rows': legacy.ROWS,
                  'template_code': sha(__file__), 'legacy_code': sha(legacy.__file__)})
    folder = Path(cfg['project_root']) / 'output_v9' / 'template_cache'
    return folder / (key + '.pptx'), folder / (key + '.json'), key


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('template ZIP CRC')
        names = archive.namelist()
        slides = [n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]
        if len(slides) != 1 or any(n.startswith(('ppt/media/', 'ppt/embeddings/')) for n in names):
            raise ValueError('template must contain exactly one slide without article media')
        # A reusable template never includes captured images or article notes.
        for name in names:
            if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', name):
                node = ET.fromstring(archive.read(name))
                texts = [e.text or '' for e in node.iter() if e.tag.endswith('}t')]
                if any(t.strip() and not t.strip().isdigit() for t in texts):
                    raise ValueError('template contains notes')


def validate_slide(slide):
    if str(slide.Name) != TEMPLATE_NAME:
        raise ValueError('template slide name')
    table = slide.Shapes.Item('V7_INFO_TABLE').Table
    legacy.verify_metadata_table(table, NEUTRAL)
    legacy.verify_source_link(table.Cell(2, 4).Shape, NEUTRAL)
    legacy.verify_analysis_table(table, NEUTRAL, '')
    legacy.verify_summary_fit(table)
    if str(slide.Shapes.Item('V7_TITLE').TextFrame.TextRange.Text).strip() != '■':
        raise ValueError('template title')
    for row, expected in enumerate(legacy.ROWS, 1):
        if abs(float(table.Rows.Item(row).Height) - expected) > 1:
            raise ValueError('template row dimensions')


def record_failure(detail, operation, stage, exc):
    """Keep the old type field and add enough evidence to locate a cache miss."""
    detail[operation + '_error'] = type(exc).__name__
    detail[operation + '_failure'] = {
        'stage': stage,
        'type': type(exc).__name__,
        'code': getattr(exc, 'code', type(exc).__name__),
        'message': str(exc)[:1000],
    }
    message = str(exc).replace('\r', ' ').replace('\n', ' ')[:300]
    print(f'[PPT 양식 진단] {operation} / {stage} / {type(exc).__name__}: {message}', flush=True)


def create_or_load(deck, cfg, report):
    enabled = cfg.get('v93_performance', {}).get('ppt_template_cache', True)
    detail = report['ppt_template_cache'] = {'enabled': enabled, 'hit': False, 'saved': False}
    path = manifest = key = None
    if enabled:
        stage = 'cache_paths'
        try:
            path, manifest, key = cache_paths(cfg)
            if path.exists() and manifest.exists():
                stage = 'read_manifest'
                meta = read_json(manifest)
                stage = 'verify_checksum'
                if meta.get('key') != key or meta.get('sha256') != sha(path):
                    raise ValueError('template checksum mismatch')
                stage = 'validate_archive'
                validate_archive(path)
                stage = 'read_slide_count'
                before = deck.Slides.Count
                try:
                    stage = 'insert_slide'
                    inserted = deck.Slides.InsertFromFile(str(path.resolve()), before, 1, 1)
                    if inserted != 1 or deck.Slides.Count != before + 1:
                        raise ValueError('template insertion count')
                    slide = deck.Slides.Item(before + 1)
                    stage = 'validate_slide'
                    validate_slide(slide)
                    detail.update(hit=True, path=str(path))
                    report['ppt_template_builds'] = 0
                    print('[PPT 양식] 저장된 기본 양식 재사용', flush=True)
                    return slide
                except Exception as primary:
                    # Remove only slides inserted during this cache attempt.
                    try:
                        while deck.Slides.Count > before:
                            deck.Slides.Item(deck.Slides.Count).Delete()
                    except Exception as cleanup:
                        record_failure(detail, 'load', stage, primary)
                        record_failure(detail, 'cleanup', 'remove_inserted_slide', cleanup)
                        # Continuing with an unremoved slide corrupts page counts.
                        raise V7Error('TEMPLATE_CLEANUP_FAILED',
                                      '재사용 양식의 임시 슬라이드를 제거하지 못했습니다. 양식 진단을 확인하세요.') from primary
                    raise
            else:
                detail['miss_reason'] = 'cache_files_missing'
        except Exception as exc:
            if getattr(exc, 'code', '') == 'TEMPLATE_CLEANUP_FAILED':
                raise
            record_failure(detail, 'load', stage, exc)
            print('[PPT 양식] 저장 양식 확인 실패 / 기본 양식을 다시 만듭니다.', flush=True)
    slide = legacy.make_frame(deck, dict(NEUTRAL), '', cfg, '양식')
    slide.Name = TEMPLATE_NAME
    report['ppt_template_builds'] = 1
    print('[PPT 양식] 기본 양식 생성', flush=True)
    if enabled and path is not None:
        temp = None
        stage = 'validate_template_deck'
        try:
            # Capture checks have removed their probe, and final slides have
            # not been created. Do not save a partial report as the template.
            if deck.Slides.Count != 1:
                raise ValueError('template deck is not empty before build')
            stage = 'validate_slide'
            validate_slide(slide)
            stage = 'create_cache_directory'
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(key + '_' + uuid4().hex[:8] + '.pptx')
            stage = 'save_copy'
            deck.SaveCopyAs(str(temp.resolve()), 24)
            stage = 'validate_saved_archive'
            validate_archive(temp)
            stage = 'replace_cache'
            os.replace(temp, path)
            stage = 'write_manifest'
            write_json(manifest, {'version': 1, 'key': key, 'sha256': sha(path)})
            detail.update(saved=True, path=str(path))
            print('[PPT 양식] 다음 실행용 기본 양식 저장', flush=True)
        except Exception as exc:
            record_failure(detail, 'save', stage, exc)
            print('[PPT 양식] 재사용 파일 저장을 생략하고 이번 PPT를 계속 작성합니다.', flush=True)
        finally:
            if temp is not None:
                try: temp.unlink(missing_ok=True)
                except OSError: pass
    return slide
