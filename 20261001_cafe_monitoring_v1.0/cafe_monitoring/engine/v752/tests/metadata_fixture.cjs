// 합성 DOM: 브라우저 실행 또는 네이버 접속 시험이 아닙니다.
const fs=require('fs'),vm=require('vm');
const {payload,script}=JSON.parse(fs.readFileSync(0,'utf8'));
function nodes(items=[]) {return items.map(i=>{
    const x=typeof i==='string'?{text:i,visible:true}:i;
    return {innerText:x.text,hidden:x.visible===false,
        getClientRects:()=>x.visible===false?[]:[{}]};
});}
const top=nodes(payload.top),bottom=nodes(payload.bottom),views=nodes(payload.views),cafe=nodes(payload.cafe);
const document={querySelectorAll:s=>{
    if(s.includes('article_info'))return views;
    if(s.includes('button_comment'))return s.includes('article_header')?top:[...top,...bottom];
    if(s.includes('cafe'))return cafe;
    return [];
}};
const context={document,getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible'})};
process.stdout.write(JSON.stringify(vm.runInNewContext('('+script+')()',context)));
