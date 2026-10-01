"""Asynchronous port. Generated at build time; original business rules retained."""
import time
from v754.core import write_json
from v8.collector import save_article
from v8.period_collect import write_collection_html
from v754.period_sources import build_item, save_collection
from .collector import collect_article
from .batch_search import load_page
from .period_scan import scan_range
from v9.collection_control import pace as shared_pace, check_stop, report_access_error

async def collect_with_pages(cfg, settings, webcfg, folder, report, checkpoint, window, words, *, search_page, article_page, timeout_error):
    report['stage'] = 'COLLECT_PERIOD'
    source_folder = folder / 'source'
    source_folder.mkdir(exist_ok=False)
    audits = {'version': '7.5.4', 'api_calls': 0, 'items': []}
    scan = {}
    items = []
    last_request = None

    async def pace():
        await shared_pace()

    async def loader(word, number):
        await pace()
        try:
            result = await load_page(search_page, webcfg, word, number, timeout_error)
            check_stop()
            return result
        except Exception as exc:
            report_access_error(exc)
            raise

    async def fetch(choice):
        await pace()
        try:
            result = await collect_article(article_page, choice.target, {**webcfg, 'capture_output_dir': source_folder, 'period_window': window.record()}, timeout_error, navigation_url=choice.navigation_url)
            check_stop()
            return result
        except Exception as exc:
            report_access_error(exc)
            raise

    def save_item(source):
        path = save_article(source_folder, source)
        item = build_item(source, path, cfg, window)
        items.append(item)
        audits['items'].append({'article_id': item['id'], 'source_file': str(path), 'trace': source.get('metadata_audit', {}), 'ppt_metadata': item['metadata']})
        return item

    def save():
        report['selected_articles'] = len(items)
        report['items'] = items
        report['missing_capture_articles'] = [i['id'] for i in items if not i['captures']]
        report['capture_failures'] = [{'article_id': i['id'], 'code': 'CAPTURE_REVIEW_REQUIRED', 'messages': i['capture_notes']} for i in items if i['capture_problem']]
        report['range_search_complete'] = scan.get('range_search_complete', False)
        report['articles_verified_complete'] = scan.get('articles_verified_complete', False)
        report['collection_complete'] = scan.get('complete', False)
        report['search_recheck_policy'] = scan.get('search_recheck_policy', 'full')
        report['skipped_search_rechecks'] = scan.get('skipped_rechecks', 0)
        serializable = {**scan, 'articles': [{k: v for k, v in r.items() if k != 'item'} for r in scan.get('articles', [])]}
        write_json(folder / 'search_audit.json', serializable)
        write_json(folder / 'metadata_audit.json', audits)
        save_collection(folder, report, items)
        checkpoint()
    try:
        ordered = await scan_range(window, words, webcfg, settings, scan, loader=loader, fetch=fetch, save_item=save_item, checkpoint=save)
        items[:] = ordered
        return items
    finally:
        save()
        write_collection_html(folder, report, scan)
