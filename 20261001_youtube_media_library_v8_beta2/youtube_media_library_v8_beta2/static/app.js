import {icon, paintIcons} from './icons.js';

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {connection:null,token:'', filter:'all', tags:new Set(), tagList:[], items:[], total:0, selected:null,
  active:null, mediaKind:'video', mediaFile:null, textKind:'timestamp', text:null, textFile:null,
  jobs:[], jobSignature:'', limit:20, listTicket:0, selectTicket:0, listBusy:false,
  textTicket:0, wantedProfile:null, pendingPlay:null, jobDetails:new Map(), cueNodes:[], activeCue:null};
const statusNames = {queued:'대기', running:'진행 중', cancelling:'취소 중', cancelled:'취소됨', success:'완료', partial:'부분 완료', failed:'실패', interrupted:'중단됨'};
const finished = new Set(['success','partial','failed','cancelled','interrupted']);
const FILE_KINDS = {
  video:{label:'원본 영상',order:0,remoteRestricted:true},
  mobile:{label:'모바일 720p',order:1,remoteRestricted:true},
  economy:{label:'절약 480p',order:2,remoteRestricted:true},
  preview:{label:'이전 호환본',order:3,remoteRestricted:true},
  audio:{label:'음원',order:4,remoteRestricted:true},
  timestamp:{label:'타임스탬프 TXT',order:5},
  transcript:{label:'원문 TXT',order:6},
  metadata:{label:'정보 JSON',order:7},
  thumbnail:{label:'썸네일',order:8},
};
let toastTimer, validateTimer, searchTimer, listAbort, detailAbort, libraryTimer;
let eventStream, fallbackTimer, reconnectTimer, sseErrors=0, lastLibraryRevision=null;

