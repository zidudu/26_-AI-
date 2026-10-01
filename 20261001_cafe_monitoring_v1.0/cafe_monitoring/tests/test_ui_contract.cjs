/* Logic/transport contract checks only. This is not a browser/layout test. */
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const fixture=JSON.parse(fs.readFileSync(process.argv[2]||path.join(__dirname,'ui_fixture.json'),'utf8'));
const elements=new Map();
function classList(){const names=new Set();return {add(...xs){xs.forEach(x=>names.add(x));},remove(...xs){xs.forEach(x=>names.delete(x));},contains(x){return names.has(x);},toggle(x,force){const on=force===undefined?!names.has(x):!!force;on?names.add(x):names.delete(x);return on;}};}
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,type:'text',tagName:'INPUT',dataset:{},style:{},options:[],classList:classList(),setAttribute(){},removeAttribute(){},remove(){elements.delete(id);},focus(){},querySelector(){return element(id+':child')}});return elements.get(id);}
const calls=[];let rejectNext=false;
const sandbox={console,Blob,TextEncoder,Uint8Array,URL,Date,Map,Set,JSON,Math,Number,String,Boolean,Array,Object,RegExp,Promise,DOMException,AbortController,crypto:require('crypto').webcrypto,
 setTimeout,clearTimeout,setInterval(){},localStorage:{getItem(){return null},setItem(){}},
 document:{getElementById:element,querySelectorAll:()=>[],querySelector:()=>element('query'),activeElement:null},window:{V10_TOKEN:'test-csrf'},
 fetch:async(url,opt)=>{calls.push({url,opt});if(rejectNext){rejectNext=false;throw new Error('connection lost');}return {ok:true,json:async()=>({id:'run-1'})}}};
