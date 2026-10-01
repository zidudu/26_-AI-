/* V10 runtime bridge. The final UI's layout/filters remain in base.js. */
'use strict';
let serverState=null, pollBusy=false, booted=false, remoteSequence=-1;
let formOverride=null, draftBeforeRun=null, activeLogId=null, lastLogId=0;
let transportAvailable=true, pendingRunRequest=null;
let codexRequestBusy=false;
let naverLoginPhase='closed',naverLoginBusy=false,naverLoginError='',naverLoginFocus=null;
const originalReadStored=readStored, originalUpdateTimes=updateTimes;
const originalOpenConfirm=openExecutionConfirm;
const originalRenderHistoryDetail=renderHistoryDetail;

async function api(path,body,timeout=30000){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const r=await fetch(path,{method:body===undefined?'GET':'POST',cache:'no-store',credentials:'same-origin',
      headers:body===undefined?{}:{'Content-Type':'application/json','X-V10-Token':window.V10_TOKEN},
      body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
    const value=await r.json();
    if(!r.ok)throw new Error(value.message||'서버 요청 실패');
    return value;
  }catch(e){if(e.name==='AbortError')throw new Error('응답을 기다리는 시간이 초과되었습니다. 실행 이력에서 처리 여부를 확인하세요.');throw e;}
  finally{clearTimeout(timer);}
}

function failure(e){showMessage('처리할 수 없습니다',e.message||String(e));}
function updateCodexAuth(){
  const a=serverState?.codexAuth||{},busy=!!a.busy||codexRequestBusy,node=$('codexAuthState');
  if(!node)return;
  node.textContent=a.message||'Codex 로그인 상태 확인 전';node.dataset.state=a.state||'unchecked';
  $('codexGuideBtn').disabled=busy||!!activeRun||!transportAvailable;
  $('codexCheckBtn').disabled=busy||!transportAvailable;
  $('codexGuideBtn').textContent=a.state==='connected'?'다시 로그인':a.state==='login_pending'||a.state==='starting'?'로그인 진행 중':'Codex 로그인';
}
async function codexRequest(action){
  if(codexRequestBusy)return;
  codexRequestBusy=true;updateCodexAuth();
  try{
    const result=await api('/api/codex/'+action,{});
    serverState.codexAuth=result;
    if(action==='login')toast(result.message);
  }catch(e){failure(e);}
  finally{codexRequestBusy=false;updateCodexAuth();}
}
function renderNaverLoginGuide(){
  if(naverLoginPhase==='closed')return;
  const question=naverLoginPhase==='relogin',a=serverState?.naverAuth||{};
  $('naverLoginQuestion').hidden=!question;$('naverLoginSteps').hidden=question;
  $('naverLoginDone').textContent=question?'다시 로그인':naverLoginPhase==='start_error'?'다시 시도':'로그인했습니다';
  for(const id of ['naverLoginDone','naverLoginCancel','naverLoginClose'])$(id).disabled=naverLoginBusy;
  const saved=['session_present','verified'].includes(a.state);
  $('naverLoginHelp').textContent=naverLoginError||(
    naverLoginBusy?'로그인 요청을 처리하고 있습니다…':question?'현재 V10에 저장된 로그인 상태입니다. 계속 사용하려면 취소를 누르세요.':
    saved?(a.browserOpen?'로그인 세션 저장됨 · 전용 Chrome을 닫고 ‘로그인했습니다’를 눌러주세요.':'로그인 세션 저장됨 · ‘로그인했습니다’를 눌러 완료해 주세요.'):
    a.browserOpen?'전용 Chrome에서 로그인해 주세요. 완료 후 이 안내창으로 돌아오세요.':a.message||'로그인 세션 저장을 확인하지 못했습니다. 다시 로그인해 주세요.');
  $('naverLoginHelp').dataset.error=naverLoginError?'true':'false';
}
function closeNaverLoginGuide(){
  naverLoginPhase='closed';$('naverLoginBackdrop').classList.remove('show');syncModalInert();
  if(naverLoginFocus?.isConnected)naverLoginFocus.focus();naverLoginFocus=null;
}
async function openNaverLogin(){
  if(naverLoginPhase!=='closed')return;
  hideTooltip();naverLoginFocus=document.activeElement;
  naverLoginPhase='opening';naverLoginError='';$('naverLoginBackdrop').classList.add('show');syncModalInert();
  await startNaverLogin(false);
  if(naverLoginPhase!=='closed')$('naverLoginDone').focus();
}
async function startNaverLogin(confirm){
  if(naverLoginBusy)return;
  naverLoginBusy=true;naverLoginError='';renderNaverLoginGuide();
  try{
    const result=await api('/api/login',{confirm});
    serverState.naverAuth=result.naverAuth;
    naverLoginPhase=result.needsConfirmation?'relogin':'instructions';updateNaverLoginStatus();
  }catch(e){naverLoginPhase='start_error';naverLoginError=e.message;}
  finally{naverLoginBusy=false;renderNaverLoginGuide();}
}
async function finishNaverLogin(){
  if(naverLoginBusy)return;
  if(naverLoginPhase==='relogin'||naverLoginPhase==='start_error')return startNaverLogin(naverLoginPhase==='relogin');
  naverLoginBusy=true;naverLoginError='';renderNaverLoginGuide();
  try{
    const result=await api('/api/login/confirm',{});
    serverState.naverAuth=result.naverAuth;updateNaverLoginStatus();closeNaverLoginGuide();toast(result.message);
  }catch(e){naverLoginError=e.message;}
  finally{naverLoginBusy=false;renderNaverLoginGuide();}
}
async function cancelNaverLogin(){
  if(naverLoginBusy)return;
  if(['relogin','closed'].includes(naverLoginPhase)){closeNaverLoginGuide();return;}
  naverLoginBusy=true;naverLoginError='';renderNaverLoginGuide();
  try{const result=await api('/api/login/cancel',{});closeNaverLoginGuide();toast(result.message);}
  catch(e){naverLoginError=e.message;}
  finally{naverLoginBusy=false;renderNaverLoginGuide();}
}
function mailStatusLabel(state){return ({disabled:'발송 안 함',not_started:'대기',sending:'전송 요청 중',submitted:'Outlook 전송 요청 수락 · 배달 확인은 Outlook에서',sent:'기존 기록: 발송됨',unknown:'발송 확인 필요 · 자동 재발송 안 함',confirmed_submitted:'사용자가 전송 여부 확인',confirmed_not_sent:'사용자가 미발송 확인'})[state]||state||'미기록';}

readStored=function(key,fallback){
  if(key===SETTINGS_KEY)return formOverride||serverState?.settings||fallback;
  if(key===HISTORY_KEY)return serverState?.runs||[];
  return originalReadStored(key,fallback);
};
saveHistory=function(){return true;}; // All execution writes are server-owned.
normalizeHistory=function(){
  historyRecords=copyObject(serverState?.runs||[]);skippedHistoryRecords=0;
  const first=historyRecords[0],d=parts(first?dateValue(first.startedAt):new Date());
  historyState.year=d.y;historyState.month=d.m-1;
  historyState.selectedId=first?.id||null;
};

saveSettings=async function(){
  if(running)return false;
  try{
    const cfg=settingsSnapshot(),issues=validateSettings(cfg,false);
    if(issues.length)throw new Error(issues.join('\n'));
    await saveSecret();
    const result=await api('/api/settings',{settings:cfg,expectedRevision:serverState.settingsRevision});
    serverState.settings=result.settings;serverState.settingsRevision=result.revision;serverState.settingsSaved=true;
    savedConfigCanonical=canonicalConfig(result.settings);savedConfigPresent=true;dirty=false;updateSaveBar();
    await refresh(true);toast('설정이 DB에 저장되었습니다. 예약 실행은 이 설정을 사용합니다.');return true;
  }catch(e){failure(e);return false;}
};

async function saveSecret(){
  const input=$('apiKey');if(input?.value){await api('/api/secrets',{apiKey:input.value});input.value='';input.placeholder='현재 서버 메모리에 설정됨';}
}

openExecutionConfirm=function(kind){
  originalOpenConfirm(kind);
  if(!pendingExecutionAction)return;
  const cfg=settingsSnapshot();
  $('actionConfirmNote').textContent=kind==='ppt'?`대상: ${pendingExecutionAction.runId}\n저장된 분석으로 PPT를 만듭니다. 재수집·추가 AI·메일 발송은 실행하지 않습니다.`:
    `선택한 수집·AI·PPT 작업을 실제로 실행합니다.${cfg.sendMail?'\n메일: '+cfg.mailTo+' / '+(cfg.mailScope==='summary10'?'요약 슬라이드':'전체 보고서'):''}\n현재 화면의 설정을 이번 실행 기록에 보존합니다.`;
};

runAll=async function(overrides=null){
  if(running||!transportAvailable)return;
  const cfg=copyObject(overrides||settingsSnapshot()),errors=validateSettings(cfg,true);
  if(errors.length){showMessage('실행 설정 확인',errors.join('\n'));return;}
  $('runBtn').disabled=true;
  try{
    await saveSecret();
    // Retain the same id after a response timeout so retry cannot double-start.
    const signature=JSON.stringify(cfg);
    if(!pendingRunRequest||pendingRunRequest.signature!==signature)pendingRunRequest={signature,id:crypto.randomUUID()};
    const run=await api('/api/runs',{settings:cfg,requestId:pendingRunRequest.id});
    pendingRunRequest=null;historyState.selectedId=run.id;
    await refresh(true);toast('실제 실행을 시작했습니다. 화면을 새로고침해도 작업은 계속됩니다.');
  }catch(e){failure(e);await refresh(true).catch(()=>{});}
  finally{$('runBtn').disabled=!transportAvailable;}
};

requestStop=function(){
  if(!activeRun)return;
  $('stopConfirmBackdrop').classList.add('show');$('stopConfirmBackdrop').setAttribute('aria-hidden','false');syncModalInert();$('stopCancelBtn').focus();
};
confirmStop=async function(){
  const rid=activeRun?.id;closeStopConfirm();if(!rid)return;
  try{const r=await api('/api/runs/'+encodeURIComponent(rid)+'/stop',{});toast(r.message);await refresh(true);}catch(e){failure(e);}
};

approvePost=async function(){
  if(running)return;const p=posts.find(x=>articleIdentity(x)===selectedPostId);
  if(!p?.analysisId){showMessage('검토할 수 없습니다','이 게시글의 저장된 분석이 없습니다.');return;}
  try{await api('/api/reviews',{analysisId:p.analysisId,state:'approved'});await refresh(true);toast('해당 원문 버전의 분석을 검토 완료로 저장했습니다.');}catch(e){failure(e);}
};

computePeriod=function(cfg,end=new Date()){
  if(!cfg.stepCollect){const s=historyRecords.find(r=>r.id===cfg.sourceRun);return {start:dateValue(s?.periodStart),end:dateValue(s?.periodEnd),note:'선택한 원본 실행 기간',mode:cfg.range};}
  if(cfg.range==='기간 직접 지정')return {start:dateValue(cfg.periodStart),end:dateValue(cfg.periodEnd),note:'한국시간 · 시작 포함/종료 제외',mode:cfg.range};
  end=new Date(Math.floor(+end/60000)*60000);
  if(cfg.range==='이전 실행 이후'){
    const values=cfg.selectedCafeIds.map(id=>dateValue(serverState?.cursors?.[id]));
    if(!values.length||values.some(v=>!v))return {start:null,end,note:'첫 실행 카페는 최근 24/48시간으로 먼저 수집하세요.',mode:cfg.range};
    return {start:new Date(Math.floor(Math.min(...values.map(Number))/60000)*60000-Number(cfg.overlap)*DAY),end,note:'카페별 수집 완료 시각부터 분 단위로 계산 · 표시 시작은 가장 이른 카페 기준',mode:cfg.range};
  }
  return {start:new Date(+end-(cfg.range==='최근 48시간'?48:24)*3600000),end,note:'완료된 분까지 수집 · 실행 요청 시 서버에서 확정',mode:cfg.range};
};

updateTimes=function(){
  originalUpdateTimes();
  const next=serverState?.nextRun;
  if(next){$('timeNextValue').innerHTML=shortTime(next);$('timeNextMeta').textContent=serverState?.schedulerMessage||'DB에 저장된 예약 · 서버 실행 중에 동작';$('overviewNextRun').textContent=formatTime(next);}
  else{$('timeNextValue').textContent=serverState?.settingsSaved?'예약 꺼짐':'설정 저장 전';$('timeNextMeta').textContent=serverState?.schedulerMessage||'설정을 저장하면 예약을 사용합니다.';$('overviewNextRun').textContent='예약 없음';}
  if(!transportAvailable){$('runBtn').disabled=true;$('timeNextMeta').textContent='서버 연결이 끊겼습니다. 실행 창 상태를 확인하세요.';}
};

runCompletionText=function(r){return `${r.stage||r.status} · ${r.posts}${r.settings?.stepPpt?' · '+r.slides:''}${r.reviewPending?' · 검토 필요 '+r.reviewPending+'건':''} · ${mailStatusLabel(r.mailStatus)}`;};
isInterruptedStatus=function(status){return /사용자 중지|오류|실패|중단|일부|확인 필요/.test(String(status||''));};
updateNaverLoginStatus=function(){
  const a=serverState?.naverAuth||{},node=$('naverLoginState'),text=node?.querySelector('.login-state-text');if(!text)return;
  const verified=a.state==='verified'&&!!a.checkedAt,ok=verified||a.state==='session_present';
  node.classList.toggle('ok',ok);node.classList.toggle('bad',!ok);
  text.textContent=verified?'카페 접근 확인 · '+clockTime(a.checkedAt):a.state==='session_present'?'로그인 세션 저장됨 · 수집 시 접근 확인':'네이버 로그인 확인 필요';
  node.title=a.message||'전용 Chrome에서 로그인한 뒤 브라우저를 닫으세요. 로그인 정보는 브라우저 프로필에 보관됩니다.';
};

statsQuickRange=function(mode){
  const to=dayKey(new Date()),end=+new Date(to+'T00:00:00Z');let from;
  if(mode==='7d'||mode==='30d')from=new Date(end-(Number(mode.slice(0,-1))-1)*DAY).toISOString().slice(0,10);
  else if(mode==='90d'||mode==='365d')from=new Date(+new Date(shiftCalendarMonths(to,mode==='90d'?-3:-12)+'T00:00:00Z')+DAY).toISOString().slice(0,10);
  else return {from:statsDates[0]||to,to:statsDates.at(-1)||to};
  return {from,to};
};

function artifactFor(r,kind='ppt'){const list=(r?.artifacts||[]).filter(a=>a.kind===(kind==='ppt'&&r.pptLinked?'linked_ppt':kind));return list.find(a=>a.path===r.ppt)||list.at(-1);}
function previewsFor(r){return (r?.artifacts||[]).filter(a=>a.kind.startsWith('preview:')).sort((a,b)=>Number(a.kind.split(':')[1])-Number(b.kind.split(':')[1]));}
function artifactURL(a){return '/api/artifacts/'+encodeURIComponent(a.id);}
pptStageDone=function(r){return !!artifactFor(r);};
pptAvailable=function(r){return !!artifactFor(r);};

renderExecutionPptPreview=function(){
  const r=activeRun||lastDisplayedRun||latestRun(),images=previewsFor(r),image=images[0],render=$('executionPreviewRender'),empty=$('executionPreviewEmpty');
  $('executionPreviewOpenBtn').disabled=!pptAvailable(r);$('executionPreviewHistoryBtn').disabled=!r;
  $('executionPreviewFile').textContent=r?pathBasename(r.ppt):'PPT 파일 없음';$('executionPreviewPath').textContent=r?.ppt||'생성된 파일 없음';
  $('executionPreviewMeta').textContent=image?'실제 PPT 첫 요약 슬라이드':'PPT 미리보기 없음';
  $('executionPreviewNote').textContent=image?'PowerPoint에서 내보낸 실제 슬라이드입니다.':'PPT 생성 후 실제 슬라이드를 표시합니다. 이관한 PPT는 PPT 열기로 확인하세요.';
  const key=image?.id||'';
  empty.hidden=!!image;render.hidden=!image;
  if(render.dataset.artifact!==key){render.dataset.artifact=key;render.innerHTML=image?`<img class="v10-real-preview" src="${artifactURL(image)}" alt="실제 PPT 첫 요약 슬라이드">`:'';}
};

renderPptPreview=function(r){
  const images=previewsFor(r),selected=Math.max(1,Math.min(images.length||1,Number(pptPreviewSelections.get(r.id)||1))),image=images[selected-1];
  return `<section class="history-section history-ppt-section"><div class="history-ppt-head"><h4>PPT 요약 슬라이드 미리보기</h4><span class="preview-note">${image?'실제 PowerPoint 슬라이드':'저장된 미리보기 이미지 없음'}</span></div>
  ${image?`<div class="ppt-slide-stage" tabindex="0" data-ppt-preview-root data-run-id="${esc(r.id)}"><button class="ppt-nav ppt-nav-prev" data-preview-slide="${selected-1}" data-run-id="${esc(r.id)}" ${selected===1?'disabled':''} aria-label="이전 슬라이드">‹</button><img class="v10-real-preview" src="${artifactURL(image)}" alt="요약 슬라이드 ${selected}"><button class="ppt-nav ppt-nav-next" data-preview-slide="${selected+1}" data-run-id="${esc(r.id)}" ${selected===images.length?'disabled':''} aria-label="다음 슬라이드">›</button></div><p>${selected} / ${images.length}장</p><div class="ppt-thumbnails">${images.map((a,i)=>`<button class="btn small" data-preview-slide="${i+1}" data-run-id="${esc(r.id)}">${i+1}</button>`).join('')}</div>`:'<p class="history-note">PPT 파일을 열어 결과를 확인하세요. V10에서 새로 생성하면 실제 슬라이드 이미지를 함께 저장합니다.</p>'}
  <div class="history-ppt-actions"><button class="btn small" data-history-action="open-ppt" ${pptAvailable(r)?'':'disabled'}>PPT 열기</button><button class="btn small" data-history-action="open-folder" ${r.artifacts?.length?'':'disabled'}>폴더에서 보기</button><button class="btn small" data-history-action="download-ppt" ${pptAvailable(r)?'':'disabled'}>PPT 다운로드</button><button class="btn small" data-history-action="attach">실제 PPT 연결</button></div></section>`;
};
renderLinkedFile=function(id){const r=historyRecords.find(r=>r.id===id),root=$('attachedFileInfo');if(root)root.textContent=r?.pptLinked?'사용자가 연결한 PPT · 해당 실행의 생성 결과인지 별도 확인 필요':'실행 결과 파일이 DB에 연결되어 있습니다.';};

historyAction=async function(action,key){
  if(action==='reset'){resetHistoryFilters();return;}
  const r=historyRecords.find(r=>r.id===historyState.selectedId);if(!r)return;
  if(action==='copy'){copyText(r[key]||'미기록');return;}
  if(action==='export'){exportHistory(r);return;}
  try{
    if(action==='attach'){await api('/api/artifacts/link',{runId:r.id},300000);await refresh(true);return;}
    const a=artifactFor(r)||r.artifacts?.[0];if(!a)throw new Error('등록된 결과 파일이 없습니다.');
    if(action==='download-ppt'){const link=document.createElement('a');link.href=artifactURL(a);link.click();return;}
    if(action==='open-ppt'||action==='open-folder')await api('/api/open-artifact',{id:a.id,folder:action==='open-folder'});
  }catch(e){failure(e);}
};
exportHistory=function(record=null){
  const payload={schema:'v10-execution-history/1',exportedAt:new Date().toISOString(),records:record?[record]:historyRecords};
  downloadBlob(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),record?record.id+'.json':'V10_실행이력.json');
};
renderHistoryDetail=function(id){
  originalRenderHistoryDetail(id);
  const r=historyRecords.find(x=>x.id===id);if(r?.state!=='mail_unknown')return;
  const root=$('historyDetail'),div=document.createElement('div');div.className='history-section';
  div.innerHTML='<strong>메일 발송 확인 필요</strong><p>Outlook 보낸 편지함에서 확인한 결과를 기록하세요. 이 버튼은 재발송하지 않습니다.</p><button class="btn small" data-mail-decision="confirmed_submitted">전송 확인</button> <button class="btn small" data-mail-decision="confirmed_not_sent">미발송 확인</button>';
  div.onclick=async e=>{const decision=e.target.dataset.mailDecision;if(!decision)return;try{await api('/api/mail/resolve',{runId:r.id,decision});await refresh(true);}catch(err){failure(err);}};root.prepend(div);
};