function toast(message, error=false) {
  const node = $('#toast'); node.textContent = message; node.hidden=false;
  node.classList.toggle('error', error); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.hidden=true, error ? 7000 : 4000);
}
async function api(path, method='GET', body, signal) {
  const headers = {'X-YME-Token':state.token};
  if (body !== undefined) headers['Content-Type']='application/json';
  let response;
  try { response = await fetch(path, {method, headers, signal, body:body === undefined ? undefined : JSON.stringify(body)}); }
  catch (e) { if(e.name==='AbortError')throw e; throw new Error('서버에 연결할 수 없습니다. 서버 PC와 네트워크 상태를 확인해 주세요.'); }
  const data = await response.json().catch(() => ({}));
  if(response.status===401){location.replace('/login');throw new Error('로그인이 만료되었습니다.');}
  if (!response.ok) {
    let detail = data.detail || `요청 실패 (${response.status})`;
    if (Array.isArray(detail)) detail = detail.map(x => x.msg || '입력을 확인해 주세요.').join(' / ');
    throw new Error(detail);
  }
  return data;
}
function protect(fn) { return (...args) => Promise.resolve().then(() => fn(...args)).catch(e => {if(e.name!=='AbortError')toast(e.message, true);}); }
function showDialog(id) { if (!$(id).open) $(id).showModal(); }
function bytes(n) { if (!n) return '0 B'; const k = Math.min(3, Math.floor(Math.log(n)/Math.log(1024))); return `${(n/1024**k).toFixed(k ? 1 : 0)} ${['B','KB','MB','GB'][k]}`; }
function duration(n) { n = Math.floor(Number(n)||0); return n ? (n >= 3600 ? `${Math.floor(n/3600)}:` : '')+`${String(Math.floor(n%3600/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}` : ''; }
function when(s) { return String(s||'').slice(0,10).replaceAll('-','.'); }
function tagNames(value) { return value.split(/[,\n]/).map(x => x.trim().replace(/^#/, '')).filter(Boolean); }
function fileOf(item, kind) { return item?.files.find(f => f.kind === kind); }
function browserVideo(file) { return file && ['.mp4','.m4v','.webm'].some(ext=>file.name.toLowerCase().endsWith(ext)); }
function highlight(text, query) {
  if (!query) return esc(text);
  const low = text.toLocaleLowerCase(), needle = query.toLocaleLowerCase(); let start=0, output='', at;
  while ((at = low.indexOf(needle, start)) !== -1) { output += esc(text.slice(start,at)) + '<mark>' + esc(text.slice(at,at+needle.length)) + '</mark>'; start=at+needle.length; }
  return output+esc(text.slice(start));
}

async function refreshTags() {
  state.tagList = await api('/api/tags');
  // 삭제된 태그가 필터에 남아 검색 결과를 가리지 않도록 정리합니다.
  for (const id of state.tags) if (!state.tagList.some(t=>t.id === id)) state.tags.delete(id);
  $('#tag-filters').innerHTML = state.tagList.length ? state.tagList.map(t => `<button data-tag="${t.id}" class="${state.tags.has(t.id)?'active':''}" aria-pressed="${state.tags.has(t.id)}"><span class="hash">#</span><span class="tag-label">${esc(t.name)}</span><span class="count">${t.count}</span></button>`).join('') : '<p class="sidebar-hint">태그를 만들어<br>자료를 분류해 보세요.</p>';
  $('#tag-suggestions').innerHTML = state.tagList.map(t => `<option value="${esc(t.name)}"></option>`).join('');
  $('#active-tags').innerHTML = [...state.tags].map(id => {const t=state.tagList.find(t=>t.id===id);return `<button class="filter-chip" data-tag="${id}"># ${esc(t?.name)} ×</button>`;}).join('');
  const mobileTags=$('#mobile-tag-filters');
  if(mobileTags) mobileTags.innerHTML=state.tagList.length?state.tagList.map(t=>`<button data-mobile-tag="${t.id}" class="mobile-tag-choice ${state.tags.has(t.id)?'active':''}" aria-pressed="${state.tags.has(t.id)}"><span># ${esc(t.name)}</span><small>${t.count}</small></button>`).join(''):'<p class="list-empty">아직 만든 태그가 없습니다.</p>';
  $('#mobile-tags').classList.toggle('active',state.tags.size>0);
  $('#reset-filters').hidden = !state.tags.size && !$('#search').value && state.filter==='all';
  if ($('#tags-dialog').open) renderTagManager();
}
async function refreshLibrary(options={}) {
  const append=options.append===true;
  if(append && state.listBusy)return;
  const ticket=++state.listTicket;
  listAbort?.abort(); listAbort=new AbortController(); state.listBusy=true;
  const offset=append?state.items.length:0;
  const params=new URLSearchParams({q:$('#search').value, sort:$('#sort').value,
    limit:'20',offset:String(offset),tags:[...state.tags].join(',')});
  if(['video','audio','transcript'].includes(state.filter))params.set('kind',state.filter);
  if(state.filter==='favorite')params.set('favorite','true');
  if(state.filter==='untagged')params.set('untagged','true');
  $('#load-more').disabled=true;
  try {
    const response=await api('/api/items?'+params,'GET',undefined,listAbort.signal);
    if(ticket!==state.listTicket)return;
    const previous=append?state.items:[];
    state.items=[...new Map([...previous,...response.items].map(x=>[x.id,x])).values()];
    state.total=response.total;
    $('#list-count').textContent=`자료 ${state.total.toLocaleString()}개 · ${state.items.length}개 표시`+(state.tags.size>1?' · 태그 모두 일치':'');
    $('#header-count').textContent=`${state.total.toLocaleString()}개의 자료`;
    $('#load-more').hidden=state.items.length>=state.total;
    $('#load-more').textContent='다음 20개 보기';renderList();
    if(state.filter==='all'&&!$('#search').value&&!state.tags.size)$('#all-count').textContent=state.total;
    $('#reset-filters').hidden=!state.tags.size&&!$('#search').value&&state.filter==='all';
    // NEVER select/load a video when a list or job summary refreshes.
    const current=state.items.find(x=>x.id===state.selected);
    if(current && state.active){state.active.tags=current.tags;state.active.favorite=current.favorite;renderItemTags();}
  } finally {if(ticket===state.listTicket){state.listBusy=false;$('#load-more').disabled=false;}}
}
function renderList() {
  const node=$('#items');
  if (!state.items.length) {
    node.innerHTML='<div class="list-empty">'+($('#search').value || state.tags.size || state.filter!=='all' ? '조건에 맞는 자료가 없습니다.<br>검색어나 태그 필터를 바꿔 보세요.' : '아직 저장된 자료가 없습니다.<br>URL 또는 기존 폴더를 추가해 주세요.')+'</div>';
    return;
  }
  node.innerHTML=state.items.map(x=>{
    const thumb=fileOf(x,'thumbnail'), badges=[['video','VIDEO'],['audio','AUDIO'],['transcript','TXT']].filter(([k])=>fileOf(x,k));
    const time=duration(x.metadata.duration_seconds);
    return `<button class="item ${x.id===state.selected?'active':''}" role="listitem" data-item="${x.id}" aria-label="${esc(x.title)}" title="${esc(x.title)}"><span class="item-thumb">${icon(fileOf(x,'video')?'video':fileOf(x,'audio')?'audio':'text')}${thumb?`<img src="/api/thumbnails/${thumb.id}" alt="" loading="lazy" decoding="async">`:''}${time?`<span class="duration">${time}</span>`:''}</span><span class="item-text"><span class="item-title">${esc(x.title)}</span><span class="item-channel">${esc(x.channel || '로컬 자료')} · ${when(x.added_at)}</span><span class="item-badges">${badges.map(([k,label])=>`<span class="file-badge">${label}</span>`).join('')}${x.favorite?'<span class="file-badge">★</span>':''}</span>${x.tags.length?`<span class="item-tag-mini">${x.tags.map(t=>'#'+esc(t.name)).join(' · ')}</span>`:''}</span></button>`;
  }).join('');
}
function clearDetail() {
  ++state.selectTicket; ++state.textTicket; detailAbort?.abort();state.selected=null;state.active=null;
  releaseMedia();state.text=null;state.cueNodes=[];state.activeCue=null;
  $('#detail-empty').hidden=false;$('#detail-content').hidden=true;
}
function releaseMedia() {
  for(const media of [$('#video-player'),$('#audio-player')]){
    media.pause();media.removeAttribute('src');media.preload='none';media.load();
  }
  $('#buffer-status').hidden=true;
}
async function selectItem(id) {
  const ticket=++state.selectTicket;
  detailAbort?.abort();detailAbort=new AbortController();
  const item=await api('/api/items/'+id+'?compact=true','GET',undefined,detailAbort.signal);
  if(ticket!==state.selectTicket)return;
  state.selected=id;state.active=item;state.pendingPlay=null;state.text=null;state.textFile=null;
  state.cueNodes=[];state.activeCue=null;++state.textTicket;
  $('#detail-empty').hidden=true;$('#detail-content').hidden=false;
  $('#detail-title').textContent=item.title;
  $('#detail-meta').textContent=[item.channel,when(item.added_at),duration(item.metadata.duration_seconds),item.metadata.playlist?.title].filter(Boolean).join('  ·  ');
  $('#favorite').classList.toggle('selected',!!item.favorite);
  let sourceOK=false;try{sourceOK=['https:','http:'].includes(new URL(item.source_url).protocol);}catch{}
  $('#source-link').hidden=!sourceOK;if(sourceOK)$('#source-link').href=item.source_url;
  renderList();renderItemTags();renderFiles();
  $('.workspace').scrollTop=0;
  $('.media-column').scrollTop=0;
  $('#text-view').scrollTop=0;
  if(state.mediaKind==='video'&&!fileOf(item,'video')&&fileOf(item,'audio'))state.mediaKind='audio';
  if(state.mediaKind==='audio'&&!fileOf(item,'audio'))state.mediaKind='video';
  $$('[data-media]').forEach(b=>b.disabled=!fileOf(item,b.dataset.media));
  $('#playback-quality').value='auto';renderMedia();
  $$('[data-text]').forEach(b=>b.disabled=b.dataset.text!=='metadata'&&!fileOf(item,b.dataset.text));
  if(state.textKind!=='metadata'&&!fileOf(item,state.textKind))state.textKind=fileOf(item,'timestamp')?'timestamp':fileOf(item,'transcript')?'transcript':'metadata';
  $$('[data-text]').forEach(b=>b.classList.toggle('active',b.dataset.text===state.textKind));
  $('#text-search').value='';$('#copy-text').disabled=true;$('#text-native').hidden=true;
  $('#text-status').textContent='탭 또는 아래 버튼을 눌러 필요한 텍스트만 불러옵니다.';
  $('#text-view').innerHTML='<div class="text-deferred"><p>자막과 원문은 필요할 때 불러옵니다.</p><button class="button" data-load-text>자막 / 정보 불러오기</button></div>';
}
async function refreshSelected() {
  if(!state.selected)return;
  const id=state.selected,ticket=state.selectTicket;
  const item=await api('/api/items/'+id+'?compact=true');
  if(id!==state.selected||ticket!==state.selectTicket)return;
  state.active=item;renderFiles();renderItemTags();
  const p=state.mediaKind==='video'?$('#video-player'):$('#audio-player');
  // Progress updates must never reset src or playback position.
  if(!p.hasAttribute('src'))renderMedia();
}
function renderItemTags() {
  if (!state.active) return;
  $('#item-tags').innerHTML=state.active.tags.map(t=>`<span class="tag-pill">${esc(t.name)}<button data-remove-tag="${t.id}" title="태그 제거: ${esc(t.name)}" aria-label="태그 제거: ${esc(t.name)}">${icon('close')}</button></span>`).join('');
}
async function saveItemTags(names) {
  const id=state.selected,ticket=state.selectTicket;
  await api(`/api/items/${id}/tags`,'PUT',{names});
  if (state.selected===id && state.selectTicket===ticket) {
    const updated=await api('/api/items/'+id+'?compact=true');
    if (state.selected===id && state.selectTicket===ticket){state.active=updated;renderItemTags();}
  }
  await refreshTags();await refreshLibrary();
}
async function addTagFromInput() {
  if(!state.active)return;
  const input=$('#tag-input'),names=tagNames(input.value);
  if(!names.length)return;
  await saveItemTags([...state.active.tags.map(t=>t.name),...names]);
  input.value='';toast('태그를 저장했습니다.');
}
function mediaAllowed(f){return state.connection?.can_stream!==false || !(FILE_KINDS[f.kind]?.remoteRestricted||f.size>5000000);}
function renderFiles() {
  const rank=kind=>FILE_KINDS[kind]?.order??100;
  const files=state.active.files.filter(f=>f.kind!=='segments').sort((a,b)=>rank(a.kind)-rank(b.kind));
  $('#file-count').textContent=`${files.length}개`;
  const label=f=>FILE_KINDS[f.kind]?.label||f.kind||f.name.split('.').at(-1).toUpperCase();
  $('#file-list').innerHTML=files.map(f=>`<div class="file-row"><span class="file-type">${esc(f.name.split('.').at(-1).toUpperCase())}</span><div class="file-name"><span title="${esc(f.name)}">${esc(f.name)}</span><small>${esc(label(f))} · ${bytes(f.size)}</small></div>${mediaAllowed(f)?`<a href="/media/${f.id}?download=1" class="icon-btn" download title="${esc(f.name)} 다운로드" aria-label="${esc(f.name)} 다운로드">${icon('download')}</a>`:'<span class="micro">개인 접속</span>'}</div>`).join('');
  $('#download-shortcuts').innerHTML='<strong>바로 다운로드</strong>'+files.map(f=>mediaAllowed(f)?`<a href="/media/${f.id}?download=1" class="button small" download="${esc(f.name)}" title="${esc(f.name)}">${icon('download')}${esc(label(f))} · ${bytes(f.size)}</a>`:`<span class="micro">${esc(label(f))} · 개인 접속 필요</span>`).join('');
}
function renderMedia() {
  releaseMedia();
  const video=$('#video-player'),audio=$('#audio-player'),item=state.active;
  const blocked=state.connection?.can_stream===false;
  $('#remote-media-notice').hidden=!blocked;
  const privateURL=state.connection?.fast_url||state.connection?.private_url;
  $('#private-media-link').hidden=!privateURL;if(privateURL)$('#private-media-link').href=privateURL;
  const isVideo=state.mediaKind==='video',original=fileOf(item,'video');
  const pref=$('#playback-quality').value;
  const preferMobile=state.connection?.remote||matchMedia('(pointer:coarse),(max-width:650px)').matches;
  let f,profile=null;
  if(isVideo){
    if(pref==='original')f=original;
    else if(pref==='compatibility'){
      f=fileOf(item,'preview')||fileOf(item,'mobile')||(browserVideo(original)?original:null);
      if(!f&&original)profile='mobile720';
    }
    else if(pref==='mobile480'){f=fileOf(item,'economy');profile='mobile480';}
    else if(pref==='mobile720'||(pref==='auto'&&preferMobile)){f=fileOf(item,'mobile');profile='mobile720';}
    else {
      f=fileOf(item,'preview')||fileOf(item,'mobile')||fileOf(item,'economy')||(browserVideo(original)?original:null);
      if(!f&&original)profile='mobile720';
    }
  }else f=fileOf(item,'audio');
  state.mediaFile=blocked?null:f;state.wantedProfile=profile;
  const available=!blocked&&!!(f||(isVideo&&original));
  video.hidden=!available||!isVideo;$('#audio-stage').hidden=!available||isVideo;$('#no-media').hidden=available;
  $('#no-media p').textContent=blocked?'관리 전용 연결': '이 항목에는 미디어 파일이 없습니다.';
  $('#playback-quality').disabled=!isVideo||!original||blocked;
  $('#playback-warning').hidden=true;$('#start-playback').hidden=!available;
  const thumb=fileOf(item,'thumbnail');
  if(thumb)video.poster='/api/thumbnails/'+thumb.id;else video.removeAttribute('poster');
  $$('[data-media]').forEach(b=>b.classList.toggle('active',b.dataset.media===state.mediaKind));
  const label=profile==='mobile480'?'절약 480p':'모바일 720p';
  $('#start-playback').textContent=f?`▶ ${isVideo?'영상':'음원'} 재생`:`${label} 만들기`;
  $('#media-filename').textContent=f?`${f.name} · ${bytes(f.size)}`:(available?`${label}가 아직 없습니다. 원본 대신 재생본을 만든 뒤 재생합니다.`:'');
  $('#media-download').hidden=!f||blocked;
  if(f){$('#media-download').href='/media/'+f.id+'?download=1';$('#media-download').innerHTML=icon('download')+`선택 파일 · ${bytes(f.size)}`;}
  $('#original-download').hidden=!original||blocked||f?.id===original?.id;
  if(original){$('#original-download').href='/media/'+original.id+'?download=1';$('#original-download').textContent=`원본 · ${bytes(original.size)}`;}
  $('#native-open').hidden=!f||!state.connection?.desktop_actions;
  $('#make-preview').hidden=!original||blocked;
  $('#make-preview').textContent='모바일 720p 재생본 생성 / 갱신';
  $('#preview-notice').hidden=!(f&&['mobile','economy','preview'].includes(f.kind));
  $('#preview-notice').textContent=f?.kind==='economy'?'절약 480p · H.264/AAC · 원본 보존':f?.kind==='mobile'?'모바일 720p · H.264/AAC · 원본 보존':'이전 브라우저 호환본 · 비트레이트 제한 없음';
  // No video/audio src is assigned until the explicit play gesture.
}
async function queueMobile(profile='mobile720') {
  if(!state.active)return;
  const id=state.selected;
  const r=await api(`/api/items/${id}/mobile-preview`,'POST',{profile});
  state.pendingPlay={id,profile,jobId:r.id};
  toast('재생본을 준비합니다. 완료 후 재생 버튼을 눌러 주세요. 원본은 유지됩니다.');
  $('#media-filename').textContent='모바일 재생본 준비 중 · 하단 작업 대기열에서 확인하세요.';
}
async function startPlayback(at=null) {
  if(!state.mediaFile){
    if(state.wantedProfile&&fileOf(state.active,'video'))return queueMobile(state.wantedProfile);
    toast('재생할 파일이 없습니다.');return;
  }
  const p=state.mediaKind==='video'?$('#video-player'):$('#audio-player');
  if(!p.hasAttribute('src')){p.src='/media/'+state.mediaFile.id;p.load();}
  if(at!==null){
    const seek=()=>{try{p.currentTime=at;}catch{}};
    if(p.readyState>=1)seek();else p.addEventListener('loadedmetadata',seek,{once:true});
  }
  try{await p.play();$('#start-playback').hidden=true;}
  catch{toast('재생 버튼을 다시 누르거나 모바일 재생본을 만들어 주세요.',true);$('#start-playback').hidden=false;}
}
async function loadText(ticket=state.selectTicket) {
  const textTicket=++state.textTicket;const kind=state.textKind;
  const f=fileOf(state.active,kind); state.textFile=f;
  $$('[data-text]').forEach(b=>b.classList.toggle('active',b.dataset.text===state.textKind));
  state.text=null;state.cueNodes=[];state.activeCue=null;$('#copy-text').disabled=true;
  $('#text-view').textContent='불러오는 중…';
  const data=f ? await api('/api/text/'+f.id) : {text:JSON.stringify(state.active.metadata,null,2),cues:[],truncated:false};
  if (ticket!==state.selectTicket || textTicket!==state.textTicket || kind!==state.textKind || f?.id!==state.textFile?.id) return;
  state.text=data;renderText();$('#copy-text').disabled=false;
  $('#text-native').hidden=!state.connection?.desktop_actions || !f || !['transcript','timestamp'].includes(state.textKind);
  $('#text-status').textContent=data.truncated ? '표시 상한 2MB · 전체 내용은 파일을 내려받아 확인하세요.' : state.textKind==='timestamp' ? '시간을 누르면 해당 위치로 이동합니다.' : '저장된 내용 그대로 표시합니다.';
}
function renderText() {
  if (!state.text) return;
  const q=$('#text-search').value.trim();
  const view=$('#text-view');
  if (state.textKind==='timestamp' && state.text.cues.length) {
    const rows=state.text.cues.filter(x=>!q || x.text.toLocaleLowerCase().includes(q.toLocaleLowerCase()));
    view.innerHTML=rows.length ? rows.map(c=>`<button class="cue" data-start="${c.start}" title="${duration(c.start) || '00:00'}로 이동"><time>${duration(c.start) || '00:00'}</time><p>${highlight(c.text,q)}</p></button>`).join('') : '<p class="list-empty">일치하는 자막이 없습니다.</p>';
  } else {
    view.innerHTML='<pre>'+highlight(state.text.text,q)+'</pre>';
  }
  state.cueNodes=$$('.cue');state.activeCue=null;
}
function followTime() {
  const p=state.mediaKind==='video'?$('#video-player'):$('#audio-player'),cues=state.cueNodes;
  if(!cues.length)return;
  let lo=0,hi=cues.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(+cues[mid].dataset.start<=p.currentTime)lo=mid+1;else hi=mid;}
  const current=lo?cues[lo-1]:null;
  if(current===state.activeCue)return;
  state.activeCue?.classList.remove('active');current?.classList.add('active');state.activeCue=current;
  if(current&&$('#follow-time').checked){
    const panel=$('#text-view'),target=current.getBoundingClientRect(),rect=panel.getBoundingClientRect();
    if(target.top<rect.top+20||target.bottom>rect.bottom-20)panel.scrollTop+=target.top-rect.top-70;
  }
}
async function pasteFromClipboard(target, append=false) {
  try {
    const text=await navigator.clipboard.readText();
    if (!text.trim()) {toast('클립보드에 텍스트가 없습니다.');return false;}
    target.value=(append && target.value.trim()?target.value.trim()+'\n':'')+text.trim();
    target.dispatchEvent(new Event('input')); target.focus();return true;
  } catch {
    target.focus();const mobile=matchMedia('(pointer:coarse)').matches;toast(mobile?'클립보드 버튼 권한이 없습니다. 입력칸을 길게 눌러 붙여넣기를 선택해 주세요.':'클립보드 접근이 허용되지 않았습니다. 이 입력칸에 Ctrl+V로 붙여넣어 주세요.');return false;
  }
}
function openAdd(text='') {
  $('#urls').value=text;showDialog('#add-dialog');protect(validateURLs)();$('#urls').focus();
}
async function validateURLs() {
  const text=$('#urls').value.trim();
  if (!text) {$('#url-validation').textContent='여러 링크를 붙여넣을 수 있습니다.';return;}
  const r=await api('/api/sources/validate','POST',{text});
  if ($('#urls').value.trim()!==text) return;
  const playlist=r.sources.filter(x=>x.kind==='playlist').length;
  $('#url-validation').textContent=`입력 ${r.sources.length}개${playlist?` · 재생목록 ${playlist}개`:''}${r.duplicates.length?` · 중복 ${r.duplicates.length}개 제외`:''}${r.errors.length?` · 잘못된 입력 ${r.errors.length}개`:''}`;
}
function getSettings() {
  return {mode:$('#mode').value, quality:$('#quality').value, audio_format:$('#audio-format').value,
    subtitle_policy:$('#subtitle-policy').value==='custom'?$('#custom-language').value.trim():$('#subtitle-policy').value,
    playlist_limit:Number($('#playlist-limit').value), tags:tagNames($('#batch-tags').value), thumbnail:$('#thumbnail').checked, mobile_preview:$('#auto-mobile').checked};
}
function applySettings(s) {
  for (const [id,key] of [['mode','mode'],['quality','quality'],['audio-format','audio_format'],['playlist-limit','playlist_limit']]) if(s[key]!==undefined) $('#'+id).value=s[key];
  if(s.subtitle_policy) {
    if(['auto','ko','en','ja','first'].includes(s.subtitle_policy)) $('#subtitle-policy').value=s.subtitle_policy;
    else {$('#subtitle-policy').value='custom';$('#custom-language').value=s.subtitle_policy;}
  }
  $('#auto-mobile').checked=s.mobile_preview!==false;$('#thumbnail').checked=s.thumbnail!==false;$('#batch-tags').value=(s.tags||[]).join(', ');updateMode();
}
function updateMode() {
  const mode=$('#mode').value;
  $('#quality').disabled=['subtitles','audio','audio_subtitles'].includes(mode);
  $('#audio-format').disabled=['subtitles','video','video_subtitles'].includes(mode);
  $('#auto-mobile').disabled=['subtitles','audio','audio_subtitles'].includes(mode);
  $('#subtitle-policy').disabled=['audio','video'].includes(mode);
  $('#custom-language-wrap').hidden=$('#subtitle-policy').value!=='custom' || $('#subtitle-policy').disabled;
}
function renderTagManager() {
  $('#tag-manager').innerHTML=state.tagList.length?state.tagList.map(t=>`<div class="tag-manage-row" data-id="${t.id}"><input value="${esc(t.name)}" maxlength="40" aria-label="태그 이름: ${esc(t.name)}"><span class="count">${t.count}개</span><button class="text-btn" data-save-tag="${t.id}">저장</button><button class="text-btn" data-delete-tag="${t.id}">삭제</button></div>`).join(''):'<p class="list-empty">아직 만든 태그가 없습니다.</p>';
}
function bindPanelControls() {
  const panels=[['quick','quickbar-body','빠른 URL 입력'],['collection','collection-body','자료 목록'],['media','media-body','재생 영역'],['text','text-body','자막 영역']];
  for(const [name,bodyId,label] of panels){
    const button=$('#toggle-'+name),body=$('#'+bodyId),key='yme-panel-'+name;
    const set=collapsed=>{
      body.hidden=collapsed;button.setAttribute('aria-expanded',String(!collapsed));
      button.setAttribute('aria-label',`${label} ${collapsed?'펼치기':'접기'}`);
      button.textContent=collapsed?'⌄':'⌃';
      if(name==='collection')$('.app').classList.toggle('collection-collapsed',collapsed);
    };
    try{set(localStorage.getItem(key)==='collapsed');}catch{set(false);}
    button.onclick=()=>{const collapsed=!body.hidden;set(collapsed);try{localStorage.setItem(key,collapsed?'collapsed':'expanded');}catch{}};
  }
  const files=$('.files-details');
  try{files.open=localStorage.getItem('yme-panel-files')!=='collapsed';}catch{}
  files.addEventListener('toggle',()=>{try{localStorage.setItem('yme-panel-files',files.open?'expanded':'collapsed');}catch{}});
  const wide=$('#toggle-wide-player');
  const setWide=value=>{
    $('#detail').classList.toggle('wide-player',value);
    wide.setAttribute('aria-pressed',String(value));
    wide.textContent=value?'기본 화면':'큰 화면';
    const listButton=$('#toggle-collection');
    if(value && listButton.getAttribute('aria-expanded')==='true'){
      listButton.click();wide.dataset.restoreList='true';
    }else if(!value && wide.dataset.restoreList==='true'){
      if(listButton.getAttribute('aria-expanded')==='false')listButton.click();
      delete wide.dataset.restoreList;
    }
  };
  try{setWide(localStorage.getItem('yme-large-player')==='true');}catch{setWide(false);}
  wide.onclick=()=>{const value=wide.getAttribute('aria-pressed')!=='true';setWide(value);try{localStorage.setItem('yme-large-player',String(value));}catch{}};
}
function bindSidebarToggle() {
  const app=$('.app'),button=$('#toggle-sidebar'),key='yme-sidebar-collapsed';
  const set=collapsed=>{
    app.classList.toggle('sidebar-collapsed',collapsed);
    button.setAttribute('aria-expanded',String(!collapsed));
    button.setAttribute('aria-label',`왼쪽 사이드바 ${collapsed?'펼치기':'접기'}`);
    button.title=`왼쪽 사이드바 ${collapsed?'펼치기':'접기'}`;
  };
  try{set(localStorage.getItem(key)==='true');}catch{set(false);}
  button.onclick=()=>{
    const collapsed=!app.classList.contains('sidebar-collapsed');
    set(collapsed);
    try{localStorage.setItem(key,String(collapsed));}catch{}
  };
}
function bindCollectionResize() {
  const area=$('.body-area'), collection=$('#collection-anchor'), handle=$('#collection-resizer');
  const key='yme-collection-width';
  let preferredWidth=null;
  try {const saved=Number(localStorage.getItem(key));if(Number.isFinite(saved)&&saved>=220)preferredWidth=saved;}catch{}
  const bounds=()=>{
    const available=area.getBoundingClientRect().width;
    const detailMin=Math.min(440,Math.max(300,Math.round(available*.35)));
    return {min:220,max:Math.max(220,Math.floor(available-detailMin-handle.offsetWidth))};
  };
  const applyWidth=()=>{
    const {min,max}=bounds();
    if(preferredWidth!==null)area.style.setProperty('--collection-width',`${Math.round(Math.min(max,Math.max(min,preferredWidth)))}px`);
    else area.style.removeProperty('--collection-width');
    const width=Math.round(collection.getBoundingClientRect().width);
    handle.setAttribute('aria-valuemin',String(min));
    handle.setAttribute('aria-valuemax',String(max));
    handle.setAttribute('aria-valuenow',String(width));
    handle.setAttribute('aria-valuetext',`${width}픽셀`);
  };
  const setWidth=(value,persist=true)=>{
    const {min,max}=bounds();
    preferredWidth=Math.round(Math.min(max,Math.max(min,value)));
    applyWidth();
    if(persist)try{localStorage.setItem(key,String(preferredWidth));}catch{}
  };
  handle.addEventListener('pointerdown',e=>{
    if(e.button!==0||matchMedia('(max-width:650px)').matches)return;
    e.preventDefault();handle.focus({preventScroll:true});handle.setPointerCapture(e.pointerId);
    $('.app').classList.add('resizing-collection');
  });
  handle.addEventListener('pointermove',e=>{
    if(!handle.hasPointerCapture(e.pointerId))return;
    setWidth(e.clientX-area.getBoundingClientRect().left,false);
  });
  const stopDrag=e=>{
    if(handle.hasPointerCapture(e.pointerId)){
      if(e.type==='pointerup')setWidth(e.clientX-area.getBoundingClientRect().left);
      handle.releasePointerCapture(e.pointerId);
    }
    $('.app').classList.remove('resizing-collection');
  };
  handle.addEventListener('pointerup',stopDrag);
  handle.addEventListener('pointercancel',stopDrag);
  handle.addEventListener('keydown',e=>{
    const {min,max}=bounds();
    const width=collection.getBoundingClientRect().width;
    const step=e.shiftKey?80:24;
    const next={ArrowLeft:width-step,ArrowRight:width+step,Home:min,End:max}[e.key];
    if(next===undefined)return;
    e.preventDefault();setWidth(next);
  });
  handle.addEventListener('dblclick',()=>{
    preferredWidth=null;applyWidth();
    try{localStorage.removeItem(key);}catch{}
  });
  $('#toggle-collection').addEventListener('click',applyWidth);
  if(window.ResizeObserver)new ResizeObserver(applyWidth).observe(area);
  else window.addEventListener('resize',applyWidth);
  applyWidth();
}
function renderJobs() {
  const active=state.jobs.filter(j=>!finished.has(j.status));
  $('#queue-count').textContent=String(active.length);
  const current=active.find(j=>j.status==='running')||active[0];
  $('#queue-status').textContent=current?.stage||(active.length?`${active.length}개 작업 대기 중`:'작업 대기열');
  $('#queue-progress').style.width=current?`${Math.max(0,Math.min(100,current.progress))}%`:'0%';
  if(!$('#queue-dialog').open)return;
  const opened=new Set($$('#queue-jobs details[open]').map(x=>x.dataset.jobLog));
  $('#queue-jobs').innerHTML=state.jobs.length?state.jobs.map(j=>{
    const label={extract:'URL 수집',import:'기존 폴더 가져오기',rescan:'등록 폴더 다시 읽기',preview:'이전 호환본 생성',mobile_preview:'모바일 재생본 생성',mobile_batch:'모바일 재생본 일괄 생성'}[j.kind]||j.kind;
    const detail=state.jobDetails.get(j.id);
    const log=detail?detail.logs+'\n\n'+JSON.stringify(detail.result,null,2):'아래 버튼을 눌러 상세 로그를 불러오세요.';
    return `<article class="job"><div class="job-heading"><strong>${label}</strong><span class="status ${j.status}">${statusNames[j.status]||j.status}</span></div><p>${esc(j.stage||'대기 중')}</p>${j.input_hint?`<p>${esc(j.input_hint)}</p>`:''}<div class="job-actions"><span class="micro">${esc(j.created_at.slice(0,19).replace('T',' '))}</span><span class="micro">${Math.floor(j.progress)}%</span>${finished.has(j.status)?`<button data-retry="${j.id}" class="text-btn">다시 실행</button>`:`<button data-cancel="${j.id}" class="text-btn">취소</button>`}</div><details data-job-log="${j.id}" ${opened.has(j.id)?'open':''}><summary>상세 로그와 결과</summary><button data-job-detail="${j.id}" class="text-btn">상세 로그 불러오기 / 갱신</button><pre>${esc(log)}</pre></details></article>`;
  }).join(''):'<p class="list-empty">아직 실행한 작업이 없습니다.</p>';
}
function connected() {
  $('#server-status').textContent=state.connection?.connection_mode==='tailscale_fast'?'개인 연결 · :8443':state.connection?.remote?'원격 서버 연결됨':'PC 서버 실행 중';
  $('.dot').classList.remove('offline');
}
function scheduleLibraryRefresh() {
  clearTimeout(libraryTimer);
  libraryTimer=setTimeout(protect(async()=>{
    await Promise.all([refreshTags(),refreshLibrary(),refreshSelected()]);
  }),350);
}
function acceptJobs(packet) {
  state.jobs=packet.jobs||[];renderJobs();connected();
  if(lastLibraryRevision!==null&&packet.library_revision!==lastLibraryRevision)scheduleLibraryRefresh();
  lastLibraryRevision=packet.library_revision;
}
async function syncJobsOnce() {acceptJobs(await api('/api/jobs/summary'));}
function stopEvents() {
  eventStream?.close();eventStream=null;clearTimeout(fallbackTimer);clearTimeout(reconnectTimer);
}
async function pollJobs() {
  if(document.hidden)return;
  try{await syncJobsOnce();}
  catch(e){$('#server-status').textContent='재연결 중';$('.dot').classList.add('offline');}
  if(!eventStream&&!document.hidden)fallbackTimer=setTimeout(pollJobs,state.jobs.some(j=>!finished.has(j.status))?3000:15000);
}
function startEvents() {
  stopEvents();if(document.hidden)return;
  if(!window.EventSource){pollJobs();return;}
  eventStream=new EventSource('/api/events');
  eventStream.onopen=()=>{sseErrors=0;connected();};
  eventStream.addEventListener('jobs',event=>{try{acceptJobs(JSON.parse(event.data));}catch{}});
  eventStream.addEventListener('auth_expired',()=>{stopEvents();location.replace('/login');});
  eventStream.onerror=()=>{
    $('#server-status').textContent='실시간 연결 재연결 중';
    if(++sseErrors>=2){
      stopEvents();pollJobs();reconnectTimer=setTimeout(startEvents,60000);
    }
  };
}
async function copyText(text) {
  try {await navigator.clipboard.writeText(text);toast('복사했습니다.');}
  catch {
    const area=document.createElement('textarea');area.value=text;document.body.append(area);area.select();
    const ok=document.execCommand('copy');area.remove();toast(ok?'복사했습니다.':'복사 권한이 없습니다. 텍스트를 선택해 Ctrl+C로 복사해 주세요.',!ok);
  }
}

function bind() {
  bindPanelControls();
  bindSidebarToggle();
  bindCollectionResize();
  $$('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
  $$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
  const setFilter=protect(async filter=>{
    state.filter=filter;
    $$('[data-filter]').forEach(x=>x.classList.toggle('active',x.dataset.filter===filter));
    $$('[data-mobile-filter]').forEach(x=>x.classList.toggle('active',x.dataset.mobileFilter===filter));
    $('#page-title').textContent={all:'전체 자료',favorite:'즐겨찾기',video:'영상',audio:'음원',transcript:'자막',untagged:'태그 미지정'}[state.filter];await refreshLibrary();
  });
  $('#main-nav').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(b)setFilter(b.dataset.filter);});
  $('#mobile-filters')?.addEventListener('click',e=>{const b=e.target.closest('[data-mobile-filter]');if(b)setFilter(b.dataset.mobileFilter);});
  for (const root of [$('#tag-filters'),$('#active-tags')]) root.addEventListener('click',protect(async e=>{
    const b=e.target.closest('[data-tag]');if(!b)return;const id=+b.dataset.tag;
    state.tags.has(id)?state.tags.delete(id):state.tags.add(id);await refreshTags();await refreshLibrary();
  }));
  $('#reset-filters').onclick=protect(async()=>{state.tags.clear();$('#search').value='';state.filter='all';$('#page-title').textContent='전체 자료';$$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));$$('[data-mobile-filter]').forEach(b=>b.classList.toggle('active',b.dataset.mobileFilter==='all'));await refreshTags();await refreshLibrary();});
  $('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(protect(refreshLibrary),400);};
  $('#sort').onchange=protect(refreshLibrary);
  $('#load-more').onclick=protect(()=>refreshLibrary({append:true}));
  $('#items').addEventListener('click',protect(async e=>{const row=e.target.closest('[data-item]');if(row){
    await selectItem(row.dataset.item);
    if(matchMedia('(max-width:650px)').matches)$('#detail').scrollIntoView({behavior:'smooth',block:'start'});
    else if(matchMedia('(max-width:900px)').matches&&$('#toggle-collection').getAttribute('aria-expanded')==='true')$('#toggle-collection').click();
  }}));
  $('#items').addEventListener('error',e=>{if(e.target.tagName==='IMG')e.target.hidden=true;},true);
  $('#add-button').onclick=()=>openAdd($('#quick-url').value);
  $('#quick-add').onclick=()=>openAdd($('#quick-url').value);
  $('#quick-paste').onclick=protect(async()=>{if(await pasteFromClipboard($('#quick-url')))openAdd($('#quick-url').value);});
  $('#dialog-paste').onclick=protect(async()=>{await pasteFromClipboard($('#urls'),true);});
  $('#quick-url').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();openAdd(e.currentTarget.value);}};
  $('#urls').oninput=()=>{clearTimeout(validateTimer);validateTimer=setTimeout(protect(validateURLs),400);};
  $('#urls').onkeydown=e=>{
    if(e.key!=='Enter'||e.shiftKey||e.ctrlKey||e.altKey||e.metaKey||e.isComposing||e.keyCode===229)return;
    e.preventDefault();
    if(!e.repeat)$('#add-form').requestSubmit();
  };
  $('#mode').onchange=updateMode;$('#subtitle-policy').onchange=updateMode;
  $('#add-form').onsubmit=e=>{
    e.preventDefault();
    if($('#submit-download').disabled)return;
    $('#submit-download').disabled=true;
    protect(async()=>{
      try {
        const text=$('#urls').value.trim();if(!text)return;
        const settings=getSettings();
        await api('/api/jobs/extract','POST',{text,settings});localStorage.setItem('yme-v8-alpha-settings',JSON.stringify(settings));$('#add-dialog').close();$('#quick-url').value='';toast('수집 작업을 대기열에 추가했습니다.');await syncJobsOnce();
      } finally {$('#submit-download').disabled=false;}
    })();
  };
  const openImport=()=>showDialog('#import-dialog');$('#import-button').onclick=openImport;$('#empty-import').onclick=openImport;
  $('#choose-folder').onclick=protect(async()=>{
    $('#choose-folder').disabled=true;
    try {toast('Windows 폴더 선택 창에서 기존 output 폴더를 골라 주세요.');const r=await api('/api/choose-folder','POST',{});if(r.path)$('#import-path').value=r.path;}
    finally {$('#choose-folder').disabled=false;}
  });
  $('#import-form').onsubmit=protect(async e=>{e.preventDefault();await api('/api/jobs/import','POST',{path:$('#import-path').value,tags:tagNames($('#import-tags').value)});$('#import-dialog').close();toast('기존 폴더를 읽고 있습니다. 원본은 변경하지 않습니다.');showDialog('#queue-dialog');await syncJobsOnce();});
  $('#rescan').onclick=protect(async()=>{await api('/api/jobs/rescan','POST',{});toast('등록된 폴더를 다시 읽습니다.');});
  $('#favorite').onclick=protect(async()=>{if(!state.active)return;await api(`/api/items/${state.selected}/favorite`,'PUT',{value:!state.active.favorite});state.active.favorite=!state.active.favorite;$('#favorite').classList.toggle('selected',state.active.favorite);await refreshLibrary();});
  $('#tag-input').onkeydown=protect(async e=>{if(e.key==='Enter'){e.preventDefault();await addTagFromInput();}});
  $('#add-item-tag').onclick=protect(addTagFromInput);
  $('#item-tags').onclick=protect(async e=>{const b=e.target.closest('[data-remove-tag]');if(b)await saveItemTags(state.active.tags.filter(t=>t.id!==+b.dataset.removeTag).map(t=>t.name));});
  $('#manage-tags').onclick=()=>{showDialog('#tags-dialog');renderTagManager();};
  $('#tag-create-form').onsubmit=protect(async e=>{e.preventDefault();await api('/api/tags','POST',{name:$('#new-tag').value});$('#new-tag').value='';await refreshTags();toast('태그를 저장했습니다.');});
  $('#tag-manager').onclick=protect(async e=>{
    const save=e.target.closest('[data-save-tag]'), del=e.target.closest('[data-delete-tag]');
    if(save){await api('/api/tags/'+save.dataset.saveTag,'PUT',{name:save.closest('.tag-manage-row').querySelector('input').value});await refreshTags();await refreshLibrary();if(state.selected)await refreshSelected();toast('태그 이름을 저장했습니다.');}
    if(del&&confirm('이 태그를 목록과 자료에서 제거할까요? 원본 파일은 삭제되지 않습니다.')){await api('/api/tags/'+del.dataset.deleteTag,'DELETE');await refreshTags();await refreshLibrary();if(state.selected)await refreshSelected();toast('태그를 삭제했습니다.');}
  });
  $('#open-folder').onclick=protect(()=>api(`/api/items/${state.selected}/open-folder`,'POST',{}));
  $('#native-open').onclick=protect(()=>state.mediaFile&&api(`/api/files/${state.mediaFile.id}/open`,'POST',{}));
  $('#text-native').onclick=protect(()=>state.textFile&&api(`/api/files/${state.textFile.id}/open`,'POST',{}));
  $('#make-preview').onclick=protect(()=>queueMobile('mobile720'));
  $('#start-playback').onclick=protect(()=>startPlayback());
  $('#playback-quality').onchange=protect(async()=>{const p=state.mediaKind==='video'?$('#video-player'):$('#audio-player');const t=p.currentTime,playing=!p.paused;renderMedia();if(playing&&state.mediaFile)await startPlayback(t);});
  $('#media-tabs').onclick=e=>{const b=e.target.closest('[data-media]');if(b&&!b.disabled){state.mediaKind=b.dataset.media;renderMedia();}};
  $('#text-tabs').onclick=protect(async e=>{const b=e.target.closest('[data-text]');if(b&&!b.disabled){state.textKind=b.dataset.text;await loadText();}});
  $('#text-view').onclick=protect(async e=>{if(e.target.closest('[data-load-text]'))return loadText();const b=e.target.closest('[data-start]');if(b)return startPlayback(+b.dataset.start);});
  $('#text-search').oninput=renderText;
  $('#copy-text').onclick=protect(()=>copyText(state.text?.text||''));
  for(const p of [$('#video-player'),$('#audio-player')]){
    p.addEventListener('timeupdate',followTime);
    p.addEventListener('error',()=>{if(p.hasAttribute('src')){$('#playback-warning').hidden=false;$('#start-playback').hidden=false;}});
    for(const name of ['waiting','stalled'])p.addEventListener(name,()=>{if(p.hasAttribute('src'))$('#buffer-status').hidden=false;});
    p.addEventListener('playing',()=>{$('#buffer-status').hidden=true;$('#start-playback').hidden=true;});
    p.addEventListener('pause',()=>{$('#buffer-status').hidden=true;});
  }
  $('#queue-button').onclick=()=>{showDialog('#queue-dialog');renderJobs();};
  $('#queue-jobs').onclick=protect(async e=>{
    const detail=e.target.closest('[data-job-detail]');
    if(detail){const j=await api('/api/jobs/'+detail.dataset.jobDetail);state.jobDetails.set(j.id,j);renderJobs();return;}
    const cancel=e.target.closest('[data-cancel]'), retry=e.target.closest('[data-retry]');
    if(cancel)await api(`/api/jobs/${cancel.dataset.cancel}/cancel`,'POST',{});
    if(retry){await api(`/api/jobs/${retry.dataset.retry}/retry`,'POST',{});toast('같은 설정으로 작업을 다시 추가했습니다.');}
    if(cancel||retry){await syncJobsOnce();}
  });
  $('#connect-mobile')?.addEventListener('click',()=>$('#system-button').click());
  $('#mobile-back-list')?.addEventListener('click',()=>$('#collection-anchor')?.scrollIntoView({behavior:'smooth'}));
  $('#mobile-tags')?.addEventListener('click',()=>showDialog('#mobile-tags-dialog'));
  $('#mobile-manage-tags')?.addEventListener('click',()=>{$('#mobile-tags-dialog').close();showDialog('#tags-dialog');renderTagManager();});
  $('#mobile-tag-filters')?.addEventListener('click',protect(async e=>{const b=e.target.closest('[data-mobile-tag]');if(!b)return;const id=+b.dataset.mobileTag;state.tags.has(id)?state.tags.delete(id):state.tags.add(id);await refreshTags();await refreshLibrary();}));
  $('#mobile-tag-reset')?.addEventListener('click',protect(async()=>{state.tags.clear();await refreshTags();await refreshLibrary();}));
  $('#mobile-system')?.addEventListener('click',()=>$('#system-button').click());
  $('#mobile-nav')?.addEventListener('click',e=>{const b=e.target.closest('[data-mobile-go]');if(!b)return;const go=b.dataset.mobileGo;if(go==='library')$('.collection').scrollIntoView({behavior:'smooth'});if(go==='viewer')$('#detail').scrollIntoView({behavior:'smooth'});if(go==='add')openAdd($('#quick-url').value);if(go==='queue'){showDialog('#queue-dialog');renderJobs();}});
  $('#system-button').onclick=protect(async()=>{
    const b=await api('/api/bootstrap');
    const mode={local:'서버 PC · 로컬',tailscale:'원격 기본 주소 · 443',tailscale_fast:'Tailscale 개인 연결 · 8443',cloudflare:'Cloudflare · 관리 전용'}[b.connection_mode];
    const links=[['기존 원격 주소 · 443',b.private_url],['개인 연결 · Tailscale 필요',b.fast_url],['Cloudflare · 관리용',b.cloudflare_url]];
    const rows=[['버전','V'+b.version],['계정',b.username],['현재 연결',mode],['가동 시간',Math.floor(b.uptime_seconds/60)+'분'],['남은 공간',b.free_gb+' GB'],['새 자료 저장',b.output],['태그 / DB',b.data_dir],['JS 런타임',b.runtimes.join(', ')||'감지되지 않음']];
    $('#system-content').innerHTML=rows.map(([k,v])=>`<div class="system-line"><span>${esc(k)}</span><code>${esc(v)}</code></div>`).join('')+
      '<div class="system-block"><h3>외부 접속 주소</h3>'+links.map(([name,url])=>`<div class="system-line"><span>${esc(name)}</span>${url?`<a class="remote-url" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`:'<span>미설정</span>'}</div>`).join('')+
      '<p>기존 공개 Funnel 주소는 유지됩니다. :8443 주소는 휴대폰 Tailscale 연결이 필요하며, 직접 연결 여부에 따라 속도가 달라집니다. 서버 PC는 켜져 있어야 합니다.</p></div>'+
      '<div class="system-block"><h3>모바일 성능 설정</h3><p>목록은 20개씩, 영상은 재생 버튼을 누를 때만 불러옵니다. 현재 목록의 원본 영상을 모바일 720p로 일괄 준비할 수 있습니다. CPU와 추가 공간을 사용하며 원본은 보존합니다.</p><button id="batch-mobile" class="button">현재 목록의 모바일본 준비 (최대 20개)</button></div>'+ 
      '<div class="system-block"><h3>로그인과 다운로드</h3><p>일반 로그인은 최대 '+b.session_hours+'시간이며, 자동 로그인 선택 시 '+b.remember_days+'일 동안 유지됩니다. 브라우저를 닫아도 PC 서버의 수집 작업은 계속됩니다. 비밀번호 변경은 서버 PC의 01_setup_beta.bat에서 합니다.</p></div>'+
      '<div class="system-block"><h3>다운로드 엔진</h3>'+Object.entries(b.versions).map(([k,v])=>`<div class="system-line"><span>${esc(k)}</span><code>${esc(v||'미설치')}</code></div>`).join('')+'<p>서버 종료 후 update_engines.bat으로 업데이트합니다.</p></div>';
    $('#batch-mobile').onclick=protect(async()=>{const item_ids=state.items.filter(x=>fileOf(x,'video')).slice(0,20).map(x=>x.id);if(!item_ids.length)return toast('현재 목록에 원본 영상이 없습니다.');if(confirm(`${item_ids.length}개 영상의 모바일 720p 재생본을 만들까요? 원본은 유지하고 시간과 저장공간이 추가로 필요합니다.`)){await api('/api/jobs/mobile-batch','POST',{item_ids,profile:'mobile720'});toast('모바일 재생본을 순차 준비합니다.');}});
    showDialog('#system-dialog');
  });
  $('#logout').onclick=protect(async()=>{await api('/api/auth/logout','POST',{});location.replace('/login');});
  $('#logout-all').onclick=protect(async()=>{if(confirm('PC와 휴대폰의 모든 로그인 세션을 종료할까요? 수집 작업은 계속됩니다.')){await api('/api/auth/logout-all','POST',{});location.replace('/login');}});
  document.addEventListener('keydown',e=>{
    const editing=['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName);
    if(e.key==='/'&&!editing&&!$('dialog[open]')){e.preventDefault();$('#search').focus();}
    if(e.key==='Enter'&&e.ctrlKey&&$('#add-dialog').open){e.preventDefault();$('#add-form').requestSubmit();}
  });
  document.addEventListener('paste',e=>{
    if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName) || $('dialog[open]'))return;
    const text=e.clipboardData?.getData('text');
    if(text && /youtu(?:be\.com|\.be)/.test(text)){e.preventDefault();openAdd(text);}
  });
}