const ctx=vm.createContext(sandbox),run=s=>vm.runInContext(s,ctx);
vm.runInContext(fs.readFileSync(root+'/web/base.js','utf8'),ctx);
vm.runInContext(fs.readFileSync(root+'/web/integration.js','utf8').replace(/\nboot\(\);\s*$/,''),ctx);
ctx.fixture=fixture;
run('const pollServer=refresh;');
run('serverState=fixture;hydrateCatalog(fixture.settings);normalizeHistory();refresh=async()=>{};failure=()=>{};toast=()=>{};');
let checks=0;function check(s,expected){assert.deepStrictEqual(JSON.parse(JSON.stringify(run(s))),expected);checks++;}
check('[samplePosts.length,sampleHistory.length,statsRecords.length,historyRecords.length]',[0,0,0,0]);
check('cafes.length',9);
check("articleIdentity({cafe:'GN7',id:'42'})!==articleIdentity({cafe:'MX5',id:'42'})",true);
check("computePeriod({...fixture.settings,range:'이전 실행 이후'},new Date('2026-09-30T00:00:00Z')).start",null);
run("serverState.cursors=Object.fromEntries(cafes.map((c,i)=>[c.id,i===0?'2026-09-27T01:25:00+09:00':'2026-09-29T01:25:00+09:00']));");
check("computePeriod({...fixture.settings,range:'이전 실행 이후'},new Date('2026-09-30T00:00:00Z')).start.toISOString()",'2026-09-25T16:25:00.000Z');
for(const hours of [24,48]){
  check(`(()=>{const p=computePeriod({...fixture.settings,range:'최근 ${hours}시간'},new Date('2026-09-30T13:29:54.123+09:00'));return [p.end.toISOString(),(+p.end-p.start)/3600000,p.start.getUTCSeconds()];})()`,['2026-09-30T04:29:00.000Z',hours,0]);
}
run("serverState.cursors[cafes[0].id]='2026-09-27T01:25:37+09:00';");
check("(()=>{const p=computePeriod({...fixture.settings,range:'이전 실행 이후',overlap:'0'},new Date('2026-09-30T13:29:54.123+09:00'));return [p.start.toISOString(),p.end.toISOString()];})()",['2026-09-26T16:25:00.000Z','2026-09-30T04:29:00.000Z']);
for(const [state,ok,label] of [['unverified',false,'네이버 로그인 확인 필요'],['session_present',true,'로그인 세션 저장됨'],['verified',true,'카페 접근 확인'],['unverified',false,'네이버 로그인 확인 필요']]){
  run(`serverState.naverAuth={state:'${state}',checkedAt:'2026-09-30T13:29:54+09:00'};updateNaverLoginStatus();`);
  check("[$('naverLoginState').classList.contains('ok'),$('naverLoginState').classList.contains('bad'),$('naverLoginState').querySelector('.login-state-text').textContent.startsWith("+JSON.stringify(label)+") ]",[ok,!ok,true]);
}
check("[summaryCountForRun({summaryCafes:[{code:'A'},{code:'B'}],settings:{summarySlide:true}}),summaryCountForRun({summaryCafes:[{code:'A'}],settings:{summarySlide:false}})]",[3,1]);
check("mailStatusLabel('unknown').includes('자동 재발송 안 함')",true);
check("pptAvailable({ppt:'pretend.pptx',artifacts:[]})",false);
check("pptAvailable({ppt:'real.pptx',artifacts:[{kind:'ppt',id:'real',path:'real.pptx'}]})",true);
check("artifactFor({pptLinked:true,ppt:'new.pptx',artifacts:[{kind:'linked_ppt',id:'old',path:'old.pptx'},{kind:'linked_ppt',id:'new',path:'new.pptx'}]}).id",'new');
check("statsQuickRange('7d').to===dayKey(new Date())",true);
check("xlsxCell('=HYPERLINK(\"x\")',1,0).includes('inlineStr')",true);
check("runCompletionText({status:'실행 중',stage:'수집',posts:'0건',slides:'—',settings:{stepPpt:false},mailStatus:'disabled'})",'수집 · 0건 · 발송 안 함');
const html=fs.readFileSync(root+'/web/index.html','utf8');
assert.equal((html.match(/id="log"/g)||[]).length,1);
assert(html.indexOf('id="executionLogSection"')>html.indexOf('id="stateMeta"'));
assert(html.indexOf('id="executionLogSection"')<html.indexOf('id="stageStrip"'));checks++;
for(const [state,busy,text] of [['unchecked',false,'Codex 로그인'],['login_pending',true,'로그인 진행 중'],['connected',false,'다시 로그인'],['error',false,'Codex 로그인']]){
  run(`serverState.codexAuth={state:'${state}',busy:${busy},message:'상태 안내'};activeRun=null;updateCodexAuth();`);
  check("[$('codexGuideBtn').textContent,$('codexGuideBtn').disabled,$('codexCheckBtn').disabled,$('codexAuthState').textContent]",[text,busy,busy,'상태 안내']);
}
run("activeRun={id:'collecting'};updateCodexAuth();");
check("[$('codexGuideBtn').disabled,$('codexCheckBtn').disabled]",[true,false]);
run('activeRun=null;');
(async()=>{
  await run("codexRequest('login')");
  assert.equal(calls.at(-1).url,'/api/codex/login');
  assert.equal(calls.at(-1).opt.headers['X-V10-Token'],'test-csrf');
  assert.deepStrictEqual(JSON.parse(calls.at(-1).opt.body),{});checks++;
  await run("codexRequest('check')");assert.equal(calls.at(-1).url,'/api/codex/check');checks++;
  const normalFetch=ctx.fetch;
  const savedAuth={state:'session_present',checkedAt:'saved',browserOpen:false};
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:true,json:async()=>({needsConfirmation:true,naverAuth:savedAuth})};};
  await run('openNaverLogin()');
  check("[naverLoginPhase,$('naverLoginBackdrop').classList.contains('show'),$('naverLoginQuestion').hidden,$('naverLoginSteps').hidden,$('naverLoginDone').textContent]",['relogin',true,false,true,'다시 로그인']);
  assert.deepStrictEqual(JSON.parse(calls.at(-1).opt.body),{confirm:false});checks++;
  const beforeCancel=calls.length;await run('cancelNaverLogin()');
  assert.equal(calls.length,beforeCancel);checks++;
  check("[naverLoginPhase,serverState.naverAuth.state]",['closed','session_present']);
  await run('openNaverLogin()');
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:true,json:async()=>({needsConfirmation:false,naverAuth:{state:'unverified',browserOpen:true}})};};
  await run('finishNaverLogin()');
  assert.deepStrictEqual(JSON.parse(calls.at(-1).opt.body),{confirm:true});checks++;
  check("[naverLoginPhase,$('naverLoginSteps').hidden,$('naverLoginDone').textContent]",['instructions',false,'로그인했습니다']);
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:false,json:async()=>({message:'전용 Chrome 창을 먼저 닫아 주세요.'})};};
  await run('finishNaverLogin()');
  check("[$('naverLoginBackdrop').classList.contains('show'),$('naverLoginHelp').textContent,serverState.naverAuth.state]",[true,'전용 Chrome 창을 먼저 닫아 주세요.','unverified']);
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:true,json:async()=>({message:'확인 완료',naverAuth:savedAuth})};};
  await run('finishNaverLogin()');
  assert.equal(calls.at(-1).url,'/api/login/confirm');checks++;
  check("[naverLoginPhase,$('naverLoginBackdrop').classList.contains('show'),serverState.naverAuth.state]",['closed',false,'session_present']);
  run("naverLoginPhase='instructions';$('naverLoginBackdrop').classList.add('show');");
  await run('cancelNaverLogin()');assert.equal(calls.at(-1).url,'/api/login/cancel');checks++;
  ctx.fetch=normalFetch;
  await run('runAll(fixture.settings)');
  const created=calls.find(c=>c.url==='/api/runs');assert(created);checks++;
  assert.equal(created.opt.headers['X-V10-Token'],'test-csrf');checks++;
  assert.equal(JSON.parse(created.opt.body).settings.evidence,true);checks++;
  rejectNext=true;await run('runAll(fixture.settings)');
  const failedId=JSON.parse(calls.at(-1).opt.body).requestId;
  await run('runAll(fixture.settings)');assert.equal(JSON.parse(calls.at(-1).opt.body).requestId,failedId);checks++;
  run("statsRecords.push({date:'2026-09-29',cafe:'GN7',cafeId:'cafe:bestcm',id:'7',keys:['후방','제동'],title:'<원문>',raw:'A & B',firstCollectedAt:'2026-09-29T10:00:00+09:00',lastCheckedAt:'2026-09-29T10:00:00+09:00',status:'미검토'});refreshCatalogFromSources();Object.assign(statsState,{from:'2026-09-01',to:'2026-09-30'});");
  check('statsKnownRecords().length',1);
  check('statsKeywordRows().reduce((n,k)=>n+k.count,0)',2);
  const book=run("statsExportWorkbook('full')");assert.equal(book.count,1);checks++;
  run("booted=true;activeRun={id:'live-run'};activeLogId=null;lastLogId=0;");
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:true,json:async()=>url.startsWith('/api/state')?{unchanged:true}:{items:[{id:17,at:'2026-09-30T13:50:00+09:00',message:'[GN7] 키워드 검색 중 <원문>'}]}};};
  await run('pollServer()');
  check("[$('log').textContent,lastLogId]",['[13:50:00] [GN7] 키워드 검색 중 <원문>\n',17]);
  ctx.fetch=async(url,opt)=>{calls.push({url,opt});return {ok:true,json:async()=>url.startsWith('/api/state')?{unchanged:true,codexAuth:{state:'connected',message:'ChatGPT 로그인 확인 완료',busy:false}}:{items:[]}};};
  await run('pollServer()');
  assert(calls.at(-1).url.endsWith('/logs?after=17'));checks++;
  check("$('log').textContent.split('키워드 검색 중').length",2);
  check("[$('codexAuthState').dataset.state,$('codexAuthState').textContent]",['connected','ChatGPT 로그인 확인 완료']);
  console.log(JSON.stringify({checks,passed:checks,scope:'JavaScript logic + API contract; no browser rendering'}));
})().catch(e=>{console.error(e);process.exitCode=1});
