"""예약 XML 인코딩과 소유 작업 확인 회귀 테스트. Windows 명령은 모의 실행합니다."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import schedule


class ScheduleFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)/'한글 & 공백 폴더'; root.mkdir()
        self.cfg={'root':root,'config_path':root/'config_v5.json','schedule_time':'09:00','output_dir':root/'output_v5'}
        self.sid='S-1-5-21-123-456-789-1001'
        self.name,self.xml=schedule.build_xml(self.cfg,self.sid,'01:07',datetime(2026,9,12,1,4))
        self.text=self.xml.decode('utf-16')
        python=root/'.venv/Scripts/python.exe'; python.parent.mkdir(parents=True); python.write_bytes(b'test')
        self.stdout=io.StringIO(); cm=redirect_stdout(self.stdout); cm.__enter__(); self.addCleanup(cm.__exit__,None,None,None)

    def invoke(self,command,outputs):
        replies=[subprocess.CompletedProcess([],0,stdout=self.sid.encode()),*outputs]
        with patch.object(schedule,'is_windows',return_value=True),patch.object(schedule,'read_settings',return_value=(self.cfg,None)),patch.object(schedule.subprocess,'run',side_effect=replies) as proc:
            result=schedule.main([command,'--time','01:07'])
        return result,[c.args[0] for c in proc.call_args_list]

    def mutated(self,element,value):
        root=ET.fromstring(self.xml)
        root.find('.//'+schedule.tag(element)).text=value
        return ET.tostring(root,encoding='utf-16')

    def test_encoding_declaration_and_stdout_encoding_can_differ(self):
        for encoding in ['utf-8','utf-8-sig','utf-16','utf-16-le','utf-16-be','cp949']:
            with self.subTest(encoding=encoding):
                data=self.text.encode(encoding)
                self.assertTrue(schedule.owns_task(data,self.name,self.cfg))
                self.assertEqual(schedule.decode_xml(data).findtext('.//'+schedule.tag('WorkingDirectory')),str(self.cfg['root']))

    def test_unicode_and_leading_newlines_are_supported(self):
        for data in ['\ufeff\r\n '+self.text,('\r\n '+self.text).encode('utf-8')]:
            with self.subTest(kind=type(data).__name__): self.assertTrue(schedule.owns_task(data,self.name,self.cfg))

    def test_incomplete_and_non_task_xml_remain_rejected(self):
        for data,reason in [(b'not xml',['XML_UNREADABLE']),(b'<Task>',['XML_UNREADABLE']),(b'<Other/>',['TASK_ROOT_MISMATCH'])]:
            with self.subTest(data=data): self.assertEqual(schedule.inspect_task(data,self.name,self.cfg)[1],reason)

    def test_actual_command_arguments_and_marker_mismatches_remain_rejected(self):
        for field,value,reason in [('Description','another task','OWNER_MARKER_MISMATCH'),
             ('Command','C:\\other\\python.exe','COMMAND_MISMATCH'),
             ('Arguments','another.py sync','ARGUMENTS_MISMATCH'),
             ('WorkingDirectory','C:\\other','WORKING_DIRECTORY_MISMATCH')]:
            with self.subTest(field=field):
                self.assertIn(reason,schedule.inspect_task(self.mutated(field,value),self.name,self.cfg)[1])

    def test_extra_actions_are_not_accepted(self):
        root=ET.fromstring(self.xml); actions=root.find(schedule.tag('Actions'))
        ET.SubElement(actions,schedule.tag('Exec'))
        self.assertIn('ACTION_MISMATCH',schedule.inspect_task(ET.tostring(root),self.name,self.cfg)[1])

    def test_equivalent_windows_command_path_notation(self):
        command='"'+str(self.cfg['root']/'.venv/Scripts/python.exe').replace('/','\\').upper()+'"'
        self.assertTrue(schedule.owns_task(self.mutated('Command',command),self.name,self.cfg))

    def test_repeat_registration_accepts_utf8_stdout_and_updates_owned_task(self):
        response=subprocess.CompletedProcess([],0,stdout=self.text.encode('utf-8'))
        result,calls=self.invoke('register',[response,subprocess.CompletedProcess([],0),response])
        self.assertEqual(result,0)
        self.assertEqual(calls[2][:4],['schtasks','/Create','/TN',self.name])
        self.assertEqual(calls[2][-1],'/F')
        self.assertEqual(calls[3],['schtasks','/Query','/TN',self.name,'/XML'])
        self.assertIn('[예약 등록 완료]',self.stdout.getvalue())

    def test_status_and_remove_read_existing_utf8_stdout(self):
        response=subprocess.CompletedProcess([],0,stdout=self.text.encode('utf-8'))
        result,calls=self.invoke('status',[response]); self.assertEqual(result,0); self.assertEqual(len(calls),2)
        result,calls=self.invoke('remove',[response,subprocess.CompletedProcess([],0)])
        self.assertEqual(result,0); self.assertEqual(calls[-1],['schtasks','/Delete','/TN',self.name,'/F'])

    def test_mismatch_and_decode_failure_record_reason_without_mutation(self):
        for data,reason in [(b'<Task>','XML_UNREADABLE'),(self.mutated('Arguments','private_token=do_not_save'),'ARGUMENTS_MISMATCH')]:
            for command in ['register','remove']:
                with self.subTest(reason=reason,command=command):
                    result,calls=self.invoke(command,[subprocess.CompletedProcess([],0,stdout=data)])
                    self.assertEqual(result,1); self.assertEqual(len(calls),2)
                    raw=(self.cfg['output_dir']/'schedule_diagnostic.json').read_text()
                    self.assertIn(reason,json.loads(raw)['checks_failed']); self.assertNotIn('private_token',raw)

    def test_failed_post_registration_check_is_not_reported_as_complete(self):
        response=subprocess.CompletedProcess([],0,stdout=self.text.encode('utf-8'))
        result,_=self.invoke('register',[response,subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],0,stdout=b'<Task>')])
        self.assertEqual(result,1)
        self.assertIn('TASK_VERIFY_FAILED',self.stdout.getvalue())
        self.assertNotIn('[예약 등록 완료]',self.stdout.getvalue())

if __name__=='__main__': unittest.main()
