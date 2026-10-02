"""Visible-page Pinterest collector, using a dedicated local browser profile.

No stealth, private endpoints, CAPTCHA solving, guessed original URLs, or access
control bypass. The user must verify authorization before automated access.
"""
from urllib.parse import urlsplit, quote
import os
from pathlib import Path
import subprocess
import time
from .network import download_image

HOSTS={'www.pinterest.com','pinterest.com','kr.pinterest.com','www.pinterest.co.kr','pinterest.co.kr'}
def target_url(target):
    target=target.strip()
    if not target: raise ValueError('키워드 또는 Pinterest URL을 입력하세요.')
    if target.startswith(('http://','https://')):
        u=urlsplit(target)
        if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443):
            raise ValueError('지원되는 Pinterest HTTPS 주소를 입력하세요. 단축 주소는 전체 주소로 바꿔주세요.')
        return target
    return 'https://www.pinterest.com/search/pins/?q='+quote(target)

EXTRACT_JS=r'''() => {
 const rows=new Map();
 for(const a of document.querySelectorAll('a[href*="/pin/"]')){
  const id=a.getAttribute('href')?.match(/\/pin\/(\d+)/)?.[1];
  const img=a.querySelector('img'); if(!id||!img)continue;
  const candidates=(img.getAttribute('srcset')||'').split(',').map(s=>{
   const [url,size='']=s.trim().split(/\s+/);return {url,score:parseFloat(size)||0};
  });
  candidates.push({url:img.currentSrc||img.src,score:img.naturalWidth||0});
  const best=candidates.filter(x=>{try{const u=new URL(x.url);return u.protocol==='https:'&&u.hostname.endsWith('.pinimg.com');}catch{return false;}}).sort((a,b)=>b.score-a.score)[0];
  if(!best)continue;
  rows.set(id,{pin_url:'https://www.pinterest.com/pin/'+id+'/',image_url:best.url,
   title:(img.alt||a.getAttribute('aria-label')||'Pinterest 이미지').slice(0,300)});
 }
 return [...rows.values()];
}'''

DETAIL_JS=r'''() => {
  const img=document.querySelector('[data-test-id="closeup-image-main"] img, [data-test-id="closeup-body-image-container"] img[elementtiming*="MainPinImage"]');
 if(!img)return null;
 const candidates=(img.getAttribute('srcset')||'').split(',').map(s=>{
  const [url,size='']=s.trim().split(/\s+/);return {url,score:parseFloat(size)||0};
 });
 candidates.push({url:img.currentSrc||img.src,score:img.naturalWidth||0});
 const best=candidates.filter(x=>{try{const u=new URL(x.url);return u.protocol==='https:'&&u.hostname.endsWith('.pinimg.com');}catch{return false;}}).sort((a,b)=>b.score-a.score)[0];
 return best?.url||null;
}'''

PIN_DETAIL_JS=r'''() => {
 const img=document.querySelector('[data-test-id="closeup-image-main"] img, [data-test-id="closeup-body-image-container"] img[elementtiming*="MainPinImage"]');
 const video=document.querySelector('video[data-test-id="duplo-hls-video"][elementtiming*="closeup-video-main"]');
 if(!img&&!video)return null;
 const candidates=(img?.getAttribute('srcset')||'').split(',').map(s=>{
  const [url,size='']=s.trim().split(/\s+/);return {url,score:parseFloat(size)||0};
 });
 if(img)candidates.push({url:img.currentSrc||img.src,score:img.naturalWidth||0});
 if(video&&!img)candidates.push({url:video.poster,score:0});
 const best=candidates.filter(x=>{try{const u=new URL(x.url);return u.protocol==='https:'&&u.hostname.endsWith('.pinimg.com');}catch{return false;}}).sort((a,b)=>b.score-a.score)[0];
 const visit=document.querySelector('[data-test-id="visit-button"]');
 const destination=visit?.href||'';
 let sourceUrl='';try{const u=new URL(destination);if(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password&&!u.hostname.endsWith('pinterest.com'))sourceUrl=u.href;}catch{}
 const title=document.querySelector('[data-test-id="pin-title"]')?.innerText||document.querySelector('[data-test-id="closeup-title"]')?.innerText||'';
 return {title:title.trim().slice(0,300),description:(document.querySelector('[data-test-id="structured-description"]')?.innerText||'').trim().slice(0,6000),
  pinner:(document.querySelector('[data-test-id="creator-profile-link"]')?.innerText||'').trim().slice(0,160),
  source_url:sourceUrl.slice(0,4096),image_url:best?.url||'',media_kind:img?'image':'video_poster'};
}'''

