"""PNG derivatives sized from the unchanged slide layout, checked against COM."""
from io import BytesIO
import hashlib
import math
from pathlib import Path
import struct
from time import perf_counter

from v754.core import V7Error, capture_files

DEFAULT_PPI = 125
POINTS_PER_INCH = 72.0
METHOD = 'effective_ppi_png_v2_com'
PREVIOUS_PPI_METHOD = 'effective_ppi_png_v1'
LEGACY_METHOD = 'pillow_lanczos_png_v1'  # read-only compatibility with V9.5.2 sources
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
GEOMETRY_TOLERANCE_PT = 0.01


def valid_ppi(value):
    return type(value) is int and (value == 0 or 96 <= value <= 300)


def dimensions(png):
    if len(png) < 33 or not png.startswith(PNG_SIGNATURE) or png[12:16] != b'IHDR':
        raise ValueError('INVALID_CAPTURE_PNG')
    width, height = struct.unpack('>II', png[16:24])
    if width <= 0 or height <= 0:
        raise ValueError('INVALID_CAPTURE_DIMENSIONS')
    return width, height


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def target_size(width, height, display_width_pt, ppi):
    """Full picture width in points -> inches -> pixels; keep aspect, never enlarge."""
    if (not all(type(n) is int and n > 0 for n in (width, height))
            or not positive(display_width_pt) or not valid_ppi(ppi) or ppi == 0):
        raise ValueError('INVALID_PPI_GEOMETRY')
    target_width = min(width, max(1, round(display_width_pt / POINTS_PER_INCH * ppi)))
    target_height = min(height, max(1, round(height * target_width / width)))
    return target_width, target_height


def resize_png(png, size):
    from PIL import Image
    original = dimensions(png)
    if (not isinstance(size, tuple) or len(size) != 2
            or any(type(n) is not int or n <= 0 or n > limit for n, limit in zip(size, original))):
        raise ValueError('INVALID_TARGET_PIXELS')
    with Image.open(BytesIO(png)) as source:
        if source.format != 'PNG':
            raise ValueError('INVALID_CAPTURE_PNG')
        source.load()
        if source.mode not in ('RGB', 'RGBA'):
            raise ValueError('UNEXPECTED_BROWSER_PNG_MODE')
        with source.resize(size, Image.Resampling.LANCZOS) as resized:
            output = BytesIO()
            resized.save(output, format='PNG')
            result = output.getvalue()
    with Image.open(BytesIO(result)) as check:
        check.load()
        if check.format != 'PNG' or check.size != size:
            raise ValueError('PPT_IMAGE_ENCODE_MISMATCH')
    return result


