"""표의 자동 줄바꿈이 폭을 넘으면 무손실 소프트 줄바꿈 후보를 실측합니다."""
import unicodedata


def logical_text(text):
    # PowerPoint soft line break is VT; remove only the layout-only character.
    return str(text).replace('\r\n', '\n').replace('\r', '\n').replace('\v', '')


def summary_variants(text, usable_width, min_font):
    text = logical_text(text)
    yield text
    # 한글/영문 혼합 폭의 보수적인 예상치입니다. COM 실측 통과가 최종 조건입니다.
    for ratio in (0.94, 0.84):
        limit = usable_width / min_font * ratio
        result, current = [], 0.0
        for c in text:
            if c == '\n':
                result.append(c)
                current = 0
                continue
            weight = 1.0 if unicodedata.east_asian_width(c) in ('W', 'F') else 0.58
            if current + weight > limit and not unicodedata.combining(c):
                result.append('\v')
                current = 0
            result.append(c)
            current += weight
        variant = ''.join(result)
        assert logical_text(variant) == text
        yield variant
