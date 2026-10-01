"""Actual browser PNGs of post header/body. Capture problems never discard readable text."""
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import struct
import time
from uuid import uuid4

TILE_HEIGHT = 1200
MAX_TILES = 24
CAPTURE_SECONDS = 20


class CaptureError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def tile_rectangles(box, max_tiles=MAX_TILES):
    x, y = max(0, math.floor(box['x'])), max(0, math.floor(box['y']))
    width = math.ceil(box['x'] + box['width']) - x
    bottom = math.ceil(box['y'] + box['height'])
    if width <= 0 or bottom <= y:
        raise ValueError('empty capture area')
    tiles = []
    while y < bottom and len(tiles) < max_tiles:
        height = min(TILE_HEIGHT, bottom-y)
        tiles.append(dict(x=x, y=y, width=width, height=height))
        y += height
    return tiles, y < bottom


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
        from collector import body_extraction_script
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
        stage = 'verify_before_capture'
        fresh = frame.evaluate(body_extraction_script(), selection)
        if not fresh or any(fresh.get(k) != raw.get(k) for k in ('title', 'date', 'body')):
            raise CaptureError('CONTENT_CHANGED_BEFORE_CAPTURE')
        article['media'] = fresh['media']
        # The outer page often holds the café name; use only a visible, labelled café element.
        cafe_name = page.evaluate('''() => {
            for (const s of ['#cafe-info-data .cafe-name', '.cafe_name', '.cafeName']) {
                const e = document.querySelector(s);
                if (e?.getClientRects().length && e.innerText?.trim()) return e.innerText.trim();
            } return null;
        }''')
        if cafe_name:
            article['metadata']['cafe_name'] = cafe_name
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
        tiles, truncated = tile_rectangles(dict(x=left, y=top, width=right-left, height=bottom-top))
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
        if not after or page.url != original_page_url or any(after.get(k) != raw.get(k) for k in ('title', 'date', 'body')):
            result['warnings'].append('CONTENT_CHANGED_DURING_CAPTURE')
            result.setdefault('diagnostics', []).append({'stage':stage,'code':'CONTENT_CHANGED_DURING_CAPTURE'})
            # Do not link a picture of a different revision to this source snapshot.
            result['files'] = []
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
