// Playwright가 지정한 본문 요소만 읽습니다. 댓글이나 메뉴는 포함하지 않습니다.
(bodyElement) => {
    const doc = bodyElement.ownerDocument;
    const visible = el => el && el.getClientRects().length > 0;
    // 미디어 모듈의 재생 UI만 제외합니다. se-video 전체를 지우지 않아
    // 작성자가 입력한 영상 설명/캡션은 남깁니다. 실제 DOM/스크린샷은 변경하지 않습니다.
    const playerUI = '.se-module-video, .prismplayer-area, .pzp, video, iframe[src*="video"], .se-video .se-media-meta-info-title';
    const readAuthorText = el => {
        if (el.matches(playerUI)) return '';
        if (!el.querySelector(playerUI)) return el.innerText || '';
        let text = '';
        for (const child of el.childNodes) {
            if (child.nodeType === 3) {
                const value = child.textContent.replace(/\s+/g, ' ');
                if (value.trim()) text += value;
                else if (text && !text.endsWith('\n')) text += ' ';
                continue;
            }
            if (child.nodeType !== 1 || child.matches('script, style, template')
                    || child.matches(playerUI)) continue;
            if (child.tagName === 'BR') { text += '\n'; continue; }
            const style = doc.defaultView.getComputedStyle(child);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const value = readAuthorText(child);
            if (!value.trim()) continue;
            const block = !['inline', 'inline-block', 'contents'].includes(style.display);
            if (block) text = text.trimEnd() + (text.trim() ? '\n\n' : '') + value + '\n\n';
            else text += value;
        }
        return text.trim();
    };
    const images = [...bodyElement.querySelectorAll('img')].filter(img => visible(img)
        && img.getBoundingClientRect().width >= 40 && img.getBoundingClientRect().height >= 40);
    const firstText = selectors => {
        for (const selector of selectors) {
            const el = doc.querySelector(selector);
            if (visible(el) && el.innerText?.trim()) return el.innerText.trim();
        }
        return null;
    };
    const count = raw => {
        if (!raw) return null;
        const match = raw.replace(/,/g, '').match(/(?:^|\s)(\d+)(?:\s|$|개|건)/);
        return match ? Number(match[1]) : null;
    };
    const viewRaw = firstText(['.article_info .count']);
    const commentRaw = firstText(['.button_comment .num', '.CommentBox .comment_title .num']);
    // 로딩 후 se-video 안에 video 태그가 생겨도 같은 영상을 두 번 세지 않습니다.
    const videos = new Set([...bodyElement.querySelectorAll('video, iframe[src*="video"], .se-video')]
        .map(el => el.closest('.se-video') || el));
    return {
        title: doc.querySelector('h3.title_text')?.innerText ?? null,
        date: doc.querySelector('.article_info .date')?.innerText ?? null,
        body: readAuthorText(bodyElement),
        document_url: doc.URL,
        media: {image_count: images.length,
            loaded_image_count: images.filter(img => img.complete && img.naturalWidth > 0).length,
            video_count: videos.size},
        metadata: {cafe_name: firstText(['.cafe_name', '.cafeName']),
            view_count: count(viewRaw), view_count_raw: viewRaw,
            comment_count: count(commentRaw), comment_count_raw: commentRaw}
    };
}