function applyForm(cfg){
  formOverride=copyObject(cfg);loadSettings();formOverride=null;
  renderCafes();renderKeywords();updateCounts();updateSettingsUI();
}

function renderRuntime(){
  const r=activeRun||lastDisplayedRun;
  $('overviewRunId').textContent=r?.id||'실행 전';$('overviewStarted').textContent=r?formatTime(r.startedAt,true):'—';
  $('overviewState').textContent=r?.status||'대기';$('overviewStage').textContent=r?.stage||'—';
  $('overviewElapsed').textContent=r?duration((r.endedAt?+dateValue(r.endedAt):Date.now())-+dateValue(r.startedAt)):'00:00:00';
  $('stateTitle').textContent=r?.status||'실행 대기';$('stateMeta').textContent=r?runCompletionText(r):'DB에 실제 실행 결과가 쌓이면 표시합니다.';
  $('detailStateSummary').textContent=r?`${r.status} · ${r.stage}`:'실행 전';
  $('approveBtn').disabled=running||!posts.find(p=>articleIdentity(p)===selectedPostId)?.analysisId;
  updateStage();updateTimes();
}

async function loadStats(){
  const all=[];let after=0;
  do{const result=await api('/api/posts?after='+after);all.push(...result.items);after=result.next;}while(after!==null);
  statsRecords.splice(0,statsRecords.length,...all);
  refreshCatalogFromSources();
  if(statsState.periodMode!=='custom')Object.assign(statsState,statsQuickRange(statsState.periodMode));
  if(!statsQueryDirty)statsDraft=statsDraftFromApplied();
}

