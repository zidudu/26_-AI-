"""Visible-page Pinterest collector, using a dedicated local browser profile.

No stealth, private endpoints, CAPTCHA solving, guessed original URLs, or access
control bypass. The user must verify authorization before automated access.
"""
from urllib.parse import urlsplit, quote
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

class Collector:
    def __init__(self,store): self.store=store
    def launch(self,pw):
        options={'headless':False,'viewport':{'width':1280,'height':900},'accept_downloads':False}
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
        from playwright.sync_api import sync_playwright
        seen=set(); added=dupes=errors=0
        with sync_playwright() as pw:
            browser=self.launch(pw)
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
                try: browser.close()
                except Exception: pass
