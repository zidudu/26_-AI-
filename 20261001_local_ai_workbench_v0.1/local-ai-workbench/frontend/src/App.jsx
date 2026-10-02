import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const MODEL_NAMES = {
  'gemma4:26b-a4b-it-qat': 'Gemma 4 · 26B A4B',
  'qwen3.6:27b-coding': 'Qwen 3.6 · 27B Coding',
  'qwen3:8b': 'Qwen 3 · 8B (빠른 초안)',
};
const DEFAULT_MODEL = 'gemma4:26b-a4b-it-qat';

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `요청 실패 (${response.status})`);
  return body;
}

function post(path, body) {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}

function localTime(value) {
  return new Date(value).toLocaleTimeString('en-GB', { timeZone: 'Asia/Seoul', hour12: false });
}

function Icon({ name, size = 18 }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    compare: <><rect x="3" y="4" width="7" height="7" rx="2" /><rect x="14" y="4" width="7" height="7" rx="2" /><rect x="3" y="15" width="7" height="7" rx="2" /><rect x="14" y="15" width="7" height="7" rx="2" /></>,
    folder: <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />,
    terminal: <><path d="m4 7 5 5-5 5M12 18h8" /></>,
    send: <><path d="m4 12 16-8-5 16-3-7-8-1Z" /><path d="m12 13 8-9" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="M5 5 19 19M19 5 5 19" />,
    chevron: <path d="m6 9 6 6 6-6" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Markdown({ children }) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children || ''}</ReactMarkdown></div>;
}

function Metrics({ result }) {
  if (!result || result.error) return null;
  const stats = [
    ['총 소요', `${result.total_seconds}초`],
    ['모델 적재', `${result.load_seconds}초`],
    ['첫 토큰', result.first_token_seconds == null ? '측정 안 됨' : `${result.first_token_seconds}초`],
    ['생성 속도', `${result.tokens_per_second} 토큰/초`],
    ['입력 / 출력', `${result.input_tokens} / ${result.output_tokens}`],
    ['GPU 적재', result.processor?.vram_percent == null ? '측정 안 됨' : `${result.processor.vram_percent}%`],
  ];
  return <div className="metric-row">{stats.map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>;
}

function AssistantMessage({ result, model }) {
  return <article className="assistant-message">
    <div className="assistant-avatar">{model.startsWith('gemma') ? 'G' : 'Q'}</div>
    <div className="assistant-body">
      <div className="message-heading"><strong>{MODEL_NAMES[model] || model}</strong><span>로컬 모델</span></div>
      {result?.error ? <p className="inline-error">{result.error}</p> : <Markdown>{result?.content || '생성된 답변이 없습니다.'}</Markdown>}
      <Metrics result={result} />
    </div>
  </article>;
}

function SourceList({ sources }) {
  if (!sources?.length) return null;
  return <details className="source-details" open>
    <summary>참고한 검색 구간 {sources.length}개 <Icon name="chevron" size={16} /></summary>
    <div className="source-list">{sources.map(source => <article className="source-item" key={source.id}>
      <span className="source-id">{source.id}</span>
      <div><strong>{source.source}</strong><small>{source.locator || '문서 본문'}</small><p>{source.excerpt}</p></div>
    </article>)}</div>
  </details>;
}

function LogPanel({ logs, open, onClose, onClear, autoScroll, setAutoScroll, connected }) {
  const endRef = useRef(null);
  useEffect(() => { if (open && autoScroll) endRef.current?.scrollIntoView({ block: 'end' }); }, [logs, open, autoScroll]);
  if (!open) return null;
  const labels = { chat: '대화', compare: '비교', index: '색인', ask: '질문', system: '시스템' };
  return <aside className="log-panel" aria-label="실행 로그">
    <div className="log-head"><div className="log-title"><Icon name="terminal" size={18} /><strong>실행 로그</strong></div><div className="log-head-actions"><span className={connected ? 'live-dot' : 'live-dot offline'} /><small>{connected ? '수신 중' : '연결 확인'}</small><button className="icon-button log-close" onClick={onClose} aria-label="로그 닫기"><Icon name="close" size={17} /></button></div></div>
    <div className="log-lines" role="log" aria-live="polite" aria-relevant="additions text">
      {logs.length === 0 ? <div className="log-empty">대기 중입니다. 비교·색인·질문을 실행하면 단계별 기록이 여기에 표시됩니다.</div> : logs.map(item => <div className={`log-line ${item.level}`} key={item.id}><time>[{localTime(item.at)}]</time><span className="log-kind">{labels[item.kind] || item.kind}</span><span className="log-message">{item.message}</span></div>)}
      <div ref={endRef} />
    </div>
    <div className="log-footer"><label><input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} /> 자동 스크롤</label><button onClick={onClear}>화면 지우기</button></div>
  </aside>;
}

