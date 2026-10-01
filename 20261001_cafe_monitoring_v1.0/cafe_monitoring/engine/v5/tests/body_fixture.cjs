// 사용자 보고의 두 본문 구조를 재현한 합성 DOM입니다. 네이버에 접속하지 않습니다.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const options = input.options;
const doc = {URL: 'https://cafe.naver.com/f-e/cafes/20179506/articles/5497597'};
function el(text, visible = true) {
    return {innerText: text, ownerDocument: doc, getClientRects: () => visible ? [{}] : [],
        querySelectorAll: () => [], querySelector: () => null, matches: () => false};
}
const elements = {
    'h3.title_text': [el(options.title ?? '합성 테스트 제목')],
    '.article_info .date': [el('2026.09.11. 12:00')],
    '.se-main-container': (options.modern ?? []).map(x => el(x.text, x.visible !== false)),
    '.article_viewer .ContentRenderer': (options.legacy ?? []).map(x => el(x.text, x.visible !== false)),
    '.ContentRenderer': (options.outside ?? []).map(x => el(x.text, x.visible !== false))
};
doc.body = el(options.pageText ?? '카페 메뉴 / 게시글 / 댓글');
doc.querySelectorAll = selector => elements[selector] ?? [];
doc.querySelector = selector => doc.querySelectorAll(selector)[0] ?? null;
const fn = vm.runInNewContext('(' + input.script + ')', {document: doc});
process.stdout.write(JSON.stringify(fn(input.argument)));
