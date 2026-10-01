// 합성 DOM. 실제 브라우저 호출 없이 배포할 판정식을 그대로 실행합니다.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../page_status.js'),'utf8');
function scan(text, options={}) {
    const el=(innerText,visible=true)=>({innerText,getClientRects:()=>visible?[{}]:[],querySelectorAll:()=>[]});
    const selectors=options.article?{'h3.title_text':el('제목'),'.article_info .date':el('2026.09.11. 12:00'),'.se-main-container':el(options.body??text)}:{};
    const document={body:{innerText:text},querySelector:s=>selectors[s]??null,
        querySelectorAll:s=>s in selectors?[selectors[s]]:s.startsWith('table')?(options.list?[el('결과')]:[]):
            s.startsWith('img[src')?(options.captcha?[el('',options.captcha==='visible')]:[]):[]};
    return vm.runInNewContext('('+source+')()', {document});
}
test('사용자가 제공한 등급 제한 문구를 공백·줄바꿈과 무관하게 감지',()=>{
    const s=scan('싼타페 등급이 되시면 읽기가 가능한 게시판 입니다.\n현재 회원은 새싹멤버 등급이시며');
    assert.equal(s.grade,true); assert.equal(s.ready,false); assert.equal(s.login,false);
});
test('본문에서 제한 안내를 인용해도 정상 게시글로 판정',()=>{
    const s=scan('싼타페 등급이 되시면 읽기가 가능한 게시판 입니다. 로그인이 필요합니다. 삭제된 게시글입니다. 자동입력방지문자', {article:true});
    assert.equal(s.ready,true);
    for(const key of ['grade','login','deleted','captcha','blocked','denied','membership']) assert.equal(s[key],false,key);
});
test('검색 결과 제목에 제한 문구가 있어도 검색을 중단하지 않음',()=>{
    const s=scan('로그인이 필요합니다. 등급이 되시면 읽기가 가능한 게시판', {list:true});
    assert.equal(s.login,false); assert.equal(s.grade,false);
});
test('빈 화면은 제한 원인을 추측하지 않음',()=>{
    const s=scan('');
    for(const key of ['ready','grade','login','deleted','captcha','blocked','denied','membership']) assert.equal(s[key],false,key);
});
test('가입·삭제·읽기 권한 안내를 구분',()=>{
    assert.equal(scan('카페 가입 후 글을 읽을 수 있습니다.').membership,true);
    assert.equal(scan('삭제되었거나 존재하지 않는 게시글입니다.').deleted,true);
    const denied=scan('게시글을 읽을 권한이 없습니다.');
    assert.equal(denied.denied,true); assert.equal(denied.grade,false);
});
test('로그인·추가 인증·요청 제한 안내 감지',()=>{
    assert.equal(scan('로그인이 필요합니다.').login,true);
    assert.equal(scan('자동입력 방지문자를 입력하세요.').captcha,true);
    assert.equal(scan('요청이 너무 많습니다.').blocked,true);
});
test('보이지 않는 CAPTCHA 요소는 무시',()=>{
    assert.equal(scan('본문', {article:true,captcha:'hidden'}).captcha,false);
});
test('보이는 CAPTCHA는 게시글이 있어도 인증 필요',()=>{
    assert.equal(scan('본문', {article:true,captcha:'visible'}).captcha,true);
});
test('공백 및 제로폭 문자만 있는 본문은 준비 완료가 아님',()=>{
    const s=scan('글', {article:true,body:' \n\u200b '}); assert.equal(s.ready,false);
});