function connection(ok){
  transportAvailable=ok;let banner=$('v10Disconnected');
  if(!ok&&!banner){banner=document.createElement('div');banner.id='v10Disconnected';banner.className='v10-disconnected';banner.textContent='서버 연결 끊김 · V10 실행 창을 확인하세요. 연결을 다시 시도합니다.';document.body.appendChild(banner);}
  if(ok&&banner)banner.remove();if(booted)$('runBtn').disabled=!ok;
}

async function refresh(force=false){
  if(pollBusy)return;pollBusy=true;
  try{
    const result=await api('/api/state'+(force?'':'?since='+remoteSequence));connection(true);
    if(result.unchanged&&result.codexAuth&&serverState)serverState.codexAuth=result.codexAuth;
    if(result.unchanged&&result.naverAuth&&serverState){serverState.naverAuth=result.naverAuth;if(booted)updateNaverLoginStatus();}
    if(!result.unchanged){
      const previousActive=activeRun?.id,previousRevision=serverState?.settingsRevision;
      serverState=result;remoteSequence=result.sequence;
      if(booted){
        historyRecords=copyObject(result.runs);
        activeRun=historyRecords.find(r=>r.id===result.activeRun)||null;
        if(activeRun&&!previousActive){draftBeforeRun={cfg:settingsSnapshot(),dirty};applyForm(activeRun.settings);}
        else if(!activeRun&&previousActive&&draftBeforeRun){const saved=draftBeforeRun;draftBeforeRun=null;applyForm(saved.cfg);dirty=saved.dirty;}
        else if(!activeRun&&!dirty&&previousRevision!==result.settingsRevision)applyForm(result.settings);
        lastDisplayedRun=activeRun||historyRecords.find(r=>r.id===lastDisplayedRun?.id)||historyRecords[0]||null;
        posts=copyObject(lastDisplayedRun?.articles||[]);
        if(!posts.some(p=>articleIdentity(p)===selectedPostId))selectedPostId=posts[0]?articleIdentity(posts[0]):null;
        sourceOptions();lockExecution(!!activeRun);renderPosts();renderRuntime();updateNaverLoginStatus();
        if($('outlookFrom'))$('outlookFrom').value=result.outlookSender||'Outlook 계정 확인 전';
        await loadStats();renderStats();
        if($('historyView').classList.contains('show')){const top=$('historyDetail').scrollTop;renderHistory();$('historyDetail').scrollTop=top;}
        if(previousActive&&!activeRun){$('log').textContent=lastDisplayedRun?.logText||'';toast(lastDisplayedRun?.status||'실행 종료');}
      }
    }
    if(booted&&activeRun){
      if(activeLogId!==activeRun.id){activeLogId=activeRun.id;lastLogId=0;$('log').textContent='';}
      const result=await api('/api/runs/'+encodeURIComponent(activeRun.id)+'/logs?after='+lastLogId);
      for(const row of result.items){$('log').textContent+='['+row.at.slice(11,19)+'] '+row.message+'\n';lastLogId=row.id;}
      if(result.items.length)$('log').scrollTop=$('log').scrollHeight;
    }
  }catch(e){connection(false);if(!booted)throw e;}
  finally{pollBusy=false;if(booted){updateCodexAuth();renderNaverLoginGuide();}}
}

