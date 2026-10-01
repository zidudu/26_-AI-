from copy import deepcopy
from pathlib import PureWindowsPath
import subprocess
import unittest
from unittest.mock import patch

from v9.schedule_tasks import classify_task, disable_selected

ROOT = r'C:\Users\Tester\Downloads\naver cafe'
NAME = 'NaverCafe_V8'


def task(version='v8', root=ROOT):
    return {'name':NAME, 'path':'\\', 'state':'Ready', 'description':'fixture', 'xml':'<Task />',
            'actions':[{'execute':root+r'\.venv_v6\Scripts\python.exe',
                        'arguments':f'-u "{root}\\{version}\\main.py" run',
                        'working_directory':root}]}


class ScheduleTaskTests(unittest.TestCase):
    def classify(self, t, **kwargs):
        return classify_task(t, ROOT, NAME, **kwargs)

    def test_exact_old_v8_task_can_be_selected(self):
        self.assertTrue(self.classify(task())['can_disable'])

    def test_current_v9_is_preserved_even_if_named_v8(self):
        actual = self.classify(task('v9'))
        self.assertEqual(actual['kind'], 'current_v9')
        self.assertFalse(actual['can_disable'])

    def test_name_alone_does_not_authorize_change(self):
        t = task()
        t['actions'][0]['arguments'] = '-u "C:\\another.py" run'
        self.assertFalse(self.classify(t)['can_disable'])

    def test_old_v5_checks_owner_and_exact_config(self):
        t = task()
        t['name'] = 'NaverCafe_V5_012345abcdef'
        t['description'] = 'NaverCafe V5 owned task '+t['name']
        t['actions'][0].update(execute=ROOT+r'\.venv\Scripts\python.exe', arguments=subprocess.list2cmdline([
            ROOT+r'\v5\main_v5.py', 'sync', '--scheduled', '--config', ROOT+r'\config_v5.json']))
        self.assertTrue(self.classify(t)['can_disable'])
        t['description'] = 'other application'
        self.assertFalse(self.classify(t)['can_disable'])

    def test_other_folder_needs_explicit_legacy_root(self):
        older = r'C:\Archive\naver_cafe_old'
        t = task('v9', older)
        self.assertFalse(self.classify(t)['can_disable'])
        self.assertTrue(self.classify(t, legacy_roots=[older])['can_disable'])

    def test_running_and_already_disabled_are_not_changed(self):
        for state in ('Running','Disabled'):
            t = task()
            t['state'] = state
            self.assertFalse(self.classify(t)['can_disable'])

    def test_multiple_actions_and_shell_commands_are_preserved(self):
        t = task()
        t['actions'].append(deepcopy(t['actions'][0]))
        self.assertFalse(self.classify(t)['can_disable'])
        t = task()
        t['actions'][0]['execute'] = r'C:\Windows\System32\cmd.exe'
        self.assertFalse(self.classify(t)['can_disable'])

    def test_extra_arguments_environment_paths_and_wildcard_name_are_preserved(self):
        for field, value in [('arguments', task()['actions'][0]['arguments']+' --extra'),
                             ('working_directory',r'%USERPROFILE%\naver_cafe')]:
            t = task()
            t['actions'][0][field] = value
            self.assertFalse(self.classify(t)['can_disable'])
        t = task()
        t['name'] = 'NaverCafe_*'
        self.assertFalse(self.classify(t)['can_disable'])

    def test_saved_classification_cannot_override_current_v9_protection(self):
        t = task('v9')
        t['classification'] = {'can_disable':True}
        snap = {'project_root':ROOT, 'tasks':[t]}
        with patch('v9.schedule_tasks.read_json', return_value={'schedule':{'task_name':NAME}}), \
             patch('v9.schedule_tasks.powershell') as invoke:
            with self.assertRaises(ValueError):
                disable_selected(PureWindowsPath(ROOT), snap, 1)
        invoke.assert_not_called()

    def test_only_selected_task_and_its_xml_are_passed_for_live_recheck(self):
        snap = {'project_root':ROOT, 'tasks':[task()]}
        with patch('v9.schedule_tasks.read_json', return_value={'schedule':{'task_name':NAME}}), \
             patch('v9.schedule_tasks.powershell', return_value={'name':NAME}) as invoke:
            disable_selected(PureWindowsPath(ROOT), snap, 1)
        action, request = invoke.call_args.args
        self.assertEqual(action, 'disable')
        self.assertEqual(request['expected_xml'], '<Task />')
        self.assertEqual(request['name'], NAME)
        self.assertIn('schedule_backups', request['backup_path'])
