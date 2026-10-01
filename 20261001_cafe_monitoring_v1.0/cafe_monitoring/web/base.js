
"use strict";
const INITIAL_CAFES=[
  {code:'GN7',name:'그랜저 GN7 · bestcm',url:'https://cafe.naver.com/bestcm'},
  {code:'MX5',name:'싼타페 MX5 · iroid',url:'https://cafe.naver.com/iroid'},
  {code:'ME',name:'아이오닉 · cafeclip',url:'https://cafe.naver.com/cafeclip'},
  {code:'제클',name:'제네시스 전차종 · newgenesisdh',url:'https://cafe.naver.com/newgenesisdh'},
  {code:'RG3',name:'G80 · fam100',url:'https://cafe.naver.com/fam100'},
  {code:'JX1',name:'GV80 · gruu',url:'https://cafe.naver.com/gruu'},
  {code:'GL3',name:'K8 · ite',url:'https://cafe.naver.com/ite'},
  {code:'KA4',name:'카니발 · story77',url:'https://cafe.naver.com/story77'},
  {code:'MQ4',name:'쏘렌토 · englishenglish',url:'https://cafe.naver.com/englishenglish'}
];
const cafes=[];
const keywordCatalog=[];
let selectedCafeIds=new Set();
const defaultKeywords=['SCC','전방카메라','경고등','후방','우측','제동','쏠림','전방 카메라','운전자 보조','크루즈','좌측','긴급','자율 주행','레이더','전방','컨트롤','계기판','보조'];
const samplePosts=[];
const sampleHistory=[];

// No network requests, credentials, crawler, scheduler or email transport are implemented here.
// UI state is kept separate from immutable per-run snapshots and filtered history selection.
const $ = id => document.getElementById(id);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const copyObject = obj => JSON.parse(JSON.stringify(obj));
const SETTINGS_KEY = 'v10_monitor_settings';
const HISTORY_KEY = 'v10_ui_history_v3';
const THEME_KEY = 'v10_theme';
const SETTINGS_IDS = ['range','periodStart','periodEnd','periodDedupeEnabled','parallel','overlap','scheduleEnabled','scheduleTime','weekdaysOnly','provider','ppi','summarySlide','rawNote','sendMail','mailTo','mailCc','mailSubject','mailScope','outputPath','stepCollect','stepAnalyze','stepPpt','sourceRun'];
const DAY = 86400000, KST = 9 * 3600000;
let keywords = [...defaultKeywords];
let posts = [];
let selectedPostId = null, running = false, activeRun = null, lastDisplayedRun = null;
let runAbort = null, pendingKeywordAction = null, keywordFocus = null;
let pendingExecutionAction = null, executionConfirmFocus = null;
let tooltipOwner = null, tooltipTimer = null, toastTimer = null, undoAction = null;
let dirty = false, storageAvailable = true;
let activePrimaryView='main';
const linkedPptFiles = new Map();
const pptPreviewSelections = new Map();
const historyState = {query:'', date:'', selectedId:null, year:2026, month:8};
let historyRecords = [];

function readStored(key,fallback){
  let raw;
  try{raw=localStorage.getItem(key);}catch{storageAvailable=false;return fallback;}
  if(raw===null)return fallback;
  try{
    const parsed=JSON.parse(raw);
    if(key===HISTORY_KEY&&!Array.isArray(parsed))throw new Error('history shape');
    if(key===SETTINGS_KEY&&(!parsed||typeof parsed!=='object'||Array.isArray(parsed)))throw new Error('settings shape');
    return parsed;
  }catch{corruptStorage.set(key,raw);return fallback;}
}

function writeStored(key,value){
  try{
    if(corruptStorage.has(key)){
      // Keep the original bytes before replacing corrupt input. If this cannot
      // be done (e.g. quota), fail the save rather than discarding old data.
      localStorage.setItem(key+':recovery:'+Date.now(),corruptStorage.get(key));
      corruptStorage.delete(key);
    }
    localStorage.setItem(key,JSON.stringify(value));storageAvailable=true;return true;
  }catch{
    storageAvailable=false;
    if(!reportedStorageErrors.has(key)){reportedStorageErrors.add(key);toast('저장하지 못했습니다. 브라우저 저장 공간·권한을 확인하고, 이력 JSON으로 별도 보관하세요.');}
    return false;
  }
}

function applyTheme(theme, persist=true){
  const dark=theme==='dark';document.body.classList.toggle('theme-dark',dark);
  $$('.theme-toggle').forEach(btn=>{btn.textContent=dark?'라이트 모드':'다크 모드';btn.setAttribute('aria-pressed',String(dark));btn.title=dark?'라이트 모드로 전환':'다크 모드로 전환';});
  if(persist){try{localStorage.setItem(THEME_KEY,dark?'dark':'light');}catch{storageAvailable=false;}}
}
function initTheme(){let theme='light';try{theme=localStorage.getItem(THEME_KEY)||'light';}catch{storageAvailable=false;}applyTheme(theme,false);}
function toggleTheme(){applyTheme(document.body.classList.contains('theme-dark')?'light':'dark');}
function dateValue(value){
  if(value instanceof Date)return Number.isFinite(+value)?new Date(+value):null;
  if(typeof value!=='string'||!value.trim())return null;
  const text=value.trim();
  const zoned=text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?(Z|[+-]\d{2}:\d{2})$/);
  if(zoned){
    const date=strictUtcParts(+zoned[1],+zoned[2],+zoned[3],+zoned[4],+zoned[5],+(zoned[6]||0),+(zoned[7]||'0').padEnd(3,'0'));
    if(!date)return null;let offset=0;
    if(zoned[8]!=='Z'){const t=zoned[8].slice(1).split(':').map(Number);if(t[0]>23||t[1]>59)return null;offset=(t[0]*60+t[1])*60000*(zoned[8][0]==='-'?-1:1);}
    return new Date(+date-offset);
  }
  const local=text.match(/^(\d{4})[.-]\s*(\d{1,2})[.-]\s*(\d{1,2})\.?[ T]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
  if(local){const date=strictUtcParts(+local[1],+local[2],+local[3],+local[4],+local[5],+(local[6]||0),+(local[7]||'0').padEnd(3,'0'));return date?new Date(+date-KST):null;}
  if(isDateKey(text))return new Date(+new Date(text+'T00:00:00Z')-KST);
  return null;
}

function parseKstLocalDateTime(value){
  const m=String(value||'').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
  if(!m)return null;const date=strictUtcParts(+m[1],+m[2],+m[3],+m[4],+m[5],+(m[6]||0),+(m[7]||'0').padEnd(3,'0'));
  return date?new Date(+date-KST):null;
}

const pad = n => String(n).padStart(2,'0');
function parts(date){
  const d = new Date(+date + KST);
  return {y:d.getUTCFullYear(),m:d.getUTCMonth()+1,d:d.getUTCDate(),h:d.getUTCHours(),min:d.getUTCMinutes(),s:d.getUTCSeconds(),weekday:d.getUTCDay()};
}
function dayKey(value){const d=dateValue(value);if(!d)return '';const p=parts(d);return `${p.y}-${pad(p.m)}-${pad(p.d)}`;}
function formatTime(value, seconds=false){const d=dateValue(value);if(!d)return '—';const p=parts(d);return `${p.y}.${pad(p.m)}.${pad(p.d)} ${pad(p.h)}:${pad(p.min)}${seconds?':'+pad(p.s):''}`;}
function shortTime(value){const d=dateValue(value);if(!d)return '—';const p=parts(d),w=['일','월','화','수','목','금','토'][p.weekday];return `<span class="year-inline">${p.y}년</span>${p.m}월 ${p.d}일(${w}) ${p.h}시 ${pad(p.min)}분`;}
function nextPeriodMeta(period){if(period?.start&&period?.end)return `수집 대상 ${periodText(period.start)} → ${periodText(period.end)}`;return period?.note||'수집 대상 기간을 계산할 수 없습니다.';}
function contextTime(value){const d=dateValue(value);if(!d)return '';return parts(d).y+'년';} 
function duration(ms){const s=Math.floor(Math.max(0,ms)/1000);return `${pad(Math.floor(s/3600))}:${pad(Math.floor(s/60)%60)}:${pad(s%60)}`;}
function clockTime(value){const p=parts(dateValue(value)||new Date());return `${pad(p.h)}:${pad(p.min)}:${pad(p.s)}`;}
function makeRunId(){const p=parts(new Date());const random=globalThis.crypto?.randomUUID?.().slice(0,6)||Math.random().toString(16).slice(2,8);return `demo_${p.y}${pad(p.m)}${pad(p.d)}_${pad(p.h)}${pad(p.min)}${pad(p.s)}_${random}`;}
function currentTimeText(value=new Date()){const p=parts(value),w=['일','월','화','수','목','금','토'][p.weekday];return `${p.y}년 ${p.m}월 ${p.d}일(${w}) ${p.h}시 ${pad(p.min)}분 ${pad(p.s)}초`;}
function updateCurrentClock(){const root=$('currentClock');if(root){const strong=root.querySelector('strong');if(strong)strong.textContent=currentTimeText();}}
function updateNaverLoginStatus(){
  const node=$('naverLoginState'),text=node?.querySelector('.login-state-text');if(!node||!text)return;
  const verified=naverAuthState.state==='verified'&&dateValue(naverAuthState.checkedAt);
  node.classList.toggle('ok',!!verified);node.classList.toggle('bad',!verified);
  text.textContent=verified?`네이버 로그인 확인됨 · ${clockTime(naverAuthState.checkedAt)}`:naverAuthState.state==='expired'?'네이버 재로그인 필요':'네이버 로그인 확인 필요 · 미연결';
  node.title=verified?`실제 검증 결과의 마지막 확인 시각: ${formatTime(naverAuthState.checkedAt,true)}. 이후 만료될 수 있습니다.`:'이 HTML은 실제 네이버 로그인 여부를 확인하지 않습니다. 프로필 선택이나 설정 변경을 로그인 성공으로 처리하지 않습니다.';
}

function toast(message, undo=null){
  clearTimeout(toastTimer);$('toastText').textContent=message;$('toast').hidden=false;undoAction=undo;$('toastUndo').hidden=!undo;
  toastTimer=setTimeout(()=>{$('toast').hidden=true;undoAction=null;},undo?9000:4500);
}
function showMessage(title,text){
  hideTooltip();ordinaryModalFocus=document.activeElement;document.querySelector('.modal-head strong').textContent=title;$('settingsSummary').className='plain-pre';$('settingsSummary').textContent=text;
  $('modalBackdrop').classList.add('show');$('modalBackdrop').setAttribute('role','dialog');$('modalBackdrop').setAttribute('aria-modal','true');$('modalBackdrop').setAttribute('aria-label',title);syncModalInert();$('modalClose').focus();
}

function closeMessage(){$('modalBackdrop').classList.remove('show');syncModalInert();if(ordinaryModalFocus?.isConnected)ordinaryModalFocus.focus();ordinaryModalFocus=null;}

function updateSaveBar(){
  const bar=$('settingsSaveBar'),button=$('saveSettingsBtn'),text=$('saveBarText'),mark=$('saveStateMark');
  if(!bar||!button||!text||!mark)return;
  bar.classList.toggle('dirty',dirty&&!running);bar.classList.toggle('locked',running);
  if(running){mark.textContent='🔒';text.textContent='실행 중 · 이번 실행의 설정으로 고정되어 있습니다.';button.textContent='설정 잠김';button.disabled=true;}
  else{button.disabled=false;
    if(dirty){mark.textContent='!';text.textContent='저장하지 않은 변경사항이 있습니다. 다음 실행에도 사용하려면 저장하세요.';button.textContent='변경사항 저장';}
    else if(!storageAvailable){mark.textContent='!';text.textContent='브라우저 저장소를 사용할 수 없습니다. 현재 페이지에서만 유지됩니다.';button.textContent='저장 재시도';}
    else{mark.textContent='✓';text.textContent=savedConfigPresent?'저장된 설정과 같습니다. 다음 실행에도 사용할 수 있습니다.':'기본 설정 사용 중 · 별도로 저장한 설정은 없습니다.';button.textContent='설정 저장';}
  }
}

function markChanged(){dirty=savedConfigCanonical===null||canonicalConfig(settingsSnapshot())!==savedConfigCanonical;updateSaveBar();}

function settingsSnapshot(){
  reconcileKeywordCatalog();
  const cfg={schemaVersion:3,catalog:catalogSnapshot(),keywords:[...keywords],keywordIds:keywords.map(k=>keywordByName(k).id),selectedCafeIds:[...selectedCafeIds].filter(id=>getCafe(id)?.active),selectedCafes:cafes.filter(c=>c.active&&selectedCafeIds.has(c.id)).map(c=>c.code)};
  SETTINGS_IDS.forEach(id=>{const e=$(id);cfg[id]=e.type==='checkbox'?e.checked:e.value;});
  // V10 fixed policy: AI evidence and post-run review tracking are always enabled.
  cfg.evidence=true;
  cfg.humanReview=true;
  cfg.summaryCafes=currentSummaryCafes();
  return cfg;
}

function saveSettings(){
  if(running)return false;
  const cfg=settingsSnapshot(),problems=validateSettings(cfg,false);
  if(problems.length){showMessage('설정 확인',problems.join('\n'));return false;}
  if(!writeStored(SETTINGS_KEY,cfg))return false;
  savedConfigCanonical=canonicalConfig(cfg);savedConfigPresent=true;dirty=false;updateSaveBar();toast('설정을 저장했습니다. 비밀번호와 API Key는 저장하지 않습니다.');return true;
}

function loadSettings(){
  const cfg=readStored(SETTINGS_KEY,null);hydrateCatalog(cfg);renderCafes();
  if(cfg&&typeof cfg==='object'&&!Array.isArray(cfg)){
    const overlapMap={'이전 확인일 + 1일':'1','겹침 없음':'0','+ 2일':'2'};
    const migrateMailDefaultOn=Number(cfg.schemaVersion||0)<3;
    SETTINGS_IDS.forEach(id=>{const e=$(id);let value=cfg[id];if(value===undefined)return;if(id==='overlap')value=overlapMap[value]??value;
      if(id==='sendMail'&&migrateMailDefaultOn){e.checked=true;return;}
      if(e.type==='checkbox'){if(typeof value==='boolean')e.checked=value;}
      else if(e.tagName==='SELECT'){if([...e.options].some(o=>o.value===String(value)))e.value=String(value);}
      else if(typeof value==='string')e.value=value.slice(0,4000);
    });
  }
  refreshCatalogFromSources();samplePosts.forEach(ensureRecordCatalog);renderKeywords();
  savedConfigCanonical=canonicalConfig(settingsSnapshot());savedConfigPresent=!!cfg;dirty=false;updateSaveBar();
}

