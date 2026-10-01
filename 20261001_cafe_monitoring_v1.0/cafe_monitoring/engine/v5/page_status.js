() => {
    // 보이는 DOM만 확인합니다. 쿠키, 입력값, 앱 내부 상태는 읽지 않습니다.
    const visible = el => !!el && el.getClientRects().length > 0;
    const title = document.querySelector('h3.title_text');
    const date = document.querySelector('.article_info .date');
    // 기존 에디터를 우선합니다. 비어 있거나 숨겨진 요소는 준비된 본문으로 보지 않습니다.
    // ContentRenderer는 게시글 영역 안에서만 찾아 댓글/메뉴를 제외합니다.
    let body = null, body_selector = null, body_index = null;
    for (const selector of ['.se-main-container', '.article_viewer .ContentRenderer']) {
        const candidates = [...document.querySelectorAll(selector)];
        const index = candidates.findIndex(el => visible(el) && (/[^\s\u200b]/.test(el.innerText || '')
            || [...el.querySelectorAll('img')].some(img => visible(img) && img.getBoundingClientRect().width >= 40 && img.getBoundingClientRect().height >= 40)));
        if (index >= 0) {
            body = candidates[index];
            body_selector = selector;
            body_index = index;
            break;
        }
    }
    const article = visible(title) && visible(body);
    const list = [...document.querySelectorAll('table.article-table tbody tr')].some(visible);
    const hasContent = article || list;
    const text = hasContent ? '' : (document.body?.innerText || '').replace(/\s+/g, '');
    const captchaElement = [...document.querySelectorAll('img[src*="captcha"], iframe[src*="captcha"], input[name*="captcha"], #captcha')].some(visible);
    const captcha = captchaElement || /자동입력방지문자|보안확인을완료|사람인지확인|verifyyouarehuman/i.test(text);
    const blocked = /비정상적인접근|비정상적인요청|요청이너무많|잠시후다시시도해/.test(text);
    const login = /로그인이필요합니다|로그인후이용|로그인후확인|로그인해주세요/.test(text);
    // 사용자 화면: "싼타페 등급이 되시면 읽기가 가능한 게시판 입니다."
    const grade = /등급이되시면읽기가가능한게시판/.test(text)
        || /등급이상.{0,25}(?:읽기|열람|이용)(?:가)?가능/.test(text);
    const membership = /카페가입후(?:에)?(?:글을)?(?:읽|열람|이용)|멤버만(?:읽|열람|이용)할수있/.test(text);
    const deleted = /삭제된게시글입니다|삭제되었거나존재하지않는게시글|존재하지않는게시글입니다/.test(text);
    const denied = /게시글을읽을권한이없|이게시글에접근할수없|(?:읽기|열람)권한이없는게시판/.test(text);
    const bodyText = visible(body) ? (body.innerText || '') : '';
    return {captcha, blocked, login, grade, membership, deleted, denied,
        title_visible: visible(title), date_visible: visible(date), body_visible: visible(body),
        body_char_count: bodyText.length, body_selector, body_index,
        ready: article && visible(date) && !!title.innerText?.trim()
            && !!date.innerText?.trim()};
}