async function boot() {
  paintIcons();bind();
  try {
    const bootstrap=await api('/api/bootstrap');state.token=bootstrap.token;state.connection=bootstrap;
    if (!bootstrap.desktop_actions) for(const id of ['choose-folder','open-folder','native-open','text-native']) {const n=$('#'+id);if(n)n.hidden=true;}
    let saved={};try{saved=JSON.parse(localStorage.getItem('yme-v8-alpha-settings')||'{}');}catch{}
    applySettings({...bootstrap.defaults,...saved});
    await Promise.all([refreshTags(),refreshLibrary()]);
    const remote=bootstrap.remote;
    $('#server-status').textContent=remote?'개인 서버 연결됨':'PC 서버 실행 중';
    $('#mobile-mode-label').textContent=bootstrap.connection_mode==='cloudflare'?'관리용 연결 · 영상은 개인 주소에서':'개인 서버에 연결됨';
    $('#mobile-connection').hidden=!remote&&!matchMedia('(max-width:650px)').matches;
    if(remote){
      for(const id of ['import-button','empty-import','open-folder','native-open','text-native']){const n=$('#'+id);if(n)n.hidden=true;}
      $('#page-title').title='파일은 서버 PC에 저장됩니다.';
    }
    if(!bootstrap.versions['yt-dlp'])toast('라이브러리는 사용 가능합니다. 새 수집은 PC에서 01_setup_beta.bat으로 다운로드 엔진을 설치한 뒤 실행하세요.');
    startEvents();
    const incoming=new URLSearchParams(location.search).get('add');
    if(incoming){
      history.replaceState(null,'',location.pathname);
      try{
        const url=new URL(incoming);
        const hosts=['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtu.be'];
        if(incoming.length<=2048&&url.protocol==='https:'&&hosts.includes(url.hostname))openAdd(incoming);
      }catch{}
    }
  } catch(e) {toast(e.message,true);$('#server-status').textContent='연결 실패';$('.dot').classList.add('offline');}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopEvents();else if(state.token){startEvents();scheduleLibraryRefresh();}});
window.addEventListener('pagehide',stopEvents);
window.addEventListener('pageshow',e=>{if(e.persisted&&state.token)startEvents();});
boot();
