"""인증은 전체 중단, 글별 열람 제한은 건너뜀으로 구분합니다."""
from pathlib import Path
from urllib.parse import urlsplit

STATUS_JS = (Path(__file__).with_name('page_status.js')).read_text(encoding='utf-8')
SESSION_CODES = (
    ('captcha', 'AUTH_REQUIRED', '추가 인증 화면이 나타났습니다. 02_login_v5.bat에서 직접 인증 후 실행하세요.'),
    ('blocked', 'REQUEST_BLOCKED', '접근 또는 요청 제한 안내가 나타나 전체 수집을 중단합니다.'),
    ('login', 'LOGIN_REQUIRED', '로그인이 필요합니다. 02_login_v5.bat에서 로그인 후 실행하세요.'),
)
ARTICLE_CODES = (
    ('grade', 'GRADE_REQUIRED', '회원 등급에 따른 읽기 제한 안내를 확인했습니다.'),
    ('membership', 'MEMBERSHIP_REQUIRED', '카페 가입 또는 멤버 열람 제한 안내를 확인했습니다.'),
    ('deleted', 'ARTICLE_DELETED', '삭제되었거나 존재하지 않는 게시글 안내를 확인했습니다.'),
    ('denied', 'ACCESS_DENIED', '게시글 읽기 권한이 없다는 안내를 확인했습니다.'),
)
SKIP_CODES = {code for _, code, _ in ARTICLE_CODES}


def read_states(page):
    states = []
    for frame in list(page.frames):
        host = urlsplit(frame.url).hostname or ''
        if host not in ('cafe.naver.com', 'nid.naver.com'):
            continue
        try:
            state = frame.evaluate(STATUS_JS)
        except Exception as exc:
            # 프레임 교체 중의 순간적인 오류만 다음 폴링으로 넘깁니다.
            if any(s in str(exc) for s in ('Execution context was destroyed',
                                           'Frame was detached', 'Cannot find context')):
                continue
            raise
        if not isinstance(state, dict):
            continue
        states.append((frame, state))
    return states


def check_states(page, states, article=False):
    from .collector import CollectorError
    for key, code, message in SESSION_CODES:
        if any(s.get(key) is True for _, s in states):
            raise CollectorError(code, message, {'detection': key})
    if any(urlsplit(f.url).hostname == 'nid.naver.com' for f in page.frames):
        raise CollectorError('LOGIN_REQUIRED', '로그인 페이지로 이동했습니다. 02_login_v5.bat를 실행하세요.')
    if article:
        for key, code, message in ARTICLE_CODES:
            if any(s.get(key) is True for _, s in states):
                raise CollectorError(code, message, {'detection': key})


def assert_session(page):
    check_states(page, read_states(page))


def assert_article_access(page):
    states = read_states(page)
    check_states(page, states, article=True)
    return states
