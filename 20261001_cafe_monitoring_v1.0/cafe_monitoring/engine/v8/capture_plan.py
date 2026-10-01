"""Browser clip rectangles: continuous coverage, bounded size, content-aware cuts."""
import math

TARGET = 1200
MAX_HEIGHT = 1560
MIN_TAIL = 420


def plan_tiles(box, intervals=(), max_tiles=24):
    values = [box[k] for k in ('x', 'y', 'width', 'height')]
    if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
        raise ValueError('invalid capture bounds')
    x, top = max(0, math.floor(box['x'])), max(0, math.floor(box['y']))
    width = math.ceil(box['x'] + box['width']) - x
    bottom = math.ceil(box['y'] + box['height'])
    if width <= 0 or bottom <= top or max_tiles < 1:
        raise ValueError('empty capture area')
    spans = []
    for interval in intervals:
        lo, hi = interval['top'], interval['bottom']
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (lo, hi)):
            raise ValueError('invalid content boundary')
        if hi > lo and hi > top and lo < bottom:
            spans.append((max(top, math.floor(lo) - 1), min(bottom, math.ceil(hi) + 1)))
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(hi, merged[-1][1])
        else:
            merged.append([lo, hi])
    safe = []
    for i in range(len(merged) - 1):
        if merged[i+1][0] > merged[i][1]:
            safe.append((merged[i][1] + merged[i+1][0]) // 2)
    tiles, cuts = [], []
    y = top
    while y < bottom and len(tiles) < max_tiles:
        remaining = bottom - y
        if remaining <= MAX_HEIGHT:
            end, strategy = bottom, 'complete_tail'
        else:
            target = min(y + TARGET, bottom - MIN_TAIL)
            low, high = y + 480, min(y + MAX_HEIGHT, bottom - MIN_TAIL)
            choices = [p for p in safe if low <= p <= high]
            if choices:
                end = min(choices, key=lambda p: (abs(p-target), p))
                strategy = 'content_gap'
            else:
                end, strategy = target, 'bounded_fallback'
        tiles.append({'x': x, 'y': y, 'width': width, 'height': end-y})
        cuts.append({'bottom': end, 'strategy': strategy,
                     'crosses_content': any(lo < end < hi for lo, hi in merged) if end < bottom else False})
        y = end
    return tiles, y < bottom, cuts