function renderCafes(){
  $('cafeList').innerHTML=cafes.filter(c=>c.active).map((c,i)=>`<div class="cafe-row"><input type="checkbox" id="cafe-${i}" class="cafe-check" data-code="${esc(c.code)}" data-cafe-id="${esc(c.id)}" ${selectedCafeIds.has(c.id)?'checked':''} ${running?'disabled':''} aria-label="${esc(c.name)} 수집"><span class="cafe-name" title="${esc(c.name)}"><label for="cafe-${i}">${esc(c.name)}</label></span><span class="cafe-code">${esc(c.code)}</span>${c.url?`<a class="open-cafe" href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" title="${esc(c.name)} 카페 열기">↗</a>`:'<span></span>'}</div>`).join('')||'<div class="empty-small">수집 사용 중인 카페가 없습니다. 카페 관리에서 추가하거나 다시 사용하세요.</div>';
}

function keywordVisualState(k){
  const added=!isDefaultKeywordName(k.name);
  if(!k.active&&added)return {rank:0,cls:'keyword-stopped-added',state:'추가 키워드 · 검색 중단 · 과거 데이터 유지'};
  if(!k.active)return {rank:1,cls:'keyword-stopped',state:'검색 중단 · 과거 데이터 유지'};
  if(added)return {rank:2,cls:'keyword-added',state:'추가 키워드 · 현재 검색 사용 중'};
  return {rank:3,cls:'keyword-default',state:'현재 검색 사용 중'};
}

function renderKeywords(){
  refreshCatalogFromSources();
  const rows=keywordCatalog.filter(k=>!k.deleted).slice().sort((a,b)=>{
    const sa=keywordVisualState(a),sb=keywordVisualState(b);if(sa.rank!==sb.rank)return sa.rank-sb.rank;
    if(sa.rank===3){const order=new Map(defaultKeywords.map((name,i)=>[normalizedTerm(name),i]));return (order.get(normalizedTerm(a.name))??999)-(order.get(normalizedTerm(b.name))??999);}
    const ta=Date.parse(a.createdAt||'')||0,tb=Date.parse(b.createdAt||'')||0;if(ta!==tb)return ta-tb;
    return String(a.name).localeCompare(String(b.name),'ko');
  });
  $('keywordWrap').innerHTML=rows.length?rows.map(k=>{const state=keywordVisualState(k);return `<span class="keyword ${state.cls}" title="${esc(state.state)}">${esc(k.name)}</span>`;}).join(''):'<div class="empty-small">등록된 검색 키워드가 없습니다. 키워드 관리에서 추가하세요.</div>';
  const suggest=$('keywordSuggest'),input=$('keywordInput');if(suggest&&input&&!suggest.hidden)renderKeywordSuggestions(input.value);
}

function hideKeywordSuggestions(){
  const box=$('keywordSuggest'),input=$('keywordInput');if(!box||!input)return;
  box.hidden=true;input.setAttribute('aria-expanded','false');
}
function keywordSuggestionRows(query=''){
  refreshCatalogFromSources();
  const q=normalizedTerm(query),rows=keywordCatalog.filter(k=>!k.deleted&&(!q||normalizedTerm(k.name).includes(q)));
  rows.sort((a,b)=>Number(a.active)-Number(b.active)||String(a.name).localeCompare(String(b.name),'ko'));
  return rows.slice(0,18);
}
function renderKeywordSuggestions(query=''){
  const box=$('keywordSuggest'),input=$('keywordInput');if(!box||!input||running){hideKeywordSuggestions();return;}
  const text=String(query||'').trim(),rows=keywordSuggestionRows(text),exact=keywordByName(text);let html='';
  const previous=rows.filter(k=>!k.active),current=rows.filter(k=>k.active);
  if(text&&!exact)html+=`<div class="keyword-suggest-group">새 키워드</div><button type="button" class="keyword-suggest-item create" data-keyword-create="${esc(text)}" role="option"><span class="keyword-suggest-name">${esc(text)}</span><span class="keyword-suggest-state">새로 추가</span></button>`;
  if(previous.length)html+=`<div class="keyword-suggest-group">이전 사용 키워드</div>`+previous.map(k=>`<button type="button" class="keyword-suggest-item previous" data-keyword-reactivate="${esc(k.id)}" role="option"><span class="keyword-suggest-name">${esc(k.name)}</span><span class="keyword-suggest-state">다시 사용</span></button>`).join('');
  if(current.length)html+=`<div class="keyword-suggest-group">현재 사용 중</div>`+current.map(k=>`<button type="button" class="keyword-suggest-item current" data-keyword-current="${esc(k.id)}" role="option"><span class="keyword-suggest-name">${esc(k.name)}</span><span class="keyword-suggest-state">사용 중</span></button>`).join('');
  if(!html)html='<div class="keyword-suggest-empty">기억된 키워드가 없습니다. 입력한 값을 새 키워드로 추가할 수 있습니다.</div>';
  box.innerHTML=html;box.hidden=false;input.setAttribute('aria-expanded','true');
}
function reactivateKeyword(id){
  if(running)return;const item=getKeyword(id);if(!item)return;
  const wasDeleted=!!item.deleted;if(wasDeleted){item.deleted=false;item.deletedAt=null;}
  if(item.active||keywords.some(k=>normalizedTerm(k)===normalizedTerm(item.name))){toast(`“${item.name}”은 현재 사용 중입니다.`);hideKeywordSuggestions();return;}
  if(keywords.length>=100){toast('사용 키워드는 100개까지 등록할 수 있습니다.');return;}
  keywords.push(item.name);$('keywordInput').value='';renderKeywords();updateCounts();markChanged();refreshCatalogViews();hideKeywordSuggestions();if(!$('keywordManagerBackdrop').hidden)renderKeywordManager();$('keywordInput').focus();toast(wasDeleted?`“${item.name}” 키워드를 다시 등록했습니다. 과거 데이터와 같은 키워드로 연결됩니다.`:`“${item.name}” 키워드를 다시 사용합니다. 과거 데이터와 같은 키워드로 연결됩니다.`);
}

function openKeywordConfirm(index,restore=false){
  if(running||(!restore&&!keywords[index]))return;
  keywordFocus=document.activeElement;pendingKeywordAction=restore?{restore:true}:{word:keywords[index],index};
  $('keywordConfirmTitle').textContent=restore?'검색 키워드 기본값 복원':'검색 키워드 중단';
  document.querySelector('.keyword-confirm-body>div').innerHTML=restore?`현재 키워드를 <strong>기본 ${defaultKeywords.length}개</strong>로 바꾸시겠습니까?`:`<strong>${esc(keywords[index])}</strong> 키워드 검색을 중단하시겠습니까?`;
  $('keywordConfirmNote').textContent='앞으로의 검색에서만 제외합니다. 이미 저장된 게시글과 키워드 연결은 과거 데이터로 유지합니다.';
  $('keywordDeleteConfirm').textContent=restore?'복원':'검색 중단';$('keywordConfirmBackdrop').classList.add('show');$('keywordConfirmBackdrop').setAttribute('aria-hidden','false');syncModalInert();$('keywordDeleteCancel').focus();
}

function openKeywordDeleteConfirm(id){
  if(running)return;const item=getKeyword(id);if(!item||item.deleted)return;
  keywordFocus=document.activeElement;pendingKeywordAction={deleteKeyword:true,id:item.id,word:item.name,wasActive:item.active};
  $('keywordConfirmTitle').textContent='키워드 삭제';
  document.querySelector('.keyword-confirm-body>div').innerHTML=`<strong>${esc(item.name)}</strong> 키워드를 등록 목록에서 삭제하시겠습니까?`;
  $('keywordConfirmNote').textContent='현재 검색 설정과 키워드 관리 목록에서는 제거됩니다. 이미 수집된 게시글, 데이터 분석의 과거 기록과 실행 이력은 삭제되지 않습니다. 같은 이름을 나중에 다시 추가하면 과거 데이터와 다시 연결됩니다.';
  $('keywordDeleteConfirm').textContent='삭제';$('keywordConfirmBackdrop').classList.add('show');$('keywordConfirmBackdrop').setAttribute('aria-hidden','false');syncModalInert();$('keywordDeleteCancel').focus();
}

function closeKeywordConfirm(){pendingKeywordAction=null;$('keywordConfirmBackdrop').classList.remove('show');$('keywordConfirmBackdrop').setAttribute('aria-hidden','true');syncModalInert();if(keywordFocus?.isConnected)keywordFocus.focus();}

function confirmKeywordAction(){
  if(!pendingKeywordAction||running)return;
  const before=[...keywords],action=pendingKeywordAction;
  if(action.deleteKeyword){
    const item=getKeyword(action.id);if(item){keywords=keywords.filter(k=>normalizedTerm(k)!==normalizedTerm(item.name));item.active=false;item.deleted=true;item.deletedAt=new Date().toISOString();}
    closeKeywordConfirm();renderKeywords();updateCounts();markChanged();refreshCatalogViews();if(!$('keywordManagerBackdrop').hidden)renderKeywordManager($('keywordInput').value);
    toast(`“${action.word}” 키워드를 등록 목록에서 삭제했습니다. 과거 데이터와 실행 이력은 유지됩니다.`);return;
  }
  keywords=action.restore?[...defaultKeywords]:keywords.filter(k=>normalizedTerm(k)!==normalizedTerm(action.word));
  closeKeywordConfirm();renderKeywords();updateCounts();markChanged();refreshCatalogViews();if(!$('keywordManagerBackdrop').hidden)renderKeywordManager($('keywordInput').value);
  toast(action.restore?'기본 키워드를 복원했습니다.':`“${action.word}” 검색을 중단했습니다. 과거 데이터는 유지됩니다.`,()=>{if(running){toast('실행 중에는 키워드를 변경할 수 없습니다.');return;}keywords=before;renderKeywords();updateCounts();markChanged();refreshCatalogViews();if(!$('keywordManagerBackdrop').hidden)renderKeywordManager($('keywordInput').value);});
}

function addKeyword(){
  if(running)return;
  const value=$('keywordInput').value.trim();
  if(!value){toast('추가할 키워드를 입력하거나 아래 등록 목록에서 이전 키워드를 다시 사용하세요.');return;}
  if(value.length>100){toast('키워드는 100자 이내로 입력하세요.');return;}
  const known=keywordByName(value);
  if(known?.active||keywords.some(k=>normalizedTerm(k)===normalizedTerm(value))){toast('이미 사용 중인 키워드입니다.');return;}
  if(known&&!known.active){reactivateKeyword(known.id);return;}
  if(keywords.length>=100){toast('사용 키워드는 100개까지 등록할 수 있습니다.');return;}
  keywords.push(value);$('keywordInput').value='';renderKeywords();updateCounts();markChanged();refreshCatalogViews();hideKeywordSuggestions();if(!$('keywordManagerBackdrop').hidden)renderKeywordManager();$('keywordInput').focus();toast(`“${value}” 새 키워드를 추가했습니다.`);
}

function updateCounts(){
  const count=cafes.filter(c=>c.active&&selectedCafeIds.has(c.id)).length;
  $('targetCount').textContent=`${count}개 카페 · 검색 사용 ${keywords.length}개`;$('statusCafe').textContent=count;$('statusKeyword').textContent=keywords.length;
  $('toggleAll').textContent=count&&count===cafes.filter(c=>c.active).length?'전체 해제':'전체 선택';
}

function updateSettingsUI(){
  $('codexAuth').classList.toggle('hidden',$('provider').value!=='codex');$('openaiAuth').classList.toggle('hidden',$('provider').value==='codex');
  $('mailFields').classList.toggle('hidden',!$('sendMail').checked);$('mailScopeNotice').classList.toggle('hidden',!$('sendMail').checked||$('mailScope').value!=='summary10');
  const n=currentSummaryCafes().length;
  $('mailScopeNoticeTitle').textContent='요약 슬라이드만 메일로 발송';
  $('mailScopeNoticeText').textContent=$('summarySlide').checked?`전체 카페 종합 요약 1장 + 대상 카페별 ${n}장, 총 ${n+1}장입니다. 전체 PPT 원본은 실행 이력에 보관합니다.`:'카페별 요약이 꺼져 있어 전체 종합 요약 1장만 첨부합니다. 전체 PPT 원본은 유지합니다.';
  $('sourceRunRow').classList.toggle('hidden',$('stepCollect').checked);
  const direct=$('range').value==='기간 직접 지정';$('periodDirectFields').classList.toggle('hidden',!direct);
  for(const id of ['periodStart','periodEnd','periodDedupeEnabled'])$(id).disabled=running||!direct;
  const on=$('periodDedupeEnabled').checked;$('periodDedupeState').classList.toggle('off',!on);$('periodDedupeState').innerHTML=on?'<strong>이미 수집한 글은 건너뜁니다</strong><span>본문이 바뀐 글은 갱신합니다.</span>':'<strong>이미 수집한 글도 다시 포함합니다</strong><span>이번 실행 결과에 다시 수집합니다.</span>';
  $('overlap').disabled=running||$('range').value!=='이전 실행 이후';$('scheduleTime').disabled=running||!$('scheduleEnabled').checked;$('weekdaysOnly').disabled=running||!$('scheduleEnabled').checked;
  $('taskNote').textContent=$('stepCollect').checked?'웹 수집 → AI 분석 → PPT 생성 → Outlook 발송(선택 시). 사후 검토는 자동화 완료 후 별도입니다.':'선택한 저장 이력을 재사용합니다. 새 수집은 하지 않습니다.';
  $('statusParallel').textContent=$('parallel').value;$('statusProvider').textContent=$('provider').value==='codex'?'Codex':'OpenAI API';$('statusPpi').textContent=$('ppi').value+' ppi';
}

function validateSettings(cfg,forRun){
  const errors=[];
  if(cfg.scheduleEnabled&&!/^([01]\d|2[0-3]):[0-5]\d$/.test(cfg.scheduleTime||''))errors.push('예약 실행 시각을 올바르게 입력하세요.');
  if(cfg.stepCollect&&cfg.range==='기간 직접 지정'){
    const start=parseKstLocalDateTime(cfg.periodStart),end=parseKstLocalDateTime(cfg.periodEnd);
    if(!start||!end)errors.push('실제로 존재하는 수집 시작·종료 날짜와 시각을 입력하세요.');
    else if(+start>=+end)errors.push('수집 종료 시각은 시작 시각보다 뒤여야 합니다.');
  }
  if(cfg.sendMail){
    const split=s=>safeText(s).split(/[;,\n]/).map(x=>x.trim()).filter(Boolean),to=split(cfg.mailTo),cc=split(cfg.mailCc);
    if(!to.length)errors.push('Outlook 발송의 받는 사람(To)을 입력하세요.');
    if([...to,...cc].some(x=>!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(x)))errors.push('이메일 주소 형식을 확인하세요. 여러 명은 세미콜론으로 구분합니다.');
    if(!cfg.stepPpt)errors.push('메일 발송에는 PPT 생성이 필요합니다.');
  }
  if(forRun){
    if(!cfg.stepCollect&&!cfg.stepAnalyze&&!cfg.stepPpt)errors.push('이번 실행 작업을 하나 이상 선택하세요.');
    if(cfg.stepCollect&&!cfg.selectedCafes?.length)errors.push('수집할 카페를 하나 이상 선택하세요.');
    if(cfg.stepCollect&&!cfg.keywords?.length)errors.push('검색 키워드를 하나 이상 등록하세요.');
    if(cfg.stepCollect&&cfg.stepPpt&&!cfg.stepAnalyze)errors.push('새 수집 결과의 PPT 작성에는 AI 분석을 함께 선택하세요. 기존 결과는 PPT만 재생성을 이용하세요.');
    if(!cfg.stepCollect){const r=historyRecords.find(r=>r.id===cfg.sourceRun);if(!r?.articles?.length)errors.push('재사용할 저장 이력에 게시글이 없습니다.');else if(cfg.stepAnalyze&&!r.hasRaw)errors.push('선택한 이력의 원문 수집이 완료되지 않았습니다.');else if(cfg.stepPpt&&!cfg.stepAnalyze&&!r.hasAnalysis)errors.push('선택한 이력에는 저장 분석 결과가 없습니다.');}
    if(!safeText(cfg.outputPath).trim())errors.push('결과 폴더를 입력하세요.');
    if(!['1','2','3'].includes(String(cfg.parallel)))errors.push('카페 병렬 수는 1~3개로 선택하세요.');
  }
  return errors;
}

function normalizeHistory(){
  const stored=readStored(HISTORY_KEY,[]),runs=[];skippedHistoryRecords=0;
  for(const row of stored){
    if(!row||typeof row!=='object'||row.source!=='simulation'||typeof row.id!=='string'||!row.id||!dateValue(row.startedAt)){skippedHistoryRecords++;continue;}
    if((row.rows!==undefined&&!Array.isArray(row.rows))||(row.articles!==undefined&&!Array.isArray(row.articles))){try{corruptStorage.set(HISTORY_KEY,localStorage.getItem(HISTORY_KEY));}catch{}}
    const r=copyObject(row);r.startedAt=dateValue(r.startedAt).toISOString();r.endedAt=dateValue(r.endedAt)?.toISOString()||null;r.updatedAt=dateValue(r.updatedAt)?.toISOString()||r.endedAt||r.startedAt;
    r.articles=(Array.isArray(r.articles)?r.articles:[]).map(normalizeArticle).filter(Boolean);
    r.rows=Array.isArray(r.rows)?r.rows.filter(Array.isArray).map(row=>row.slice(0,6).map(v=>safeText(v))):r.articles.map(a=>[a.cafe,a.id,a.key,a.type,a.status,'저장 결과']);
    r.stepsDone=Array.isArray(r.stepsDone)?r.stepsDone.filter(v=>['collect','analyze','report','mail'].includes(v)):[];
    r.plannedSteps=Array.isArray(r.plannedSteps)?r.plannedSteps.filter(v=>['collect','analyze','report','mail'].includes(v)):[];
    r.settings=r.settings&&typeof r.settings==='object'&&!Array.isArray(r.settings)?r.settings:null;
    if(r.settings)for(const key of ['selectedCafeIds','selectedCafes','keywords'])if(!Array.isArray(r.settings[key]))r.settings[key]=[];
    if(!Array.isArray(r.completedCafeIds))delete r.completedCafeIds;
    for(const key of ['type','stage','notes','status','provider','ppt','summaryPpt','output','analysis','logPath','logText','period','periodNote','keywords','cafes','parallel','posts','slides'])r[key]=safeText(r[key]);
    r.status=r.status||'상태 미기록';r.hasAnalysis=r.hasAnalysis===true;r.hasRaw=r.hasRaw===true;
    if(r.status==='실행 중'){r.status='중단 · 페이지 종료';r.stage='중단';r.endedAt=r.updatedAt;r.notes+='\n이 화면의 실행 흐름은 종료되었습니다. 표시된 종료 시각은 마지막 기록 시점입니다.';}
    runs.push(r);
  }
  if(skippedHistoryRecords){try{const original=localStorage.getItem(HISTORY_KEY);if(original!==null)corruptStorage.set(HISTORY_KEY,original);}catch{}}
  const samples=sampleHistory.map(row=>({...copyObject(row),source:'sample',startedAt:dateValue(row.started)?.toISOString(),endedAt:dateValue(row.ended)?.toISOString(),periodStart:null,periodEnd:null,hasAnalysis:true,hasRaw:true,articles:copyObject(samplePosts.filter(p=>row.rows.some(v=>v[0]===p.cafe&&String(v[1])===String(p.id)))),stepsDone:[],stage:'완료',dataNote:'기록 기반 예시입니다. 실제 파일·수집 데이터와 연결되어 있지 않습니다.'}));
  const unique=new Map();for(const r of runs.sort((a,b)=>+dateValue(a.updatedAt)-+dateValue(b.updatedAt)))unique.set(r.id,r);
  historyRecords=[...unique.values(),...samples].sort((a,b)=>+dateValue(b.startedAt)-+dateValue(a.startedAt));
  const first=historyRecords[0];if(first){const p=parts(dateValue(first.startedAt));historyState.year=p.y;historyState.month=p.m-1;historyState.selectedId=first.id;}
}

function saveHistory(){
  const rows=historyRecords.filter(r=>r.source==='simulation');
  return writeStored(HISTORY_KEY,rows);
}

function latestRun(){return historyRecords.slice().sort((a,b)=>+dateValue(b.startedAt)-+dateValue(a.startedAt))[0]||null;}
function computeNextRun(cfg,now=new Date()){
  if(!cfg.scheduleEnabled||!/^([01]\d|2[0-3]):[0-5]\d$/.test(cfg.scheduleTime))return null;
  const [h,m]=cfg.scheduleTime.split(':').map(Number),p=parts(now);let result=new Date(Date.UTC(p.y,p.m-1,p.d,h-9,m,0));
  if(+result<=+now)result=new Date(+result+DAY);
  if(cfg.weekdaysOnly)while([0,6].includes(parts(result).weekday))result=new Date(+result+DAY);
  return result;
}
function computePeriod(cfg,end=new Date()){
  if(!cfg.stepCollect){const r=historyRecords.find(r=>r.id===cfg.sourceRun);return {start:dateValue(r?.periodStart),end:dateValue(r?.periodEnd),note:'저장 결과 재사용 · 원본 실행의 대상 기간',mode:'저장 결과 재사용'};}
  if(cfg.range==='기간 직접 지정')return {start:parseKstLocalDateTime(cfg.periodStart),end:parseKstLocalDateTime(cfg.periodEnd),note:cfg.periodDedupeEnabled?'기간 지정 · 동일 본문 제외 · 변경 본문 갱신 · 예약 처리 완료 시각 유지':'기간 지정 · 기존 저장 글도 포함 · 예약 처리 완료 시각 유지',mode:cfg.range};
  if(cfg.range==='이전 실행 이후'){
    const prior=historyRecords.filter(checkpointEligible).sort((a,b)=>+dateValue(b.periodEnd)-+dateValue(a.periodEnd));
    const wanted=Array.isArray(cfg.selectedCafeIds)&&cfg.selectedCafeIds.length?cfg.selectedCafeIds:(cfg.selectedCafes||[]).map(c=>getCafe(c)?.id||c);
    if(!wanted.length)return {start:null,end,note:'수집할 카페를 먼저 선택하세요.',mode:cfg.range};
    const ends=[];
    for(const id of wanted){
      const r=prior.find(r=>Array.isArray(r.completedCafeIds)?r.completedCafeIds.includes(id):(r.settings?.selectedCafeIds||[]).includes(id)||(r.settings?.selectedCafes||[]).includes(cafeLabel(id)));
      if(!r)return {start:null,end,note:`${cafeLabel(id)}의 정상 수집 기준 시각이 없습니다. 최근 24시간 또는 48시간 수집을 먼저 실행하세요.`,mode:cfg.range};
      ends.push(+dateValue(r.periodEnd));
    }
    // Use the oldest required checkpoint so adding another selected cafe never
    // skips its unprocessed interval. Dedupe can remove overlapping records.
    const start=new Date(Math.min(...ends)-Number(cfg.overlap||0)*DAY);
    return {start,end,note:`선택 카페의 정상 수집 종료 기준 · ${Number(cfg.overlap||0)}일 재확인 · 기간 지정 실행 제외`,mode:cfg.range};
  }
  return {start:new Date(+end-(cfg.range==='최근 48시간'?48:24)*3600000),end,note:'실행 시작 시 확정',mode:cfg.range};
}

function periodText(value){const d=dateValue(value);if(!d)return '—';const p=parts(d);return `${p.m}월 ${p.d}일 ${p.h}시 ${pad(p.min)}분`;}
function periodHTML(start,end){return start&&end?`${esc(periodText(start))}<span class="period-arrow">→</span>${esc(periodText(end))}`:'대상 기간을 확인할 수 없습니다.';}
function updateTimes(){
  updateCurrentClock();
  const cfg=settingsSnapshot();
  const recent=historyRecords.filter(r=>r!==activeRun&&r.endedAt).slice().sort((a,b)=>+dateValue(b.startedAt)-+dateValue(a.startedAt))[0]||latestRun();
  const frozen=activeRun||lastDisplayedRun;

  if(recent){
    $('timeRecentValue').innerHTML=shortTime(recent.startedAt);$('timeRecentValue').setAttribute('datetime',recent.startedAt);
    $('timeRecentMeta').textContent=`${recent.status}${recent.endedAt?' · '+clockTime(recent.endedAt).slice(0,5)+' 종료':''}`;
  }else{$('timeRecentValue').textContent='실행 기록 없음';$('timeRecentValue').removeAttribute('datetime');$('timeRecentMeta').textContent='첫 실행 전입니다.';}

  $('runTimeItem').classList.toggle('running-highlight',!!activeRun);
  if(activeRun){
    const elapsed=duration(Date.now()-+dateValue(activeRun.startedAt));
    $('timeRunValue').innerHTML=shortTime(activeRun.startedAt);$('timeRunValue').classList.remove('state-word');$('timeRunValue').setAttribute('datetime',activeRun.startedAt);
    $('timeRunMeta').textContent=`${activeRun.stage} · ${elapsed}`;$('timeRunMeta').classList.add('in-progress');
    $('overviewElapsed').textContent=elapsed;
    $('runBtn').textContent=`■ 실행 중지 · ${elapsed}`;$('runBtn').classList.add('running-stop');$('runBtn').classList.remove('primary');
  }else{
    $('timeRunValue').textContent='대기';$('timeRunValue').classList.add('state-word');$('timeRunValue').removeAttribute('datetime');
    $('timeRunMeta').classList.remove('in-progress');$('timeRunMeta').textContent='실행 중인 자동화 없음';
    $('runBtn').textContent='전체 실행';$('runBtn').classList.remove('running-stop');$('runBtn').classList.add('primary');
  }

  const next=computeNextRun(cfg);
  if(next){const nextPeriod=computePeriod(cfg,next);$('timeNextValue').innerHTML=shortTime(next);$('timeNextValue').setAttribute('datetime',next.toISOString());$('timeNextMeta').textContent=nextPeriodMeta(nextPeriod);$('overviewNextRun').textContent=formatTime(next);}
  else{$('timeNextValue').textContent=cfg.scheduleEnabled?'시각 미입력':'예약 꺼짐';$('timeNextValue').removeAttribute('datetime');$('timeNextMeta').textContent=cfg.scheduleEnabled?'자동 실행 설정을 확인하세요.':'다음 자동 실행 일정이 없습니다.';$('overviewNextRun').textContent=cfg.scheduleEnabled?'시각 미입력':'예약 사용 안 함';}

  const period=frozen?{start:dateValue(frozen.periodStart),end:dateValue(frozen.periodEnd),note:frozen.periodNote,mode:frozen.period}:computePeriod(cfg);
  $('timePeriodLabel').textContent=frozen?(frozen.settings?.stepCollect?'이번 실행 수집 대상':'재사용 자료의 수집 대상'):'지금 실행 시 수집 대상';
  $('periodMode').textContent=period.mode||'';$('timePeriodValue').innerHTML=periodHTML(period.start,period.end);
  $('periodNote').textContent=frozen?(frozen.settings?.stepCollect?'실행 시작 시 확정된 기간 · 종료 후에도 이력에 유지':period.note||'원본 기간 미확인'):period.note;
  $('overviewPeriod').textContent=period.start&&period.end?`${periodText(period.start)} ~ ${periodText(period.end)}`:period.note||'원본 기간 미확인';
  updateGlobalHeader(activePrimaryView);
  renderExecutionPptPreview();
}
function updateStage(){
  const r=activeRun||lastDisplayedRun;
  $$('.stage').forEach(el=>{
    const key=el.dataset.stage;el.classList.remove('active','done','skipped','waiting');if(!r)return;
    if(r.stepsDone?.includes(key))el.classList.add('done');
    else if(r.stageKey===key&&activeRun)el.classList.add('active');
    else if(!r.plannedSteps?.includes(key))el.classList.add('skipped');
  });
}
function appendLog(message,record=activeRun){
  const line=`[${clockTime(new Date())}] ${message}`;const el=$('log');el.textContent+=line+'\n';el.scrollTop=el.scrollHeight;
  if(record){record.logText=(record.logText||'')+line+'\n';record.updatedAt=new Date().toISOString();}
}
function renderPosts(){
  const analyzed=!(activeRun||lastDisplayedRun)||!!(activeRun||lastDisplayedRun).hasAnalysis;
  if(!posts.length){$('resultBody').innerHTML='<tr><td colspan="7">표시할 수집 게시글이 없습니다.</td></tr>';clearPostDetail();return;}
  if(!posts.some(p=>articleIdentity(p)===selectedPostId))selectedPostId=articleIdentity(posts[0]);
  $('resultBody').innerHTML=posts.map((p,i)=>{const key=articleIdentity(p);return `<tr data-post="${esc(key)}" tabindex="0" role="row" class="${key===selectedPostId?'selected':''}" aria-selected="${key===selectedPostId}"><td>${i+1}</td><td>${esc(p.cafe)}</td><td>${esc(p.id)}</td><td title="${esc(p.title)}">${esc(p.title)}</td><td title="${esc(p.key)}">${esc(p.key)}</td><td>${esc(analyzed?p.type:'미분석')}</td><td><span class="status-text ${p.status==='검토완료'?'ok':'review'}">${esc(p.status)}</span></td></tr>`;}).join('');
  selectPost(selectedPostId);
}

function clearPostDetail(){selectedPostId=null;$('detailTitle').textContent='게시글을 선택하세요.';$('rawText').textContent='선택된 게시글이 없습니다.';$('analysisDetail').textContent='선택된 게시글이 없습니다.';$('approveBtn').disabled=true;$('originalBtn').disabled=true;}
function selectPost(ref){
  let p=posts.find(x=>articleIdentity(x)===ref);
  if(!p){const same=posts.filter(x=>String(x.id)===String(ref));if(same.length===1)p=same[0];}
  if(!p){clearPostDetail();return;}
  selectedPostId=articleIdentity(p);$$('#resultBody tr[data-post]').forEach(tr=>{const yes=tr.dataset.post===selectedPostId;tr.classList.toggle('selected',yes);tr.setAttribute('aria-selected',String(yes));});
  $('detailTitle').textContent=`${p.cafe} · ${p.id} · ${p.title}`;$('rawText').textContent=p.raw;
  const entries=[['문서 구분',p.doc],['이슈명',p.issue],['원문 근거',p.evidence],['키워드',p.key],['검토 상태',p.status]];
  const analyzed=!(activeRun||lastDisplayedRun)||!!(activeRun||lastDisplayedRun).hasAnalysis;
  $('analysisDetail').innerHTML=analyzed?'<div class="analysis-grid">'+entries.map(([k,v])=>`<div class="k">${esc(k)}</div><div>${esc(v)}</div>`).join('')+'</div>':'이 실행에는 아직 AI 분석 결과가 없습니다.';
  $('originalBtn').disabled=!/^https:\/\/cafe\.naver\.com\//.test(p.url||'');
  $('approveBtn').disabled=running||p.status==='검토완료'||p.status==='검토생략'||(lastDisplayedRun&&!lastDisplayedRun.hasAnalysis);
}

function sourceOptions(){
  const selected=$('sourceRun').value;
  $('sourceRun').innerHTML='<option value="">재사용할 실행을 선택하세요</option>'+historyRecords.filter(r=>r.articles?.length&&(r.hasRaw||r.hasAnalysis)).map(r=>`<option value="${esc(r.id)}">${r.type==='V9 이관'?'V9 이관':'V10'} · ${esc(formatTime(r.startedAt))} · ${esc(r.type)}</option>`).join('');
  if([...$('sourceRun').options].some(o=>o.value===selected))$('sourceRun').value=selected;
}

function lockExecution(locked){
  running=locked;$('runBtn').disabled=false;
  $('runBtn').setAttribute('aria-busy',String(locked));
  $('stopBtn').classList.toggle('hidden',!locked);$('stopBtn').disabled=!locked;
  $('pptOnlyBtn').disabled=locked;
  $$('.config-pane input,.config-pane select,.config-pane button:not(.help)').forEach(e=>e.disabled=locked);
  document.querySelector('.workspace').classList.toggle('settings-locked',locked);
  updateSettingsUI();updateCounts();updateSaveBar();updateTimes();
}
function requestStop(){if(!running||!runAbort)return;$('stopConfirmBackdrop').classList.add('show');$('stopConfirmBackdrop').setAttribute('aria-hidden','false');syncModalInert();$('stopCancelBtn').focus();}

function closeStopConfirm(){$('stopConfirmBackdrop').classList.remove('show');$('stopConfirmBackdrop').setAttribute('aria-hidden','true');syncModalInert();if(running)$('runBtn').focus();}

function confirmStop(){
  if(!runAbort){closeStopConfirm();return;}
  closeStopConfirm();$('stopBtn').disabled=true;$('runBtn').disabled=true;runAbort.abort();
}
function openExecutionConfirm(kind){
  if(running)return;executionConfirmFocus=document.activeElement;
  if(kind==='all'){
    pendingExecutionAction={kind:'all'};$('actionConfirmTitle').textContent='전체 실행 확인';$('actionConfirmQuestion').textContent='현재 설정으로 전체 실행을 시작하시겠습니까?';$('actionConfirmNote').textContent='현재 HTML은 화면 테스트입니다. 선택한 단계의 진행·중지 동작을 확인하며 실제 수집·AI 요청·PPT 파일 생성·메일 발송은 하지 않습니다.';$('actionConfirmRunBtn').textContent='전체 실행';
  }else if(kind==='ppt'){
    const chosen=historyRecords.find(r=>r.id===historyState.selectedId&&r.hasAnalysis&&r.articles?.length)||historyRecords.find(r=>r.hasAnalysis&&r.articles?.length);
    if(!chosen){showMessage('PPT 재작성 불가','재사용할 저장 분석 결과가 없습니다.');return;}
    pendingExecutionAction={kind:'ppt',runId:chosen.id};$('actionConfirmTitle').textContent='PPT만 재생성 확인';$('actionConfirmQuestion').textContent='저장된 분석 결과로 PPT만 다시 생성하시겠습니까?';$('actionConfirmNote').textContent=`대상: ${chosen.id}\n웹 수집·AI 분석·메일 발송은 실행하지 않습니다. 기본 설정은 변경하지 않습니다. 현재 HTML에서는 PPT 생성 단계의 화면 동작만 테스트합니다.`;$('actionConfirmRunBtn').textContent='PPT 재생성';
  }else return;
  $('actionConfirmBackdrop').classList.add('show');$('actionConfirmBackdrop').setAttribute('aria-hidden','false');syncModalInert();$('actionConfirmCancelBtn').focus();
}

function closeExecutionConfirm(){pendingExecutionAction=null;$('actionConfirmBackdrop').classList.remove('show');$('actionConfirmBackdrop').setAttribute('aria-hidden','true');syncModalInert();if(executionConfirmFocus?.isConnected)executionConfirmFocus.focus();executionConfirmFocus=null;}

function confirmExecutionAction(){
  const action=pendingExecutionAction;if(!action||running)return;
  pendingExecutionAction=null;$('actionConfirmBackdrop').classList.remove('show');$('actionConfirmBackdrop').setAttribute('aria-hidden','true');executionConfirmFocus=null;syncModalInert();
  if(action.kind==='all')runAll();else if(action.kind==='ppt')doPptOnly(action.runId);
}

function primaryRunAction(){if(running)requestStop();else openExecutionConfirm('all');}
function recordStatus(stage,key){
  if(!activeRun)return;activeRun.stage=stage;activeRun.stageKey=key;
  $('overviewStage').textContent=stage;$('detailStateSummary').textContent=`${stage} · 실행 설정 고정`;$('stateTitle').textContent=stage;
  updateStage();updateTimes();saveHistory();refreshHistoryIfOpen();
}
function interruptibleWait(ms,signal){
  return new Promise((resolve,reject)=>{if(signal.aborted){reject(new DOMException('중지','AbortError'));return;}
    const abort=()=>{clearTimeout(timer);reject(new DOMException('중지','AbortError'));};
    const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);signal.addEventListener('abort',abort,{once:true});
  });
}
function articleIdentity(a){return JSON.stringify([getCafe(a?.cafeId||a?.cafe)?.id||a?.cafeId||a?.cafe||'',String(a?.id||'')]);}