async function databaseManager(){
  showMessage('DB 관리','');
  $('settingsSummary').className='v10-db-controls';
  const c=serverState.counts||{};
  $('settingsSummary').innerHTML=`<p>고유 게시글 ${c.posts||0}건 · 원문 버전 ${c.post_versions||0}개 · 실행 ${c.runs||0}회</p><label>기존 naver_cafe 폴더<input id="legacyImportPath" value="${esc(serverState.legacyRoot||'')}" placeholder="C:\\...\\naver_cafe"></label><div><button class="btn small" id="chooseLegacyPath">폴더 선택</button> <button class="btn primary" id="importLegacy">기존 V9 자료 이관</button> <button class="btn" id="backupDb">DB 백업</button></div><p>원본 파일을 유지하며 DB에 읽어옵니다. 같은 자료는 중복 이관하지 않습니다. 기존 예약 작업은 별도로 확인하세요.</p><button class="btn small" id="checkOutlook">Outlook 발신 계정 확인</button><pre id="dbResult"></pre>`;
  $('chooseLegacyPath').onclick=async()=>{try{const r=await api('/api/choose-directory',{},300000);if(r.path)$('legacyImportPath').value=r.path;}catch(e){$('dbResult').textContent=e.message;}};
  $('importLegacy').onclick=async()=>{
    $('importLegacy').disabled=true;$('dbResult').textContent='기존 원문·분석·PPT를 확인하고 있습니다…';
    try{const r=await api('/api/import',{path:$('legacyImportPath').value},300000);$('dbResult').textContent=r.results.map(x=>`${x.status}: ${x.runId||x.source}${x.message?' · '+x.message:''}${x.missing?.length?' · 누락 '+x.missing.length+'개':''}`).join('\n');await refresh(true);}
    catch(e){$('dbResult').textContent=e.message;}finally{if($('importLegacy'))$('importLegacy').disabled=false;}
  };
  $('backupDb').onclick=async()=>{try{const r=await api('/api/backup',{});$('dbResult').textContent='백업 저장: '+r.path;}catch(e){$('dbResult').textContent=e.message;}};
  $('checkOutlook').onclick=async()=>{try{const r=await api('/api/outlook-account',{});$('outlookFrom').value=r.sender;$('dbResult').textContent='발신 계정: '+r.sender;}catch(e){$('dbResult').textContent=e.message;}};
}

