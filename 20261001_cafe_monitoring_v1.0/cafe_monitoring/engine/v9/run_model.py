"""Shared run states and composite article identity; no output/UI dependencies."""
import re
from v8.configuration import V8Error

DONE={'completed','partial','completed_empty'}
FINISHED=DONE|{'failed'}
LABELS={'completed':'완료','partial':'부분 완료·검토 필요','completed_empty':'0건 완료',
        'failed':'실패','collected':'수집 완료','new':'대기','waiting':'처리할 구간 없음'}
PATTERN=re.compile(r'(?:batch|period)_\d{8}_\d{6}_\d{6}_[a-f0-9]{8}')


def key(item):
    return str(item['cafe_id'])+':'+str(item['id'])


def failure(exc):
    return {'code':getattr(exc,'code',type(exc).__name__),'message':str(exc)}


def unique_items(items):
    values=[key(i) for i in items]
    if len(set(values))!=len(values):
        raise V8Error('DUPLICATE_ARTICLE','같은 카페·게시글이 중복되었습니다.')
    return set(values)

