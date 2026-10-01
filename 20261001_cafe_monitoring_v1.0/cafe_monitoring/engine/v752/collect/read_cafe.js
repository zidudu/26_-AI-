// 배너 이미지/작성자 차종을 카페 이름으로 추측하지 않습니다.
() => {
    const selectors = ['#cafe-info-data .cafe-name', '#cafe-info-data .cafe_name',
        '#cafeName', '.cafe_name', '.cafeName'];
    const attempts = [];
    for (const selector of selectors) {
        const nodes = [...document.querySelectorAll(selector)];
        const shown = nodes.filter(el => el.getClientRects().length &&
            getComputedStyle(el).visibility !== 'hidden');
        const texts = [...new Set(shown.map(el => (el.innerText || '').trim()).filter(Boolean))];
        attempts.push({selector, matched: nodes.length, texts: texts.slice(0, 5).map(t => t.slice(0, 200))});
        if (!texts.length) continue;
        if (texts.length !== 1 || texts[0].length > 200 || ['NAVER', '네이버', '네이버 카페', '카페홈'].includes(texts[0]))
            return {value: null, status: 'conflict_or_generic', selector, attempts};
        return {value: texts[0], status: 'observed', selector, attempts};
    }
    return {value: null, status: 'missing', selector: null, attempts};
}
