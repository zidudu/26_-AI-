"""V9 sync capture with unchanged V8.2 evidence and metadata rules."""
from datetime import datetime, timezone
from pathlib import Path
import time
from uuid import uuid4
from v8 import post_capture as capture_source
from v8.post_capture import CAPTURE_SECONDS, CaptureError, snapshot_diff

def capture_post(page, raw, output_dir, article, *, ppi=125, capture_columns=2):
    result = {'status': 'failed', 'files': [], 'warnings': [],
              'captured_at': datetime.now(timezone.utc).isoformat(), 'source_url': article['url'],
              'source_body_sha256': article['body_sha256'], 'scope': 'post_header_and_body',
              'comments_included': False, 'video_played': False, 'images_analyzed_by_ai': False}
    png_by_index = {}
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
            boundaries = body.evaluate(Path(capture_source.__file__).with_name('capture_boundaries.js').read_text(encoding='utf-8'))
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
            stage = 'save_raw_and_ppt_png'
            from v9.ppt_images import save_capture
            result['files'].append(save_capture(png, output_dir, rel, index, clip, ppi))
            if ppi:
                png_by_index[index] = png
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
        from v9.ppt_images import finalize_capture_images
        try:
            finalize_capture_images(result['files'], png_by_index, output_dir, ppi, capture_columns)
        finally:
            png_by_index.clear()
        if saved_scroll:
            try:
                page.evaluate('(p) => window.scrollTo(p.x, p.y)', saved_scroll)
            except Exception:
                pass
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result
