'use strict';

const LIBRARY = 'http://127.0.0.1:8765/';
const HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com',
  'music.youtube.com', 'youtu.be', 'www.youtu.be']);
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const PLAYLIST_ID = /^[A-Za-z0-9_-]{10,200}$/;
const title = document.querySelector('#page-title');
const message = document.querySelector('#page-message');
const error = document.querySelector('#error');
const send = document.querySelector('#send');
const manual = document.querySelector('#manual-url');

function sourceFromUrl(raw) {
  let page;
  try { page = new URL(raw); } catch { return null; }
  if (page.protocol !== 'https:' || !HOSTS.has(page.hostname) || page.port) return null;
  if (page.pathname.replace(/\/$/, '') === '/playlist') {
    const id = page.searchParams.get('list') || '';
    if (!PLAYLIST_ID.test(id) || id.startsWith('RD') || ['LL', 'WL'].includes(id)) return null;
    return {kind: '재생목록', url: `https://www.youtube.com/playlist?list=${id}`};
  }
  const parts = page.pathname.split('/').filter(Boolean);
  let id = '';
  if (page.hostname.endsWith('youtu.be')) id = parts[0] || '';
  else if (page.pathname.replace(/\/$/, '') === '/watch') id = page.searchParams.get('v') || '';
  else if (['shorts', 'live', 'embed'].includes(parts[0])) id = parts[1] || '';
  return VIDEO_ID.test(id) ? {kind: '영상', url: `https://www.youtube.com/watch?v=${id}`} : null;
}

async function open(url) {
  error.hidden = true;
  try { await chrome.tabs.create({url}); window.close(); }
  catch (e) { error.textContent = `탭을 열지 못했습니다: ${e.message}`; error.hidden = false; }
}

function openSource(source) {
  return open(LIBRARY + '?add=' + encodeURIComponent(source.url));
}

document.querySelector('#library').addEventListener('click', () => open(LIBRARY));
document.querySelector('#manual-form').addEventListener('submit', event => {
  event.preventDefault();
  const source = sourceFromUrl(manual.value.trim());
  if (!source) {
    error.textContent = 'HTTPS YouTube 영상·Shorts·라이브 또는 일반 재생목록 링크를 확인해 주세요.';
    error.hidden = false;
    manual.focus();
    return;
  }
  openSource(source);
});
manual.addEventListener('input', () => { error.hidden = true; });

(async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const source = sourceFromUrl(tab?.url || '');
    if (!source) {
      title.textContent = '지원하지 않는 페이지';
      message.textContent = 'YouTube 영상, Shorts, 라이브 또는 일반 재생목록 페이지에서 사용해 주세요.';
      return;
    }
    title.textContent = tab.title || source.kind;
    message.textContent = `${source.kind} 주소를 수집 창에 채웁니다.`;
    send.disabled = false;
    send.addEventListener('click', () => openSource(source));
  } catch (e) {
    title.textContent = '현재 탭을 읽지 못했습니다';
    message.textContent = e.message;
  }
})();
