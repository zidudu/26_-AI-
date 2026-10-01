// 게시글 프레임의 표시된 숫자만 읽습니다. 누락/충돌은 0이 아닙니다.
() => {
    const visible = el => {
        if (!el || !el.getClientRects().length) return false;
        const style = getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    };
    const parse = (text, kind) => {
        const re = kind === 'comment_count'
            ? /^(?:댓글\s*)?(\d+|\d{1,3}(?:,\d{3})+)(?:\s*개)?$/
            : /^(?:조회(?:수)?\s*)?(\d+|\d{1,3}(?:,\d{3})+)(?:\s*회)?$/;
        const match = text.trim().match(re);
        if (!match) return null;
        const n = Number(match[1].replace(/,/g, ''));
        return Number.isSafeInteger(n) && n >= 0 ? n : null;
    };
    const read = (kind, selectors) => {
        const attempts = [];
        for (const selector of selectors) {
            const nodes = [...document.querySelectorAll(selector)];
            const shown = nodes.filter(visible);
            const texts = shown.map(el => (el.innerText || '').trim());
            const values = texts.map(t => parse(t, kind));
            attempts.push({selector, matched: nodes.length, visible: shown.length,
                texts: texts.slice(0, 10).map(t => t.slice(0, 100))});
            if (!shown.length) continue;
            const unique = [...new Set(values)];
            const status = unique.length > 1 ? 'conflict'
                : unique[0] === null ? 'invalid' : 'observed';
            return {value: status === 'observed' ? unique[0] : null,
                raw: texts[0].slice(0, 100), status, selector, attempts};
        }
        return {value: null, raw: null, status: 'missing', selector: null, attempts};
    };
    return {
        comment_count: read('comment_count', [
            '.article_header a.button_comment[data-nlog-area="content.comment_count"] .num',
            '.article_header .button_comment .num',
            '.button_comment .num'
        ]),
        view_count: read('view_count', ['.article_header .article_info .count', '.article_info .count'])
    };
}