async function boot(){
  try{
    await refresh(true);await loadStats();
    const calendarDate=parts(dateValue(statsDates.at(-1))||new Date());
    statsCalendarState.year=calendarDate.y;statsCalendarState.month=calendarDate.m-1;
    init();booted=true;
    savedConfigPresent=serverState.settingsSaved;
    activeRun=historyRecords.find(r=>r.id===serverState.activeRun)||null;
    if(activeRun){draftBeforeRun={cfg:copyObject(serverState.settings),dirty:false};applyForm(activeRun.settings);}
    lastDisplayedRun=activeRun||historyRecords[0]||null;posts=copyObject(lastDisplayedRun?.articles||[]);
    selectedPostId=posts[0]?articleIdentity(posts[0]):null;
    $('log').textContent=lastDisplayedRun?.logText||'[READY] 실제 DB에 연결되었습니다.\n';
    lockExecution(!!activeRun);renderPosts();renderRuntime();updateNaverLoginStatus();
    $('naverLoginBtn').onclick=openNaverLogin;
    $('naverLoginDone').onclick=finishNaverLogin;
    $('naverLoginCancel').onclick=cancelNaverLogin;
    $('naverLoginClose').onclick=cancelNaverLogin;
    $('databaseBtn').onclick=databaseManager;
    $('pathBtn').textContent='선택';$('pathBtn').onclick=async()=>{try{const r=await api('/api/choose-directory',{},300000);if(r.path){$('outputPath').value=r.path;markChanged();}}catch(e){failure(e);}};
    $('codexGuideBtn').onclick=()=>codexRequest('login');
    $('codexCheckBtn').onclick=()=>codexRequest('check');
    updateCodexAuth();
    $('outlookFrom').value=serverState.outlookSender||'DB 관리에서 계정 확인';
    $('v10Connect').hidden=true;
    setInterval(()=>refresh(),2000);
    if($('provider').value==='codex')codexRequest('check');
  }catch(e){$('v10Connect').innerHTML='<strong>V10에 연결할 수 없습니다</strong><span>'+esc(e.message)+'</span><span>HTML 파일을 직접 열지 말고 02_start_v10.bat를 실행하세요.</span><button onclick="location.reload()">다시 연결</button>';}
}
boot();
