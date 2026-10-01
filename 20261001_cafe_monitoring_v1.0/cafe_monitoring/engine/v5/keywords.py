"""쉼표로 구분한 검색어를 입력 순서대로 정리합니다. 구문 내부 공백은 유지합니다."""
from collector import CollectorError
from search import validate_keyword


def parse_keywords(value):
    if isinstance(value, str):
        values = value.replace('，', ',').split(',')
    elif isinstance(value, list):
        values = value
    else:
        raise CollectorError('INVALID_KEYWORDS', '검색어는 쉼표로 구분한 문자열 또는 JSON 배열로 입력하세요.')
    result = []
    for item in values:
        keyword = validate_keyword(item)
        if keyword not in result:
            result.append(keyword)
    if not result:
        raise CollectorError('INVALID_KEYWORDS', '검색어를 하나 이상 입력하세요.')
    return result