def atomic_png(path, png):
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_bytes(png)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_capture(png, output_dir, relative_folder, index, clip, ppi=DEFAULT_PPI):
    """Save the original unchanged; wait for all this post's tiles before sizing."""
    width, height = dimensions(png)
    rel = Path(relative_folder) / f'post_{index:03}.png'
    original = {'path': rel.as_posix(), 'index': index, 'width': width, 'height': height,
                'clip': clip, 'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png)}
    atomic_png(Path(output_dir) / rel, png)
    original['ppt_image'] = {'status': 'original' if ppi == 0 else 'pending',
        'reason': 'disabled' if ppi == 0 else 'awaiting_post_layout', 'method': METHOD,
        'ppi': ppi, 'source_path': original['path'], 'source_sha256': original['sha256'],
        'source_width': width, 'source_height': height, 'source_bytes': len(png)}
    return original


def placements_for(pages):
    """Record full-picture and crop geometry, including fit/continuation/tail packing."""
    from v754.powerpoint import capture_geometry
    placements = {}
    for page_number, page in enumerate(pages, 1):
        for column, tile in enumerate(page['tiles']):
            row = {'page': page_number, 'column': column, 'part': tile['part'],
                   'total_parts': tile['total_parts'], 'geometry': capture_geometry(tile, column)}
            placements.setdefault(tile['capture']['index'], []).append(row)
    return placements


def plan_from_pixels(entries, capture_columns):
    from v8.capture_layout import plan_article
    # V9.5.3 regression model only. Do not use this to create new derivatives:
    # Office scales large pictures individually when AddPicture uses -1 sizes.
    captures = [{**entry, 'office_width': float(entry['width']),
                 'office_height': float(entry['height'])} for entry in entries]
    return plan_article({'captures': captures, 'display_text': ''}, {'capture_columns': capture_columns})


def plan_from_measurements(entries, measurements, capture_columns):
    from v8.capture_layout import plan_article
    indexes = [e['index'] for e in entries]
    if len(set(indexes)) != len(indexes) or set(measurements) != set(indexes):
        raise ValueError('PPT_IMAGE_MEASUREMENT_INCOMPLETE')
    captures = []
    for entry in entries:
        measured = measurements[entry['index']]
        w, h = measured['office_width'], measured['office_height']
        if (not positive(w) or not positive(h)
                or abs((w/h)/(entry['width']/entry['height']) - 1) > .04):
            raise ValueError('PPT_IMAGE_MEASUREMENT_INVALID')
        captures.append({**entry, 'office_width': w, 'office_height': h})
    return plan_article({'captures': captures, 'display_text': ''}, {'capture_columns': capture_columns})


def largest_width(placements):
    widths = [row['geometry']['PictureWidth'] for row in placements]
    if not widths or not all(positive(w) for w in widths):
        raise ValueError('INVALID_PPI_PLACEMENTS')
    return max(widths)


def finalize_capture_images(entries, png_by_index, output_dir, ppi=DEFAULT_PPI, capture_columns=2):
    """Use only screenshot bytes retained for this post; never read saved PNGs."""
    if not entries or ppi == 0:
        return
    try:
        if not valid_ppi(ppi):
            raise ValueError('INVALID_PPT_IMAGE_PPI')
        from v9.ppt_measurements import measure_originals
        measurements = measure_originals(entries, output_dir)
        placements = placements_for(plan_from_measurements(entries, measurements, capture_columns))
    except Exception as exc:
        for entry in entries:
            entry['ppt_image'].update(status='failed', reason='PPT_IMAGE_MEASUREMENT_FAILED',
                                      measurement_error=getattr(exc, 'code', type(exc).__name__),
                                      exception_type=type(exc).__name__)
        return
    for entry in entries:
        info = entry['ppt_image']; started = perf_counter()
        try:
            png = png_by_index[entry['index']]
            if hashlib.sha256(png).hexdigest() != entry['sha256'] or dimensions(png) != (entry['width'], entry['height']):
                raise ValueError('PPT_IMAGE_MEMORY_SOURCE_MISMATCH')
            uses = placements[entry['index']]
            width_pt = largest_width(uses)
            size = target_size(entry['width'], entry['height'], width_pt, ppi)
            info.update(layout_version='v8.2_original_capture_layout_com', capture_columns=capture_columns,
                        office_measurement={'method': 'powerpoint_com', **measurements[entry['index']]},
                        placements=uses, display_width_pt=width_pt,
                        display_width_inch=width_pt / POINTS_PER_INCH,
                        target_width=size[0], target_height=size[1],
                        original_effective_ppi=entry['width'] / (width_pt / POINTS_PER_INCH),
                        effective_ppi=size[0] / (width_pt / POINTS_PER_INCH),
                        com_layout_checked=False)
            if size == (entry['width'], entry['height']):
                info.update(status='original', reason='native_resolution_limit')
                continue
            optimized = resize_png(png, size)
            raw_path = Path(entry['path'])
            optimized_rel = raw_path.parent / f'ppt_ppi_{ppi}' / raw_path.name
            path = Path(output_dir) / optimized_rel
            path.parent.mkdir(exist_ok=True)
            atomic_png(path, optimized)
            info.pop('reason', None)
            info.update(status='ready', path=optimized_rel.as_posix(), width=size[0], height=size[1],
                        bytes=len(optimized), sha256=hashlib.sha256(optimized).hexdigest())
        except Exception as exc:
            info.update(status='failed', reason='PILLOW_MISSING' if isinstance(exc, ImportError)
                        else 'PPT_IMAGE_OPTIMIZATION_FAILED', exception_type=type(exc).__name__)
        finally:
            info['seconds'] = round(perf_counter() - started, 6)


async def finalize_capture_images_async(entries, png_by_index, output_dir, ppi=DEFAULT_PPI, capture_columns=2):
    """Keep browser tasks responsive and drain finalization before clearing bytes."""
    if not entries or ppi == 0:
        return
    import asyncio
    task = asyncio.create_task(asyncio.to_thread(finalize_capture_images, entries, dict(png_by_index),
                                                output_dir, ppi, capture_columns))
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.done():
                task.result()
                raise
            cancelled = exc
    if cancelled is not None:
        raise cancelled
    return result


def same_placements(recorded, actual):
    if not isinstance(recorded, list) or len(recorded) != len(actual):
        return False
    for old, new in zip(recorded, actual):
        if any(old.get(key) != new[key] for key in ('page', 'column', 'part', 'total_parts')):
            return False
        a, b = old.get('geometry', {}), new['geometry']
        if set(a) != set(b):
            return False
        if any(type(a[k]) not in (int, float) or not math.isfinite(a[k])
               or abs(a[k] - b[k]) > GEOMETRY_TOLERANCE_PT for k in b):
            return False
    return True


def _candidate(article, source, original, info, raw_cap, actual_placements):
    source_path = Path(article['refreshed_source_file'])
    draft_source = {**source, 'source_file': str(source_path)}
    raw, _ = capture_files({'source': draft_source,
        'capture': {**source['capture'], 'files': [original]}}, source_path.parent)
    if (not raw or Path(raw[0]['path']) != Path(raw_cap['path'])
            or raw_cap.get('original_sha256') != original['sha256']):
        raise V7Error('PPT_IMAGE_ORIGINAL_INVALID', '원본 캡처 검증에 실패했습니다.')
    if (info.get('source_path') != original['path'] or info.get('source_sha256') != original['sha256']
            or info.get('source_width') != original['width'] or info.get('source_height') != original['height']
            or type(info.get('bytes')) is not int or info['bytes'] <= 0
            or type(original.get('bytes')) is not int or original['bytes'] <= 0):
        raise V7Error('PPT_IMAGE_SOURCE_MISMATCH', 'PPT용 PNG의 원본 연결·크기 기록이 다릅니다.')
    method = info.get('method')
    if method in (METHOD, PREVIOUS_PPI_METHOD):
        if method == METHOD:
            measured = info.get('office_measurement', {})
            if (measured.get('method') != 'powerpoint_com'
                    or not positive(measured.get('office_width')) or not positive(measured.get('office_height'))):
                raise V7Error('PPT_IMAGE_MEASUREMENT_INVALID', 'PPT용 PNG의 원본 실측 기록이 없습니다.')
        ppi = info.get('ppi')
        if not valid_ppi(ppi) or ppi == 0 or not same_placements(info.get('placements'), actual_placements):
            raise V7Error('PPT_IMAGE_LAYOUT_MISMATCH', '수집 시 배치와 PowerPoint 원본 기준 배치가 달라 원본을 사용합니다.')
        recorded_width = largest_width(info['placements'])
        expected = target_size(original['width'], original['height'], recorded_width, ppi)
        actual_width = largest_width(actual_placements)
        com_expected = target_size(original['width'], original['height'], actual_width, ppi)
        if ((info.get('width'), info.get('height')) != expected
                or (info.get('target_width'), info.get('target_height')) != expected
                or any(abs(a-b) > 1 for a, b in zip(expected, com_expected))):
            raise V7Error('PPT_IMAGE_PPI_MISMATCH', '최종 배치 크기 × PPI와 축소 PNG 픽셀이 다릅니다.')
        selected = {'mode': 'effective_ppi', 'ppi': ppi, 'com_layout_checked': True,
                    'display_width_inch': actual_width / POINTS_PER_INCH,
                    'effective_ppi': info['width'] / (actual_width / POINTS_PER_INCH),
                    'com_target_width': com_expected[0], 'com_target_height': com_expected[1]}
    elif method == LEGACY_METHOD:
        # Old saved runs remain usable, explicitly marked as relative resizing.
        level = info.get('level')
        if (type(level) is not int or not 96 <= level < 150 or info.get('reference_level') != 150
                or (info.get('width'), info.get('height')) != tuple(max(1,(n*level+75)//150)
                       for n in (original['width'], original['height']))):
            raise V7Error('PPT_IMAGE_SOURCE_MISMATCH', '기존 비율 축소본 기록이 다릅니다.')
        selected = {'mode': 'legacy_ratio', 'level': level, 'com_layout_checked': False}
    else:
        raise V7Error('PPT_IMAGE_METHOD_UNKNOWN', '알 수 없는 이미지 처리 방식입니다.')
    checked, notes = capture_files({'source': draft_source,
        'capture': {**source['capture'], 'files': [{**info, 'index': original['index']}]}}, source_path.parent)
    if not checked or Path(checked[0]['path']) == Path(raw_cap['path']):
        raise V7Error('PPT_IMAGE_FILE_INVALID', 'PPT용 PNG의 경로·해시 검증에 실패했습니다.')
    return {**checked[0], **selected, 'bytes': info['bytes'], 'source_bytes': original['bytes'], 'notes': notes}


def prepare_ppt_images(deck, articles, report, *, plans):
    """Accept PPI copies only after the original COM-based final plan agrees."""
    from v8.analysis_engine import load_bound_source
    from v754.powerpoint import get_dimensions
    audit = {'optimized': 0, 'effective_ppi_images': 0, 'legacy_ratio_images': 0,
             'original': 0, 'fallback': 0, 'items': [], 'raw_bytes_for_optimized': 0,
             'optimized_bytes': 0, 'geometry_tolerance_pt': GEOMETRY_TOLERANCE_PT,
             'target_pixel_rounding_tolerance': 1}
    report['ppt_image_optimization'] = audit
    pending = []
    plan_by_identity = {(a['cafe_id'], a['id']): placements_for(pages) for a, pages in plans}

    def fallback(row, reason):
        audit['fallback'] += 1; row['reason'] = reason
        report.setdefault('warnings', []).append({'code': 'PPT_IMAGE_ORIGINAL_FALLBACK',
            'article_id': row['article_id'], 'capture_index': row['index'], 'reason': reason})
        print(f"[PPT 이미지 원본 대체] {article_key(row)} / {reason}", flush=True)

    for article in articles:
        source, _ = load_bound_source(article)
        originals = {f.get('index', i): f for i, f in enumerate(source.get('capture', {}).get('files', []), 1)}
        actual = plan_by_identity[(article['cafe_id'], article['id'])]
        for cap in article['captures']:
            cap.pop('ppt_image', None)
            original = originals.get(cap['index'], {}); info = original.get('ppt_image') or {}
            row = {'cafe_id': article['cafe_id'], 'article_id': article['id'], 'index': cap['index'],
                   'original_path': cap['path'], 'original_sha256': cap.get('original_sha256'),
                   'original_width': original.get('width'), 'original_height': original.get('height'),
                   'original_bytes': original.get('bytes'),
                   'original_verification': cap.get('verification'), 'selected': 'original'}
            if info:
                row['derivative_record'] = {k: info[k] for k in ('status', 'method', 'path', 'sha256',
                    'bytes', 'width', 'height', 'ppi', 'reason', 'measurement_error', 'office_measurement') if k in info}
            audit['items'].append(row)
            if not info or info.get('status') == 'original':
                audit['original'] += 1; row['reason'] = info.get('reason', 'legacy_capture_no_derivative')
                continue
            try:
                if info.get('status') != 'ready':
                    raise V7Error(info.get('reason', 'PPT_IMAGE_NOT_READY'), 'PPT용 이미지 생성 미완료')
                candidate = _candidate(article, source, original, info, cap, actual.get(cap['index'], []))
                probe_article = {'id': article['id'], 'captures': [candidate], 'capture_problem': False, 'capture_notes': []}
                pending.append((probe_article, cap, row, info, original))
            except Exception as exc:
                if getattr(exc, 'code', '') == 'PPT_IMAGE_ORIGINAL_INVALID':
                    raise
                if getattr(exc, 'code', '') == 'PPT_IMAGE_LAYOUT_MISMATCH':
                    row['layout_comparison'] = {'recorded': info.get('placements'),
                                               'actual': actual.get(cap['index'], [])}
                fallback(row, getattr(exc, 'code', 'PPT_IMAGE_VALIDATION_FAILED'))
    if pending:
        get_dimensions(deck, [p[0] for p in pending], {'capture_failures': []})
        for probe_article, cap, row, info, original in pending:
            if not probe_article['captures']:
                fallback(row, 'PPT_IMAGE_OFFICE_INSERT_FAILED'); continue
            candidate = probe_article['captures'][0]; cap['ppt_image'] = candidate
            row.update(selected='optimized', path=candidate['path'], width=info['width'], height=info['height'],
                       bytes=info['bytes'], sha256=info['sha256'], verification=candidate['verification'], mode=candidate['mode'])
            if candidate['mode'] == 'effective_ppi':
                audit['effective_ppi_images'] += 1
                row.update({k:candidate[k] for k in ('ppi','display_width_inch','effective_ppi','com_target_width','com_target_height','com_layout_checked')})
                print(f"[PPT PPI] {article_key(row)} / {row['display_width_inch']:.4f} inch × {row['ppi']}ppi → {row['width']}×{row['height']}px / 유효 {row['effective_ppi']:.2f}ppi / 원본 배치 일치", flush=True)
            else:
                audit['legacy_ratio_images'] += 1; row['level'] = candidate['level']
            audit['optimized'] += 1
            audit['raw_bytes_for_optimized'] += original['bytes']; audit['optimized_bytes'] += info['bytes']
    print(f"[PPT 이미지] PNG 축소본 {audit['optimized']}개 / 원본 {audit['original']}개 / 원본 대체 {audit['fallback']}개", flush=True)
    print(f"[PPT PPI 검증] 최종 배치 기준 {audit['effective_ppi_images']}개 / 기존 비율 축소본 {audit['legacy_ratio_images']}개", flush=True)
    if audit['optimized']:
        print(f"[PPT 이미지 bytes·DRM 적용 전] 원본 {audit['raw_bytes_for_optimized']:,} → 축소본 {audit['optimized_bytes']:,}", flush=True)


def article_key(row):
    return f"{row['cafe_id']}:{row['article_id']} / 캡처 {row['index']}"


def verify_inserted_ppi(picture, tile):
    derivative = tile['capture'].get('ppt_image')
    if not derivative or derivative['mode'] != 'effective_ppi':
        return None
    width = float(picture.PictureFormat.Crop.PictureWidth)
    height = float(picture.PictureFormat.Crop.PictureHeight)
    if not positive(width) or not positive(height):
        raise V7Error('PPT_IMAGE_PPI_MISMATCH', 'PPT 그림의 최종 표시 크기를 확인하지 못했습니다.')
    if abs(width - tile['width']) > 0.01 or abs(height - tile['full_height']) > 0.01:
        raise V7Error('PPT_IMAGE_PPI_MISMATCH', 'PPT 그림의 실측 표시 크기가 PPI 계산에 사용한 배치와 다릅니다.')
    # One raster may be reused at several sizes. Its pixel budget is based on
    # the largest use; smaller uses legitimately have a higher effective PPI.
    planned_width = derivative['display_width_inch'] * POINTS_PER_INCH
    expected = target_size(tile['capture']['width'], tile['capture']['height'],
                           planned_width, derivative['ppi'])
    if (width > planned_width + 0.01
            or any(abs(a-b) > 1 for a,b in zip(expected, (derivative['width'], derivative['height'])))):
        raise V7Error('PPT_IMAGE_PPI_MISMATCH', 'PPT 그림의 픽셀 크기가 최종 표시 크기 × PPI와 다릅니다.')
    # Geometry was already checked by the original verifier. Record real COM
    # points here, independently of both raster DPI metadata and probe dimensions.
    return {'width_inch': width / POINTS_PER_INCH, 'height_inch': height / POINTS_PER_INCH,
            'pixels': [derivative['width'], derivative['height']], 'requested_ppi': derivative['ppi'],
            'pixel_budget_checked': True,
            'effective_ppi_x': derivative['width'] / (width / POINTS_PER_INCH),
            'effective_ppi_y': derivative['height'] / (height / POINTS_PER_INCH)}


def insert_tile(slide, tile, column):
    from v754.powerpoint import insert_tile as original_insert
    cap = tile['capture']; derivative = cap.get('ppt_image')
    if not derivative:
        return original_insert(slide, tile, column)
    picture = original_insert(slide, {**tile, 'capture': {**cap, 'path': derivative['path']}}, column)
    label = (f"{derivative['ppi']}ppi / 표시 크기 기준" if derivative['mode'] == 'effective_ppi'
             else f"기존 비율 {derivative['level']}/150 / PPI 설정 아님")
    picture.AlternativeText = (f"실제 게시글 캡처 {cap['index']} / 전체 표시 / PPT용 PNG {label} / 원본 별도 보존"
                               f" / PNG SHA256: {derivative['original_sha256']}")
    return picture