function articleContentFingerprint(a){
  // The result of an AI analysis and the search terms are not raw content.
  return JSON.stringify({title:safeText(a?.title),raw:safeText(a?.raw),media:Array.isArray(a?.media)?a.media:[],contentHash:safeText(a?.contentHash)});
}

function periodStoredArticles(){
  const map=new Map();
  const records=historyRecords.filter(r=>r.source==='simulation'&&r.stepsDone?.includes('collect')).slice().sort((a,b)=>+(dateValue(a.endedAt)||dateValue(a.startedAt)||new Date(0))-+(dateValue(b.endedAt)||dateValue(b.startedAt)||new Date(0)));
  for(const r of records){
    for(const a of r.articles||[]){const valid=normalizeArticle(a);if(!valid)continue;ensureRecordCatalog(valid);map.set(articleIdentity(valid),valid);}
    for(const update of Array.isArray(r.matchUpdates)?r.matchUpdates:[]){const a=map.get(update.identity);if(a){a.keys=uniqueTerms([...articleTerms(a),...(update.keys||[])]);a.key=a.keys.join(', ');}}
  }
  return map;
}

function applyPeriodDuplicatePolicy(candidates,cfg){
  const input=new Map();for(const raw of candidates){const a=normalizeArticle(raw);if(!a)continue;ensureRecordCatalog(a);const key=articleIdentity(a),before=input.get(key);if(before){a.keys=uniqueTerms([...articleTerms(before),...articleTerms(a)]);a.key=a.keys.join(', ');}input.set(key,a);}
  const result={posts:[],skipped:0,updated:0,newCount:0,recollected:0,matchUpdates:[],reusedCount:0};
  if(!cfg.stepCollect){result.posts=[...input.values()].map(a=>({...a,_collectionAction:'재사용'}));result.reusedCount=result.posts.length;return result;}
  const useSaved=cfg.stepCollect&&cfg.range==='기간 직접 지정',stored=useSaved?periodStoredArticles():new Map();
  for(const a of input.values()){
    const key=articleIdentity(a),previous=stored.get(key);
    if(!previous){result.posts.push({...a,_collectionAction:'신규'});result.newCount++;continue;}
    if(!cfg.periodDedupeEnabled){result.posts.push({...a,_collectionAction:'재수집'});result.recollected++;continue;}
    if(articleContentFingerprint(previous)!==articleContentFingerprint(a)){result.posts.push({...a,_collectionAction:'갱신'});result.updated++;continue;}
    result.skipped++;
    const oldTerms=new Set(articleTerms(previous).map(normalizedTerm));const added=articleTerms(a).filter(k=>!oldTerms.has(normalizedTerm(k)));
    if(added.length)result.matchUpdates.push({identity:key,keys:uniqueTerms([...articleTerms(previous),...added])});
  }
  return result;
}

async function runAll(){throw new Error('백엔드 연결이 필요합니다.');}

function updateReviewState(){
  if(!running&&lastDisplayedRun){lastDisplayedRun.reviewPending=lastDisplayedRun.hasAnalysis?posts.filter(p=>!['검토완료','검토생략'].includes(p.status)).length:0;$('stateMeta').textContent=runCompletionText(lastDisplayedRun);}
}

function approvePost(){
  if(running)return;const p=posts.find(x=>articleIdentity(x)===selectedPostId);
  if(!p||['검토완료','검토생략'].includes(p.status)||(lastDisplayedRun&&!lastDisplayedRun.hasAnalysis))return;
  p.status='검토완료';const r=lastDisplayedRun;appendLog(`[REVIEW] ${p.cafe} · ${p.id} 사후 검토 완료`,r);
  if(r){r.articles=copyObject(posts);r.rows=posts.map(a=>[a.cafe,a.id,a.key,a.type,a.status,'화면 테스트']);r.updatedAt=new Date().toISOString();updateReviewState();saveHistory();}
  const next=posts.find(x=>!['검토완료','검토생략'].includes(x.status));selectedPostId=articleIdentity(next||p);renderPosts();refreshHistoryIfOpen();
}

function pptOnly(){if(running)return;openExecutionConfirm('ppt');}
function doPptOnly(runId){
  const source=historyRecords.find(r=>r.id===runId&&r.hasAnalysis&&r.articles?.length);
  if(!source){showMessage('PPT 재작성 불가','선택한 이력에 재사용할 분석 결과가 없습니다.');return;}
  const cfg={...settingsSnapshot(),stepCollect:false,stepAnalyze:false,stepPpt:true,sendMail:false,sourceRun:source.id,summaryCafes:copyObject(summaryCafesForRun(source))};
  return runAll(cfg);
}