class Collector:
    def __init__(self,store): self.store=store
    def launch(self,pw,*,headless=False):
        options={'headless':headless,'viewport':{'width':1280,'height':900},'accept_downloads':False}
        channel=self.store.settings()['browser_channel']
        if channel in ('chrome','msedge'): options['channel']=channel
        try: return pw.chromium.launch_persistent_context(str(self.store.root/'pinterest_profile'),**options)
        except Exception as e: raise ValueError('브라우저 실행 실패. 03_install_browser.bat을 실행하거나 설정에서 Chrome/Edge를 선택하세요. 다른 수집 창은 닫아주세요.') from e
    @staticmethod
    def guard(page):
        u=urlsplit(page.url)
        if u.hostname not in HOSTS: raise ValueError('Pinterest 외부 페이지로 이동해 수집을 중단했습니다.')
        text=page.locator('body').inner_text(timeout=5000)[:16000].lower()
        if 'captcha' in page.url.lower() or any(t in text for t in ('verify you are human','unusual traffic','prove you are human','비정상적인 트래픽')):
            raise ValueError('사람 확인 또는 접근 제한 화면입니다. 우회하지 않고 중단했습니다.')
        if '/login' in u.path or '/auth/' in u.path: raise ValueError('로그인이 필요합니다. 로그인 창을 먼저 열어 직접 로그인하세요.')
    def login(self,ctx,payload):
        channel=self.store.settings()['browser_channel']
        if os.name=='nt' and channel in ('chrome','msedge'):
            paths=[]
            if channel=='chrome':
                for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
                    if os.environ.get(key): paths.append(Path(os.environ[key])/'Google'/'Chrome'/'Application'/'chrome.exe')
            else:
                for key in ('PROGRAMFILES(X86)','PROGRAMFILES'):
                    if os.environ.get(key): paths.append(Path(os.environ[key])/'Microsoft'/'Edge'/'Application'/'msedge.exe')
            browser=next((path for path in paths if path.is_file()),None)
            if browser is None: raise ValueError('설정한 Chrome/Edge를 찾지 못했습니다. 브라우저 설정을 확인하세요.')
            profile=self.store.root/'pinterest_profile'
            subprocess.Popen([str(browser),f'--user-data-dir={profile}','--no-first-run',
                              'https://www.pinterest.com/login/'],close_fds=True)
            ctx.update(message='일반 브라우저의 전용 프로필을 열었습니다. 직접 로그인한 뒤 창을 닫으세요. 실제 로그인 성공 여부는 다음 수집에서 확인합니다.')
            return
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=self.launch(pw)
            try:
                page=browser.pages[0] if browser.pages else browser.new_page()
                page.goto('https://www.pinterest.com/login/',wait_until='domcontentloaded',timeout=45000)
                ctx.update(message='열린 브라우저에서 직접 로그인한 뒤 작업 목록의 로그인 완료를 누르세요. 최대 3분 대기합니다.')
                until=time.monotonic()+180
                while time.monotonic()<until:
                    ctx.check()
                    if ctx.finish.is_set() or page.is_closed(): break
                    ctx.cancel.wait(.5)
                ctx.update(message='전용 로그인 프로필을 저장했습니다. 실제 로그인 성공 여부는 다음 수집에서 확인합니다.')
            finally:
                try: browser.close()
                except Exception: pass
    def collect(self,ctx,payload):
        if not payload.get('permission_confirmed'): raise ValueError('자동 접근에 대한 허가를 확인하세요.')
        url=target_url(payload['target']); limit=payload.get('limit',50)
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        seen=set(); added=dupes=errors=0
        with sync_playwright() as pw:
            browser=self.launch(pw); detail=None
            try:
                page=browser.pages[0] if browser.pages else browser.new_page()
                response=page.goto(url,wait_until='domcontentloaded',timeout=45000)
                if response and response.status in (401,403,429): raise ValueError(f'Pinterest HTTP {response.status}. 제한을 우회하지 않습니다.')
                page.wait_for_timeout(2500); stalled=0
                for _ in range(100):
                    ctx.check(); self.guard(page); count=0
                    for item in page.evaluate(EXTRACT_JS):
                        if len(seen)>=limit: break
                        if item['pin_url'] in seen: continue
                        seen.add(item['pin_url']); count+=1; ctx.check()
                        try:
                            if detail is None: detail=browser.new_page()
                            detail_response=detail.goto(item['pin_url'],wait_until='domcontentloaded',timeout=45000)
                            if detail_response and detail_response.status in (401,403,429):
                                raise ValueError(f'Pinterest 핀 HTTP {detail_response.status}. 제한을 우회하지 않습니다.')
                            try: detail.locator('[data-test-id="closeup-image-main"] img, [data-test-id="closeup-body-image-container"] img[elementtiming*="MainPinImage"], video[data-test-id="duplo-hls-video"][elementtiming*="closeup-video-main"]').first.wait_for(timeout=7000)
                            except PlaywrightTimeoutError: pass
                            self.guard(detail)
                            if urlsplit(detail.url).path.rstrip('/') != urlsplit(pin['pin_url']).path.rstrip('/'):
                                raise ValueError('핀 상세 화면이 다른 페이지로 이동했습니다.')
                            full_image=detail.evaluate(DETAIL_JS)
                            if full_image: item['image_url']=full_image
                            else: ctx.update(log=f'{item["pin_url"]}: 상세 이미지를 찾지 못해 보드 미리보기를 사용합니다.')
                            raw=download_image(item['image_url'],pinterest_only=True)
                            meta={**item,'source':'pinterest','crawl_keyword':payload['target'],'tags':payload.get('tags',[]),
                                  'collection_id':payload.get('collection_id',''),'license_note':'권리 미확인 · Pin 링크는 원저작자 출처 또는 이용 허가가 아닙니다.'}
                            _,fresh=self.store.add_image(raw,meta); added+=int(fresh); dupes+=int(not fresh)
                        except (ValueError,OSError) as e:
                            errors+=1; ctx.update(log=f'{item["pin_url"]}: {e}')
                        ctx.update(progress=len(seen),added=added,duplicates=dupes,errors=errors,message=f'{len(seen)}/{limit}개 확인 · 신규 {added} · 중복 {dupes} · 실패 {errors}')
                        if ctx.cancel.wait(.7): ctx.check()
                    if len(seen)>=limit: break
                    stalled=0 if count else stalled+1
                    if stalled>=6: break
                    page.evaluate('window.scrollBy(0,Math.max(700,innerHeight*.9))'); page.wait_for_timeout(2000)
                if not seen: raise ValueError('이미지를 찾지 못했습니다. 로그인·검색 결과·권한 또는 Pinterest 화면 변경을 확인하세요.')
                ctx.update(message=f'신규 {added} · 중복 {dupes} · 실패 {errors}. {len(seen)}/{limit}개 핀을 확인했습니다. 부족한 수량을 임의로 채우지 않았습니다.')
            finally:
                try:
                    if detail: detail.close()
                except Exception: pass
                try: browser.close()
                except Exception: pass

    def sync(self,ctx,payload,sources):
        """Discover a board or keyword, collect details, then save pending files."""
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

        source=sources.get(payload['source_id'])
        if source is None: raise ValueError('등록된 수집 보드를 찾을 수 없습니다.')
        label='검색 결과' if source['kind']=='keyword' else '보드'
        deadline=time.monotonic()+source['run_minutes']*60
        media_reserve=max(60,min(600,source['run_minutes']*15))
        detail_deadline=deadline-media_reserve
        time_limited=False
        discovered=detail_done=detail_errors=added=dupes=media_errors=0
        seen=set()
        with sync_playwright() as pw:
            browser=self.launch(pw,headless=True)
            try:
                page=browser.pages[0] if browser.pages else browser.new_page()
                response=page.goto(source['target_url'],wait_until='domcontentloaded',timeout=45000)
                if response and response.status in (401,403,429):
                    raise ValueError(f'Pinterest 보드 HTTP {response.status}. 접근 제한을 우회하지 않습니다.')
                page.wait_for_timeout(2500)
                stalled=0
                for _ in range(100):
                    ctx.check(); self.guard(page); count=0
                    for item in page.evaluate(EXTRACT_JS):
                        if len(seen)>=source['scan_limit']: break
                        if item['pin_url'] in seen: continue
                        seen.add(item['pin_url']);count+=1
                        discovered+=int(sources.seen(source,item))
                    ctx.update(message=f'{label} 탐색 중 · 확인 {len(seen)} · 새 핀 {discovered}')
                    if len(seen)>=source['scan_limit']: break
                    if time.monotonic()>=detail_deadline:
                        time_limited=True; break
                    stalled=0 if count else stalled+1
                    if stalled>=6: break
                    page.evaluate('window.scrollBy(0,Math.max(700,innerHeight*.9))')
                    page.wait_for_timeout(2000)
                if not seen:
                    raise ValueError(f'{label}에서 핀을 찾지 못했습니다. 로그인 상태나 Pinterest 화면을 확인하세요.')

                detail=browser.new_page()
                try:
                    for pin in sources.detail_candidates(source['id'],source['download_limit']):
                        ctx.check()
                        if time.monotonic()>=detail_deadline:
                            time_limited=True; break
                        try:
                            response=detail.goto(pin['pin_url'],wait_until='domcontentloaded',timeout=45000)
                            if response and response.status in (401,403,429):
                                raise ValueError(f'Pinterest 핀 HTTP {response.status}. 접근 제한을 우회하지 않습니다.')
                            try: detail.locator('[data-test-id="closeup-image-main"] img, [data-test-id="closeup-body-image-container"] img[elementtiming*="MainPinImage"]').first.wait_for(timeout=7000)
                            except PlaywrightTimeoutError: pass
                            self.guard(detail)
                            parsed=detail.evaluate(PIN_DETAIL_JS)
                            if not parsed or not parsed['image_url']:
                                raise ValueError('핀 상세 화면에서 이미지를 찾지 못했습니다.')
                            parsed['title']=parsed['title'] or pin['title']
                            sources.record_detail(source['id'],pin['pin_url'],parsed)
                            detail_done+=1
                        except ValueError as exc:
                            if any(term in str(exc) for term in ('접근 제한','로그인이 필요','사람 확인','Pinterest 외부')):
                                raise
                            detail_errors+=1;sources.detail_failed(source['id'],pin['pin_url'],str(exc))
                            ctx.update(log=f'{pin["pin_url"]}: 상세 수집 실패: {exc}')
                        except (OSError,PlaywrightError) as exc:
                            detail_errors+=1;sources.detail_failed(source['id'],pin['pin_url'],type(exc).__name__)
                            ctx.update(log=f'{pin["pin_url"]}: 상세 수집 실패: {type(exc).__name__}')
                        ctx.update(errors=detail_errors+media_errors,
                                   message=f'상세 수집 · 성공 {detail_done} · 실패 {detail_errors} · 새 핀 {discovered}')
                        if ctx.cancel.wait(.7): ctx.check()
                finally:
                    detail.close()

                media_targets=sources.pending(source['id'],source['download_limit'])
                ctx.update(total=len(media_targets),progress=0)
                for index,pin in enumerate(media_targets,1):
                    ctx.check()
                    if time.monotonic()>=deadline:
                        time_limited=True; break
                    try:
                        raw=download_image(pin['image_url'],pinterest_only=True)
                        meta={'pin_url':pin['pin_url'],'image_url':pin['image_url'],
                              'title':pin['title'],'description':pin['description'],
                              'source_url':pin['source_url'],'source':'pinterest',
                              'collection_id':source['collection_id'],
                              'crawl_keyword':source['query_text'] if source['kind']=='keyword' else source['name'],
                              'tags':['동영상 썸네일'] if pin['media_kind']=='video_poster' else [],
                              'license_note':'권리 미확인 · Pin 링크와 게시자 이름은 원저작자 출처 또는 이용 허가가 아닙니다.'}
                        image,fresh=self.store.add_image(raw,meta)
                        sources.succeeded(source['id'],pin['pin_url'],image['id'])
                        added+=int(fresh);dupes+=int(not fresh)
                    except (ValueError,OSError) as exc:
                        media_errors+=1;sources.failed(source['id'],pin['pin_url'],str(exc))
                        ctx.update(log=f'{pin["pin_url"]}: 파일 저장 실패: {exc}')
                    ctx.update(progress=index,added=added,duplicates=dupes,errors=detail_errors+media_errors,
                               message=f'확인 {len(seen)} · 새 핀 {discovered} · 상세 {detail_done} · 이미지 신규 {added} · 중복 {dupes} · 실패 {detail_errors+media_errors}')
                    if ctx.cancel.wait(.7): ctx.check()
                suffix=' · 실행 시간 상한에 도달해 다음 실행에서 계속' if time_limited else ''
                ctx.update(message=f'확인 {len(seen)} · 새 핀 {discovered} · 상세 {detail_done} · 이미지 신규 {added} · 중복 {dupes} · 실패 {detail_errors+media_errors}{suffix}')
            finally:
                browser.close()
