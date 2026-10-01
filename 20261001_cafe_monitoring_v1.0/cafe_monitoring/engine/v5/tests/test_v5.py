"""실사이트를 호출하지 않는 V5 이력·증분 검색·중단 복구 통합 테스트."""
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timedelta
from functools import partial
import hashlib
import io
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import collector
from collector import CollectorError,Target,KST
from settings import read_settings,DEFAULT_CONFIG
from search import SearchSelection,search_url
from history import History,valid_article
from incremental_search import scan_keyword
from run_report import RunReport
from sync_runner import execute
from run_lock import RunLock
import main_v5
import schedule

NOW=datetime(2026,9,12,9,0,tzinfo=KST)

def choice(aid,keyword='고장'):
    t=Target('20179506',str(aid))
    return SearchSelection(t,t.url+'?art=TEST_ONLY_TOKEN',{'keyword':keyword,'selected_article_id':str(aid),
        'selected_title':f'글 {aid}','selected_list_date':'2026-09-12T08:00:00+09:00','page':1,'result_rank':1})

def snapshot(ids,keyword='고장',page=1,dates=None,pages=(1,),empty=False):
    return {'document_url':search_url('20179506',keyword,page),'query_values':[keyword],
      'scope':'제목만','board':'전체 게시판','period':'전체기간','sort':'최신순','current_page':str(page),
      'empty':empty,'pagination_present':True,'page_numbers':list(pages),'next_group':False,
      'rows':[{'number':str(a),'title':f'글 {a}','date':dates[i] if dates else '08:00',
        'href':Target('20179506',str(a)).url+'?art=TEST_ONLY_TOKEN'} for i,a in enumerate(ids)]}


