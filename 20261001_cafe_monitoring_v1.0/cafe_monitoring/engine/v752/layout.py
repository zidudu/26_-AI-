"""PowerPoint 없이 검사할 수 있는 캡처 배치 계산. 이미지 파일은 변경하지 않습니다."""
from __future__ import annotations

import math

from .core import V7Error

GAP = 16.0


def scaled_height(width, height, box_width):
    if not all(type(n) in (int, float) and math.isfinite(n) and n > 0
               for n in (width, height, box_width)):
        raise V7Error("IMAGE_DIMENSIONS", "캡처 이미지 크기가 올바르지 않습니다.")
    result = box_width * (height / width)
    if not math.isfinite(result) or result <= 0:
        raise V7Error("IMAGE_DIMENSIONS", "캡처의 표시 높이를 계산할 수 없습니다.")
    return result


def balanced_slices(width, height, box_width, box_height, overlap, min_scale):
    """최대 15% 보정으로 한 칸에 맞추거나, 마지막 조각까지 같은 높이로 분할합니다."""
    full_height = scaled_height(width, height, box_width)
    if not (type(box_height) in (int, float) and math.isfinite(box_height) and box_height > 0
            and type(overlap) in (int, float) and math.isfinite(overlap) and 0 <= overlap < box_height
            and type(min_scale) in (int, float) and math.isfinite(min_scale) and 0.85 <= min_scale <= 1):
        raise V7Error("IMAGE_DIMENSIONS", "캡처 배치 크기·겹침·축소 설정이 올바르지 않습니다.")
    fit = min(1.0, box_height / full_height)
    scale = fit if fit >= min_scale else 1.0
    display_width = box_width * scale
    full_height *= scale
    # 미세한 소수 오차가 불필요한 분할을 만들지 않게 합니다.
    count = max(1, math.ceil((full_height - overlap - 1e-7) / (box_height - overlap)))
    if count > 1000:
        raise V7Error("IMAGE_TOO_LONG", "한 이미지의 분할 수가 1000개를 넘습니다.")
    visible = (full_height + (count - 1) * overlap) / count
    parts = []
    for i in range(count):
        start = i * (visible - overlap)
        parts.append({
            "start": start, "height": full_height - start if i == count - 1 else visible,
            "full_height": full_height, "width": display_width,
            "start_fraction": start / full_height,
            "end_fraction": 1.0 if i == count - 1 else (start + visible) / full_height,
            "fit_scale": scale, "slot_width": box_width,
        })
    return parts


def capture_pages(captures, cfg, area_width, area_height):
    """원본 캡처 하나를 그림 하나로 배치합니다. 분할·내용 잘라내기를 하지 않습니다."""
    if not all(type(n) in (int, float) and math.isfinite(n) and n > 0
               for n in (area_width, area_height)):
        raise V7Error("IMAGE_DIMENSIONS", "캡처 표시 영역이 올바르지 않습니다.")
    requested = cfg["capture_columns"]
    if type(requested) is not int or requested not in (1, 2, 3):
        raise V7Error("CONFIG_ERROR", "capture_columns 설정을 확인하세요.")
    # 이전 3열 설정도 최신 요구사항인 최대 두 장으로 제한합니다.
    maximum = min(requested, 2)
    pages = []
    for start in range(0, len(captures), maximum):
        batch = captures[start:start + maximum]
        columns = len(batch)
        slot_width = (area_width - (columns - 1) * GAP) / columns
        tiles = []
        for column, cap in enumerate(batch):
            at_width_height = scaled_height(cap["office_width"], cap["office_height"], slot_width)
            scale = min(1.0, area_height / at_width_height)
            width, height = slot_width * scale, at_width_height * scale
            if not (width > 0 and height > 0):
                raise V7Error("IMAGE_DIMENSIONS", "캡처 맞춤 크기를 계산할 수 없습니다.")
            tiles.append({
                "capture": cap, "part": 1, "total_parts": 1,
                "start": 0.0, "height": height, "full_height": height, "width": width,
                "start_fraction": 0.0, "end_fraction": 1.0,
                "fit_scale": scale, "slot_width": slot_width,
                "x_offset": column * (slot_width + GAP) + (slot_width - width) / 2,
                "y_offset": (area_height - height) / 2,
            })
        pages.append({"columns": columns, "tiles": tiles})
    return pages
