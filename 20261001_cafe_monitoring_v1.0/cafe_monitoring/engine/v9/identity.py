"""Read first-party cafe identity from the navigated cafe home, not article ads."""
import re
from urllib.parse import urlparse, parse_qs
from v8.configuration import V8Error

SCRIPT = """() => ({
  url: location.href,
  globals: [window.g_sClubId, window.g_nClubId, window.g_sClubid]
    .filter(x => x !== undefined && x !== null).map(String),
  slug: typeof window.g_sCafeUrl === 'string' ? window.g_sCafeUrl : null,
  frames: Array.from(document.querySelectorAll('iframe#cafe_main,iframe[name="cafe_main"]'))
    .map(x => x.src),
  canonical: Array.from(document.querySelectorAll('link[rel="canonical"],meta[property="og:url"]'))
    .map(x => x.href || x.content)
})"""


def id_from_url(value):
    u=urlparse(value or '')
    if u.scheme not in ('http','https') or u.hostname != 'cafe.naver.com':
        return None
    match=re.search(r'^/(?:f-e|ca-fe)/cafes/([1-9][0-9]*)(?:/|$)',u.path)
    if match:
        return match.group(1)
    if u.path.lower() in ('/articlelist.nhn','/cafemain.nhn'):
        q=parse_qs(u.query)
        values=q.get('search.clubid') or q.get('clubid') or q.get('clubId') or []
        if len(values)==1 and re.fullmatch(r'[1-9][0-9]*', values[0]):
            return values[0]
    return None


def normalize_home_slug(value):
    """Accept a bare cafe slug or a URL for exactly that first-party cafe home."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith('//'):
        value = 'https:' + value
    if re.match(r'^https?://', value, re.I):
        try:
            parsed = urlparse(value)
            if (parsed.hostname != 'cafe.naver.com' or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 80, 443)):
                return None
            value = parsed.path
        except ValueError:
            return None
    value = value.strip('/')
    return value.lower() if re.fullmatch(r'[A-Za-z0-9_-]+', value) else None


def is_service_root(value):
    """Observed g_sCafeUrl may be the shared origin, not a cafe identifier."""
    if not isinstance(value, str):
        return False
    value=value.strip()
    if value.startswith('//'):
        value='https:'+value
    try:
        u=urlparse(value)
        return (u.scheme in ('http','https') and u.hostname=='cafe.naver.com'
                and u.username is None and u.password is None
                and u.port in (None,443 if u.scheme=='https' else 80)
                and u.path in ('','/') and not u.query and not u.fragment)
    except ValueError:
        return False


def extract(snapshot, cafe):
    u=urlparse(snapshot.get('url',''))
    if u.hostname != 'cafe.naver.com' or u.scheme != 'https':
        raise V8Error('CAFE_LOGIN_OR_REDIRECT','카페 홈을 열지 못했습니다. 로그인·접근 권한을 확인하세요.')
    raw_slug=snapshot.get('slug')
    service_root=is_service_root(raw_slug)
    actual_slug=normalize_home_slug(raw_slug)
    if raw_slug and not service_root and actual_slug != cafe['slug'].lower():
        code = 'CAFE_SLUG_MISMATCH' if actual_slug else 'CAFE_SLUG_UNVERIFIED'
        raise V8Error(code, f"요청 카페 {cafe['slug']} / 홈 식별값 {str(raw_slug)[:240]!r} / "
                      f"해석한 슬러그 {actual_slug!r}. 카페 일치를 확인하지 못했습니다.")
    if u.path.rstrip('/') != '/'+cafe['slug']:
        # A shared origin alone cannot bind an unknown numeric redirect to a slug.
        redirected_id=id_from_url(snapshot['url'])
        if redirected_id is None or (not cafe.get('club_id') and actual_slug != cafe['slug'].lower()):
            raise V8Error('CAFE_HOME_UNVERIFIED','요청한 카페와 현재 홈 주소의 연결을 확인하지 못했습니다.')
    evidence=[]
    for value in snapshot.get('globals',[]):
        if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,11}',value):
            evidence.append(('home_global',value))
    for kind in ('frames','canonical'):
        for value in snapshot.get(kind,[]):
            cid=id_from_url(value)
            if cid: evidence.append((kind,cid))
    cid=id_from_url(snapshot['url'])
    if cid: evidence.append(('home_redirect',cid))
    ids={v for _,v in evidence}
    if not ids:
        raise V8Error('CAFE_ID_UNRESOLVED','카페 홈에서 clubId를 확인하지 못했습니다. 31번 연결 결과를 확인하세요.')
    if len(ids)!=1:
        raise V8Error('CAFE_ID_AMBIGUOUS','카페 홈 식별자가 서로 다릅니다. 임의로 선택하지 않았습니다.')
    cid=ids.pop()
    if cafe.get('club_id') and cafe['club_id']!=cid:
        raise V8Error('CAFE_ID_MISMATCH',f"설정 ID {cafe['club_id']}와 홈 ID {cid}가 다릅니다.")
    return cid, {'url':snapshot['url'],'methods':sorted({k for k,_ in evidence}),
                 'home_slug_raw':raw_slug,'home_slug_normalized':actual_slug,
                 'home_value_kind':'service_root' if service_root else 'cafe_slug_or_absent',
                 'identity_binding':'requested_slug_url' if u.path.rstrip('/') == '/'+cafe['slug'] else 'numeric_redirect_checked'}


def resolve(page, cafe):
    response=page.goto('https://cafe.naver.com/'+cafe['slug'],wait_until='domcontentloaded',timeout=45000)
    if response and response.status in (403,429):
        raise V8Error('REQUEST_BLOCKED',f'카페 접근 제한 응답: HTTP {response.status}. 웹 요청을 중단합니다.')
    if response and response.status>=400:
        raise V8Error('CAFE_HOME_HTTP_ERROR',f'카페 홈 응답 오류: HTTP {response.status}')
    last=None
    for attempt in range(5):
        snapshot=page.evaluate(SCRIPT)
        try:
            return extract(snapshot,cafe)
        except V8Error as exc:
            exc.identity_snapshot = snapshot
            if exc.code!='CAFE_ID_UNRESOLVED': raise
            last=exc
        if attempt<4: page.wait_for_timeout(700)
    raise last


async def resolve_async(page,cafe):
    from v9.collection_control import check_stop
    check_stop()
    response=await page.goto('https://cafe.naver.com/'+cafe['slug'],wait_until='domcontentloaded',timeout=45000)
    if response and response.status in (403,429):
        raise V8Error('REQUEST_BLOCKED',f'카페 접근 제한 응답: HTTP {response.status}. 웹 요청을 중단합니다.')
    if response and response.status>=400:
        raise V8Error('CAFE_HOME_HTTP_ERROR',f'카페 홈 응답 오류: HTTP {response.status}')
    last=None
    for attempt in range(5):
        check_stop()
        snapshot=await page.evaluate(SCRIPT)
        try:
            return extract(snapshot,cafe)
        except V8Error as exc:
            exc.identity_snapshot=snapshot
            if exc.code!='CAFE_ID_UNRESOLVED': raise
            last=exc
        if attempt<4: await page.wait_for_timeout(700)
    raise last