class V5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); cfg=json.loads(DEFAULT_CONFIG.read_text())
        cfg.update(keywords=['고장'],initial_count=3,request_interval_seconds=1)
        self.path=self.root/'config_v5.json'; self.path.write_text(json.dumps(cfg))
        self.cfg,_=read_settings(self.path)
        self.h=History(self.cfg); self.addCleanup(self.h.close)
        self.stdout=redirect_stdout(io.StringIO()); self.stdout.__enter__(); self.addCleanup(self.stdout.__exit__,None,None,None)
        self.stderr=redirect_stderr(io.StringIO()); self.stderr.__enter__(); self.addCleanup(self.stderr.__exit__,None,None,None)

    def saved(self,aid,keyword='고장',folder=None):
        c=choice(aid,keyword)
        a=collector.build_article({'title':f'글 {aid}','date':'2026.09.12. 08:00','body':f'본문 {aid}'},c.target)
        a.update(searches=[c.metadata],matched_keywords=[keyword])
        a['collected_at']=NOW.isoformat()
        return a,collector.save_article(folder or self.root/'old',a)

    def scanner(self,pages):
        loader=MagicMock(side_effect=pages)
        return partial(scan_keyword,loader=loader,pause=lambda _:None,now_fn=lambda:NOW),loader

    def collect(self,errors=None):
        def do(page,target,cfg,timeout,navigation_url=None):
            if errors and target.article_id in errors: raise errors[target.article_id]
            return collector.build_article({'title':f'글 {target.article_id}','date':'2026.09.12. 08:00','body':f'본문 {target.article_id}'},target)
        return MagicMock(side_effect=do)

    def run_sync(self,pages,collect=None,mode='sync'):
        scan,loader=self.scanner(pages)
        run=RunReport(self.cfg,mode); collect=collect or self.collect()
        result=execute(run,self.h,None,TimeoutError,scanner=scan,collect=collect,pause=lambda _:None,now_fn=lambda:NOW)
        summary=json.loads((run.folder/'summary.json').read_text())
        self.assertEqual(summary['considered_unique_count'],sum(summary[k] for k in ['saved_count','existing_count','held_count','skipped_count','failed_count','unprocessed_count','deferred_count']))
        return result,summary,collect,run,loader

    def test_two_runs_do_not_reopen_saved_articles(self):
        first=self.run_sync([snapshot([3,2,1])]); second=self.run_sync([snapshot([3,2,1])])
        self.assertEqual(first[2].call_count,3); second[2].assert_not_called()
        self.assertEqual((second[0],second[1]['saved_count'],second[1]['existing_count']),(0,0,3))
        self.assertEqual(len(list(self.cfg['output_dir'].rglob('20179506_*.json'))),3)

    def test_new_arrival_only_is_collected(self):
        self.run_sync([snapshot([3,2,1])])
        _,s,collect,_,_=self.run_sync([snapshot([4,3,2,1])])
        self.assertEqual([c.args[1].article_id for c in collect.call_args_list],['4'])
        self.assertEqual((s['saved_count'],s['existing_count']),(1,3))

    def test_multi_keyword_overlap_keeps_both_terms(self):
        self.cfg['keywords']=['고장','클락션 고장']; self.cfg['initial_count']=2
        _,s,collect,run,_=self.run_sync([snapshot([3,2]),snapshot([2,1],'클락션 고장')])
        self.assertEqual((s['selected_total'],s['duplicate_count'],s['found_unique_count']),(4,1,3))
        self.assertEqual(collect.call_count,3)
        common=next(i for i in s['items'] if i['article_id']=='2')
        a=json.loads((run.folder/common['result_file']).read_text())
        self.assertEqual(set(a['matched_keywords']),{'고장','클락션 고장'})
        self.assertNotIn('TEST_ONLY_TOKEN',(run.folder/'summary.json').read_text())
        self.assertNotIn('TEST_ONLY_TOKEN',self.cfg['history_db'].read_bytes().decode('utf-8',errors='ignore'))

    def test_new_keyword_adds_match_without_fetching_existing(self):
        self.cfg['initial_count']=1
        self.run_sync([snapshot([2])])
        self.cfg['keywords']=['클락션 고장']
        _,s,collect,_,_=self.run_sync([snapshot([2],'클락션 고장')])
        collect.assert_not_called()
        self.assertEqual({m['keyword'] for m in self.h.match_list('2')},{'고장','클락션 고장'})

    def test_existing_id_is_not_a_search_stop_boundary(self):
        self.cfg['initial_count']=1; self.run_sync([snapshot([9])])
        pages=[snapshot([10,9],page=1,pages=(1,2)),snapshot([8,7],page=2,pages=(1,2),dates=['08:00','2026.09.10.'])]
        _,s,collect,_,loader=self.run_sync(pages)
        self.assertEqual(loader.call_count,2)
        self.assertEqual({c.args[1].article_id for c in collect.call_args_list},{'10','8'})
        self.assertEqual(s['keyword_results'][0]['reason'],'PREVIOUS_RANGE_REACHED')

    def test_page_budget_keeps_cursor_and_resumes_with_overlap(self):
        self.cfg['initial_count']=1; self.run_sync([snapshot([10])])
        self.cfg['max_pages_per_keyword_per_run']=2
        pages=[snapshot([12,11],page=1,pages=(1,2,3)),snapshot([10,9],page=2,pages=(1,2,3))]
        _,s,_,_,_=self.run_sync(pages)
        self.assertFalse(s['coverage_complete']); self.assertEqual(self.h.scan('고장')['next_page'],3)
        last=self.h.scan('고장')['last_completed_at']
        second=[snapshot([10,9],page=2,pages=(1,2,3)),snapshot([8,7],page=3,pages=(1,2,3),dates=['08:00','2026.09.10.'])]
        _,s,collect,_,loader=self.run_sync(second)
        self.assertEqual([c.args[3] for c in loader.call_args_list],[2,3])
        self.assertTrue(s['coverage_complete']); self.assertEqual(collect.call_count,1)
        self.assertIsNone(self.h.scan('고장')['cycle_started_at'])

    def test_failed_search_page_does_not_advance_cursor_or_watermark(self):
        self.cfg['initial_count']=20
        loader=MagicMock(side_effect=[snapshot([3,2],pages=(1,2)),CollectorError('SEARCH_NOT_READY','test')])
        with self.assertRaises(CollectorError):
            scan_keyword(None,self.cfg,self.h,'고장',TimeoutError,lambda *args:None,loader=loader,pause=lambda _:None,now_fn=lambda:NOW)
        state=self.h.scan('고장')
        self.assertEqual(state['next_page'],2); self.assertNotIn('last_completed_at',state)
        self.assertEqual(self.h.stats(),{'pending':2})

    def test_empty_search_is_successful_zero_new_run(self):
        code,s,collect,_,_=self.run_sync([snapshot([],empty=True)])
        self.assertEqual(code,0); self.assertEqual(s['saved_count'],0); collect.assert_not_called()
        self.assertIsNotNone(self.h.scan('고장')['last_completed_at'])

    def test_grade_is_held_then_skipped_without_navigation(self):
        self.cfg['initial_count']=1
        first=self.run_sync([snapshot([1])],self.collect({'1':CollectorError('GRADE_REQUIRED','test')}))
        second=self.run_sync([snapshot([1])])
        self.assertEqual(first[1]['skipped_count'],1); second[2].assert_not_called()
        self.assertEqual(second[1]['held_count'],1)
        self.assertEqual(self.h.requeue(['고장'],'held'),1)
        third=self.run_sync([],mode='recheck')
        self.assertEqual(third[1]['saved_count'],1)

    def test_retry_cooldown_and_exhaustion_are_persistent(self):
        self.cfg['initial_count']=1; self.cfg['max_attempts']=2
        self.run_sync([snapshot([1])],self.collect({'1':CollectorError('PAGE_NOT_READY','test')}))
        _,s,collect,_,_=self.run_sync([],mode='resume'); collect.assert_not_called()
        self.assertEqual(s['deferred_count'],1)
        self.h.set_status('1','retry',next_retry_at=(NOW-timedelta(seconds=1)).isoformat())
        self.run_sync([],self.collect({'1':CollectorError('PAGE_NOT_READY','test')}),mode='resume')
        self.assertEqual(self.h.get('1')['status'],'error')
        self.assertEqual(self.h.requeue(['고장'],'errors'),1)

    def test_login_stops_and_resume_collects_only_remaining(self):
        run=RunReport(self.cfg,'sync'); scan,_=self.scanner([snapshot([3,2,1])])
        collect=self.collect({'2':CollectorError('LOGIN_REQUIRED','test')})
        self.assertEqual(execute(run,self.h,None,TimeoutError,scanner=scan,collect=collect,pause=lambda _:None,now_fn=lambda:NOW),1)
        self.assertEqual(self.h.stats(),{'pending':2,'saved':1})
        _,s,collect,_,_=self.run_sync([],mode='resume')
        self.assertEqual(collect.call_count,2); self.assertEqual(s['saved_count'],2)

    def test_interruption_and_recovery_preserve_success(self):
        run=RunReport(self.cfg,'sync'); scan,_=self.scanner([snapshot([3,2,1])])
        with self.assertRaises(KeyboardInterrupt):
            execute(run,self.h,None,TimeoutError,scanner=scan,collect=self.collect({'2':KeyboardInterrupt()}),pause=lambda _:None)
        self.assertEqual(self.h.get('3')['status'],'saved')
        self.assertEqual(self.h.recover_and_check()['interrupted_recovered'],1)
        _,_,collect,_,_=self.run_sync([],mode='resume')
        self.assertEqual({c.args[1].article_id for c in collect.call_args_list},{'2','1'})

    def test_collection_limit_persists_unprocessed_queue(self):
        self.cfg['max_collect_per_run']=1
        _,s,collect,_,_=self.run_sync([snapshot([3,2,1])])
        self.assertEqual((s['saved_count'],s['unprocessed_count']),(1,2))
        self.cfg['max_collect_per_run']=5
        _,_,collect,_,_=self.run_sync([],mode='resume'); self.assertEqual(collect.call_count,2)

    def test_missing_saved_file_is_requeued(self):
        _,s,_,run,_=self.run_sync([snapshot([3,2,1])])
        item=s['items'][0]; (run.folder/item['result_file']).unlink()
        self.assertEqual(self.h.recover_and_check()['missing_or_invalid_files'],1)
        _,_,collect,_,_=self.run_sync([],mode='resume'); self.assertEqual(collect.call_count,1)

    def test_import_is_idempotent_and_does_not_open_browser(self):
        a,path=self.saved(1)
        r=self.h.import_results([path.parent]); self.assertEqual(r['files_registered'],1)
        self.assertEqual(self.h.import_results([path.parent])['unchanged_files'],1)
        self.cfg['initial_count']=1
        _,s,collect,_,_=self.run_sync([snapshot([1])]); collect.assert_not_called()
        self.assertEqual(s['existing_count'],1)

    def test_invalid_json_and_wrong_hash_not_marked_saved(self):
        a,path=self.saved(1); a['body']='tampered'; path.write_text(json.dumps(a))
        self.assertEqual(self.h.import_results([path.parent])['invalid_files'],1)
        self.assertIsNone(self.h.get('1'))

    def test_file_saved_before_database_commit_is_recovered(self):
        self.h.observe(choice(1)); self.h.set_status('1','collecting',attempts=1)
        a,path=self.saved(1,folder=self.cfg['output_dir']/'interrupted_run')
        self.h.import_results([self.cfg['output_dir']]); self.h.recover_and_check()
        _,s,collect,_,_=self.run_sync([],mode='resume')
        collect.assert_not_called(); self.assertEqual(self.h.get('1')['status'],'saved')

    def test_import_v4_explicit_grade_but_not_unknown_timeout(self):
        folder=self.root/'old'; folder.mkdir()
        def item(aid,status,code):
            c=choice(aid)
            return {'article_id':str(aid),'url':c.target.url,'selected_title':f'글 {aid}','status':status,'code':code,'searches':[c.metadata]}
        (folder/'summary.json').write_text(json.dumps({'cafe_id':'20179506','collector_version':'4.0.0','items':[item(1,'skipped','GRADE_REQUIRED'),item(2,'failed','PAGE_NOT_READY')]}))
        self.h.import_results([folder]); self.assertEqual(self.h.get('1')['status'],'held'); self.assertIsNone(self.h.get('2'))
        self.h.requeue(['고장'],'held'); self.h.import_results([folder])
        self.assertEqual(self.h.get('1')['status'],'pending')

    def test_wrong_cafe_and_profile_history_scope_rejected(self):
        cfg=self.cfg|{'profile_dir':self.root/'other_profile'}
        with self.assertRaises(CollectorError) as e: History(cfg)
        self.assertEqual(e.exception.code,'HISTORY_SCOPE_MISMATCH')

    def test_page_transaction_rolls_back_queue_and_cursor_together(self):
        c=choice(1); bad=choice(2); bad.metadata['keyword']=None
        with self.assertRaises(CollectorError): self.h.save_scan_page('고장',{'next_page':2},[c,bad])
        self.assertIsNone(self.h.get('1')); self.assertIsNone(self.h.scan('고장'))

    def test_process_lock_blocks_second_run_and_releases(self):
        path=self.root/'lock'
        with RunLock(path):
            with self.assertRaises(CollectorError):
                with RunLock(path): pass
        with RunLock(path): pass

    def test_scheduler_xml_handles_paths_and_no_password(self):
        cfg=self.cfg|{'root':self.root/'공백 & 특수 폴더','config_path':self.root/'공백 & 특수 폴더/config_v5.json'}
        name,xml=schedule.build_xml(cfg,'S-1-5-21-123-456-789-1001','09:00',datetime(2026,9,12,10))
        self.assertTrue(schedule.owns_task(xml,name,cfg))
        root=schedule.decode_xml(xml)
        self.assertEqual(root.findtext('.//'+schedule.tag('LogonType')),'InteractiveToken')
        self.assertEqual(root.findtext('.//'+schedule.tag('RunLevel')),'LeastPrivilege')
        self.assertEqual(root.findtext('.//'+schedule.tag('StartBoundary')),'2026-09-13T09:00:00')
        self.assertEqual(root.findtext('.//'+schedule.tag('MultipleInstancesPolicy')),'IgnoreNew')
        self.assertNotIn('Password',xml.decode('utf-16'))
        other=xml.decode('utf-16').replace('owned task','other task').encode('utf-16')
        self.assertFalse(schedule.owns_task(other,name,cfg))

    def test_invalid_configuration_is_rejected_before_browser(self):
        cfg=json.loads(self.path.read_text()); cfg['max_collect_per_run']=0; self.path.write_text(json.dumps(cfg))
        with patch.object(main_v5,'browser_command') as browser:
            self.assertEqual(main_v5.main(['sync','--config',str(self.path)]),1)
            browser.assert_not_called()

    def test_cli_import_and_no_pending_resume_work_without_browser(self):
        self.saved(1,folder=self.root/'output_v4')
        with patch.object(main_v5,'browser_command') as browser:
            self.assertEqual(main_v5.main(['import','--config',str(self.path)]),0)
            self.assertEqual(main_v5.main(['resume','--config',str(self.path)]),0)
            browser.assert_not_called()

    def test_cli_cancellation_and_final_error_status(self):
        with patch('builtins.input',return_value='/q'):
            self.assertEqual(main_v5.main(['sync','--prompt','--config',str(self.path)]),130)
        status=json.loads((self.cfg['output_dir']/'latest_status.json').read_text())
        self.assertEqual(status['code'],'CANCELLED')

    def test_database_failure_after_json_write_recovers_without_refetch(self):
        self.cfg['initial_count']=1
        with patch.object(self.h,'mark_saved',side_effect=sqlite3.OperationalError('simulated')):
            with self.assertRaises(sqlite3.OperationalError): self.run_sync([snapshot([1])])
        self.assertEqual(self.h.get('1')['status'],'collecting')
        self.assertEqual(len(list(self.cfg['output_dir'].rglob('20179506_*.json'))),1)
        self.h.import_results([self.cfg['output_dir']]); self.h.recover_and_check()
        _,_,collect,_,_=self.run_sync([],mode='resume')
        collect.assert_not_called(); self.assertEqual(self.h.get('1')['status'],'saved')

    def test_final_summary_failure_never_emits_success_event(self):
        run=RunReport(self.cfg,'resume')
        with patch('run_report.atomic_json',side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError): run.finish(self.h)
        events=[json.loads(line) for line in (run.folder/'events.jsonl').read_text().splitlines()]
        self.assertNotIn('run_finished',[e['event'] for e in events])
        self.assertFalse(run.finalized)

    def test_absolute_page_limit_preserves_incomplete_cycle(self):
        self.cfg.update(initial_count=50,max_search_page=2,max_pages_per_keyword_per_run=3)
        _,s,_,_,loader=self.run_sync([snapshot([4,3],pages=(1,2,3)),snapshot([2,1],page=2,pages=(1,2,3))])
        self.assertEqual(loader.call_count,2)
        self.assertEqual(s['keyword_results'][0]['reason'],'SEARCH_PAGE_LIMIT')
        self.assertFalse(s['coverage_complete'])
        self.assertIsNone(self.h.scan('고장').get('last_completed_at'))

    def test_malformed_import_metadata_is_counted_and_not_saved(self):
        a,path=self.saved(1); a['searches']=['invalid']; path.write_text(json.dumps(a))
        (path.parent/'summary.json').write_text('[]')
        report=self.h.import_results([path.parent])
        self.assertEqual(report['invalid_files'],2); self.assertIsNone(self.h.get('1'))

    def test_scheduled_task_registers_owned_xml_and_verifies(self):
        sid='S-1-5-21-123-456-789-1001'
        name,xml=schedule.build_xml(self.cfg,sid,'09:00')
        python=self.root/'.venv/Scripts/python.exe'; python.parent.mkdir(parents=True); python.write_bytes(b'test')
        responses=[subprocess.CompletedProcess([],0,stdout=sid.encode()),subprocess.CompletedProcess([],1,stdout=b''),
                   subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],0,stdout=xml)]
        with patch.object(schedule,'is_windows',return_value=True),patch.object(schedule,'read_settings',return_value=(self.cfg,None)),patch.object(schedule.subprocess,'run',side_effect=responses) as proc:
            self.assertEqual(schedule.main(['register','--time','09:00']),0)
        creation=proc.call_args_list[2].args[0]
        self.assertEqual(creation[:4],['schtasks','/Create','/TN',name])
        self.assertNotIn('/F',creation)
        self.assertFalse(Path(creation[-1]).exists())

    def test_scheduled_task_conflict_does_not_create_or_remove(self):
        sid='S-1-5-21-123-456-789-1001'
        name,xml=schedule.build_xml(self.cfg,sid,'09:00')
        foreign=xml.decode('utf-16').replace('owned task','foreign task').encode('utf-16')
        for command in ['register','remove']:
            responses=[subprocess.CompletedProcess([],0,stdout=sid.encode()),subprocess.CompletedProcess([],0,stdout=foreign)]
            with patch.object(schedule,'is_windows',return_value=True),patch.object(schedule,'read_settings',return_value=(self.cfg,None)),patch.object(schedule.subprocess,'run',side_effect=responses) as proc:
                self.assertEqual(schedule.main([command]),1)
                self.assertEqual(proc.call_count,2)

    def test_browser_context_closes_after_collection_exception(self):
        fake=MagicMock(); context=MagicMock(); fake.sync_playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value=context
        fake.Error=RuntimeError; fake.TimeoutError=TimeoutError
        with patch.dict(sys.modules,{'playwright.sync_api':fake}),patch.object(main_v5,'execute',side_effect=CollectorError('LOGIN_REQUIRED','test')):
            with self.assertRaises(CollectorError): main_v5.browser_command(self.cfg,None,'sync',None,self.h)
        context.close.assert_called_once()

if __name__=='__main__': unittest.main()
