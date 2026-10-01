() => {
    // 화면에 렌더링된 검색 조건과 목록만 읽습니다. 로그인 정보나 앱 내부 상태를 읽지 않습니다.
    const visible = el => !!el && el.getClientRects().length > 0;
    const text = el => el?.innerText?.trim() ?? '';
    const boxes = [...document.querySelectorAll('.SearchBox')].filter(visible);
    const top = boxes[0];
    const buttons = [...document.querySelectorAll('.FormSelectButton > button')].filter(visible);
    const rows = [...document.querySelectorAll('table.article-table tbody tr')].filter(visible);
    return {
        document_url: document.URL,
        query_values: boxes.map(box => box.querySelector('input[placeholder="검색어를 입력해주세요"]')?.value ?? ''),
        scope: text(top?.querySelector('.target .FormSelectButton > button')),
        period: text(top?.querySelector('.period .FormSelectButton > button')),
        board: text(top?.querySelector('.menu .FormSelectButton > button')),
        sort: text(buttons.find(el => /^(최신순|정확도순)$/.test(text(el)))),
        current_page: text(document.querySelector('button.number[aria-pressed="true"]')),
        pagination_present: visible(document.querySelector('.Pagination')),
        page_numbers: [...document.querySelectorAll('.Pagination button.number')]
            .filter(el => visible(el) && !el.disabled).map(el => Number(text(el))),
        next_group: [...document.querySelectorAll('.Pagination button.type_next')]
            .some(el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true'),
        empty: [...document.querySelectorAll('.article-board .nodata')]
            .some(el => visible(el) && text(el) === '등록된 게시글이 없습니다.'),
        rows: rows.map(row => {
            const cells = [...row.querySelectorAll('td')];
            const link = row.querySelector('a.article');
            return {number: text(cells[0]), title: text(link),
                href: link?.href ?? '', date: text(cells[3])};
        }).filter(row => row.href)
    };
}