// Calendar/filter/list/detail are all derived from one filter state.
function isInterruptedStatus(status){return /사용자 중지|오류|실패|중단/.test(String(status||''));}
function matchingHistory(){
  const q=historyState.query.trim().toLocaleLowerCase();
  return historyRecords.filter(r=>(!historyState.date||dayKey(r.startedAt)===historyState.date)&&(!q||[r.id,r.type,r.status,r.provider,r.type==='V9 이관'?'V9 이관':'V10'].join(' ').toLocaleLowerCase().includes(q))).sort((a,b)=>+dateValue(b.startedAt)-+dateValue(a.startedAt));
}
function renderHistory(){
  const visible=matchingHistory();if(!visible.some(r=>r.id===historyState.selectedId))historyState.selectedId=visible[0]?.id||null;
  const dateLabel=historyState.date?historyState.date.replaceAll('-','.'):'전체 날짜';
  $('historyFilterSummary').textContent=`${dateLabel} · ${visible.length}건${historyState.query.trim()?' · 검색 적용':''}`;
  const allBtn=$('historyAllBtn');if(allBtn){const filtered=!!historyState.date||!!historyState.query.trim();allBtn.classList.toggle('active-filter',filtered);allBtn.textContent=filtered?'전체 내역으로':'전체 내역';allBtn.disabled=false;}
  $('historyRunList').innerHTML=visible.length?visible.map(r=>`<button type="button" class="history-run-item ${r.id===historyState.selectedId?'active':''} ${isInterruptedStatus(r.status)?'interrupted':''}" data-history-id="${esc(r.id)}" aria-pressed="${r.id===historyState.selectedId}"><div class="history-run-id" title="${esc(r.id)}">${esc(r.id)}</div><div class="history-run-sub"><span>${esc(formatTime(r.startedAt,true))}</span><span>${esc(r.status)}</span></div><div class="history-run-type"><span class="source-label">${r.type==='V9 이관'?'V9 이관':'V10'}</span>${esc(r.type)} · ${esc(r.posts||'')}${r.source==='sample'?' · PPT '+esc(r.slides):''}</div></button>`).join(''):`<div class="empty-small">${historyState.date?esc(dateLabel)+'에 ':''}조건에 맞는 실행 기록이 없습니다.</div>`;
  renderCalendar();renderHistoryDetail(historyState.selectedId);
}
function renderCalendar(){
  const year=historyState.year,month=historyState.month;
  const recordYears=historyRecords.map(r=>parts(dateValue(r.startedAt)).y).filter(Number.isFinite),currentYear=parts(new Date()).y;
  const minYear=Math.min(currentYear-10,...recordYears,year),maxYear=Math.max(currentYear+2,...recordYears,year);
  const yearSelect=$('calendarYear'),monthSelect=$('calendarMonth');
  if(yearSelect){yearSelect.innerHTML=Array.from({length:maxYear-minYear+1},(_,i)=>minYear+i).map(y=>`<option value="${y}">${y}년</option>`).join('');yearSelect.value=String(year);}
  if(monthSelect){monthSelect.innerHTML=Array.from({length:12},(_,i)=>`<option value="${i}">${i+1}월</option>`).join('');monthSelect.value=String(month);}
  const stats=new Map();historyRecords.forEach(r=>{const key=dayKey(r.startedAt);if(!key)return;const s=stats.get(key)||{count:0,bad:0};s.count++;if(isInterruptedStatus(r.status))s.bad++;stats.set(key,s);});
  const first=new Date(Date.UTC(year,month,1)).getUTCDay(),days=new Date(Date.UTC(year,month+1,0)).getUTCDate();
  let html='';for(let i=0;i<first;i++)html+='<div class="calendar-day blank" aria-hidden="true"></div>';
  for(let day=1;day<=days;day++){
    const key=`${year}-${pad(month+1)}-${pad(day)}`,stat=stats.get(key)||{count:0,bad:0},count=stat.count,bad=stat.bad,selected=historyState.date===key;
    const stateClass=!count?'':bad===0?' ok-day':bad===count?' fail-day':' mixed-day';
    const stateText=!count?'실행 없음':bad?`실행 ${count}건 · 중지/오류 ${bad}건`:`실행 ${count}건 · 정상 기록`;
    html+=`<button type="button" class="calendar-day${count?' has-run':''}${stateClass}${selected?' selected':''}${key===dayKey(new Date())?' today':''}" data-date="${key}" aria-pressed="${selected}" aria-label="${year}년 ${month+1}월 ${day}일 ${stateText}" title="${key} · ${stateText}"><span>${day}</span>${count?`<span class="run-count">${count}회</span>`:''}</button>`;
  }
  for(let i=(first+days)%7;i!==0&&i<7;i++)html+='<div class="calendar-day blank" aria-hidden="true"></div>';
  $('calendarGrid').innerHTML=html;$('calendarFilterText').textContent=historyState.date?historyState.date.replaceAll('-','.')+' 선택':'전체 날짜';
  $('calendarReset').setAttribute('aria-pressed',String(!historyState.date&&!historyState.query.trim()));
  $('calendarReset').title='날짜 선택과 검색어를 모두 지우고 모든 실행 기록을 표시합니다.';
  $('calendarReset').setAttribute('aria-label','전체 날짜와 실행 기록 보기');
}
function selectHistoryDate(key){historyState.date=historyState.date===key?'':key;historyState.selectedId=null;renderHistory();}
function resetHistoryFilters(){
  historyState.date='';historyState.query='';historyState.selectedId=null;$('historySearch').value='';renderHistory();toast(`전체 날짜의 실행 기록 ${historyRecords.length}건을 표시합니다.`);
}
function changeHistoryMonth(delta){const d=new Date(Date.UTC(historyState.year,historyState.month+delta,1));historyState.year=d.getUTCFullYear();historyState.month=d.getUTCMonth();renderCalendar();}
function updateGlobalHeader(view=activePrimaryView){
  if(view!=='stats')closeStatsExportMenu();
  activePrimaryView=view;
  const map={main:'operationBtn',history:'historyBtn',stats:'statsBtn'};
  ['operationBtn','historyBtn','statsBtn'].forEach(id=>{const b=$(id);if(!b)return;const active=id===map[view];b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  const historyActions=$('historyHeaderActions');if(historyActions)historyActions.hidden=view!=='history';
  const statsActions=$('statsHeaderActions');if(statsActions)statsActions.hidden=view!=='stats';
  const run=$('runBtn');if(run)run.hidden=view!=='main'&&!running;
}

function openOperation(){
  hideTooltip();$('historyView').classList.remove('show');$('historyView').setAttribute('aria-hidden','true');$('statsView').classList.remove('show');$('statsView').setAttribute('aria-hidden','true');
  updateGlobalHeader('main');syncModalInert();$('operationBtn')?.focus();
}

function openHistory(){hideTooltip();$('statsView').classList.remove('show');$('statsView').setAttribute('aria-hidden','true');$('historyView').classList.add('show');$('historyView').setAttribute('aria-hidden','false');updateGlobalHeader('history');syncModalInert();renderHistory();$('historyBtn')?.focus();}

function closeHistory(){openOperation();}

function switchHistoryToStats(){openStats();}

function switchStatsToHistory(){openHistory();}

function refreshHistoryIfOpen(){if($('historyView').classList.contains('show'))renderHistory();}
function historyDetailsPairs(r){
  const cfg=r.settings;return [
    ['실행 유형',r.type],['상태',r.status],['실행 시작',formatTime(r.startedAt,true)],['실행 종료',r.endedAt?formatTime(r.endedAt,true):'진행 중'],
    ['경과 시간',duration((r.endedAt?+dateValue(r.endedAt):Date.now())-+dateValue(r.startedAt))],['현재 / 최종 단계',r.stage||r.status],
    ['수집 범위',r.periodStart&&r.periodEnd?formatTime(r.periodStart)+' → '+formatTime(r.periodEnd):r.period+' · 정확한 기간 미기록'],['카페',r.cafes],
    ['키워드',r.keywords],['카페 병렬 수',r.parallel],['AI 방식',r.provider],['게시글 / PPT',`${r.posts} / ${r.slides}`],
    ['이번 실행 작업',cfg?[cfg.stepCollect?'웹 수집':null,cfg.stepAnalyze?'AI 분석':null,cfg.stepPpt?'PPT 생성':null,cfg.sendMail&&cfg.stepPpt?'Outlook 발송':null].filter(Boolean).join(' → '):'이전 시안값'],
    ['예약 설정',cfg?(cfg.scheduleEnabled?cfg.scheduleTime+' KST'+(cfg.weekdaysOnly?' · 주말 제외':''):'예약 사용 안 함'):'미기록'],
    ['기간 지정',(cfg?.stepCollect&&cfg.range==='기간 직접 지정')?`${cfg.periodStart} ~ ${cfg.periodEnd}`:'사용 안 함'],['중복 처리',(cfg?.stepCollect&&cfg.range==='기간 직접 지정')?(cfg.periodDedupeEnabled?`${r.dedupePolicy||'이미 수집한 게시글 건너뛰기'} · 제외 ${r.duplicatesSkipped||0}건 · 갱신 ${r.articlesUpdated||0}건 · 신규 ${r.newArticles||0}건`:`${r.dedupePolicy||'기존 저장 게시글도 다시 포함'} · 재수집 ${r.articlesRecollected||0}건 · 신규 ${r.newArticles||0}건`):'기본 정책'],['재확인 기간',cfg?(cfg.stepCollect&&cfg.range==='이전 실행 이후'?cfg.overlap+'일':'현재 수집 방식에 미적용'):'미기록'],['PPT 이미지',cfg?cfg.ppi+' ppi':'미기록'],
    ['보내는 사람(From)',cfg?.sendMail?(r.outlookSender||'Outlook 연결 후 자동 표시'):'발송 안 함'],['To',cfg?.sendMail?cfg.mailTo:'발송 안 함'],['CC',cfg?.sendMail?(cfg.mailCc||'없음'):'—'],
    ['메일 제목',cfg?.sendMail?cfg.mailSubject:'—'],['발송 범위',cfg?.sendMail?(cfg.mailScope==='summary10'?`요약 슬라이드만 (${summaryCountForRun(r)}장)`:'전체 보고서'):'—'],['메일 실행 결과',mailStatusLabel(r.mailStatus)],
  ];
}

function pathBasename(value){
  const text=String(value||'').trim();if(!text)return 'PPT 파일 미기록';
  return text.split(/[\\/]/).filter(Boolean).pop()||text;
}
function previewPeriod(r){
  if(r.periodStart&&r.periodEnd)return `${periodText(r.periodStart)} → ${periodText(r.periodEnd)}`;
  return r.period||'수집 기간 미기록';
}

function renderExecutionPptPreview(){
  const meta=$('executionPreviewMeta'),empty=$('executionPreviewEmpty'),render=$('executionPreviewRender'),file=$('executionPreviewFile'),path=$('executionPreviewPath'),note=$('executionPreviewNote'),openBtn=$('executionPreviewOpenBtn'),historyBtn=$('executionPreviewHistoryBtn');
  if(!meta||!render)return;
  const r=activeRun||lastDisplayedRun||latestRun(),linked=r?linkedPptFiles.get(r.id):null;
  const signature=JSON.stringify([r?.id,r?.status,r?.stageKey,r?.stepsDone,r?.updatedAt,r?.posts,r?.slides,r?.summaryCafes,linked?.name,linked?.size]);
  if(previewRenderSignature===signature)return;previewRenderSignature=signature;
  historyBtn.disabled=!r;openBtn.disabled=!pptAvailable(r);
  if(!r){empty.hidden=false;render.hidden=true;render.innerHTML='';file.textContent='PPT 파일 없음';path.textContent='실행 기록 없음';meta.textContent='PPT 생성 단계가 끝나면 기록 기반 예시를 표시합니다.';note.textContent='현재 HTML은 실제 PPT를 생성하지 않습니다.';return;}
  const wants=!!r.settings?.stepPpt||r.source==='sample';file.textContent=linked?.name||pathBasename(r.ppt);path.textContent=r.ppt||'경로 미기록';
  if(!pptStageDone(r)){
    empty.hidden=false;render.hidden=true;render.innerHTML='';empty.textContent=wants?(running?'PPT 생성 단계가 끝나지 않았습니다.':'PPT 생성 단계를 완료하지 않아 표시할 결과가 없습니다.'):'이 실행은 PPT 생성을 선택하지 않았습니다.';
    meta.textContent=running?'작업 진행 중':'PPT 생성 미완료';note.textContent='미생성·중지 상태의 경로를 완료된 PPT로 표시하지 않습니다.';return;
  }
  empty.hidden=true;render.hidden=false;
  meta.textContent=linked?'파일명 연결됨 · 그림은 실행 기록 기반 예시':'실행 기록 기반 첫 페이지 예시 · 실제 PPT 이미지 아님';
  note.textContent='실제 PPT 슬라이드를 렌더링한 화면이 아닙니다. 연결한 파일의 내용은 PPT 열기로 확인하세요.';
  render.innerHTML=`<div class="ppt-slide-preview" aria-label="실행 기록으로 구성한 첫 페이지 예시"><div class="ppt-topline"></div><div class="ppt-inner"><div class="ppt-kicker">AUTOMOTIVE COMMUNITY MONITORING · ALL</div><div class="ppt-title">전체 카페 종합 요약</div><div class="ppt-subtitle">${esc(r.type)} · ${esc(formatTime(r.startedAt,true))}</div><div class="ppt-divider"></div><div class="ppt-summary"><div class="k">수집 대상</div><div class="v">${esc(previewPeriod(r))}</div><div class="k">카페</div><div class="v">${esc(r.cafes||'미기록')}</div><div class="k">처리 결과</div><div class="v">${esc(r.posts||'—')} · ${esc(r.slides||'미기록')}</div><div class="k">AI</div><div class="v">${esc(r.provider||'미기록')}</div></div><div class="ppt-footer"><span>${esc(r.id)}</span><span>1 / ${summaryCountForRun(r)}</span></div></div></div>`;
}

function renderPptPreview(r){
  const linked=linkedPptFiles.get(r.id);const fileName=linked?.name||pathBasename(r.ppt);
  const slideText=String(r.slides||'미기록');const includeCafeSummary=r.settings?.summarySlide!==false;
  const slides=summarySlidesForRun(r);
  const selected=Math.max(1,Math.min(slides.length,Number(pptPreviewSelections.get(r.id)||1)));
  const slide=slides[selected-1]||slides[0];
  const statusText=linked?'파일명 연결 · 그림은 기록 기반 예시':'실행 기록 기반 예시 · 실제 파일 미연결';
  const previewClass=linked?'ppt-slide-preview linked':'ppt-slide-preview';
  const scopeText=r.settings?.sendMail?(r.settings.mailScope==='summary10'?`요약 슬라이드만 (${slides.length}장)`:'전체 보고서'):'메일 발송 안 함';
  if(!pptStageDone(r)&&!linked)return `<section class="history-section"><h4>PPT 미생성</h4><p class="history-note">PPT 생성 단계를 완료하지 않았습니다. 미완료 경로를 생성된 파일로 표시하지 않습니다.</p><button class="btn small" data-history-action="attach">실제 PPT 연결</button></section>`;
  return `<section class="history-section history-ppt-section"><div class="history-ppt-head"><h4>PPT 요약 슬라이드 미리보기</h4><span class="preview-note">실행 기록으로 구성한 예시입니다. 연결한 PPT의 실제 이미지가 아닙니다.</span></div>
    <div class="history-ppt-layout">
      <div class="ppt-preview-frame">
        <div class="ppt-slide-stage" tabindex="0" data-ppt-preview-root data-run-id="${esc(r.id)}" aria-label="PPT 요약 미리보기. 키보드 왼쪽과 오른쪽 화살표로 슬라이드를 이동할 수 있습니다.">
          <button type="button" class="ppt-nav ppt-nav-prev" data-preview-slide="${Math.max(1,selected-1)}" data-run-id="${esc(r.id)}" aria-label="이전 슬라이드" title="이전 슬라이드 (←)" ${selected===1?'disabled':''}>‹</button>
          <div class="${previewClass}" aria-label="${esc(fileName)} ${selected}번 슬라이드 미리보기">
            <div class="ppt-topline"></div><div class="ppt-inner">
              <div class="ppt-kicker">AUTOMOTIVE COMMUNITY MONITORING · ${esc(slide.code)}</div>
              <div class="ppt-title">${esc(slide.label)}</div>
              <div class="ppt-subtitle">${esc(r.type)} · ${esc(formatTime(r.startedAt,true))}</div>
              <div class="ppt-divider"></div>
              <div class="ppt-summary">
                <div class="k">수집 대상</div><div class="v">${esc(previewPeriod(r))}</div>
                <div class="k">카페</div><div class="v">${selected===1?esc(r.cafes||'미기록'):esc(slide.code)}</div>
                <div class="k">처리 결과</div><div class="v">게시글 ${esc(r.posts||'—')} · PPT ${esc(slideText)}</div>
                <div class="k">AI</div><div class="v">${esc(r.provider||'미기록')}</div>
              </div>
              <div class="ppt-footer"><span>${esc(r.id)}</span><span>${selected} / ${slides.length}</span></div>
            </div>
          </div>
          <button type="button" class="ppt-nav ppt-nav-next" data-preview-slide="${Math.min(slides.length,selected+1)}" data-run-id="${esc(r.id)}" aria-label="다음 슬라이드" title="다음 슬라이드 (→)" ${selected===slides.length?'disabled':''}>›</button>
        </div>
        <div class="ppt-preview-caption"><span><b>${selected}번 슬라이드</b> · ${esc(slide.label)}</span><span class="ppt-preview-keyhint">기록 기반 예시 · 키보드 ← →</span></div>
        <div class="ppt-thumb-strip" aria-label="요약 슬라이드 선택">${slides.map(s=>`<button type="button" class="ppt-thumb ${s.n===selected?'active':''}" data-preview-slide="${s.n}" data-run-id="${esc(r.id)}" aria-pressed="${s.n===selected}" title="${s.n}번 · ${esc(s.label)}"><span class="thumb-no">${s.n}</span><span class="thumb-code">${esc(s.code)}</span></button>`).join('')}</div>
      </div>
      <div class="ppt-result-info">
        <div class="ppt-file-title">${esc(fileName)}</div>
        <div class="ppt-file-path">${esc(r.ppt||'PPT 경로가 기록되지 않았습니다.')}</div>
        <div class="ppt-file-meta">
          <div class="k">슬라이드</div><div class="v">${esc(slideText)}</div>
          <div class="k">미리보기</div><div class="v">${includeCafeSummary?`요약 ${slides.length}장 · 전체 1장 + 카페별 ${slides.length-1}장`:'요약 1장 · 전체 카페 종합 요약'}</div>
          <div class="k">메일 첨부</div><div class="v">${esc(scopeText)}</div>
          <div class="k">연결 상태</div><div class="v">${esc(statusText)}</div>
          <div class="k">실행 ID</div><div class="v">${esc(r.id)}</div>
        </div>
        <div class="ppt-result-actions">
          <button class="btn small open-ppt" data-history-action="open-ppt" ${pptAvailable(r)?'':'disabled'}>PPT 열기</button>
          <button class="btn small" data-history-action="open-folder">폴더에서 보기</button>
          <button class="btn small" data-history-action="copy" data-key="ppt">경로 복사</button>
          <button class="btn small" data-history-action="attach">${linked?'다른 PPT 연결':'실제 PPT 연결'}</button>
        </div>
        <div class="ppt-preview-status">${linked?`<strong>${esc(linked.name)}</strong> 파일이 선택되어 있습니다. 위 그림은 파일 내용이 아닌 실행 기록으로 구성한 예시입니다. 브라우저를 닫으면 파일 연결은 해제됩니다.`:(includeCafeSummary?`실제 V10에서는 실행 완료 시 <strong>preview_slide01.png ~ preview_slide${pad(slides.length)}.png</strong>를 함께 저장하고 실행 이력을 열 때 자동으로 표시하는 구조를 가정합니다.`:`실제 V10에서는 실행 완료 시 <strong>preview_slide01.png</strong>를 저장하고 실행 이력을 열 때 자동으로 표시하는 구조를 가정합니다.`)}</div>
      </div>
    </div>
  </section>`;
}

function renderHistoryDetail(id){
  const r=historyRecords.find(x=>x.id===id),root=$('historyDetail');
  if(!r){root.innerHTML=`<div class="history-empty"><strong>선택한 조건의 실행 기록이 없습니다.</strong><p>${historyState.date?esc(historyState.date.replaceAll('-','.'))+'의 기록을 찾지 못했습니다.':'검색 조건을 확인하세요.'} 다른 날짜를 선택하거나 전체 기록으로 돌아가세요.</p><button class="btn small" data-history-action="reset">전체 기록 보기</button></div>`;return;}
  const pairs=historyDetailsPairs(r);const rows=r.rows||[];
  root.innerHTML=`<div class="history-detail-record ${isInterruptedStatus(r.status)?'interrupted-record':''}"><div class="history-detail-head"><div class="title"><h3>${esc(r.id)}</h3><p>${esc(r.type)} · ${esc(r.status)} · ${esc(formatTime(r.startedAt,true))}</p><p class="record-provenance">${r.type==='V9 이관'?'기존 실행 기록을 DB로 이관':'V10 실제 실행 기록 · 당시 설정 보존'}</p></div><div class="history-detail-actions"><button class="btn small" data-history-action="export" title="현재 선택한 실행의 설정, 실행 시간, 상태, 결과 파일 경로, 수집·분석 정보를 JSON 파일로 저장합니다.">실행 기록 내보내기</button></div></div>
  ${renderPptPreview(r)}
  <section class="history-section"><h4>실행 정보 · 당시 설정</h4><div class="history-meta">${pairs.map(([k,v])=>`<div class="k">${esc(k)}</div><div>${esc(v||'—')}</div>`).join('')}<div class="k">특이사항</div><div class="span3">${esc(r.notes||'없음')}</div></div></section>
  <section class="history-section"><h4>결과 파일 / 경로</h4><div class="history-file-list">${([['결과 폴더','output'],['PPT 전체본','ppt'],...(r.summaryPpt?[['메일 첨부용 요약 PPT','summaryPpt']]:[]),['분석 결과','analysis'],['로그','logPath']]).map(([label,key])=>`<div class="name">${label}</div><div class="history-path">${esc(r[key]||'미기록')}</div><div><button class="history-copy" data-history-action="copy" data-key="${key}" aria-label="${label} 경로 복사">복사</button></div>`).join('')}</div><p class="history-note">파일을 열 때 존재 여부와 저장 당시 해시를 검사합니다. 미리보기는 실제 요약 슬라이드입니다. 상세 슬라이드는 PPT 파일에서 확인하세요.</p><div id="attachedFileInfo" class="file-linked"></div></section>
  <section class="history-section"><h4>수집 / 분석 내용 <span class="source-label">DB 저장 자료</span></h4><div class="history-table-wrap"><table class="history-table"><thead><tr><th style="width:52px">카페</th><th style="width:84px">게시글</th><th>키워드</th><th style="width:83px">AI 구분</th><th style="width:86px">검토</th><th style="width:89px">처리</th></tr></thead><tbody>${rows.length?rows.map(row=>`<tr tabindex="0" data-history-post="${esc(row[1])}" data-history-cafe="${esc(row[0])}">${row.map(v=>`<td title="${esc(v)}">${esc(v)}</td>`).join('')}</tr>`).join(''):'<tr><td colspan="6">저장된 게시글 목록이 없습니다.</td></tr>'}</tbody></table></div><div id="historyArticleDetail" class="history-note">게시글 행을 누르면 저장된 원문과 분석 내용을 확인합니다.</div></section>
  <section class="history-section"><h4>실행 로그</h4><pre class="history-log">${esc(r.logText||'기록 없음')}</pre></section></div>`;
  renderLinkedFile(r.id);
}
function renderLinkedFile(id){
  const root=$('attachedFileInfo');if(!root)return;const file=linkedPptFiles.get(id);
  root.innerHTML=file?`<span>${esc(file.name)} · ${(file.size/1024).toFixed(1)} KB</span><button class="btn small" data-history-action="download-ppt">연결 파일 받기</button><span class="history-note">파일 연결은 이 페이지를 닫기 전까지만 유지됩니다.</span>`:'<span class="history-note">연결된 실제 PPT 없음 · 로컬 파일은 업로드하지 않습니다.</span>';
}
async function copyText(text){
  try{await navigator.clipboard.writeText(text);toast('경로를 복사했습니다.');}
  catch{showMessage('경로 복사','이 환경에서는 클립보드를 사용할 수 없습니다. 아래 경로를 선택해 복사하세요.\n\n'+text);}
}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.style.display='none';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function exportHistory(record=null){const payload={schema:'v10-ui-history/7',exportedAt:new Date().toISOString(),mode:'ui-prototype',notice:'시안과 화면 테스트 기록입니다. 실제 카페 수집 또는 AI/PPT/메일 실행 기록이 아닙니다.',records:record?[record]:historyRecords};downloadBlob(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),record?record.id+'.json':'V10_실행이력.json');}
function historyAction(action,key){
  if(action==='reset'){resetHistoryFilters();return;}const r=historyRecords.find(r=>r.id===historyState.selectedId);if(!r)return;
  if(action==='copy'&&['output','ppt','summaryPpt','analysis','logPath'].includes(key)){copyText(r[key]||'미기록');return;}
  if(action==='attach'){$('pptFileInput').dataset.runId=r.id;$('pptFileInput').value='';$('pptFileInput').click();return;}
  if(action==='export'){exportHistory(r);return;}
  if(action==='download-ppt'||action==='open-ppt'){
    const linked=linkedPptFiles.get(r.id);
    if(linked){downloadBlob(new Blob([linked],{type:'application/octet-stream'}),linked.name);toast('연결한 PPT 파일을 내려받도록 요청했습니다. 실제 슬라이드는 PowerPoint에서 확인하세요.');return;}
    if(action==='download-ppt')return;
    if(!pptAvailable(r)){showMessage('PPT 결과 없음','PPT 생성 단계가 완료되지 않았거나 기록된 PPT 경로가 없습니다. 실제 파일을 연결한 경우에만 열 수 있습니다.');return;}
    showMessage('PPT 열기 안내','이 HTML은 Windows의 파일 경로를 직접 실행하지 않습니다. 기록된 경로의 실제 파일 존재 여부도 아직 확인하지 않았습니다.\n\n실제 PPT 연결 버튼으로 파일을 선택하거나 아래 경로를 확인하세요.\n\n'+(r.ppt||'미기록'));return;
  }
  if(action==='open-folder')showMessage('결과 폴더 안내','실제 Windows 탐색기 연결은 아직 없습니다. 아래는 실행에 기록된 경로입니다.\n\n'+(r.output||'미기록'));
}

function selectHistoryArticle(id,cafe){
  const run=historyRecords.find(r=>r.id===historyState.selectedId),root=$('historyArticleDetail');
  if(!run||!root)return;
  const matches=(run.articles||[]).filter(x=>String(x.id)===String(id)&&(!cafe||x.cafe===cafe||x.cafeId===cafe));
  if(matches.length!==1){root.textContent=matches.length?'카페까지 지정된 게시글 행을 선택하세요.':'이 실행에는 해당 게시글의 원문이 저장되어 있지 않습니다.';return;}
  const a=matches[0];root.innerHTML=`<div class="history-review-text"><div><strong>원문 · ${esc(a.title)}</strong>\n\n${esc(a.raw)}</div><div><strong>AI 분석</strong>\n\n${esc(a.doc)}\n${esc(a.issue)}\n\n근거: ${esc(a.evidence)}\n검토: ${esc(a.status)}</div></div><div style="margin-top:9px"><button class="btn small stats-button" type="button" data-history-open-stats="${esc(a.id)}" data-history-open-stats-cafe="${esc(a.cafe)}">데이터 분석에서 보기 →</button></div>`;
}

// ===== 데이터 분석 Alpha =====
let statsDates=[];
const statsRecords=[];
const statsState={periodMode:'all',cafe:'all',keyword:'all',from:'2026-09-19',to:'2026-09-21'};
const statsTrendView={granularity:'day',page:null};
let statsQueryDirty=false;
const statsCalendarState={year:2026,month:8};
let statsSelectedRecordId=null;
let statsDetailDate='';
let statsDetailDateEnd='';
let statsDetailCafe='';
let statsDetailKeyword='';
function statsRecordKey(r){return articleIdentity(r);}

function statsRecordsInRange(){return statsKnownRecords().filter(r=>isDateKey(r.date)&&r.date>=statsState.from&&r.date<=statsState.to);}

function statsFilteredRecords(){return statsRecordsInRange().filter(r=>(statsState.cafe==='all'||r.cafeId===statsState.cafe)&&(statsState.keyword==='all'||r.keywordIds.includes(statsState.keyword)));}

function statsApplyDetailPeriod(rows){return statsDetailDate?rows.filter(r=>r.date>=statsDetailDate&&r.date<=(statsDetailDateEnd||statsDetailDate)):rows;}

function statsCafeRows(){
  let base=statsFilteredRecords();if(statsDetailKeyword)base=base.filter(r=>r.keywordIds.includes(statsDetailKeyword));
  const selected=statsApplyDetailPeriod(base);
  return statsCatalogCafes().map(c=>({...c,count:base.filter(r=>r.cafeId===c.id).length,selectedCount:selected.filter(r=>r.cafeId===c.id).length}));
}

function statsKeywordScopePosts(){let rows=statsFilteredRecords();return statsDetailCafe?rows.filter(r=>r.cafeId===statsDetailCafe):rows;}

function statsKeywordRows(){
  const base=statsKeywordScopePosts(),selected=statsApplyDetailPeriod(base);
  return statsCatalogKeywords().map(k=>{const matched=base.filter(r=>r.keywordIds.includes(k.id));return {...k,count:matched.length,selectedCount:selected.filter(r=>r.keywordIds.includes(k.id)).length,multiCount:matched.filter(r=>r.keywordIds.length>1).length};});
}

function statsCalendarRecords(){return statsFilteredRecords().filter(r=>(!statsDetailCafe||r.cafeId===statsDetailCafe)&&(!statsDetailKeyword||r.keywordIds.includes(statsDetailKeyword)));}

function statsUniquePosts(){return statsFilteredRecords().length;}

function statsPeriodModeName(mode=statsState.periodMode){return ({'7d':'최근 7일','30d':'최근 30일','90d':'최근 3개월','365d':'최근 1년',all:'전체 기간',custom:'기간 직접 지정'})[mode]||'전체 기간';}

function statsPeriodLabel(){const range=`${statsState.from.replaceAll('-','.')} ~ ${statsState.to.replaceAll('-','.')}`;return statsState.periodMode==='all'?`전체 기간 · ${range}`:statsState.periodMode==='custom'?range:`${statsPeriodModeName()} · ${range}`;}

function statsDateRange(){
  if(statsRangeError(statsState.from,statsState.to))return [];
  const dates=[],start=+new Date(statsState.from+'T00:00:00Z'),end=+new Date(statsState.to+'T00:00:00Z');
  for(let time=start;time<=end;time+=DAY)dates.push(new Date(time).toISOString().slice(0,10));
  return dates;
}

function statsDateSeries(){
  const counts=new Map();for(const r of statsFilteredRecords())counts.set(r.date,(counts.get(r.date)||0)+1);
  return statsDateRange().map(date=>({date,d:date.slice(5).replace('-','/'),v:counts.get(date)||0}));
}

function statsGranularityPageSize(granularity){return granularity==='day'?14:granularity==='week'?10:12;}
function statsTrendPage(rows,unit){
  const size=statsGranularityPageSize(unit),pages=Math.max(1,Math.ceil(rows.length/size));
  if(statsTrendView.page==null){let index=statsDetailDate?rows.findIndex(r=>r.start<=statsDetailDate&&r.end>=statsDetailDate):-1;if(index<0)index=rows.findLastIndex(r=>r.v>0);if(index<0)index=rows.length-1;statsTrendView.page=Math.max(0,Math.floor(index/size));}
  const page=Math.max(0,Math.min(pages-1,statsTrendView.page));statsTrendView.page=page;return {rows:rows.slice(page*size,(page+1)*size),page,pages};
}

function statsTrendWindowLabel(rows){if(!rows.length)return '데이터 없음';const a=rows[0],b=rows.at(-1),start=a.start||a.date,end=b.end||b.date;if(statsTrendView.granularity==='month'){const sm=start.slice(0,7).replace('-','.'),em=end.slice(0,7).replace('-','.');return sm===em?sm:`${sm} ~ ${em}`;}return start===end?start.replaceAll('-','.'):`${start.replaceAll('-','.')} ~ ${end.replaceAll('-','.')}`;}
function statsGranularityName(g){return ({day:'일별',week:'주별',month:'월별'})[g]||'일별';}
function statsGroupTrendRows(rows,granularity){if(granularity==='day')return rows.map(r=>({...r,start:r.date,end:r.date,key:r.date,label:r.d}));const map=new Map();for(const row of rows){const d=new Date(row.date+'T00:00:00Z');let key;if(granularity==='week'){const shift=(d.getUTCDay()+6)%7;key=new Date(+d-shift*86400000).toISOString().slice(0,10);}else key=row.date.slice(0,7);let g=map.get(key);if(!g){g={key,start:row.date,end:row.date,v:0};map.set(key,g);}g.end=row.date;g.v+=row.v;}return [...map.values()].map(g=>{const s=g.start.split('-'),e=g.end.split('-');const label=granularity==='month'?`${s[0]}.${s[1]}`:`${Number(s[1])}/${Number(s[2])}~${Number(e[1])}/${Number(e[2])}`;return {...g,date:g.start,d:label,label};});}
function renderStatsBars(){
  const cafeRows=statsCafeRows(),keyRows=statsKeywordRows(),maxCafe=Math.max(1,...cafeRows.map(c=>c.count)),maxKey=Math.max(1,...keyRows.map(k=>k.count));
  const dateSelected=!!statsDetailDate,label=statsSelectedPeriodLabel();
  $('statsCafeChart').classList.toggle('date-highlight',dateSelected);$('statsKeywordChart').classList.toggle('date-highlight',dateSelected);
  $('statsCafeScope').textContent=dateSelected?'선택 날짜 / 조회 기간':(statsDetailKeyword?`키워드 · ${keywordLabel(statsDetailKeyword)}`:'조회 기간 기준');
  $('statsKeywordScope').textContent=dateSelected?'선택 날짜 / 조회 기간':(statsDetailCafe?`카페 · ${cafeLabel(statsDetailCafe)}`:'조회 기간 기준');
  const scopeTip=dateSelected?`${label}의 건수 / 왼쪽 조회 기간의 건수. 카페·키워드 선택은 유지하며 전체 막대 길이는 바뀌지 않습니다.`:'왼쪽 조회 조건과 카페·키워드 선택에 해당하는 수집 게시글 수입니다.';
  $('statsCafeScope').title=scopeTip;$('statsKeywordScope').title=scopeTip;
  const fill=(row,max)=>`<div class="stats-track"><div class="stats-fill" style="width:${row.count/max*100}%">${dateSelected?`<span class="stats-date-segment" style="width:${row.count?row.selectedCount/row.count*100:0}%"></span>`:''}</div></div>`;
  const number=row=>dateSelected?`<span class="stats-selected-number">${row.selectedCount}</span><span class="stats-count-divider"> / </span><span>${row.count}</span>`:String(row.count);
  $('statsCafeChart').innerHTML=cafeRows.map(c=>{const selected=statsDetailCafe===c.id;return `<div class="stats-bar-row ${selected?'selected':''}" data-stats-cafe="${esc(c.id)}" role="button" tabindex="0" aria-pressed="${selected}" title="${esc(c.name)} · ${dateSelected?label+' '+c.selectedCount+'건 / 조회 기간 ':''}${c.count}건${c.active?'':' · 수집 중단'}"><div class="stats-bar-label" title="${esc(c.name)}${c.active?'':' · 수집 중단'}">${esc(c.name)}${c.active?'':' (수집 중단)'}</div>${fill(c,maxCafe)}<div class="stats-bar-value" data-total="${c.count}" data-selected="${dateSelected?c.selectedCount:''}">${number(c)}</div></div>`;}).join('');
  $('statsKeywordChart').innerHTML=keyRows.map(k=>{const selected=statsDetailKeyword===k.id;return `<div class="stats-bar-row ${selected?'selected ':''}${k.multiCount?'has-overlap':''}" data-stats-key="${esc(k.id)}" role="button" tabindex="0" aria-pressed="${selected}" title="${esc(k.name)} · ${dateSelected?label+' '+k.selectedCount+'건 / 조회 기간 ':''}${k.count}건${k.multiCount?' · 다른 키워드와 함께 매칭된 게시글 포함':''}${k.deleted?' · 삭제됨':k.active?'':' · 검색 중단'}"><div class="stats-bar-label" title="${esc(k.name)}${k.deleted?' · 삭제됨':k.active?'':' · 검색 중단'}">${esc(k.name)}${k.deleted?' (삭제됨)':k.active?'':' (검색 중단)'}</div>${fill(k,maxKey)}<div class="stats-bar-value" data-total="${k.count}" data-selected="${dateSelected?k.selectedCount:''}">${number(k)}</div></div>`;}).join('');
  const status=$('statsDateStatus');if(status){status.hidden=!dateSelected;$('statsDateText').textContent=`날짜 확인 · ${label} · ${statsApplyDetailPeriod(statsCalendarRecords()).length}건`;$('statsDateLegend').textContent='진한 부분: 선택 날짜 · 옅은 부분: 조회 기간';}
}

function statsTrendValues(){
  const effectiveCafe=statsDetailCafe||(statsState.cafe!=='all'?statsState.cafe:''),effectiveKeyword=statsDetailKeyword||(statsState.keyword!=='all'?statsState.keyword:'');
  const source=statsRecordsInRange().filter(r=>(!effectiveCafe||r.cafeId===effectiveCafe)&&(!effectiveKeyword||r.keywordIds.includes(effectiveKeyword)));
  const counts=new Map();for(const r of source)counts.set(r.date,(counts.get(r.date)||0)+1);
  const rows=statsDateRange().map(date=>({date,d:date.slice(5).replace('-','/'),v:counts.get(date)||0}));
  const title=effectiveCafe&&effectiveKeyword?`${cafeLabel(effectiveCafe)} · ${keywordLabel(effectiveKeyword)}`:effectiveCafe?`카페 · ${cafeLabel(effectiveCafe)}`:effectiveKeyword?`키워드 · ${keywordLabel(effectiveKeyword)}`:'전체 게시글';
  return {title,rows};
}

function statsTrendAxisLabelIndexes(rows,width=720){
  if(!rows.length)return new Set();
  const labelWidth=statsTrendView.granularity==='week'?118:statsTrendView.granularity==='month'?83:64;
  const target=Math.max(2,Math.floor((width-64)/labelWidth)),n=rows.length;
  if(n<=target)return new Set(rows.map((_,i)=>i));
  const set=new Set([0,n-1]);for(let k=1;k<target-1;k++)set.add(Math.round(k*(n-1)/(target-1)));
  return set;
}

function renderStatsTrend(){
  const wrap=$('statsTrendWrap');if(!wrap)return;
  const error=statsRangeError(statsState.from,statsState.to);if(error){wrap.innerHTML=`<div class="stats-record-empty">${esc(error)}</div>`;return;}
  const source=statsTrendValues(),unit=statsTrendView.granularity,grouped=statsGroupTrendRows(source.rows,unit),paged=statsTrendPage(grouped,unit),rows=paged.rows;
  $('statsTrendTitle').textContent=source.title;$$('#statsTrendTabs [data-trend-granularity]').forEach(b=>{const active=b.dataset.trendGranularity===unit;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
  $('statsTrendWindow').textContent=statsTrendWindowLabel(rows);$('statsTrendPrev').disabled=paged.page<=0;$('statsTrendNext').disabled=paged.page>=paged.pages-1;
  if(!rows.length){wrap.innerHTML='<div class="stats-record-empty">표시할 날짜가 없습니다.</div>';return;}
  const w=Math.max(230,wrap.clientWidth||720),h=Math.max(105,wrap.clientHeight||220),left=30,right=25,top=24,bottom=30,plotW=w-left-right,plotH=h-top-bottom;
  const max=Math.max(1,...rows.map(r=>r.v)),ticks=[...new Set([0,Math.ceil(max/4),Math.ceil(max/2),Math.ceil(3*max/4),max])].sort((a,b)=>a-b);
  const dates=rows.map(r=>+new Date(r.date+'T00:00:00Z')),lo=dates[0],hi=dates.at(-1);
  const pts=rows.map((r,i)=>({...r,x:lo===hi?left+plotW/2:left+(dates[i]-lo)/(hi-lo)*plotW,y:top+plotH-(r.v/max)*plotH}));
  const line=pts.map((p,i)=>(i?'L':'M')+p.x.toFixed(2)+' '+p.y.toFixed(2)).join(' '),area=line+` L ${pts.at(-1).x} ${top+plotH} L ${pts[0].x} ${top+plotH} Z`;
  const grid=ticks.map(v=>{const y=top+plotH-v/max*plotH;return `<line class="stats-axis" x1="${left}" y1="${y}" x2="${w-right}" y2="${y}"/><text class="stats-trend-label" x="${left-7}" y="${y+3}" text-anchor="end">${v}</text>`;}).join('');
  const labels=statsTrendAxisLabelIndexes(rows,w);
  const nodes=pts.map((p,i)=>{const selected=!!statsDetailDate&&p.start<=(statsDetailDateEnd||statsDetailDate)&&p.end>=statsDetailDate,label=p.start===p.end?p.start.replaceAll('-','.'):`${p.start.replaceAll('-','.')} ~ ${p.end.replaceAll('-','.')}`,anchor=rows.length===1?'middle':i===0?'start':i===rows.length-1?'end':'middle';
    return `<g class="stats-trend-node ${selected?'selected':''}" data-stats-trend-start="${p.start}" data-stats-trend-end="${p.end}" data-stats-trend-label="${esc(label)}" data-stats-trend-count="${p.v}" tabindex="0" role="button" aria-pressed="${selected}" aria-label="${esc(label)} ${p.v}건. 전체 기간 막대를 유지하면서 이 구간을 강조하고 게시글 목록을 표시합니다."><circle class="stats-trend-point" cx="${p.x}" cy="${p.y}" r="4"/><circle class="stats-trend-hit" cx="${p.x}" cy="${p.y}" r="13" fill="transparent"/>${labels.has(i)||selected?`<text class="stats-trend-value" x="${p.x}" y="${p.y-10}" text-anchor="${anchor}">${p.v}</text>`:''}${labels.has(i)?`<text class="stats-trend-label" x="${p.x}" y="${h-7}" text-anchor="${anchor}">${p.d}</text>`:''}</g>`;
  }).join('');
  wrap.innerHTML=`<svg class="stats-trend-svg" viewBox="0 0 ${w} ${h}" role="group" aria-label="${esc(source.title)} ${statsGranularityName(unit)} 게시글 수 추이">${grid}<path class="stats-trend-area" d="${area}"/><path class="stats-trend-line" d="${line}"/>${nodes}</svg><div class="stats-trend-tooltip" id="statsTrendTooltip" role="tooltip" hidden></div>`;
}

function showStatsTrendTooltip(node,event){
  const tip=$('statsTrendTooltip'),wrap=$('statsTrendWrap');if(!tip||!wrap||!node)return;
  tip.innerHTML=`<strong>${esc(node.dataset.statsTrendLabel)}</strong>수집 게시글 ${esc(node.dataset.statsTrendCount)}건`;tip.hidden=false;
  const r=wrap.getBoundingClientRect(),n=node.getBoundingClientRect(),t=tip.getBoundingClientRect();
  let x=event&&Number.isFinite(event.clientX)?event.clientX-r.left:n.left+n.width/2-r.left,y=event&&Number.isFinite(event.clientY)?event.clientY-r.top:n.top-r.top;
  x=Math.max(t.width/2+3,Math.min(r.width-t.width/2-3,x));y=Math.max(t.height+10,Math.min(r.height-3,y));tip.style.left=x+'px';tip.style.top=y+'px';
}

function hideStatsTrendTooltip(){const tip=$('statsTrendTooltip');if(tip)tip.hidden=true;}
function selectStatsTrendPeriod(start,end){
  if(!validStatsDetailPeriod(start,end)){toast('현재 조회 기간 안의 날짜를 선택하세요.');return;}
  const same=statsDetailDate===start&&(statsDetailDateEnd||statsDetailDate)===end;
  statsSelectedRecordId=null;statsDetailDate=same?'':start;statsDetailDateEnd=same?'':end;
  const d=new Date(start+'T00:00:00Z');statsCalendarState.year=d.getUTCFullYear();statsCalendarState.month=d.getUTCMonth();
  // A date is a highlight within the existing scope, NOT a replacement filter.
  // Keep the cafe/keyword choices and all comparison bars / trend points.
  renderStatsBars();renderStatsTrend();renderStatsDetails();renderStatsCalendar();
}

function selectStatsDetailCafe(ref){
  const c=getCafe(ref);if(!c||!statsCatalogCafes().some(v=>v.id===c.id))return;
  statsSelectedRecordId=null;statsDetailCafe=statsDetailCafe===c.id?'':c.id;renderStatsBars();renderStatsTrend();renderStatsDetails();renderStatsCalendar();
}

function selectStatsDetailKeyword(ref){
  const k=getKeyword(ref);if(!k||!statsCatalogKeywords().some(v=>v.id===k.id))return;
  statsSelectedRecordId=null;statsDetailKeyword=statsDetailKeyword===k.id?'':k.id;renderStatsBars();renderStatsTrend();renderStatsDetails();renderStatsCalendar();
}

function clearStatsDetailSelection(){statsSelectedRecordId=null;statsDetailDate='';statsDetailDateEnd='';statsDetailCafe='';statsDetailKeyword='';statsTrendView.page=null;renderStatsBars();renderStatsTrend();renderStatsDetails();renderStatsCalendar();}

function renderStatsDetails(){
  const rows=statsDetailRows(),drill=[statsSelectedPeriodLabel(),statsDetailCafe?cafeLabel(statsDetailCafe):'',statsDetailKeyword?keywordLabel(statsDetailKeyword):''].filter(Boolean);
  const base=[statsState.cafe!=='all'?cafeLabel(statsState.cafe):'',statsState.keyword!=='all'?keywordLabel(statsState.keyword):''].filter(Boolean);
  $('statsDetailTitle').textContent=drill.length?`확인 · ${drill.join(' · ')}`:base.length?`조회 조건 · ${base.join(' · ')}`:'전체 데이터';$('statsDetailCount').textContent=`${rows.length}건`;
  const clear=$('statsDetailDateClear');clear.hidden=!drill.length;clear.classList.toggle('hidden',!drill.length);clear.textContent='선택 해제';
  if(!rows.some(r=>statsRecordKey(r)===statsSelectedRecordId))statsSelectedRecordId=rows[0]?statsRecordKey(rows[0]):null;
  $('statsDetailBody').innerHTML=rows.length?rows.map(r=>`<tr class="${statsRecordKey(r)===statsSelectedRecordId?'selected':''}" data-stats-record="${esc(statsRecordKey(r))}" tabindex="0" aria-selected="${statsRecordKey(r)===statsSelectedRecordId}"><td>${esc(r.date.replaceAll('-','.'))}</td><td title="${esc(cafeDisplayName(r.cafeId))}">${esc(cafeLabel(r.cafeId))}</td><td>${esc(r.id)}</td><td title="${esc(r.title)}">${esc(r.title)}</td><td title="${esc(r.keys.join(', '))}">${esc(r.keys.join(', '))}</td><td>${esc(r.type)}</td><td>${esc(r.status)}</td></tr>`).join(''):'<tr><td colspan="7">선택한 조건에 저장된 게시글이 없습니다.</td></tr>';
  renderStatsRecordDetail();
}

function renderStatsRecordDetail(){
  const root=$('statsRecordDetail'),r=statsDetailRows().find(r=>statsRecordKey(r)===statsSelectedRecordId);
  if(!r){root.innerHTML='<div class="stats-record-empty">표시할 게시글이 없습니다. 날짜 또는 카페·키워드 선택을 해제하면 조회 범위를 넓힐 수 있습니다.</div>';return;}
  root.innerHTML=`<div class="stats-record-head"><div class="title"><strong>${esc(r.title)}</strong><span>${esc(cafeDisplayName(r.cafeId))} · 게시글 ${esc(r.id)} · ${esc(r.date.replaceAll('-','.'))}</span></div><div class="stats-record-actions"><button class="btn small" type="button" data-stats-open-original>원문 카페 열기 ↗</button><button class="btn small" type="button" data-stats-open-history>관련 실행 이력 보기</button></div></div><div class="stats-record-meta"><div class="k">차종</div><div>${esc(r.vehicle||'미기록')}</div><div class="k">AI 구분</div><div>${esc(r.type)}</div><div class="k">키워드</div><div>${esc(r.keys.join(', '))}</div><div class="k">검토</div><div>${esc(r.status)}</div><div class="k">최초 수집</div><div>${esc(r.firstCollectedAt||'미기록')}</div><div class="k">마지막 확인</div><div>${esc(r.lastCheckedAt||'미기록')}</div><div class="k">실행 ID</div><div>${esc(r.runId||'미연결')}</div><div class="k">게시 시각</div><div>${esc(formatTime(r.publishedAt,true))}</div><div class="k">DB 연결</div><div>SQLite DB 연결됨</div></div><div class="stats-record-content"><div class="stats-record-col"><div class="stats-record-label">원문 본문</div><div class="stats-record-text">${esc(r.raw)}</div></div><div class="stats-record-col"><div class="stats-record-label">AI 분석</div><div class="stats-record-text">${esc(r.analysis||'분석 미기록')}</div><div class="stats-evidence"><strong>근거</strong><br>${esc(r.evidence)}</div></div></div>`;
}

function selectStatsRecord(id){if(!statsDetailRows().some(r=>statsRecordKey(r)===id))return;statsSelectedRecordId=id;renderStatsDetails();}

function openStatsRelatedHistory(){
  const r=statsDetailRows().find(r=>statsRecordKey(r)===statsSelectedRecordId);if(!r)return;
  const h=historyRecords.find(v=>v.id===r.runId);if(!h){showMessage('연결된 실행 이력 없음','이 게시글의 실행 이력이 현재 자료에 없습니다. 다른 실행을 대신 열지 않습니다.');return;}
  historyState.date='';historyState.query='';historyState.selectedId=h.id;$('historySearch').value='';switchStatsToHistory();
}

function openHistoryArticleInStats(id,cafe){
  const candidates=statsKnownRecords().filter(r=>String(r.id)===String(id)&&(!cafe||r.cafe===cafe||r.cafeId===cafe));
  if(candidates.length!==1){showMessage('연결된 분석 데이터 없음','선택한 카페·게시글에 대응하는 분석 데이터가 없습니다. 다른 게시글을 대신 선택하지 않습니다. 전체 데이터 분석은 상단 이동 버튼으로 열 수 있습니다.');return;}
  const target=candidates[0];resetStats();statsSelectedRecordId=statsRecordKey(target);const d=new Date(target.date+'T00:00:00Z');if(Number.isFinite(+d)){statsCalendarState.year=d.getUTCFullYear();statsCalendarState.month=d.getUTCMonth();}switchHistoryToStats();$('statsRecordDetail').scrollTop=0;toast(`${cafeLabel(target.cafeId)} · ${target.id} 게시글을 열었습니다.`);
}

function openStatsOriginal(){
  const r=statsDetailRows().find(r=>statsRecordKey(r)===statsSelectedRecordId);if(!r)return;
  const c=getCafe(r.cafeId);const url=/^https:\/\/cafe\.naver\.com\//.test(r.url||'')?r.url:c?.url;
  if(url){window.open(url,'_blank','noopener,noreferrer');if(!r.url)toast('기록에 게시글 주소가 없어 카페 메인 화면을 엽니다.');}else toast('이 게시글의 카페 주소가 기록되어 있지 않습니다.');
}


function syncStatsSelects(){
  const draft=statsDraft||statsDraftFromApplied(),c=$('statsCafeFilter'),k=$('statsKeywordFilter');
  c.innerHTML='<option value="all">전체 카페</option>'+statsCatalogCafes().map(x=>`<option value="${esc(x.id)}">${esc(x.code)} · ${esc(x.name)}${x.active?'':' (수집 중단)'}</option>`).join('');
  k.innerHTML='<option value="all">전체 키워드</option>'+statsCatalogKeywords().map(x=>`<option value="${esc(x.id)}">${esc(x.name)}${x.deleted?' (삭제됨)':x.active?'':' (검색 중단)'}</option>`).join('');
  for(const [node,key] of [[c,'cafe'],[k,'keyword']]){const value=draft[key];if(![...node.options].some(o=>o.value===value)){node.add(new Option('사용할 수 없는 조건 · 다시 선택',value));}node.value=value;}
}

function setStatsPeriodModeUI(mode){const custom=$('statsCustomRangeRow'),showCustom=mode==='custom';custom.hidden=!showCustom;custom.classList.toggle('hidden',!showCustom);$('statsFromDate').disabled=!showCustom;$('statsToDate').disabled=!showCustom;}
function syncStatsPeriodControls(){
  const draft=statsDraft||statsDraftFromApplied();$('statsPeriodPreset').value=draft.periodMode;setStatsPeriodModeUI(draft.periodMode);$('statsFromDate').value=draft.from;$('statsToDate').value=draft.to;
}

function statsQuickRange(mode){
  const to=statsDates.at(-1)||dayKey(new Date()),end=+new Date(to+'T00:00:00Z');let from;
  if(mode==='7d'||mode==='30d')from=new Date(end-(Number(mode.slice(0,-1))-1)*DAY).toISOString().slice(0,10);
  else if(mode==='90d'||mode==='365d')from=new Date(+new Date(shiftCalendarMonths(to,mode==='90d'?-3:-12)+'T00:00:00Z')+DAY).toISOString().slice(0,10);
  else from=statsDates[0]||to;
  return {from,to};
}

function statsPendingQuerySummary(){
  const q=statsDraft||statsReadDraft(),period=q.periodMode==='custom'?`${q.from||'시작일'} ~ ${q.to||'종료일'}`:statsPeriodModeName(q.periodMode),c=q.cafe==='all'?'전체 카페':cafeLabel(q.cafe),k=q.keyword==='all'?'전체 키워드':keywordLabel(q.keyword);return `${period} · ${c} · ${k}`;
}

function statsAppliedQuerySummary(){
  const c=statsState.cafe==='all'?'전체 카페':cafeLabel(statsState.cafe),k=statsState.keyword==='all'?'전체 키워드':keywordLabel(statsState.keyword),range=`${statsState.from} ~ ${statsState.to}`;
  return `${statsPeriodModeName()} · ${c} · ${k}${['all','custom'].includes(statsState.periodMode)?'':' · 오늘 기준'} · ${range}`;
}

function updateStatsQueryState(){
  const box=$('statsQueryState'),detail=$('statsQueryStateDetail');if(!box||!detail)return;
  box.classList.toggle('dirty',statsQueryDirty||!!statsQueryError);box.querySelector('strong').textContent=statsQueryError?'! '+statsQueryError:statsQueryDirty?'! 조건 변경됨 · 조회 적용 필요':'✓ 현재 조회 조건 적용됨';detail.textContent=statsQueryDirty||statsQueryError?statsPendingQuerySummary():statsAppliedQuerySummary();
}

function markStatsQueryDirty(){statsDraft=statsReadDraft();statsQueryDirty=statsDraftSignature(statsDraft)!==statsDraftSignature(statsDraftFromApplied());statsQueryError='';updateStatsQueryState();}

function renderStatsCalendar(){
  const year=statsCalendarState.year,month=statsCalendarState.month,currentYear=parts(new Date()).y,years=statsDates.map(d=>+d.slice(0,4));
  const min=Math.min(year,currentYear-10,...years),max=Math.max(year,currentYear+2,...years),ys=$('statsCalendarYear'),ms=$('statsCalendarMonth');
  ys.innerHTML=Array.from({length:max-min+1},(_,i)=>min+i).map(y=>`<option value="${y}">${y}년</option>`).join('');ys.value=String(year);ms.innerHTML=Array.from({length:12},(_,i)=>`<option value="${i}">${i+1}월</option>`).join('');ms.value=String(month);
  const counts=new Map();for(const r of statsCalendarRecords())counts.set(r.date,(counts.get(r.date)||0)+1);
  const first=new Date(Date.UTC(year,month,1)).getUTCDay(),days=new Date(Date.UTC(year,month+1,0)).getUTCDate();let html='';
  for(let i=0;i<first;i++)html+='<div class="calendar-day blank" aria-hidden="true"></div>';
  for(let day=1;day<=days;day++){
    const key=`${year}-${pad(month+1)}-${pad(day)}`,count=counts.get(key)||0,inside=key>=statsState.from&&key<=statsState.to,selected=!!statsDetailDate&&key>=statsDetailDate&&key<=(statsDetailDateEnd||statsDetailDate);
    html+=`<button type="button" class="calendar-day${count?' has-run':''}${selected?' selected':''}${inside?'':' outside-query'}" data-stats-date="${key}" aria-pressed="${selected}" ${inside?'':'disabled'} title="${key} · ${inside?'저장된 게시글 '+count+'건':'현재 조회 기간 밖'}"><span>${day}</span>${count?`<span class="run-count">${count}건</span>`:''}</button>`;
  }
  for(let i=(first+days)%7;i!==0&&i<7;i++)html+='<div class="calendar-day blank" aria-hidden="true"></div>';
  $('statsCalendarGrid').innerHTML=html;$('statsCalendarFilterText').textContent=statsDetailDate?statsSelectedPeriodLabel()+' 강조':'조회 기간 전체';$('statsCalendarReset').setAttribute('aria-pressed',String(!statsDetailDate));$('statsCalendarReset').title='날짜 강조만 해제합니다. 조회 조건과 카페·키워드 선택은 유지됩니다.';
}

function selectStatsCalendarDate(key){
  if(!validStatsDetailPeriod(key,key)){toast('현재 조회 기간 안의 날짜를 선택하세요.');return;}
  if(statsDetailDate!==key){const grouped=statsGroupTrendRows(statsTrendValues().rows,statsTrendView.granularity),index=grouped.findIndex(r=>r.start<=key&&r.end>=key);if(index>=0)statsTrendView.page=Math.floor(index/statsGranularityPageSize(statsTrendView.granularity));}
  selectStatsTrendPeriod(key,key);
}

function clearStatsDetailDate(){statsSelectedRecordId=null;statsDetailDate='';statsDetailDateEnd='';renderStatsBars();renderStatsTrend();renderStatsDetails();renderStatsCalendar();}

function changeStatsCalendarMonth(delta){const d=new Date(Date.UTC(statsCalendarState.year,statsCalendarState.month+delta,1));statsCalendarState.year=d.getUTCFullYear();statsCalendarState.month=d.getUTCMonth();renderStatsCalendar();}
function renderStats(){
  refreshCatalogFromSources();if(statsState.periodMode==='all'){statsState.from=statsDates[0];statsState.to=statsDates.at(-1);}
  if(!statsDraft)statsDraft=statsDraftFromApplied();if(!statsQueryDirty&&!statsQueryError)statsDraft=statsDraftFromApplied();
  renderStatsBars();renderStatsTrend();renderStatsDetails();syncStatsSelects();syncStatsPeriodControls();renderStatsCalendar();$('statsSumPeriod').textContent=statsPeriodLabel();$('statsSumCafes').textContent=(statsState.cafe==='all'?statsCatalogCafes().length:1)+'개';$('statsSumPosts').textContent=statsUniquePosts()+'건';updateStatsQueryState();
}

function applyStatsControls(){
  const q=statsReadDraft();statsDraft=q;const range=q.periodMode==='custom'?{from:q.from,to:q.to}:statsQuickRange(q.periodMode);
  let error=statsRangeError(range.from,range.to);
  if(!error&&q.cafe!=='all'&&!statsCatalogCafes().some(c=>c.id===q.cafe))error='카페 조회 조건을 다시 선택하세요.';
  if(!error&&q.keyword!=='all'&&!statsCatalogKeywords().some(k=>k.id===q.keyword))error='키워드 조회 조건을 다시 선택하세요.';
  if(error){statsQueryDirty=true;statsQueryError=error;updateStatsQueryState();return false;}
  Object.assign(statsState,{...q,...range});statsSelectedRecordId=null;statsDetailDate='';statsDetailDateEnd='';statsDetailCafe='';statsDetailKeyword='';statsTrendView.page=null;statsQueryDirty=false;statsQueryError='';statsDraft=statsDraftFromApplied();
  const d=new Date(range.to+'T00:00:00Z');statsCalendarState.year=d.getUTCFullYear();statsCalendarState.month=d.getUTCMonth();renderStats();return true;
}

function resetStats(){statsSelectedRecordId=null;statsDetailDate='';statsDetailDateEnd='';statsDetailCafe='';statsDetailKeyword='';statsTrendView.granularity='day';statsTrendView.page=null;Object.assign(statsState,{periodMode:'all',cafe:'all',keyword:'all',from:statsDates[0],to:statsDates.at(-1)});statsQueryDirty=false;statsQueryError='';statsDraft=statsDraftFromApplied();const d=new Date(statsState.to+'T00:00:00Z');statsCalendarState.year=d.getUTCFullYear();statsCalendarState.month=d.getUTCMonth();renderStats();}

function openStats(){hideTooltip();$('historyView').classList.remove('show');$('historyView').setAttribute('aria-hidden','true');$('statsView').classList.add('show');$('statsView').setAttribute('aria-hidden','false');updateGlobalHeader('stats');syncModalInert();renderStats();$('statsBtn')?.focus();}

function closeStats(){openOperation();}


// One viewport-level tooltip avoids clipping inside the independently scrolling panes.
function showTooltip(owner){
  clearTimeout(tooltipTimer);tooltipOwner=owner;const tip=$('sharedTooltip');tip.textContent=owner.dataset.tip;tip.hidden=false;owner.setAttribute('aria-describedby','sharedTooltip');
  const r=owner.getBoundingClientRect(),t=tip.getBoundingClientRect();let left=Math.min(Math.max(12,r.left),innerWidth-t.width-12),top=r.bottom+8;
  if(top+t.height>innerHeight-10)top=Math.max(10,r.top-t.height-8);tip.style.left=left+'px';tip.style.top=top+'px';
}
function hideTooltip(){clearTimeout(tooltipTimer);tooltipOwner?.removeAttribute('aria-describedby');tooltipOwner=null;$('sharedTooltip').hidden=true;}
function deferTooltipHide(){clearTimeout(tooltipTimer);tooltipTimer=setTimeout(hideTooltip,120);}
function trapFocus(event){
  const modal=activeModal();if(!modal||event.key!=='Tab')return;
  const els=[...modal.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')].filter(e=>!e.disabled&&e.tabIndex>=0&&e.getClientRects().length);
  const first=els[0],last=els.at(-1);if(!first)return;
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
}

function bindEvents(){
  bindCatalogEvents();
  window.addEventListener('beforeunload', e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  $('operationBtn').onclick=openOperation;$('historyBtn').onclick=openHistory;$('statsBtn').onclick=openStats;$('historyAllBtn').onclick=resetHistoryFilters;$('statsExcelBtn').onclick=e=>{e.stopPropagation();toggleStatsExportMenu();};$('statsExcelFullBtn').onclick=()=>exportStatsExcel('full');$('statsExcelCountsBtn').onclick=()=>exportStatsExcel('counts');$('runBtn').onclick=primaryRunAction;$('saveSettingsBtn').onclick=saveSettings;$('naverLoginBtn').onclick=openNaverLogin;$('executionPreviewHistoryBtn').onclick=()=>{const r=activeRun||lastDisplayedRun||latestRun();if(!r){toast('확인할 실행 기록이 없습니다.');return;}historyState.selectedId=r.id;openHistory();};$('executionPreviewOpenBtn').onclick=()=>{const r=activeRun||lastDisplayedRun||latestRun();if(!r){toast('열 수 있는 PPT 기록이 없습니다.');return;}const prev=historyState.selectedId;historyState.selectedId=r.id;historyAction('open-ppt');historyState.selectedId=prev;};
  $('stopBtn').onclick=requestStop;$('pptOnlyBtn').onclick=pptOnly;
  $('actionConfirmCancelBtn').onclick=closeExecutionConfirm;$('actionConfirmRunBtn').onclick=confirmExecutionAction;$('actionConfirmBackdrop').onclick=e=>{if(e.target===$('actionConfirmBackdrop'))closeExecutionConfirm();};
  $('stopCancelBtn').onclick=closeStopConfirm;$('stopConfirmBtn').onclick=confirmStop;$('stopConfirmBackdrop').onclick=e=>{if(e.target===$('stopConfirmBackdrop'))closeStopConfirm();};
  $('toggleAll').onclick=toggleAllCafes;
  $('keywordManagerBtn').onclick=openKeywordManager;$('keywordManagerClose').onclick=closeKeywordManager;$('keywordManagerDone').onclick=closeKeywordManager;$('keywordManagerBackdrop').onclick=e=>{if(e.target===$('keywordManagerBackdrop'))closeKeywordManager();};$('keywordManagerList').onclick=e=>{const toggle=e.target.closest('[data-keyword-toggle]'),del=e.target.closest('[data-keyword-delete]');if(toggle)requestKeywordToggle(toggle.dataset.keywordToggle);if(del)openKeywordDeleteConfirm(del.dataset.keywordDelete);};
  $('restoreKeywords').onclick=()=>openKeywordConfirm(null,true);$('keywordDeleteCancel').onclick=closeKeywordConfirm;$('keywordDeleteConfirm').onclick=confirmKeywordAction;
  $('keywordConfirmBackdrop').onclick=e=>{if(e.target===$('keywordConfirmBackdrop'))closeKeywordConfirm();};
  $('keywordAddBtn').onclick=addKeyword;
  $('keywordInput').addEventListener('focus',e=>renderKeywordManager(e.target.value));
  $('keywordInput').addEventListener('input',e=>renderKeywordManager(e.target.value));
  $('keywordInput').onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();if(e.target.value){e.target.value='';renderKeywordManager();}else closeKeywordManager();return;}if(e.key==='Enter'&&!e.isComposing){e.preventDefault();addKeyword();}};
  $$('.config-pane input,.config-pane select').filter(e=>!e.classList.contains('cafe-check')).forEach(e=>{
    const handler=()=>{if(e.id==='apiKey')return;updateCounts();updateSettingsUI();markChanged();updateTimes();updateNaverLoginStatus();};
    e.addEventListener('change',handler);if(['text','time','datetime-local','email'].includes(e.type)&&e.id!=='keywordInput')e.addEventListener('input',handler);
  });
  $$('.secret-toggle').forEach(b=>b.onclick=()=>{const e=$(b.dataset.target);e.type=e.type==='password'?'text':'password';b.textContent=e.type==='password'?'표시':'숨김';});
  $('codexGuideBtn').onclick=()=>codexRequest('login');
  $('codexCheckBtn').onclick=()=>codexRequest('check');
  $('pathBtn').textContent='안내';$('pathBtn').onclick=()=>showMessage('결과 폴더 경로','결과 폴더 입력칸에 실제 프로그램에서 사용할 경로를 적으세요.\n이 단일 HTML은 Windows 폴더를 만들거나 파일을 저장하지 않습니다.\n\n현재 값: '+$('outputPath').value);
  $('modalClose').onclick=closeMessage;$('modalBackdrop').onclick=e=>{if(e.target===$('modalBackdrop'))closeMessage();};
  $('clearLog').onclick=()=>{$('log').textContent='';toast('화면 로그만 비웠습니다. 실행 이력의 로그는 유지됩니다.');};
  $('approveBtn').onclick=approvePost;$('originalBtn').onclick=openSelectedOriginal;
  $('resultBody').onclick=e=>{const row=e.target.closest('[data-post]');if(row)selectPost(row.dataset.post);};
  $('resultBody').onkeydown=e=>{if(['Enter',' '].includes(e.key)){const row=e.target.closest('[data-post]');if(row){e.preventDefault();selectPost(row.dataset.post);}}};
    $('statsCafeChart').onclick=e=>{const row=e.target.closest('[data-stats-cafe]');if(row)selectStatsDetailCafe(row.dataset.statsCafe);};$('statsKeywordChart').onclick=e=>{const row=e.target.closest('[data-stats-key]');if(row)selectStatsDetailKeyword(row.dataset.statsKey);};$('statsTrendWrap').addEventListener('pointermove',e=>{const node=e.target.closest('[data-stats-trend-start]');if(node)showStatsTrendTooltip(node,e);else hideStatsTrendTooltip();});$('statsTrendWrap').addEventListener('pointerleave',hideStatsTrendTooltip);$('statsTrendWrap').addEventListener('click',e=>{const node=e.target.closest('[data-stats-trend-start]');if(node)selectStatsTrendPeriod(node.dataset.statsTrendStart,node.dataset.statsTrendEnd);});$('statsTrendWrap').addEventListener('keydown',e=>{if(!['Enter',' '].includes(e.key))return;const node=e.target.closest('[data-stats-trend-start]');if(node){e.preventDefault();selectStatsTrendPeriod(node.dataset.statsTrendStart,node.dataset.statsTrendEnd);}});$('statsDetailBody').onclick=e=>{const row=e.target.closest('[data-stats-record]');if(row)selectStatsRecord(row.dataset.statsRecord);};$('statsDetailBody').onkeydown=e=>{if(['Enter',' '].includes(e.key)){const row=e.target.closest('[data-stats-record]');if(row){e.preventDefault();selectStatsRecord(row.dataset.statsRecord);}}};$('statsRecordDetail').onclick=e=>{if(e.target.closest('[data-stats-open-history]'))openStatsRelatedHistory();if(e.target.closest('[data-stats-open-original]'))openStatsOriginal();};$('statsApplyBtn').onclick=applyStatsControls;$('statsResetBtn').onclick=resetStats;$('statsPeriodPreset').onchange=e=>{setStatsPeriodModeUI(e.target.value);markStatsQueryDirty();};['statsCafeFilter','statsKeywordFilter','statsFromDate','statsToDate'].forEach(id=>{$(id).addEventListener('change',markStatsQueryDirty);});$('statsTrendTabs').onclick=e=>{const b=e.target.closest('[data-trend-granularity]');if(b)changeStatsGranularity(b.dataset.trendGranularity);};$('statsTrendPrev').onclick=()=>changeStatsTrendPage(-1);$('statsTrendNext').onclick=()=>changeStatsTrendPage(1);$('statsCalendarPrev').onclick=()=>changeStatsCalendarMonth(-1);$('statsCalendarNext').onclick=()=>changeStatsCalendarMonth(1);$('statsCalendarReset').onclick=clearStatsDetailDate;$('statsDetailDateClear').onclick=clearStatsDetailSelection;$('statsCalendarYear').onchange=e=>{statsCalendarState.year=Number(e.target.value);renderStatsCalendar();};$('statsCalendarMonth').onchange=e=>{statsCalendarState.month=Number(e.target.value);renderStatsCalendar();};$('statsCalendarGrid').onclick=e=>{const b=e.target.closest('[data-stats-date]');if(b)selectStatsCalendarDate(b.dataset.statsDate);};
  $('historySearch').oninput=e=>{historyState.query=e.target.value;renderHistory();};
  $('historyRunList').onclick=e=>{const b=e.target.closest('[data-history-id]');if(b){historyState.selectedId=b.dataset.historyId;renderHistory();$('historyDetail').scrollTop=0;}};
  $('calendarGrid').onclick=e=>{const b=e.target.closest('[data-date]');if(b)selectHistoryDate(b.dataset.date);};
  $('calendarPrev').onclick=()=>changeHistoryMonth(-1);$('calendarNext').onclick=()=>changeHistoryMonth(1);$('calendarReset').onclick=resetHistoryFilters;
  $('calendarYear').onchange=e=>{historyState.year=Number(e.target.value);renderCalendar();};$('calendarMonth').onchange=e=>{historyState.month=Number(e.target.value);renderCalendar();};
  $$('.theme-toggle').forEach(btn=>btn.onclick=toggleTheme);
  $('historyDetail').onclick=e=>{const preview=e.target.closest('[data-preview-slide]');if(preview){pptPreviewSelections.set(preview.dataset.runId,Number(preview.dataset.previewSlide));const top=$('historyDetail').scrollTop;renderHistoryDetail(preview.dataset.runId);$('historyDetail').scrollTop=top;return;}const toStats=e.target.closest('[data-history-open-stats]');if(toStats){openHistoryArticleInStats(toStats.dataset.historyOpenStats,toStats.dataset.historyOpenStatsCafe);return;}const action=e.target.closest('[data-history-action]');if(action){historyAction(action.dataset.historyAction,action.dataset.key);return;}const row=e.target.closest('[data-history-post]');if(row)selectHistoryArticle(row.dataset.historyPost,row.dataset.historyCafe);};
  $('historyDetail').onkeydown=e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('[data-history-post]')){e.preventDefault();selectHistoryArticle(e.target.dataset.historyPost,e.target.dataset.historyCafe);}};
  $('exportHistoryBtn').onclick=()=>exportHistory();
  $('pptFileInput').onchange=e=>{const file=e.target.files?.[0],id=e.target.dataset.runId;if(!file||!id)return;if(!/\.pptx?$/i.test(file.name)){toast('PPT 또는 PPTX 파일을 선택하세요.');return;}linkedPptFiles.set(id,file);historyState.selectedId=id;renderHistoryDetail(id);toast('선택한 PPT를 이 실행에 연결했습니다. 미리보기 정보가 자동 갱신되었습니다. 서버로 업로드하지 않습니다.');};
  $('toastUndo').onclick=()=>{const fn=undoAction;undoAction=null;$('toast').hidden=true;fn?.();};$('toastClose').onclick=()=>{$('toast').hidden=true;undoAction=null;};
  $$('.help').forEach(e=>{e.setAttribute('role','button');e.setAttribute('aria-label','도움말: '+e.dataset.tip);});
  document.addEventListener('mouseover',e=>{const owner=e.target.closest('.help');if(owner)showTooltip(owner);});
  document.addEventListener('mouseout',e=>{if(e.target.closest('.help'))deferTooltipHide();});
  document.addEventListener('focusin',e=>{const owner=e.target.closest('.help');if(owner)showTooltip(owner);});
  document.addEventListener('focusout',e=>{if(e.target.closest('.help'))deferTooltipHide();});
  $('sharedTooltip').onmouseenter=()=>clearTimeout(tooltipTimer);$('sharedTooltip').onmouseleave=deferTooltipHide;
  document.addEventListener('click',e=>{const owner=e.target.closest('.help');if(owner){e.preventDefault();e.stopPropagation();showTooltip(owner);}});
  document.addEventListener('click',e=>{if(!e.target.closest('#statsExportWrap'))closeStatsExportMenu();});
  document.addEventListener('scroll',hideTooltip,true);window.addEventListener('resize',hideTooltip);
  document.addEventListener('keydown',e=>{
    trapFocus(e);if(e.target.matches('.help')&&['Enter',' '].includes(e.key)){e.preventDefault();showTooltip(e.target);return;}
    if(['ArrowLeft','ArrowRight'].includes(e.key)&&$('historyView').classList.contains('show')&&historyState.selectedId){
      const editable=e.target.matches('input,textarea,select,[contenteditable="true"]');
      if(!editable){
        const total=summaryCountForRun(historyRecords.find(r=>r.id===historyState.selectedId));
        const current=Math.max(1,Math.min(total,Number(pptPreviewSelections.get(historyState.selectedId)||1)));
        const next=Math.max(1,Math.min(total,current+(e.key==='ArrowLeft'?-1:1)));
        if(next!==current){
          e.preventDefault();
          pptPreviewSelections.set(historyState.selectedId,next);
          const top=$('historyDetail').scrollTop;
          renderHistoryDetail(historyState.selectedId);
          $('historyDetail').scrollTop=top;
        }
        return;
      }
    }
    if(e.key==='Escape'){if($('naverLoginBackdrop').classList.contains('show')){e.preventDefault();cancelNaverLogin();return;}if(!$('statsExportMenu')?.hidden){closeStatsExportMenu();$('statsExcelBtn')?.focus();return;}if($('keywordConfirmBackdrop').classList.contains('show')){closeKeywordConfirm();return;}if(!$('keywordManagerBackdrop').hidden){closeKeywordManager();return;}if(!$('cafeManagerBackdrop').hidden){closeCafeManager();return;}if(!$('sharedTooltip').hidden){hideTooltip();return;}if($('stopConfirmBackdrop').classList.contains('show')){closeStopConfirm();return;}if($('actionConfirmBackdrop').classList.contains('show')){closeExecutionConfirm();return;}if($('modalBackdrop').classList.contains('show')){closeMessage();return;}if($('statsView').classList.contains('show')){closeStats();return;}if($('historyView').classList.contains('show'))closeHistory();}
  });
}
// Audit runtime state. These are UI-only values; no login, collection, AI, PPT,
// scheduler, or mail backend is contacted by this document.
const APP_REVISION='v10-ui-audit-20260929';
const MAX_QUERY_DAYS=36600;
const corruptStorage=new Map();
const reportedStorageErrors=new Set();
let savedConfigCanonical=null;
let statsDraft=null;
let ordinaryModalFocus=null;
let cafeManagerEditingId=null, cafeManagerPendingId=null, cafeManagerFocus=null;
let keywordManagerFocus=null;
let previewRenderSignature='';
const naverAuthState={state:'unverified',checkedAt:null};
let skippedHistoryRecords=0;
let statsQueryError='';
let statsResizeObserver=null;
let statsResizeTimer=null;
let savedConfigPresent=false;

function normalizedTerm(value){return String(value??'').normalize('NFKC').trim().toLowerCase();}

function uniqueTerms(values){
  const seen=new Set();
  return (Array.isArray(values)?values:[]).filter(v=>typeof v==='string'&&v.trim()).map(v=>v.trim()).filter(v=>{const key=normalizedTerm(v);if(seen.has(key))return false;seen.add(key);return true;});
}

function newCatalogId(prefix){return prefix+':'+(globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+'-'+Math.random().toString(36).slice(2));}

function cleanCafeUrl(value){
  let u;try{u=new URL(String(value||'').trim());}catch{throw new Error('카페 메인 주소를 https://cafe.naver.com/카페주소 형태로 입력하세요.');}
  if(u.protocol!=='https:'||!['cafe.naver.com','m.cafe.naver.com'].includes(u.hostname)||u.port||u.username||u.password)throw new Error('네이버 카페의 HTTPS 메인 주소만 등록할 수 있습니다.');
  const m=u.pathname.match(/^\/([A-Za-z0-9_-]+)\/?$/);
  if(!m||['ca-fe','cafes','articles','article','mycafe','manage','home'].includes(m[1].toLowerCase()))throw new Error('게시글 주소가 아닌 카페 메인 주소를 입력하세요.');
  return 'https://cafe.naver.com/'+m[1].toLowerCase();
}

function getCafe(ref){return cafes.find(c=>c.id===ref)||cafes.find(c=>normalizedTerm(c.code)===normalizedTerm(ref));}

function cafeLabel(ref){return getCafe(ref)?.code||String(ref||'미기록');}

function cafeDisplayName(ref){return getCafe(ref)?.name||cafeLabel(ref);}

function keywordByName(name){return keywordCatalog.find(k=>normalizedTerm(k.name)===normalizedTerm(name));}

function getKeyword(ref){return keywordCatalog.find(k=>k.id===ref)||keywordByName(ref);}

function keywordLabel(ref){return getKeyword(ref)?.name||String(ref||'미기록');}

function ensureKeyword(name,active=false){
  name=String(name??'').trim();if(!name)return null;
  let k=keywordByName(name);
  if(!k){k={id:'keyword:'+encodeURIComponent(normalizedTerm(name)),name,active,createdAt:new Date().toISOString(),deleted:false,deletedAt:null};keywordCatalog.push(k);}
  else if(active&&k.deleted){k.deleted=false;k.deletedAt=null;}
  return k;
}

function reconcileKeywordCatalog(){
  keywords=uniqueTerms(keywords);const current=new Set(keywords.map(normalizedTerm));
  keywords.forEach(k=>ensureKeyword(k,true));keywordCatalog.forEach(k=>k.active=current.has(normalizedTerm(k.name)));
}

function ensureRecordCatalog(record){
  if(!record||typeof record!=='object'||Array.isArray(record))return;
  let c=getCafe(record.cafeId||record.cafe);
  if(!c&&typeof record.cafe==='string'&&record.cafe){
    c={id:record.cafeId||'legacy-cafe:'+encodeURIComponent(record.cafe),code:record.cafe,name:record.cafeName||record.cafe,url:'',active:false,createdAt:null};cafes.push(c);
  }
  if(c)record.cafeId=c.id;
  const terms=uniqueTerms(Array.isArray(record.keys)?record.keys:typeof record.key==='string'?record.key.split(/[,;\n]/):[]);
  if(terms.length){record.keywordIds=terms.map(t=>ensureKeyword(t,false).id);if(Array.isArray(record.keys))record.keys=terms;}
  else if(!Array.isArray(record.keywordIds))record.keywordIds=[];
}

function refreshCatalogFromSources(){
  reconcileKeywordCatalog();statsRecords.forEach(ensureRecordCatalog);
  historyRecords.forEach(r=>(r.articles||[]).forEach(ensureRecordCatalog));
  statsDates=[...new Set(statsRecords.map(r=>r?.date).filter(isDateKey))].sort();
  if(!statsDates.length)statsDates=[dayKey(new Date())];
}

function catalogSnapshot(){return {version:1,cafes:copyObject(cafes),keywords:copyObject(keywordCatalog)};}

function hydrateCatalog(cfg){
  cafes.length=0;keywordCatalog.length=0;
  const stored=cfg?.catalog;
  const source=Array.isArray(stored?.cafes)?stored.cafes:INITIAL_CAFES;
  const ids=new Set(),codes=new Set(),urls=new Set();
  for(const item of source){
    if(!item||typeof item.code!=='string'||typeof item.name!=='string')continue;
    let url='';try{if(item.url)url=cleanCafeUrl(item.url);}catch{continue;}
    const code=item.code.trim(),id=typeof item.id==='string'&&item.id?item.id:'cafe:'+(url.split('/').pop()||encodeURIComponent(code));
    if(!code||ids.has(id)||codes.has(normalizedTerm(code))||(url&&urls.has(url)))continue;
    ids.add(id);codes.add(normalizedTerm(code));if(url)urls.add(url);
    cafes.push({id,code,name:item.name.trim()||code,url,active:item.active!==false,naverCafeId:typeof item.naverCafeId==='string'?item.naverCafeId:null,createdAt:item.createdAt||null});
  }
  if(Array.isArray(stored?.keywords))for(const item of stored.keywords){
    if(!item||typeof item.name!=='string'||!item.name.trim()||keywordByName(item.name))continue;
    const id=typeof item.id==='string'&&item.id?item.id:'keyword:'+encodeURIComponent(normalizedTerm(item.name));
    if(keywordCatalog.some(k=>k.id===id))continue;
    keywordCatalog.push({id,name:item.name.trim(),active:item.active!==false&&!item.deleted,createdAt:item.createdAt||null,deleted:item.deleted===true,deletedAt:item.deletedAt||null});
  }
  keywords=uniqueTerms(Array.isArray(cfg?.keywords)?cfg.keywords:(stored?keywordCatalog.filter(k=>k.active).map(k=>k.name):defaultKeywords));
  reconcileKeywordCatalog();
  const wanted=Array.isArray(cfg?.selectedCafeIds)?cfg.selectedCafeIds:(Array.isArray(cfg?.selectedCafes)?cfg.selectedCafes.map(c=>getCafe(c)?.id).filter(Boolean):cafes.filter(c=>c.active).map(c=>c.id));
  selectedCafeIds=new Set(wanted.filter(id=>getCafe(id)?.active));
}

function statsCatalogCafes(){const used=new Set(statsKnownRecords().map(r=>r.cafeId));return cafes.filter(c=>c.active||used.has(c.id));}

function statsCatalogKeywords(){const used=new Set(statsKnownRecords().flatMap(r=>r.keywordIds));return keywordCatalog.filter(k=>k.active||used.has(k.id));}

function canonicalConfig(cfg){
  // Snapshot-derived summary metadata is not an editable setting.
  const value=copyObject(cfg);delete value.summaryCafes;return JSON.stringify(value);
}

function toggleAllCafes(){
  if(running)return;
  const active=cafes.filter(c=>c.active),on=active.some(c=>!selectedCafeIds.has(c.id));
  selectedCafeIds=new Set(on?active.map(c=>c.id):[]);renderCafes();updateCounts();updateSettingsUI();markChanged();
}

function currentSummaryCafes(){
  if(!$('stepCollect').checked){const r=historyRecords.find(r=>r.id===$('sourceRun').value);return r?summaryCafesForRun(r):[];}
  return cafes.filter(c=>c.active&&selectedCafeIds.has(c.id)).map(c=>({id:c.id,code:c.code,name:c.name,url:c.url}));
}

function summaryCafesForRun(r){
  if(Array.isArray(r?.summaryCafes))return r.summaryCafes.filter(c=>c&&typeof c.code==='string').map(c=>({...c,name:typeof c.name==='string'?c.name:c.code}));
  if(Array.isArray(r?.settings?.summaryCafes))return r.settings.summaryCafes.filter(c=>c&&typeof c.code==='string').map(c=>({...c,name:typeof c.name==='string'?c.name:c.code}));
  const codes=r?.settings?.selectedCafes,defs=Array.isArray(r?.settings?.catalog?.cafes)?r.settings.catalog.cafes:INITIAL_CAFES;
  if(Array.isArray(codes))return codes.map(code=>defs.find(c=>c.code===code)||{code,name:code});
  return INITIAL_CAFES.map(c=>({...c}));
}

function summarySlidesForRun(r){
  const head={n:1,code:'ALL',label:'전체 카페 종합 요약'};
  return r?.settings?.summarySlide===false?[head]:[head,...summaryCafesForRun(r).map((c,i)=>({n:i+2,code:c.code,label:c.name+' 요약'}))];
}

function summaryCountForRun(r){return summarySlidesForRun(r).length;}

function refreshCatalogViews(){
  refreshCatalogFromSources();const cs=new Set(statsCatalogCafes().map(c=>c.id)),ks=new Set(statsCatalogKeywords().map(k=>k.id));
  if(statsState.cafe!=='all'&&!cs.has(statsState.cafe))statsState.cafe='all';if(statsState.keyword!=='all'&&!ks.has(statsState.keyword))statsState.keyword='all';
  if(statsDetailCafe&&!cs.has(statsDetailCafe))statsDetailCafe='';if(statsDetailKeyword&&!ks.has(statsDetailKeyword))statsDetailKeyword='';
  renderStats();
}

function catalogChanged(){refreshCatalogFromSources();renderCafes();renderKeywords();updateCounts();updateSettingsUI();markChanged();refreshCatalogViews();}

function clearCafeManagerForm(){
  cafeManagerEditingId=null;$('cafeNameInput').value='';$('cafeCodeInput').value='';$('cafeUrlInput').value='';$('cafeCodeInput').readOnly=false;$('cafeUrlInput').readOnly=false;$('cafeFormCancel').hidden=true;$('cafeFormSubmit').textContent='카페 추가';$('cafeFormError').textContent='';
}

function renderCafeManager(){
  $('cafeManagerCount').textContent=`등록 ${cafes.length}개 · 수집 사용 ${cafes.filter(c=>c.active).length}개`;
  $('cafeManagerList').innerHTML=cafes.map(c=>`<div class="catalog-item ${c.active?'':'inactive'}" data-catalog-id="${esc(c.id)}"><div><div class="catalog-item-name">${esc(c.name)}</div><div class="catalog-item-meta">${esc(c.code)} · ${c.active?'수집 사용':'수집 중단 · 과거 데이터 유지'}${c.url?' · '+esc(c.url):''}</div></div><div class="catalog-item-actions"><button class="btn small" data-cafe-edit="${esc(c.id)}" type="button">이름 수정</button><button class="btn small" data-cafe-toggle="${esc(c.id)}" type="button">${c.active?'수집 중단':'다시 사용'}</button></div></div>`).join('');
}

function openCafeManager(){if(running)return;hideTooltip();cafeManagerFocus=document.activeElement;cafeManagerPendingId=null;$('cafeArchiveConfirm').hidden=true;clearCafeManagerForm();renderCafeManager();$('cafeManagerBackdrop').hidden=false;syncModalInert();$('cafeNameInput').focus({preventScroll:true});document.querySelector('.catalog-content').scrollTop=0;}

function closeCafeManager(){$('cafeManagerBackdrop').hidden=true;cafeManagerPendingId=null;syncModalInert();cafeManagerFocus?.focus();}

function editCafeManager(id){const c=getCafe(id);if(!c)return;clearCafeManagerForm();cafeManagerEditingId=c.id;$('cafeNameInput').value=c.name;$('cafeCodeInput').value=c.code;$('cafeUrlInput').value=c.url;$('cafeCodeInput').readOnly=true;$('cafeUrlInput').readOnly=true;$('cafeFormSubmit').textContent='이름 반영';$('cafeFormCancel').hidden=false;$('cafeNameInput').focus();}

function submitCafeManager(event){
  event.preventDefault();if(running)return;
  try{
    const name=$('cafeNameInput').value.trim(),code=$('cafeCodeInput').value.trim();
    if(!name||name.length>80)throw new Error('카페 이름을 1~80자로 입력하세요.');
    if(cafeManagerEditingId){const c=getCafe(cafeManagerEditingId);if(!c)throw new Error('수정할 카페가 없습니다.');c.name=name;}
    else{
      if(!/^[A-Za-z0-9가-힣_-]{1,12}$/.test(code)||normalizedTerm(code)==='all')throw new Error('짧은 이름은 한글·영문·숫자·_·-로 1~12자 입력하세요.');
      const url=cleanCafeUrl($('cafeUrlInput').value);
      if(cafes.some(c=>c.url===url))throw new Error('이미 등록된 주소입니다. 중단한 카페는 아래에서 다시 사용하세요.');
      if(cafes.some(c=>normalizedTerm(c.code)===normalizedTerm(code)))throw new Error('이미 사용 중인 짧은 이름입니다.');
      if(cafes.filter(c=>c.active).length>=100)throw new Error('수집 사용 카페를 100개까지 등록할 수 있습니다.');
      const c={id:newCatalogId('cafe'),code,name,url,naverCafeId:null,active:true,createdAt:new Date().toISOString()};cafes.push(c);selectedCafeIds.add(c.id);
    }
    catalogChanged();renderCafeManager();clearCafeManagerForm();toast('카페 목록에 반영했습니다. 다음 실행에도 사용하려면 설정 저장을 눌러주세요.');
  }catch(error){$('cafeFormError').textContent=error.message;}
}

function requestCafeToggle(id){
  if(running)return;const c=getCafe(id);if(!c)return;
  if(!c.active){if(!c.url){$('cafeFormError').textContent='이 과거 기록에는 카페 주소가 없습니다. 실제 연결 단계에서 주소 확인이 필요합니다.';return;}if(cafes.filter(c=>c.active).length>=100){$('cafeFormError').textContent='수집 사용 카페가 100개입니다. 다른 카페를 중단한 뒤 다시 사용하세요.';return;}c.active=true;selectedCafeIds.add(c.id);catalogChanged();renderCafeManager();return;}
  cafeManagerPendingId=c.id;$('cafeArchiveText').textContent=`“${c.name}” 수집을 중단하시겠습니까? 과거 데이터는 유지합니다.`;$('cafeArchiveConfirm').hidden=false;$('cafeArchiveCancel').focus();
}

function confirmCafeArchive(){if(running||!cafeManagerPendingId)return;const c=getCafe(cafeManagerPendingId);if(c){c.active=false;selectedCafeIds.delete(c.id);}cafeManagerPendingId=null;$('cafeArchiveConfirm').hidden=true;clearCafeManagerForm();catalogChanged();renderCafeManager();toast('수집을 중단했습니다. 과거 데이터는 유지됩니다.');}

function isDefaultKeywordName(name){return defaultKeywords.some(x=>normalizedTerm(x)===normalizedTerm(name));}
function keywordManagerSortGroup(k){return keywordVisualState(k).rank;}
function keywordManagerRowHtml(k){
  const state=keywordVisualState(k),cls=(k.active?'':'inactive ')+state.cls;
  const meta=state.state;
  return `<div class="catalog-item ${cls}" data-keyword-manager-id="${esc(k.id)}"><div><div class="catalog-item-name"><i class="keyword-state-dot" aria-hidden="true"></i>${esc(k.name)}</div><div class="catalog-item-meta">${meta}</div></div><div class="catalog-item-actions"><button class="btn small" type="button" data-keyword-toggle="${esc(k.id)}">${k.active?'검색 중단':'다시 사용'}</button><button class="btn small danger keyword-delete-action" type="button" data-keyword-delete="${esc(k.id)}">삭제</button></div></div>`;
}
function keywordManagerGroupHtml(label,kind,rows){return rows.length?`<div class="keyword-manager-group ${kind}"><b>${label}</b><span>${rows.length}개</span></div>${rows.map(keywordManagerRowHtml).join('')}`:'';}
function renderKeywordManager(query=''){
  refreshCatalogFromSources();
  const q=normalizedTerm(query),rows=keywordCatalog.filter(k=>!k.deleted&&(!q||normalizedTerm(k.name).includes(q))).slice();
  const defaultOrder=new Map(defaultKeywords.map((name,i)=>[normalizedTerm(name),i]));
  rows.sort((a,b)=>{const ga=keywordManagerSortGroup(a),gb=keywordManagerSortGroup(b);if(ga!==gb)return ga-gb;if(ga===3)return (defaultOrder.get(normalizedTerm(a.name))??999)-(defaultOrder.get(normalizedTerm(b.name))??999);const ta=Date.parse(a.createdAt||'')||0,tb=Date.parse(b.createdAt||'')||0;if(ta!==tb)return ta-tb;return String(a.name).localeCompare(String(b.name),'ko');});
  const visible=keywordCatalog.filter(k=>!k.deleted),active=visible.filter(k=>k.active).length,inactive=visible.filter(k=>!k.active).length,added=visible.filter(k=>!isDefaultKeywordName(k.name)).length;
  $('keywordManagerCount').textContent=`등록 ${visible.length}개 · 검색 사용 ${active}개 · 검색 중단 ${inactive}개 · 추가 ${added}개`;
  if(!rows.length){$('keywordManagerList').innerHTML='<div class="keyword-manager-empty">검색어와 일치하는 등록 키워드가 없습니다. 위 입력값을 새 키워드로 추가할 수 있습니다.</div>';return;}
  const stopped=rows.filter(k=>!k.active),custom=rows.filter(k=>k.active&&!isDefaultKeywordName(k.name)),current=rows.filter(k=>k.active&&isDefaultKeywordName(k.name));
  $('keywordManagerList').innerHTML=keywordManagerGroupHtml('검색 중단','stopped',stopped)+keywordManagerGroupHtml('추가 키워드','added',custom)+keywordManagerGroupHtml('현재 사용 중','current',current);
}

function openKeywordManager(){
  if(running)return;hideTooltip();keywordManagerFocus=document.activeElement;$('keywordInput').value='';hideKeywordSuggestions();renderKeywordManager();$('keywordManagerBackdrop').hidden=false;syncModalInert();$('keywordInput').focus({preventScroll:true});$('keywordManagerBackdrop').querySelector('.catalog-content').scrollTop=0;
}

function closeKeywordManager(){
  hideKeywordSuggestions();$('keywordManagerBackdrop').hidden=true;syncModalInert();keywordManagerFocus?.focus();
}

function requestKeywordToggle(id){
  if(running)return;const item=getKeyword(id);if(!item)return;
  if(item.active){const index=keywords.findIndex(k=>normalizedTerm(k)===normalizedTerm(item.name));if(index>=0)openKeywordConfirm(index,false);return;}
  reactivateKeyword(id);renderKeywordManager($('keywordInput').value);
}

function bindCatalogEvents(){
  $('cafeManagerBtn').onclick=openCafeManager;$('cafeManagerClose').onclick=closeCafeManager;$('cafeManagerDone').onclick=closeCafeManager;
  $('cafeManagerBackdrop').onclick=e=>{if(e.target===$('cafeManagerBackdrop'))closeCafeManager();};$('cafeManagerForm').onsubmit=submitCafeManager;$('cafeFormCancel').onclick=clearCafeManagerForm;
  $('cafeManagerList').onclick=e=>{const a=e.target.closest('[data-cafe-edit]'),b=e.target.closest('[data-cafe-toggle]');if(a)editCafeManager(a.dataset.cafeEdit);if(b)requestCafeToggle(b.dataset.cafeToggle);};
  $('cafeArchiveCancel').onclick=()=>{cafeManagerPendingId=null;$('cafeArchiveConfirm').hidden=true;};$('cafeArchiveApply').onclick=confirmCafeArchive;
  $('cafeList').onchange=e=>{const check=e.target.closest('.cafe-check');if(!check||running)return;if(check.checked)selectedCafeIds.add(check.dataset.cafeId);else selectedCafeIds.delete(check.dataset.cafeId);updateCounts();updateSettingsUI();markChanged();};
  const keyHandler=e=>{if(!['Enter',' '].includes(e.key))return;const a=e.target.closest('[data-stats-cafe]'),b=e.target.closest('[data-stats-key]');if(a||b){e.preventDefault();if(a)selectStatsDetailCafe(a.dataset.statsCafe);if(b)selectStatsDetailKeyword(b.dataset.statsKey);}};
  $('statsCafeChart').onkeydown=keyHandler;$('statsKeywordChart').onkeydown=keyHandler;
  ['statsFromDate','statsToDate'].forEach(id=>$(id).addEventListener('input',markStatsQueryDirty));
}

function strictUtcParts(y,m,d,h=0,min=0,sec=0,ms=0){
  if(![y,m,d,h,min,sec,ms].every(Number.isInteger)||y<1||y>9999||m<1||m>12||d<1||d>31||h<0||h>23||min<0||min>59||sec<0||sec>59||ms<0||ms>999)return null;
  const result=new Date(0);result.setUTCFullYear(y,m-1,d);result.setUTCHours(h,min,sec,ms);
  return result.getUTCFullYear()===y&&result.getUTCMonth()===m-1&&result.getUTCDate()===d?result:null;
}

function isDateKey(value){
  if(typeof value!=='string')return false;
  const m=value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return !!(m&&strictUtcParts(+m[1],+m[2],+m[3]));
}

function safeText(value,fallback=''){return typeof value==='string'?value:typeof value==='number'&&Number.isFinite(value)?String(value):fallback;}

function normalizeArticle(a){
  if(!a||typeof a!=='object'||Array.isArray(a)||!safeText(a.id)||(!safeText(a.cafe)&&!safeText(a.cafeId)))return null;
  const r={...a,id:safeText(a.id),cafe:safeText(a.cafe),cafeId:safeText(a.cafeId),title:safeText(a.title,'제목 미기록'),raw:safeText(a.raw),key:safeText(a.key),doc:safeText(a.doc),issue:safeText(a.issue),evidence:safeText(a.evidence),type:safeText(a.type,'미분류'),status:safeText(a.status,'미검토'),url:safeText(a.url)};
  if(Array.isArray(a.keys))r.keys=uniqueTerms(a.keys);else r.keys=uniqueTerms(r.key.split(/[,;\n]/));
  return r;
}

function checkpointEligible(r){
  return r?.source==='simulation'&&r.status==='완료'&&r.stepsDone?.includes('collect')&&r.settings?.stepCollect!==false&&r.period!=='기간 직접 지정'&&r.settings?.range!=='기간 직접 지정'&&!!dateValue(r.periodEnd);
}

function articleTerms(a){return uniqueTerms(Array.isArray(a?.keys)?a.keys:safeText(a?.key).split(/[,;\n]/));}

function openSelectedOriginal(){const p=posts.find(x=>articleIdentity(x)===selectedPostId);if(p&&/^https:\/\/cafe\.naver\.com\//.test(p.url||''))window.open(p.url,'_blank','noopener,noreferrer');}

function runCompletionText(r){
  const names={collect:'웹 수집',analyze:'AI 분석',report:'PPT 생성',mail:'Outlook 발송'};
  const done=(r.stepsDone||[]).map(k=>names[k]).filter(Boolean);
  return `${done.length?done.join(' · ')+' 단계 완료':'완료된 처리 단계 없음'} (화면 테스트)${r.reviewPending?' · 사후 검토 '+r.reviewPending+'건':''} · 실제 외부 실행 없음`;
}

function statsKnownRecords(){
  const map=new Map();
  for(const raw of statsRecords){
    const row=normalizeArticle(raw);if(!row)continue;ensureRecordCatalog(row);
    const id=statsRecordKey(row),previous=map.get(id);
    if(!previous){map.set(id,row);continue;}
    const t=r=>+(dateValue(r.lastCheckedAt)||dateValue(r.firstCollectedAt)||dateValue(r.date)||new Date(0));
    const winner=t(row)>=t(previous)?row:previous;
    const keys=uniqueTerms([...articleTerms(previous),...articleTerms(row)]);
    map.set(id,{...winner,keys,key:keys.join(', '),keywordIds:keys.map(k=>ensureKeyword(k,false).id)});
  }
  return [...map.values()];
}

function statsRangeError(from,to){
  if(!isDateKey(from)||!isDateKey(to))return '시작일과 종료일을 모두 올바른 날짜로 입력하세요.';
  if(from>to)return '종료일은 시작일과 같거나 뒤여야 합니다. 입력한 날짜는 바꾸지 않았습니다.';
  if((+new Date(to+'T00:00:00Z')-+new Date(from+'T00:00:00Z'))/DAY+1>MAX_QUERY_DAYS)return '이 HTML에서는 한 번에 100년 이내 범위를 조회할 수 있습니다. 더 짧은 기간으로 나누어 조회하세요.';
  return '';
}

function statsSelectedPeriodLabel(){
  if(!statsDetailDate)return '';
  const format=s=>s.replaceAll('-','.');return statsDetailDateEnd&&statsDetailDateEnd!==statsDetailDate?`${format(statsDetailDate)} ~ ${format(statsDetailDateEnd)}`:format(statsDetailDate);
}

function validStatsDetailPeriod(start,end){return isDateKey(start)&&isDateKey(end)&&start<=end&&start>=statsState.from&&end<=statsState.to;}

function statsDetailRows(){return statsApplyDetailPeriod(statsCalendarRecords());}

// ===== 데이터 분석 Excel(.xlsx) 내보내기 =====
// 단일 HTML에서도 외부 라이브러리 없이 열 수 있는 최소 XLSX(Open XML) 파일을 생성한다.
function xlsxXml(value){return String(value??'').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[ch]));}
function xlsxColName(index){let n=index+1,out='';while(n){n--;out=String.fromCharCode(65+n%26)+out;n=Math.floor(n/26);}return out;}
function xlsxCell(value,row,col,style=0){
  const ref=xlsxColName(col)+row,s=style?` s="${style}"`:'';
  if(typeof value==='number'&&Number.isFinite(value))return `<c r="${ref}"${s}><v>${value}</v></c>`;
  if(typeof value==='boolean')return `<c r="${ref}" t="b"${s}><v>${value?1:0}</v></c>`;
  const text=xlsxXml(value);return `<c r="${ref}" t="inlineStr"${s}><is><t xml:space="preserve">${text}</t></is></c>`;
}
function xlsxSheetXml(rows,widths=[]){
  const safeRows=Array.isArray(rows)?rows:[],maxCols=Math.max(1,...safeRows.map(r=>Array.isArray(r)?r.length:0));
  const cols=Array.from({length:maxCols},(_,i)=>`<col min="${i+1}" max="${i+1}" width="${Number(widths[i]||14)}" customWidth="1"/>`).join('');
  const body=safeRows.map((values,ri)=>{const arr=Array.isArray(values)?values:[values];return `<row r="${ri+1}">${arr.map((value,ci)=>xlsxCell(value,ri+1,ci,ri===0?1:(typeof value==='string'&&value.length>35?2:0))).join('')}</row>`;}).join('');
  const last=`${xlsxColName(maxCols-1)}${Math.max(1,safeRows.length)}`;
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:${last}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${cols}</cols><sheetData>${body}</sheetData>${safeRows.length?`<autoFilter ref="A1:${last}"/>`:''}</worksheet>`;
}
function xlsxCrc32(bytes){
  if(!xlsxCrc32.table){xlsxCrc32.table=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=(c&1)?0xEDB88320^(c>>>1):c>>>1;return c>>>0;});}
  let crc=0xFFFFFFFF;for(const b of bytes)crc=xlsxCrc32.table[(crc^b)&255]^(crc>>>8);return (crc^0xFFFFFFFF)>>>0;
}
function xlsxU16(n){return new Uint8Array([n&255,(n>>>8)&255]);}
function xlsxU32(n){return new Uint8Array([n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255]);}
function xlsxJoin(parts){const total=parts.reduce((n,p)=>n+p.length,0),out=new Uint8Array(total);let at=0;for(const p of parts){out.set(p,at);at+=p.length;}return out;}
function xlsxZip(entries){
  const enc=new TextEncoder(),locals=[],centrals=[];let offset=0;
  const now=new Date(),year=Math.max(1980,now.getFullYear()),dosTime=(now.getHours()<<11)|(now.getMinutes()<<5)|(now.getSeconds()>>1),dosDate=((year-1980)<<9)|((now.getMonth()+1)<<5)|now.getDate();
  for(const entry of entries){
    const name=enc.encode(entry.name),data=typeof entry.data==='string'?enc.encode(entry.data):entry.data,crc=xlsxCrc32(data),size=data.length;
    const local=xlsxJoin([xlsxU32(0x04034b50),xlsxU16(20),xlsxU16(0x0800),xlsxU16(0),xlsxU16(dosTime),xlsxU16(dosDate),xlsxU32(crc),xlsxU32(size),xlsxU32(size),xlsxU16(name.length),xlsxU16(0),name,data]);
    const central=xlsxJoin([xlsxU32(0x02014b50),xlsxU16(20),xlsxU16(20),xlsxU16(0x0800),xlsxU16(0),xlsxU16(dosTime),xlsxU16(dosDate),xlsxU32(crc),xlsxU32(size),xlsxU32(size),xlsxU16(name.length),xlsxU16(0),xlsxU16(0),xlsxU16(0),xlsxU16(0),xlsxU32(0),xlsxU32(offset),name]);
    locals.push(local);centrals.push(central);offset+=local.length;
  }
  const central=xlsxJoin(centrals),end=xlsxJoin([xlsxU32(0x06054b50),xlsxU16(0),xlsxU16(0),xlsxU16(entries.length),xlsxU16(entries.length),xlsxU32(central.length),xlsxU32(offset),xlsxU16(0)]);
  return new Blob([...locals,central,end],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
function statsExportRows(){return statsDetailRows().slice().sort((a,b)=>a.date.localeCompare(b.date)||cafeLabel(a.cafeId).localeCompare(cafeLabel(b.cafeId),'ko')||String(a.id).localeCompare(String(b.id)));}
function statsExportWorkbook(mode='full'){
  const records=statsExportRows(),cafeCounts=new Map(),keywordCounts=new Map(),dateCounts=new Map();
  for(const r of records){cafeCounts.set(r.cafeId,(cafeCounts.get(r.cafeId)||0)+1);dateCounts.set(r.date,(dateCounts.get(r.date)||0)+1);for(const id of r.keywordIds||[])keywordCounts.set(id,(keywordCounts.get(id)||0)+1);}
  const detailRows=[['최초 수집일','카페 코드','카페명','게시글 ID','제목','키워드','AI 구분','검토','차종','최초 수집','마지막 확인','실행 ID','원문 본문','AI 분석','근거'],...records.map(r=>[r.date,cafeLabel(r.cafeId),cafeDisplayName(r.cafeId),String(r.id||''),r.title||'',(r.keys||[]).join(', '),r.type||'',r.status||'',r.vehicle||'',r.firstCollectedAt||'',r.lastCheckedAt||'',r.runId||'',r.raw||'',r.analysis||'',r.evidence||''])];
  const dateSelected=!!statsDetailDate,selectedLabel=statsSelectedPeriodLabel()||'';
  const cafeChartRows=statsCafeRows();
  const keywordChartRows=statsKeywordRows();
  const cafeRows=dateSelected
    ?[['카페 코드','카페명','현재 상태','조회 기간 게시글 수','선택 날짜/구간 게시글 수'],...cafeChartRows.map(c=>[c.code,c.name,c.active?'수집 중':'수집 중단',c.count,c.selectedCount])]
    :[['카페 코드','카페명','현재 상태','게시글 수'],...cafeChartRows.map(c=>[c.code,c.name,c.active?'수집 중':'수집 중단',c.count])];
  const keywordRows=dateSelected
    ?[['키워드','현재 상태','조회 기간 매칭 게시글 수','선택 날짜/구간 매칭 게시글 수'],...keywordChartRows.map(k=>[k.name,k.deleted?'삭제됨':k.active?'검색 사용 중':'검색 중단',k.count,k.selectedCount])]
    :[['키워드','현재 상태','매칭 게시글 수'],...keywordChartRows.map(k=>[k.name,k.deleted?'삭제됨':k.active?'검색 사용 중':'검색 중단',k.count])];
  const trendSource=statsTrendValues(),trendGrouped=statsGroupTrendRows(trendSource.rows,statsTrendView.granularity);
  const trendRows=[['집계 단위','시작일','종료일','표시','게시글 수'],...trendGrouped.map(r=>[statsGranularityName(statsTrendView.granularity),r.start,r.end,r.label||r.d||r.date,r.v])];
  const drill=[selectedLabel?`기간 ${selectedLabel}`:'',statsDetailCafe?`카페 ${cafeLabel(statsDetailCafe)}`:'',statsDetailKeyword?`키워드 ${keywordLabel(statsDetailKeyword)}`:''].filter(Boolean).join(' · ')||'없음';
  const keywordMatchTotal=keywordChartRows.reduce((sum,k)=>sum+k.count,0);
  const selectedKeywordMatchTotal=dateSelected?keywordChartRows.reduce((sum,k)=>sum+k.selectedCount,0):'';
  const baseScopeRows=statsCalendarRecords();
  const summaryRows=[['항목','값'],['적용 조회 조건',statsAppliedQuerySummary()],['현재 상세 선택',drill],['조회 기간 고유 게시글 수',baseScopeRows.length],...(dateSelected?[['선택 날짜/구간 고유 게시글 수',records.length]]:[]),['비교 그래프 범위 키워드 매칭 수',keywordMatchTotal],...(dateSelected?[['선택 날짜/구간 키워드 매칭 수',selectedKeywordMatchTotal]]:[]),['게시글이 있는 카페 수',new Set(baseScopeRows.map(r=>r.cafeId)).size],['현재 추이 집계 단위',statsGranularityName(statsTrendView.granularity)],['Excel 생성 시각',currentTimeText()]];
  const infoRows=[['항목','값'],['내보내기 범위',mode==='counts'?'현재 데이터 분석 화면의 게시글 수/집계 수치':'현재 데이터 분석 화면에 표시되는 수집 데이터'],['적용 조회 조건',statsAppliedQuerySummary()],['현재 상세 선택',drill],['내보낸 고유 게시글 수',records.length],['Excel 생성 시각',currentTimeText()],['미적용 조회 조건 변경',statsQueryDirty?statsPendingQuerySummary():'없음'],['집계 범위','수집 데이터는 상세 선택 범위입니다. 카페 그래프는 카페 선택을, 키워드 그래프는 키워드 선택을 제외한 비교 범위를 유지합니다.'],['날짜 기준','최초 수집일(KST)'],['집계 주의','키워드별 매칭 수는 한 게시글이 여러 키워드에 해당하면 각각 1건으로 집계되어 합계에 중복이 포함될 수 있습니다.']];
  const sheets=mode==='counts'?
    [
      {name:'게시글 수 요약',rows:summaryRows,widths:[31,46]},
      {name:'카페별 게시글 수',rows:cafeRows,widths:dateSelected?[12,32,14,20,24]:[12,32,14,16]},
      {name:'키워드별 게시글 수',rows:keywordRows,widths:dateSelected?[28,16,23,27]:[28,16,18]},
      {name:'기간별 추이',rows:trendRows,widths:[14,16,16,20,14]},
      {name:'조회 정보',rows:infoRows,widths:[24,85]}
    ]:
    [
      {name:'수집 데이터',rows:detailRows,widths:[13,11,26,14,40,28,14,12,12,20,20,32,65,65,55]},
      {name:'카페별 집계',rows:cafeRows,widths:dateSelected?[12,32,14,20,24]:[12,32,14,16]},
      {name:'키워드별 집계',rows:keywordRows,widths:dateSelected?[28,16,23,27]:[28,16,18]},
      {name:'기간별 추이',rows:trendRows,widths:[14,16,16,20,14]},
      {name:'조회 정보',rows:infoRows,widths:[24,85]}
    ];
  const contentTypes=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`;
  const rootRels=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  const workbook=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s,i)=>`<sheet name="${xlsxXml(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')}</sheets></workbook>`;
  const workbookRels=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')}<Relationship Id="rId${sheets.length+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`;
  const styles=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF079C46"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="top"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
  const entries=[{name:'[Content_Types].xml',data:contentTypes},{name:'_rels/.rels',data:rootRels},{name:'xl/workbook.xml',data:workbook},{name:'xl/_rels/workbook.xml.rels',data:workbookRels},{name:'xl/styles.xml',data:styles},...sheets.map((s,i)=>({name:`xl/worksheets/sheet${i+1}.xml`,data:xlsxSheetXml(s.rows,s.widths)}))];
  return {blob:xlsxZip(entries),count:records.length};
}
function exportStatsExcel(mode='full'){
  const {blob,count}=statsExportWorkbook(mode),p=parts(new Date()),suffix=mode==='counts'?'_게시글수':'',name=`V10_데이터분석${suffix}_${p.y}${pad(p.m)}${pad(p.d)}_${pad(p.h)}${pad(p.min)}.xlsx`;
  downloadBlob(blob,name);closeStatsExportMenu();
  const type=mode==='counts'?'게시글 수 Excel':'전체 Excel';
  toast(statsQueryDirty?`${type} 저장 · 적용하지 않은 조회 조건 변경은 제외했습니다.`:`${type}을 저장했습니다. · 현재 상세 ${count}건`);
}
function statsExportScopeSummary(){
  const count=statsExportRows().length,drill=[statsSelectedPeriodLabel(),statsDetailCafe?cafeLabel(statsDetailCafe):'',statsDetailKeyword?keywordLabel(statsDetailKeyword):''].filter(Boolean).join(' · ');
  return `${statsAppliedQuerySummary()} · 현재 상세 ${count}건${drill?' · '+drill:''}${statsQueryDirty?' · 미적용 변경 있음':''}`;
}
function closeStatsExportMenu(){const menu=$('statsExportMenu'),btn=$('statsExcelBtn');if(menu)menu.hidden=true;if(btn)btn.setAttribute('aria-expanded','false');}
function toggleStatsExportMenu(force){const menu=$('statsExportMenu'),btn=$('statsExcelBtn');if(!menu||!btn)return;const open=force===undefined?menu.hidden:!!force;menu.hidden=!open;btn.setAttribute('aria-expanded',String(open));if(open){const scope=$('statsExportScope');if(scope)scope.textContent=statsExportScopeSummary();$('statsExcelFullBtn')?.focus();}}