export default function ChatApp() {
  const [mode, setMode] = useState('chat');
  const [health, setHealth] = useState(null);
  const [logOpen, setLogOpen] = useState(() => window.matchMedia('(min-width: 971px)').matches);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [logs, setLogs] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const cursor = useRef(0);
  const polling = useRef(false);

  const [history, setHistory] = useState([]);
  const [comparePrompt, setComparePrompt] = useState('');
  const [maxTokens, setMaxTokens] = useState(256);
  const [comparison, setComparison] = useState(null);
  const [activePrompt, setActivePrompt] = useState('');
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareError, setCompareError] = useState('');
  const [compareJob, setCompareJob] = useState(null);

  const [chatModel, setChatModel] = useState(DEFAULT_MODEL);
  const [chatPrompt, setChatPrompt] = useState('');
  const [chatTurns, setChatTurns] = useState([]);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState('');

  const [folder, setFolder] = useState('');
  const folderRevision = useRef(0);
  const indexSubmission = useRef(false);
  const [indexSubmitting, setIndexSubmitting] = useState(false);
  const [index, setIndex] = useState(null);
  const [job, setJob] = useState(null);
  const [folderError, setFolderError] = useState('');
  const [answerModel, setAnswerModel] = useState(DEFAULT_MODEL);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState([]);
  const [askBusy, setAskBusy] = useState(false);
  const conversationEnd = useRef(null);

  useEffect(() => {
    let active = true;
    async function refreshHealth() {
      try {
        const next = await api('/api/health');
        if (active) setHealth(next);
      } catch {
        if (active) setHealth({ ollama_connected: false, models: [], compare_models: Object.keys(MODEL_NAMES), chat_models: Object.keys(MODEL_NAMES), embedding_model: 'qwen3-embedding:0.6b', error: '서버 상태를 읽지 못했습니다.' });
      }
    }
    refreshHealth();
    const timer = setInterval(refreshHealth, 10000);
    api('/api/compare/history').then(setHistory).catch(() => {});
    return () => { active = false; clearInterval(timer); };
  }, []);

  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 970px)');
    const handleResize = event => { if (event.matches) setLogOpen(false); };
    narrow.addEventListener('change', handleResize);
    return () => narrow.removeEventListener('change', handleResize);
  }, []);

  useEffect(() => {
    let alive = true;
    async function poll() {
      if (polling.current || !alive) return;
      polling.current = true;
      try {
        const data = await api(`/api/activity?since=${cursor.current}&limit=150`);
        if (!alive) return;
        if (data.last_id < cursor.current) { cursor.current = 0; setLogs([]); return; }
        if (data.events.length) {
          cursor.current = data.events.at(-1).id;
          setLogs(previous => [...previous, ...data.events].slice(-300));
        }
      } catch { /* The connection chip and normal actions surface server errors. */ }
      finally { polling.current = false; }
    }
    poll();
    const timer = setInterval(poll, 800);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  useEffect(() => { if (mode === 'compare' || turns.length || chatTurns.length) conversationEnd.current?.scrollIntoView({ block: 'end' }); }, [comparison, compareJob, turns, chatTurns, compareBusy, askBusy, chatBusy, mode]);

  useEffect(() => {
    if (!compareJob || ['complete', 'error'].includes(compareJob.state)) return;
    const timer = setInterval(async () => {
      try {
        const next = await api(`/api/compare/job/${compareJob.id}`);
        setCompareJob(next);
        if (next.state === 'complete') {
          setComparison(previous => previous ?? next.item);
          setHistory(previous => [next.item, ...previous.filter(item => item.id !== next.item.id)].slice(0, 10));
          setCompareBusy(false);
        }
        if (next.state === 'error') { setCompareError(next.error); setCompareBusy(false); }
      } catch (exc) { setCompareError(exc.message); setCompareBusy(false); setCompareJob(null); }
    }, 800);
    return () => clearInterval(timer);
  }, [compareJob?.id, compareJob?.state]);

  useEffect(() => {
    if (!job || ['complete', 'error'].includes(job.state)) return;
    const revision = folderRevision.current;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const next = await api(`/api/index/job/${job.id}`);
        if (!active || revision !== folderRevision.current) return;
        setJob(next);
        if (next.state === 'complete') setIndex(next.summary);
        if (next.state === 'error') setFolderError(next.error);
      } catch (exc) { if (active && revision === folderRevision.current) setFolderError(exc.message); }
    }, 900);
    return () => { active = false; clearInterval(timer); };
  }, [job?.id, job?.state]);

  const allReady = health?.ollama_connected && [...health.compare_models, health.embedding_model].every(item => health.models.includes(item));
  const compareReady = health?.ollama_connected && health.compare_models.every(item => health.models.includes(item));
  const askReady = health?.ollama_connected && health.models.includes(answerModel) && health.models.includes(health.embedding_model);
  const chatReady = health?.ollama_connected && health.models.includes(chatModel);
  const selectableModels = (health?.chat_models || Object.keys(MODEL_NAMES)).filter(model => health?.models?.includes(model));

  function newChat() {
    if (mode === 'compare') { setComparison(null); setCompareJob(null); setComparePrompt(''); setActivePrompt(''); setCompareError(''); }
    else if (mode === 'chat') { setChatTurns([]); setChatPrompt(''); setChatError(''); }
    else { setTurns([]); setQuestion(''); setFolderError(''); }
    setSidebarOpen(false);
  }

  function changeMode(next) { setMode(next); setSidebarOpen(false); }

  async function runCompare(event) {
    event.preventDefault();
    const prompt = comparePrompt.trim();
    if (!prompt || compareBusy || !compareReady) return;
    setActivePrompt(prompt); setComparison(null); setCompareJob(null); setCompareError(''); setCompareBusy(true); setComparePrompt('');
    try {
      setCompareJob(await post('/api/compare/start', { prompt, max_tokens: Number(maxTokens) }));
    } catch (exc) { setCompareError(exc.message); setCompareBusy(false); }
  }

  async function askChat(event) {
    event.preventDefault();
    const text = chatPrompt.trim();
    if (!text || chatBusy || !chatReady) return;
    const id = `${Date.now()}-${Math.random()}`;
    const previous = chatTurns.filter(turn => turn.result && !turn.result.error).slice(-5);
    const messages = previous.flatMap(turn => [
      { role: 'user', content: turn.question },
      { role: 'assistant', content: turn.result.content },
    ]);
    messages.push({ role: 'user', content: text });
    setChatTurns(turns => [...turns, { id, question: text, model: chatModel, pending: true }]);
    setChatPrompt(''); setChatBusy(true); setChatError('');
    try {
      const result = await post('/api/chat', { model: chatModel, messages, max_tokens: Number(maxTokens) });
      setChatTurns(turns => turns.map(turn => turn.id === id ? { id, question: text, model: chatModel, result } : turn));
    } catch (exc) {
      setChatTurns(turns => turns.map(turn => turn.id === id ? { id, question: text, model: chatModel, error: exc.message } : turn));
    } finally { setChatBusy(false); }
  }

  async function inspectIndex() {
    const revision = folderRevision.current;
    const requestedFolder = folder;
    setFolderError('');
    try {
      const status = await api(`/api/index/status?folder=${encodeURIComponent(requestedFolder)}`);
      if (revision === folderRevision.current) setIndex(status.index);
    } catch (exc) {
      if (revision === folderRevision.current) { setIndex(null); setFolderError(exc.message); }
    }
  }

  async function createIndex() {
    if (indexSubmission.current) return;
    indexSubmission.current = true;
    setIndexSubmitting(true);
    const revision = folderRevision.current;
    const requestedFolder = folder;
    setFolderError(''); setIndex(null);
    try {
      const next = await post('/api/index', { folder: requestedFolder });
      if (revision === folderRevision.current) setJob(next);
    } catch (exc) { if (revision === folderRevision.current) setFolderError(exc.message); }
    finally {
      if (revision === folderRevision.current) {
        indexSubmission.current = false;
        setIndexSubmitting(false);
      }
    }
  }

  async function askFolder(event) {
    event.preventDefault();
    const text = question.trim();
    if (!text || !index || askBusy || !askReady) return;
    const id = `${Date.now()}-${Math.random()}`;
    setTurns(previous => [...previous, { id, question: text, pending: true }]);
    setQuestion(''); setAskBusy(true); setFolderError('');
    try {
      const result = await post('/api/ask', { folder, question: text, model: answerModel });
      setTurns(previous => previous.map(turn => turn.id === id ? { id, question: text, result } : turn));
    } catch (exc) {
      setTurns(previous => previous.map(turn => turn.id === id ? { id, question: text, error: exc.message } : turn));
    } finally { setAskBusy(false); }
  }

  const currentPrompt = comparison?.prompt || activePrompt;
  const currentResults = comparison?.results || compareJob?.results || [];
  const sendDisabled = mode === 'compare' ? compareBusy || !compareReady || !comparePrompt.trim() : mode === 'chat' ? chatBusy || !chatReady || !chatPrompt.trim() : askBusy || !index || !askReady || !question.trim();

  return <div className={`workbench ${logOpen ? 'with-log' : ''}`}>
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="sidebar-brand"><span className="brand-mark">◈</span><div><strong>로컬 AI 작업대</strong><small>내 PC에서 실행하는 AI</small></div></div>
      <button className="new-chat" onClick={newChat} disabled={compareBusy || chatBusy || askBusy}><Icon name="plus" /> 새 대화</button>
      <nav className="mode-nav" aria-label="작업 선택">
        <button className={mode === 'chat' ? 'active' : ''} onClick={() => changeMode('chat')}><Icon name="plus" /><span><strong>일반 대화</strong><small>한 모델로 빠르게 질문</small></span></button>
        <button className={mode === 'compare' ? 'active' : ''} onClick={() => changeMode('compare')}><Icon name="compare" /><span><strong>모델 비교</strong><small>같은 질문, 두 모델</small></span></button>
        <button className={mode === 'folder' ? 'active' : ''} onClick={() => changeMode('folder')}><Icon name="folder" /><span><strong>폴더 질문</strong><small>내 문서에 질문하기</small></span></button>
      </nav>
      <div className="sidebar-history"><h2>최근 모델 비교</h2>{compareBusy && <button className={!comparison && mode === 'compare' ? 'selected' : ''} onClick={() => { setMode('compare'); setComparison(null); setActivePrompt(compareJob?.prompt || activePrompt); setSidebarOpen(false); }}><span>진행 중인 비교</span><small>{compareJob?.results.length || 0}/2 모델 완료</small></button>}{comparison && compareError && <p className="inline-error">{compareError}</p>}{history.length ? history.map(item => <button key={item.id} className={comparison?.id === item.id ? 'selected' : ''} onClick={() => { setMode('compare'); setComparison(item); setActivePrompt(item.prompt); setComparePrompt(''); setSidebarOpen(false); }} title={item.prompt}><span>{item.prompt}</span><small>{new Date(item.created_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}</small></button>) : <p>아직 비교 기록이 없습니다.</p>}</div>
      <div className="sidebar-bottom"><span className={allReady ? 'status-light' : 'status-light warn'} /><div><strong>{!health ? '로컬 AI 확인 중' : allReady ? '로컬 AI 연결됨' : '모델 상태 확인 필요'}</strong><small>{!health ? 'Ollama 상태를 읽고 있습니다.' : allReady ? 'Ollama가 이 PC에서 응답 중' : (health.error || '필요한 모델을 확인해 주세요.')}</small></div></div>
    </aside>
    {sidebarOpen && <button className="sidebar-scrim" onClick={() => setSidebarOpen(false)} aria-label="탐색 닫기" />}

    <main className="main-panel">
      <header className="main-header"><button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="탐색 열기"><Icon name="menu" /></button><div className="main-heading"><strong>{mode === 'chat' ? '일반 대화' : mode === 'compare' ? '모델 비교' : '폴더 질문'}</strong><small>{mode === 'chat' ? '한 모델과 대화하고 잠시 메모리에 유지합니다.' : mode === 'compare' ? '두 모델의 답변과 실행 시간을 비교합니다.' : '지정한 폴더의 문서를 바탕으로 답변합니다.'}</small></div><button className="header-log-toggle" onClick={() => setLogOpen(value => !value)}><Icon name="terminal" size={16} /> {logOpen ? '로그 숨기기' : '로그 보기'}</button></header>
      {!allReady && health && <div className="connection-warning">{health.error || `필요한 모델: ${[...health.compare_models, health.embedding_model].filter(item => !health.models.includes(item)).join(', ')}`}</div>}
      <div className="conversation-scroll">
        <div className="conversation-inner">
          {mode === 'chat' && <section className="folder-config" aria-label="대화 모델 설정"><div className="config-top"><strong>대화 모델</strong><span>마지막 요청 후 {chatModel === 'qwen3.6:27b-coding' ? '1분' : '2분'}간 메모리에 유지합니다.</span></div><div className="config-bottom"><span>Gemma는 이 PC에서 생성 속도가 더 빠르게 측정되었습니다.</span><label>모델 <select value={chatModel} onChange={e => setChatModel(e.target.value)} disabled={!selectableModels.length}>{!selectableModels.includes(chatModel) && <option value={chatModel} disabled>{health ? `${MODEL_NAMES[chatModel] || chatModel} (사용 불가)` : '모델 확인 중...'}</option>}{selectableModels.map(model => <option key={model} value={model}>{MODEL_NAMES[model] || model}</option>)}</select></label></div></section>}
          {mode === 'folder' && <section className="folder-config" aria-label="문서 폴더 설정"><div className="config-top"><Icon name="folder" size={17} /><strong>대상 폴더</strong><span>원본 파일은 읽기만 합니다</span></div><div className="folder-controls"><input aria-label="Windows 폴더 경로" value={folder} onChange={e => { folderRevision.current += 1; indexSubmission.current = false; setIndexSubmitting(false); setFolder(e.target.value); setIndex(null); setJob(null); setTurns([]); }} placeholder="예: D:\문서\프로젝트" /><button onClick={inspectIndex} disabled={!folder.trim()}>색인 확인</button><button className="solid" onClick={createIndex} disabled={!folder.trim() || indexSubmitting || (job && !['complete', 'error'].includes(job.state))}>색인 생성</button></div><div className="config-bottom"><span className={index ? 'index-ready' : ''}>{indexSubmitting ? '색인 요청 중...' : job && !['complete', 'error'].includes(job.state) ? `${job.phase}${job.total ? ` · ${job.done}/${job.total}` : ''}` : index ? `색인 준비됨 · ${index.indexed_files}개 파일 · ${index.chunks}개 구간` : '색인을 생성하거나 기존 색인을 확인해 주세요.'}</span><label>답변 모델 <select value={answerModel} onChange={e => setAnswerModel(e.target.value)}>{health?.compare_models.map(model => <option key={model} value={model}>{MODEL_NAMES[model] || model}</option>)}</select></label></div>{index?.skipped?.length > 0 && <details className="skipped-list"><summary>제외된 파일/제한 {index.skipped.length}건</summary><ul>{index.skipped.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}{folderError && <p className="inline-error">{folderError}</p>}</section>}

          {mode === 'chat' && chatTurns.length === 0 && <div className="welcome"><span className="welcome-symbol">◈</span><h1>무엇을 물어볼까요?</h1><p>기본값은 Gemma 4입니다. 코딩은 Qwen 3.6을 선택하고, 짧은 초안은 Qwen 3 8B를 선택해 보세요.</p></div>}
          {mode === 'chat' && chatTurns.map(turn => <div className="thread qa-thread" key={turn.id}><div className="user-message">{turn.question}</div>{turn.pending ? <div className="pending-message"><span className="spinner" /> {MODEL_NAMES[turn.model] || turn.model} 답변을 기다리고 있습니다. 오른쪽 실행 로그에서 적재 상태를 볼 수 있습니다.</div> : turn.error ? <p className="inline-error">{turn.error}</p> : <AssistantMessage model={turn.model} result={turn.result} />}</div>)}
          {mode === 'chat' && chatError && <p className="inline-error">{chatError}</p>}

          {mode === 'compare' && !currentPrompt && <div className="welcome"><span className="welcome-symbol">◈</span><h1>어떤 답변을 비교해 볼까요?</h1><p>같은 질문을 Gemma와 Qwen에 순서대로 보내고 결과와 속도를 확인합니다.</p></div>}
          {mode === 'compare' && currentPrompt && <div className="thread"><div className="user-message">{currentPrompt}</div>{currentResults.map(result => <AssistantMessage key={result.model} model={result.model} result={result} />)}{compareBusy && !comparison && <div className="pending-message"><span className="spinner" /> {currentResults.length ? '첫 모델 결과가 준비되었습니다. 두 번째 모델을 실행 중입니다.' : '첫 모델을 실행 중입니다.'} 오른쪽 실행 로그에서 진행 상황을 볼 수 있습니다.</div>}{compareError && !comparison && <p className="inline-error">{compareError}</p>}</div>}

          {mode === 'folder' && turns.length === 0 && <div className="welcome folder-welcome"><span className="welcome-symbol"><Icon name="folder" size={27} /></span><h1>내 문서에서 무엇을 찾을까요?</h1><p>폴더를 색인한 뒤 질문해 주세요. 답변과 함께 검색된 원문을 보여드립니다.</p></div>}
          {mode === 'folder' && turns.map(turn => <div className="thread qa-thread" key={turn.id}><div className="user-message">{turn.question}</div>{turn.pending ? <div className="pending-message"><span className="spinner" /> 문서를 검색하고 답변을 만들고 있습니다.</div> : turn.error ? <p className="inline-error">{turn.error}</p> : <><AssistantMessage model={turn.result.answer.model} result={turn.result.answer} /><SourceList sources={turn.result.sources} /><p className="source-note">인용 번호와 원문 발췌를 직접 대조해 주세요.</p></>}</div>)}
          <div ref={conversationEnd} />
        </div>
      </div>
      <form className="composer-wrap" onSubmit={mode === 'compare' ? runCompare : mode === 'chat' ? askChat : askFolder}><div className="composer"><textarea aria-label={mode === 'compare' ? '비교할 질문' : mode === 'chat' ? '일반 대화 질문' : '문서에 할 질문'} value={mode === 'compare' ? comparePrompt : mode === 'chat' ? chatPrompt : question} onChange={e => mode === 'compare' ? setComparePrompt(e.target.value) : mode === 'chat' ? setChatPrompt(e.target.value) : setQuestion(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} placeholder={mode === 'compare' ? '두 모델에 같은 질문을 해보세요' : mode === 'chat' ? '한 모델에 질문해 보세요' : '폴더 문서에 대해 질문해 보세요'} maxLength={mode === 'folder' ? 4000 : 12000} required /><div className="composer-bottom"><div className="composer-options">{mode === 'folder' ? <span className="model-pill">{index ? `${index.indexed_files}개 문서 색인됨` : '먼저 폴더를 색인해 주세요'}</span> : <><span className="model-pill">{mode === 'chat' ? MODEL_NAMES[chatModel] || chatModel : 'Gemma 4 + Qwen 3.6'}</span><label className="token-select">출력 <select value={maxTokens} onChange={e => setMaxTokens(Number(e.target.value))}><option value={128}>128 토큰</option><option value={256}>256 토큰</option><option value={512}>512 토큰</option><option value={1024}>1024 토큰</option></select></label></>}</div><button className="send-button" type="submit" disabled={sendDisabled} aria-label="질문 보내기"><Icon name="send" size={20} /></button></div></div><small className="composer-note">{mode === 'compare' ? '두 모델은 순서대로 실행되며 첫 답변부터 표시됩니다.' : mode === 'chat' ? `첫 질문은 모델 적재로 느릴 수 있습니다. 같은 모델은 ${chatModel === 'qwen3.6:27b-coding' ? '1분' : '2분'}간 유지됩니다.` : '문서 내용은 로컬에서 처리됩니다. 이미지 PDF의 OCR은 지원하지 않습니다.'}</small></form>
    </main>
    <LogPanel logs={logs} open={logOpen} onClose={() => setLogOpen(false)} onClear={() => setLogs([])} autoScroll={autoScroll} setAutoScroll={setAutoScroll} connected={health?.ollama_connected} />
  </div>;
}
