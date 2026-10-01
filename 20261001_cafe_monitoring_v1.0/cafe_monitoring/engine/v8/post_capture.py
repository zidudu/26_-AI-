"""Actual browser PNGs of post header/body. Capture problems never discard readable text."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import struct
import time
from uuid import uuid4

MAX_TILES = 24
CAPTURE_SECONDS = 20


class CaptureError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def snapshot_diff(before, after):
    """원문 판정에 쓰는 필드와 해시를 기록하며 단순 공백 변경은 구분합니다."""
    fields, changed = {}, []
    for key in ('title', 'date', 'body'):
        left = before.get(key) if isinstance(before, dict) else None
        right = after.get(key) if isinstance(after, dict) else None
        def normalized(value):
            if not isinstance(value, str):
                return None
            return ' '.join(value.replace('\u200b', '').split())
        norm_l, norm_r = normalized(left), normalized(right)
        different = norm_l is None or norm_r is None or norm_l != norm_r
        if different:
            changed.append(key)
        def fingerprint(value):
            return hashlib.sha256(value.encode('utf-8')).hexdigest() if isinstance(value, str) else None
        fields[key] = {'changed': different, 'raw_changed': left != right,
                       'before_length': len(left) if isinstance(left, str) else None,
                       'after_length': len(right) if isinstance(right, str) else None,
                       'before_sha256': fingerprint(left), 'after_sha256': fingerprint(right),
                       'before_normalized_sha256': fingerprint(norm_l),
                       'after_normalized_sha256': fingerprint(norm_r)}
    return {'changed_fields': changed, 'fields': fields}


def capture_post(page, raw, output_dir, article):
    result = {'status': 'failed', 'files': [], 'warnings': [],
              'captured_at': datetime.now(timezone.utc).isoformat(), 'source_url': article['url'],
              'source_body_sha256': article['body_sha256'], 'scope': 'post_header_and_body',
              'comments_included': False, 'video_played': False, 'images_analyzed_by_ai': False}
    frame, saved_scroll = None, None
    stage = 'find_frame'
    original_page_url = page.url
    deadline = time.monotonic() + CAPTURE_SECONDS
    def budget():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return max(1, int(remaining * 1000))
    try:
        from v754.collect.collector import body_extraction_script
        frame = page.frame(name='cafe_main')
        if frame is None:
            raise CaptureError('CAPTURE_FRAME_NOT_FOUND')
        selection = {'selector': raw['body_selector'], 'index': raw.get('body_index', 0)}
        body = frame.locator(selection['selector']).nth(selection['index'])
        saved_scroll = page.evaluate('() => ({x: scrollX, y: scrollY})')
        # Bounded scroll loads lazy images. Each actual image is recorded only once within the post.
        images = body.locator('img')
        image_total = images.count()
        stage = 'image_scroll'
        for i in range(min(image_total, 40)):
            if time.monotonic() > deadline - 5:
                result['warnings'].append('IMAGE_LOAD_TIME_LIMIT')
                break
            image = images.nth(i)
            if not image.is_visible():
                continue
            try:
                image.scroll_into_view_if_needed(timeout=min(800, budget()))
            except Exception as exc:
                # One image's scroll failure must not prevent a best-effort screenshot.
                result['warnings'].append('IMAGE_SCROLL_FAILED')
                result.setdefault('diagnostics', []).append({'stage':stage, 'image_index':i,
                                                            'exception_type':type(exc).__name__})
                continue
            page.wait_for_timeout(70)
        stage = 'metadata_before_capture'
        from v754.collect.metadata import settle_metadata, check_after_capture
        settle_metadata(page, article)
        # Metadata polling has its own bounded wait; screenshot work gets a fresh budget.
        deadline = time.monotonic() + CAPTURE_SECONDS
        stage = 'verify_before_capture'
        fresh = frame.evaluate(body_extraction_script(), selection)
        before_diff = snapshot_diff(raw, fresh)
        result.setdefault('diagnostics', []).append({'stage': stage, **before_diff})
        if before_diff['changed_fields']:
            raise CaptureError('CONTENT_CHANGED_BEFORE_CAPTURE')
        if page.url != original_page_url or (raw.get('document_url') and fresh.get('document_url') != raw.get('document_url')):
            raise CaptureError('CAPTURE_WRONG_PAGE')
        article['media'] = fresh['media']
        stage = 'measure_capture_area'
        boxes = [body.bounding_box(timeout=budget()),
                 frame.locator('h3.title_text').bounding_box(timeout=budget()),
                 frame.locator('.article_info .date').bounding_box(timeout=budget())]
        if any(b is None for b in boxes):
            raise CaptureError('CAPTURE_AREA_NOT_VISIBLE')
        # Bounding boxes use the main frame's viewport coordinates, even for iframe descendants.
        scroll = page.evaluate('() => ({x: scrollX, y: scrollY})')
        left = min(b['x'] for b in boxes) + scroll['x']
        top = min(b['y'] for b in boxes) + scroll['y']
        right = max(b['x']+b['width'] for b in boxes) + scroll['x']
        bottom = max(b['y']+b['height'] for b in boxes) + scroll['y']
        # A scrollable/clipped iframe cannot honestly be labelled a complete post capture.
        frame_info = frame.evaluate('() => ({h: innerHeight, w: innerWidth, sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth})')
        if frame_info['sh'] > frame_info['h'] + 8 or frame_info['sw'] > frame_info['w'] + 8:
            result['warnings'].append('SCROLLABLE_IFRAME_REVIEW_REQUIRED')
        from v8.capture_plan import plan_tiles
        boundary_spans = []
        boundary_state = 'unavailable'
        try:
            boundaries = body.evaluate(Path(__file__).with_name('capture_boundaries.js').read_text(encoding='utf-8'))
            offset = boxes[0]['y'] + scroll['y']
            boundary_spans = [dict(top=b['top'] + offset, bottom=b['bottom'] + offset)
                              for b in boundaries['intervals']]
            boundary_state = 'limited' if boundaries.get('limited') else 'measured'
        except Exception as exc:
            result.setdefault('diagnostics', []).append({'stage': 'capture_boundaries',
                'exception_type': type(exc).__name__, 'fallback': 'continuous_bounded_tiles'})
        tiles, truncated, cuts = plan_tiles(dict(x=left, y=top, width=right-left, height=bottom-top), boundary_spans)
        result['layout'] = {'version': '8.2.0', 'boundary_state': boundary_state,
                            'cuts': cuts, 'coverage': 'continuous', 'pixels_removed': 0}
        if truncated:
            result['warnings'].append('CAPTURE_HEIGHT_LIMIT')
        if image_total > 40:
            result['warnings'].append('IMAGE_LOAD_COUNT_LIMIT')
        if fresh['media']['loaded_image_count'] < fresh['media']['image_count']:
            result['warnings'].append('IMAGE_NOT_LOADED')
        rel = Path('captures') / f"{article['cafe_id']}_{article['article_id']}_{uuid4().hex[:8]}"
        folder = Path(output_dir) / rel
        stage = 'prepare_capture_folder'
        folder.mkdir(parents=True, exist_ok=False)
        for index, clip in enumerate(tiles, 1):
            stage = 'screenshot'
            if index == 1:
                result['captured_at'] = datetime.now(timezone.utc).isoformat()
            png = page.screenshot(type='png', full_page=True, clip=clip, animations='disabled', timeout=budget())
            if png[:8] != b'\x89PNG\r\n\x1a\n':
                raise CaptureError('INVALID_CAPTURE_PNG')
            width, height = struct.unpack('>II', png[16:24])
            stage = 'save_png'
            name = f'post_{index:03}.png'
            temporary = folder / (name + '.tmp')
            temporary.write_bytes(png)
            temporary.replace(folder / name)
            result['files'].append({'path': (rel/name).as_posix(), 'index': index,
                                    'width': width, 'height': height, 'clip': clip,
                                    'sha256': hashlib.sha256(png).hexdigest()})
        stage = 'verify_after_capture'
        after = frame.evaluate(body_extraction_script(), selection)
        after_diff = snapshot_diff(raw, after)
        result.setdefault('diagnostics', []).append({'stage': stage, **after_diff})
        wrong_page = page.url != original_page_url or (raw.get('document_url') and (after or {}).get('document_url') != raw.get('document_url'))
        if wrong_page or after_diff['changed_fields']:
            result['warnings'].append('CONTENT_CHANGED_DURING_CAPTURE')
            result.setdefault('diagnostics', []).append({'stage':stage,'code':'CONTENT_CHANGED_DURING_CAPTURE'})
            # Do not link a picture of a different revision to this source snapshot.
            result['files'] = []
        check_after_capture(page, article)
        result['metadata_observed_at'] = article['metadata'].get('observed_at')
        result['capture_finished_at'] = datetime.now(timezone.utc).isoformat()
        result['status'] = ('partial' if result['warnings'] else 'captured') if result['files'] else 'failed'
    except Exception as exc:
        code = getattr(exc, 'code', None) or ('CAPTURE_TIMED_OUT' if type(exc).__name__ == 'TimeoutError'
                                            else 'CAPTURE_STEP_FAILED')
        result['warnings'].append(code)
        result.setdefault('diagnostics', []).append({'stage':stage, 'code':code,
                                                    'exception_type':type(exc).__name__})
        result['status'] = 'partial' if result['files'] else 'failed'
    finally:
        if saved_scroll:
            try:
                page.evaluate('(p) => window.scrollTo(p.x, p.y)', saved_scroll)
            except Exception:
                pass
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result