function statsDraftFromApplied(){return {periodMode:statsState.periodMode,cafe:statsState.cafe,keyword:statsState.keyword,from:statsState.from,to:statsState.to};}

function statsReadDraft(){return {periodMode:$('statsPeriodPreset').value,cafe:$('statsCafeFilter').value,keyword:$('statsKeywordFilter').value,from:$('statsFromDate').value,to:$('statsToDate').value};}

function statsDraftSignature(q){return JSON.stringify([q.periodMode,q.cafe,q.keyword,q.periodMode==='custom'?q.from:'',q.periodMode==='custom'?q.to:'']);}

function shiftCalendarMonths(dateKey,delta){
  const date=new Date(dateKey+'T00:00:00Z'),y=date.getUTCFullYear(),m=date.getUTCMonth()+delta,d=date.getUTCDate();
  const first=new Date(Date.UTC(y,m,1)),lastDay=new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate();
  return new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth(),Math.min(d,lastDay))).toISOString().slice(0,10);
}

function changeStatsGranularity(unit){if(!['day','week','month'].includes(unit)||statsTrendView.granularity===unit)return;statsTrendView.granularity=unit;statsTrendView.page=null;clearStatsDetailDate();}

function changeStatsTrendPage(delta){
  const grouped=statsGroupTrendRows(statsTrendValues().rows,statsTrendView.granularity),paged=statsTrendPage(grouped,statsTrendView.granularity),next=Math.max(0,Math.min(paged.pages-1,paged.page+delta));if(next===paged.page)return;
  statsTrendView.page=next;clearStatsDetailDate();
}

