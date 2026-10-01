"""V5 검색어와 V7.5.3 표시·분석 설정을 보존하며 새 기간 설정을 읽습니다."""
from .core import read_json, V7Error
from .collect.search import validate_keyword

PERIOD_KEYS = {'keywords', 'period_start', 'period_end', 'max_pages_per_keyword', 'verify_search_pages'}


def keywords(value):
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, list) or not value:
        raise V7Error('INVALID_KEYWORDS', '검색어를 쉼표로 구분해 한 개 이상 지정하세요.')
    result = list(dict.fromkeys(validate_keyword(x) for x in value))
    return result


def load_period_config(root):
    legacy = read_json(root / 'config_v5.json')
    path = root / 'config_v754.json'
    custom = read_json(path) if path.is_file() else {}
    if not isinstance(custom, dict) or not isinstance(legacy, dict):
        raise V7Error('CONFIG_ERROR', '설정은 JSON 객체여야 합니다.')
    cfg = {'keywords': keywords(custom.get('keywords', legacy.get('keywords', ['불량', '고장']))),
           'period_start': custom.get('period_start'), 'period_end': custom.get('period_end'),
           'max_pages_per_keyword': custom.get('max_pages_per_keyword', 100),
           'verify_search_pages': custom.get('verify_search_pages', True)}
    if type(cfg['max_pages_per_keyword']) is not int or not 1 <= cfg['max_pages_per_keyword'] <= 1000:
        raise V7Error('CONFIG_ERROR', 'max_pages_per_keyword는 1~1000 정수입니다. 한도 도달은 미완료입니다.')
    if cfg['verify_search_pages'] is not True:
        raise V7Error('CONFIG_ERROR', 'V7.5.4에서는 검색 페이지 재확인을 켜 두세요(verify_search_pages: true).')
    for name in ('period_start', 'period_end'):
        if cfg[name] is not None and not isinstance(cfg[name], str):
            raise V7Error('CONFIG_ERROR', name + '는 날짜·시각 문자열 또는 null이어야 합니다.')
    return cfg
