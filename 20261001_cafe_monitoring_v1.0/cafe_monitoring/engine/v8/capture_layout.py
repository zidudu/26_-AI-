"""Whole captures, consistent scale, and small trailing capture packing."""
from v754.layout import GAP, capture_pages as legacy_pages
from v754.powerpoint import CAPTURE_W, CAPTURE_H


def capture_pages(captures, cfg, area_width, area_height):
    pages = legacy_pages(captures, cfg, area_width, area_height)
    if len(captures) < 2:
        return pages
    # A continuation must not become larger merely because it is alone.
    normal_columns = min(cfg['capture_columns'], 2)
    normal_width = (area_width - (normal_columns - 1) * GAP) / normal_columns
    reference_scale = min(normal_width / c['office_width'] for c in captures)
    reference_scale = min(reference_scale, *(area_height / c['office_height'] for c in captures))
    for page in pages:
        for tile in page['tiles']:
            cap = tile['capture']
            # Preserve each full original image; geometry never crops its contents.
            width, height = cap['office_width'] * reference_scale, cap['office_height'] * reference_scale
            column = page['tiles'].index(tile)
            slot = tile['slot_width']
            tile.update(width=width, height=height, full_height=height, fit_scale=width/slot,
                        x_offset=column * (slot + GAP) + (slot-width)/2,
                        y_offset=(area_height-height)/2)
    # Only move a single short final capture, in reading order after the prior
    # page's last column. Keep every pixel and every original capture index.
    if len(pages) >= 2 and len(pages[-1]['tiles']) == 1:
        tail = pages[-1]['tiles'][0]
        previous = pages[-2]
        anchor = previous['tiles'][-1]
        if tail['height'] <= area_height * .30:
            cap = anchor['capture']; extra = tail['capture']
            packed_scale = min(reference_scale, (area_height-8.0) / (cap['office_height']+extra['office_height']))
            # Limit any shrink needed to share the last column to 15%.
            if packed_scale >= reference_scale * .85:
                column = len(previous['tiles'])-1
                slot = anchor['slot_width']
                for tile in (anchor, tail):
                    c = tile['capture']; w, h = c['office_width']*packed_scale, c['office_height']*packed_scale
                    tile.update(width=w, height=h, full_height=h, fit_scale=w/slot,
                                slot_width=slot, x_offset=column*(slot+GAP)+(slot-w)/2)
                total = anchor['height'] + 8.0 + tail['height']
                anchor['y_offset'] = (area_height-total)/2
                tail['y_offset'] = anchor['y_offset'] + anchor['height'] + 8.0
                previous['tiles'].append(tail)
                previous['tail_packed'] = True
                pages.pop()
    return pages


def plan_article(article, cfg):
    text = article['display_text'].replace('\r\n','\n').replace('\r','\n')
    pages = capture_pages(article['captures'], cfg, CAPTURE_W, CAPTURE_H)
    if not pages:
        pages = [{'columns': 0, 'tiles': []}]
    return [dict(p, kind='captures', summary=text, summary_continues=False,
                 text_measurement='not_measured') for p in pages]