function setNaverLoginState(state,checkedAt=null){
  if(!['verified','expired','unverified'].includes(state))throw new Error('유효하지 않은 인증 상태');
  if(state==='verified'&&!dateValue(checkedAt))throw new Error('실제 확인 시각이 필요합니다.');
  naverAuthState.state=state;naverAuthState.checkedAt=checkedAt;updateNaverLoginStatus();
}

function activeModal(){
  const layered=['naverLoginBackdrop','stopConfirmBackdrop','actionConfirmBackdrop','modalBackdrop','keywordConfirmBackdrop'].map($).find(e=>e.classList.contains('show'));if(layered)return layered;
  if(!$('keywordManagerBackdrop').hidden)return $('keywordManagerBackdrop');
  if(!$('cafeManagerBackdrop').hidden)return $('cafeManagerBackdrop');
  return null;
}

function syncModalInert(){
  const modal=activeModal(),statsOpen=$('statsView').classList.contains('show'),historyOpen=$('historyView').classList.contains('show');
  $('mainApp').inert=!!modal||statsOpen||historyOpen;$('statsView').inert=!!modal||!statsOpen;$('historyView').inert=!!modal||!historyOpen;
  if($('globalTopbar'))$('globalTopbar').inert=!!modal;
  for(const id of ['naverLoginBackdrop','cafeManagerBackdrop','keywordManagerBackdrop','stopConfirmBackdrop','actionConfirmBackdrop','modalBackdrop','keywordConfirmBackdrop'])$(id).inert=!!modal&&$(id)!==modal;
}

