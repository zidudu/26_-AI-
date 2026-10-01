"""고정 줄 수로 자르지 않고 표시 영역에 맞는지 확인한 뒤 분할합니다."""
from __future__ import annotations

import re
import unicodedata

from .core import V7Error


def split_for_box(text, fits):
    """전체 맞춤 우선. 필요할 때 문장 → 공백 → 문자 순으로 경계를 찾습니다.

    반환값을 이어 붙이면 입력과 정확히 같아야 합니다. 공백도 버리지 않습니다.
    fits는 실제 Office 측정 함수 또는 오프라인 테스트에서 주입한 함수입니다.
    """
    if not text or fits(text):
        return text, ""

    def longest_fit(boundaries):
        # 고정 글꼴·영역에서 접두부의 맞춤 여부를 이분 탐색합니다.
        low, high, best = 0, len(boundaries) - 1, 0
        while low <= high:
            middle = (low + high) // 2
            end = boundaries[middle]
            if fits(text[:end]):
                best = end
                low = middle + 1
            else:
                high = middle - 1
        return best

    sentences = [m.end() for m in re.finditer(r'[.!?。！？][\"\'”’)]*\s+|\n+', text)]
    end = longest_fit(sentences)
    if not end:
        end = longest_fit([m.end() for m in re.finditer(r'\s+', text)])
    if not end:
        # 공백 없는 긴 문자열에만 사용합니다. 결합 문자/ZWJ 앞에서는 자르지 않습니다.
        boundaries = [i for i in range(1, len(text))
                      if not unicodedata.combining(text[i])
                      and text[i] not in ('\ufe0f', '\u200d') and text[i - 1] != '\u200d']
        end = longest_fit(boundaries)
    if not end or not text[:end].strip():
        raise V7Error('TEXT_OVERFLOW', '텍스트를 최소 글꼴 크기로도 표시할 수 없습니다.')
    return text[:end], text[end:]


def paginate_text(text, fits):
    pages = []
    while text:
        first, remaining = split_for_box(text, fits)
        pages.append(first)
        text = remaining
        if len(pages) > 1000:
            raise V7Error('TEXT_TOO_LONG', '내용 상세 슬라이드가 1000장을 넘습니다.')
    return pages
