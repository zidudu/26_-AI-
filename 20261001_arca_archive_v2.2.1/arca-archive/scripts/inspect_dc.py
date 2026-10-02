"""Fetch the user-selected public gallery once and inspect structural selectors."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lxml import html
from arca_archive.config import load_settings
from arca_archive.fetch.http_fetcher import HttpFetcher
from urllib.parse import urljoin
settings = load_settings()
folder = settings.data_path/'diagnostics'/'dc-structure'
folder.mkdir(parents=True, exist_ok=True)
fetcher = HttpFetcher(settings)
try:
    url = 'https://gall.dcinside.com/mgallery/board/lists/?id=aichatting'
    result = fetcher.fetch_page(url)
    (folder/'list.html').write_text(result.html, encoding='utf-8')
    root = html.fromstring(result.html)
    rows = root.xpath('//tr[contains(concat(" ",normalize-space(@class)," ")," ub-content ")]')
    print(json.dumps({'status':result.status,'rows':len(rows), 'classes': [r.get('class') for r in rows[:3]], 'sample_row':html.tostring(rows[-1],encoding='unicode')[:4000] if rows else ''},ensure_ascii=False))
    if result.status == 200 and rows:
        links = rows[-1].xpath('.//td[contains(@class,"gall_tit")]/a[contains(@href,"/view/")]/@href')
        if links:
            import time
            time.sleep(2)
            article_url = urljoin(url,links[0])
            article = fetcher.fetch_page(article_url)
            (folder/'article.html').write_text(article.html,encoding='utf-8')
            (folder/'source.json').write_text(json.dumps({'list':url,'article':article_url,'list_status':result.status,'article_status':article.status}), encoding='utf-8')
            doc = html.fromstring(article.html)
            print(json.dumps({'article_url':article_url,'status':article.status,'selectors':{key:len(doc.xpath(xp)) for key,xp in {'title':'//*[contains(@class,"title_subject")]','body':'//*[contains(@class,"write_div")]','comments':'//*[contains(@class,"cmt_info")]','images':'//*[contains(@class,"write_div")]//img','videos':'//*[contains(@class,"write_div")]//video'}.items()},'scripts':doc.xpath('//script[@src]/@src')[-20:]},ensure_ascii=False))
finally:
    fetcher.close()