function pptStageDone(r){return !!(r&&(r.source==='sample'||(r.stepsDone||[]).includes('report')));}

function pptAvailable(r){return !!(r&&(linkedPptFiles.has(r.id)||(pptStageDone(r)&&(/\.pptx?$/i.test(r.ppt||'')))));}

function init(){
  initTheme();normalizeHistory();sourceOptions();loadSettings();updateCounts();updateSettingsUI();updateSaveBar();renderPosts();bindEvents();updateNaverLoginStatus();updateGlobalHeader('main');updateTimes();renderHistory();renderStats();syncModalInert();
  $('statsDateClearBtn').onclick=clearStatsDetailDate;
  $('statsTrendWrap').addEventListener('focusin',e=>{const n=e.target.closest('[data-stats-trend-start]');if(n)showStatsTrendTooltip(n);});$('statsTrendWrap').addEventListener('focusout',hideStatsTrendTooltip);
  if(typeof ResizeObserver==='function'){
    let size='';statsResizeObserver=new ResizeObserver(entries=>{const r=entries[0].contentRect,key=Math.round(r.width)+':'+Math.round(r.height);if(key===size||r.width===0||r.height===0)return;size=key;clearTimeout(statsResizeTimer);statsResizeTimer=setTimeout(()=>{if($('statsView').classList.contains('show'))renderStatsTrend();},80);});statsResizeObserver.observe($('statsTrendWrap'));
  }
  appendLog('[READY] V10 DB·실행 서버에 연결되었습니다.');
  if(corruptStorage.size||skippedHistoryRecords)toast('일부 저장 데이터 형식이 잘못되었습니다. 화면은 복구했으며 원본 저장값은 보존합니다. 저장 시 복구 사본을 남깁니다.');
  else if(!storageAvailable)toast('브라우저 저장소 제한 · 이 페이지를 닫으면 변경사항이 사라질 수 있습니다.');
  setInterval(updateTimes,1000);
}

