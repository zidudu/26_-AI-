"""Bounded integration on the user-selected aichatting gallery."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arca_archive.common import RunLock, now_iso
from arca_archive.config import load_settings
from arca_archive.db import Database
from arca_archive.cli import setup_logging
from arca_archive.pipeline.runner import run_channel, CrawlContext, initial_fetcher_kind
from arca_archive.pipeline.collect import collect_article
from arca_archive.pipeline.media import download_media_for_article
from arca_archive.archive_bundle import file_hash
from arca_archive.sites import get_site

settings=load_settings()
setup_logging(settings,console=False)
folder=settings.data_path/'diagnostics'/'v2-acceptance'
def save(name,data):
    (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
db=Database(settings.db_path)
lock=RunLock(settings.lock_path)
if not lock.acquire():
    raise SystemExit('Crawler active; stop first.')
try:
    ch=db.get_channel_by_slug('dc-aichatting')
    if not ch:
        ch=db.create_channel('dc-aichatting',site='dcinside',site_channel_id='aichatting',fetch_mode='http',
                             interval_minutes=60,initial_pages=1,max_pages_per_run=3,include_notices=0,recheck_days=3)
finally:
    lock.release()
settings.crawl.max_articles_per_run=3
settings.crawl.max_rechecks_per_run=0
settings.media.max_files_per_article_per_run=5
settings.media.run_time_budget_minutes=5
run=run_channel(settings,db,ch['id'],trigger='manual')
save('dc_run.json',run)
print(json.dumps({'run':run['id'],'status':run['status'],'code':run['code'],'stats':run['stats']},ensure_ascii=False),flush=True)
# The already inspected public post has two attachments and replies; verify them explicitly.
site=get_site('dcinside')
aid=site.internal_article_id(ch,'385799')
if not db.get_article(aid):
    db.upsert_discovered(ch['id'],[{'id':aid,'remote_id':'385799','url':site.article_request(ch,{'remote_id':'385799'}).url}],None)
lock=RunLock(settings.lock_path)
if not lock.acquire():
    raise SystemExit('Crawler active; retry later.')
ctx=None
try:
    trial=db.create_run(ch,'acceptance')
    ctx=CrawlContext(settings,db,trial,ch)
    ctx.use_fetcher(initial_fetcher_kind(ch))
    outcome=collect_article(ctx,db.get_article(aid))
    if outcome in ('collected','updated','unchanged'):
        download_media_for_article(ctx,db.get_article(aid))
    db.finish_run(trial['id'],'partial' if ctx.stats.has_item_failures else 'success','ACCEPTANCE',None,ctx.stats.as_dict())
    save('dc_known_post.json',{'article_id':aid,'outcome':outcome,'run':trial['id'],'stats':ctx.stats.as_dict(),
        'stored_comments':len(db.list_comments(aid)),
        'media':[{'id':m['id'],'state':m['state'],'size':m['file_size'],'content_type':m['content_type'],
                  'hash_ok':bool(m['file_path'] and file_hash(m['file_path'])==m['sha256'])} for m in db.list_media(aid)]})
    print(json.dumps({'known_post':aid,'outcome':outcome,'comments':len(db.list_comments(aid)), 'downloaded':ctx.stats.media_downloaded,'failed':ctx.stats.media_failed}),flush=True)
finally:
    if ctx:
        ctx.close_fetcher()
    lock.release()
db.close()
