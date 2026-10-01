"""Windows 작업 스케줄러 XML 등록. 일반 사용자·로그인 세션·비밀번호 미저장."""
import argparse
from datetime import datetime,timedelta
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collector import CollectorError
from settings import read_settings,DEFAULT_CONFIG

NS='http://schemas.microsoft.com/windows/2004/02/mit/task'
SCHEDULE_VERSION='5.0.1'
ET.register_namespace('',NS)
def tag(name): return '{'+NS+'}'+name
def is_windows(): return os.name=='nt'

def task_identity(root,sid):
    digest=hashlib.sha256((str(Path(root).resolve()).casefold()+'|'+sid).encode()).hexdigest()[:12]
    return 'NaverCafe_V5_'+digest

def build_xml(cfg,sid,at,now=None):
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+',sid): raise ValueError('Invalid Windows SID')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',at): raise ValueError('Invalid HH:MM')
    now=now or datetime.now(); hour,minute=map(int,at.split(':'))
    start=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if start<=now: start+=timedelta(days=1)
    name=task_identity(cfg['root'],sid)
    root=ET.Element(tag('Task'),{'version':'1.3'})
    def el(parent,name,text=None,**attrs):
        x=ET.SubElement(parent,tag(name),attrs)
        if text is not None: x.text=str(text)
        return x
    info=el(root,'RegistrationInfo'); el(info,'Description','NaverCafe V5 owned task '+name)
    triggers=el(root,'Triggers'); trigger=el(triggers,'CalendarTrigger')
    el(trigger,'StartBoundary',start.isoformat()); el(trigger,'Enabled','true')
    daily=el(trigger,'ScheduleByDay'); el(daily,'DaysInterval','1')
    principals=el(root,'Principals'); principal=el(principals,'Principal',id='Author')
    el(principal,'UserId',sid); el(principal,'LogonType','InteractiveToken'); el(principal,'RunLevel','LeastPrivilege')
    settings=el(root,'Settings')
    for k,v in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),
                ('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true'),('AllowStartOnDemand','true'),
                ('Enabled','true'),('Hidden','false'),('WakeToRun','false'),('ExecutionTimeLimit','PT4H')]: el(settings,k,v)
    actions=el(root,'Actions',Context='Author'); action=el(actions,'Exec')
    el(action,'Command',str(cfg['root']/'.venv/Scripts/python.exe'))
    el(action,'Arguments',subprocess.list2cmdline([str(cfg['root']/'v5/main_v5.py'),'sync','--scheduled','--config',str(cfg['config_path'])]))
    el(action,'WorkingDirectory',str(cfg['root']))
    return name,ET.tostring(root,encoding='utf-16',xml_declaration=True)

def decode_xml(data):
    """출력 바이트와 XML의 encoding 선언이 다른 경우도 엄격하게 해독합니다.

    바이트를 바로 XML 파서에 넣으면 선언을 우선하여 정상 출력도 거부할 수
    있습니다. 대체 문자나 XML 일부를 버리는 방식은 사용하지 않습니다.
    """
    if isinstance(data,str): return ET.fromstring(data.lstrip('\ufeff \t\r\n'))
    try: return ET.fromstring(data)
    except (ET.ParseError,ValueError): pass
    for encoding in ('utf-8-sig','utf-16-le','utf-16-be','mbcs','oem','cp949'):
        try:
            decoded=data.decode(encoding,errors='strict').lstrip('\ufeff \t\r\n')
            if '\x00' in decoded or not decoded.startswith('<'): continue
            return ET.fromstring(decoded)
        except (UnicodeError,LookupError,ET.ParseError,ValueError): continue
    raise ET.ParseError('Task XML could not be decoded or parsed')

def same_windows_path(actual,expected):
    if not isinstance(actual,str) or not actual.strip(): return False
    actual=actual.strip()
    if len(actual)>=2 and actual[0]==actual[-1]=='"': actual=actual[1:-1]
    # 경로 표기만 정규화합니다. 환경 변수 확장이나 다른 파일로의 대체는 하지 않습니다.
    return ntpath.normcase(ntpath.normpath(actual))==ntpath.normcase(ntpath.normpath(str(expected)))

def inspect_task(xml,name,cfg):
    """인코딩 오류와 실제 실행 정보 불일치를 구분합니다. 이름만으로 승인하지 않습니다."""
    try: root=decode_xml(xml)
    except (ET.ParseError,UnicodeError,ValueError,TypeError): return None,['XML_UNREADABLE']
    errors=[]
    if root.tag!=tag('Task'): return root,['TASK_ROOT_MISMATCH']
    desc=root.findtext(tag('RegistrationInfo')+'/'+tag('Description'))
    if desc!='NaverCafe V5 owned task '+name: errors.append('OWNER_MARKER_MISMATCH')
    groups=root.findall(tag('Actions'))
    if len(groups)!=1 or len(groups[0])!=1 or groups[0][0].tag!=tag('Exec'):
        return root,errors+['ACTION_MISMATCH']
    action=groups[0][0]
    if not same_windows_path(action.findtext(tag('Command')),cfg['root']/'.venv/Scripts/python.exe'):
        errors.append('COMMAND_MISMATCH')
    expected=subprocess.list2cmdline([str(cfg['root']/'v5/main_v5.py'),'sync','--scheduled','--config',str(cfg['config_path'])])
    if action.findtext(tag('Arguments'))!=expected: errors.append('ARGUMENTS_MISMATCH')
    if not same_windows_path(action.findtext(tag('WorkingDirectory')),cfg['root']):
        errors.append('WORKING_DIRECTORY_MISMATCH')
    return root,errors

def owns_task(xml,name,cfg):
    return not inspect_task(xml,name,cfg)[1]

def record_diagnostic(cfg,name,errors):
    """불일치 항목만 기록합니다. 다른 작업의 명령 인수나 원본 XML은 저장하지 않습니다."""
    from run_report import atomic_json
    try:
        cfg['output_dir'].mkdir(parents=True,exist_ok=True)
        path=cfg['output_dir']/'schedule_diagnostic.json'
        atomic_json(path,{'schedule_version':SCHEDULE_VERSION,'time':datetime.now().astimezone().isoformat(),
                         'task_name':name,'checks_failed':errors})
        print(f'[예약 진단] {", ".join(errors)}\n진단 파일: {path}')
    except OSError: print(f'[예약 진단] {", ".join(errors)} / 진단 파일 저장 실패')

def main(argv=None):
    parser=argparse.ArgumentParser(description='V5 매일 예약 등록·조회·해제')
    parser.add_argument('command',choices=['register','status','remove']); parser.add_argument('--time'); parser.add_argument('--prompt',action='store_true')
    args=parser.parse_args(argv)
    try:
        print(f'[예약 도구 V{SCHEDULE_VERSION}]')
        if not is_windows(): raise CollectorError('WINDOWS_ONLY','예약 등록은 사용자 Windows PC에서 실행하세요.')
        cfg,_=read_settings()
        identity=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,check=True)
        match=re.search(rb'S-1-\d+(?:-\d+)+',identity.stdout)
        if not match: raise CollectorError('USER_LOOKUP_FAILED','Windows 사용자 식별자를 확인하지 못했습니다.')
        sid=match.group().decode('ascii'); at=args.time or cfg['schedule_time']
        if args.prompt:
            answer=input(f'매일 실행 시각 HH:MM (Enter: {at}, 취소: /q): ').strip()
            if answer=='/q': return 130
            if answer: at=answer
        name,xml=build_xml(cfg,sid,at)
        existing=subprocess.run(['schtasks','/Query','/TN',name,'/XML'],capture_output=True)
        task=None
        if existing.returncode==0:
            task,errors=inspect_task(existing.stdout,name,cfg)
            if errors:
                record_diagnostic(cfg,name,errors)
                if errors==['XML_UNREADABLE']:
                    raise CollectorError('TASK_XML_UNREADABLE','기존 예약 정보를 읽지 못했습니다. 예약 진단 파일을 확인하세요.')
                raise CollectorError('TASK_CONFLICT','기존 예약의 실행 정보가 V5 설정과 일치하지 않습니다. 예약 진단 항목을 확인하세요.')
        if args.command=='status':
            if existing.returncode:
                print('등록된 V5 작업을 조회하지 못했습니다. 미등록 또는 조회 권한 오류일 수 있습니다.'); return 1
            print(f'[예약 등록 확인] {name}\n시작 기준: {task.findtext(".//"+tag("StartBoundary"))}\n매일 반복 / Windows 로그인 세션에서 실행')
            return 0
        if args.command=='remove':
            if existing.returncode: print('삭제할 V5 예약 작업을 조회하지 못했습니다.'); return 1
            subprocess.run(['schtasks','/Delete','/TN',name,'/F'],check=True)
            print('[예약 해제 완료] 수집 결과와 이력은 유지됩니다.'); return 0
        python=cfg['root']/'.venv/Scripts/python.exe'
        if not python.is_file(): raise CollectorError('NOT_INSTALLED','먼저 01_install_v5.bat를 실행하세요.')
        print(f'등록할 작업: {name}\n매일 {at} (이 PC의 현지 시각)\n프로그램 폴더: {cfg["root"]}')
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(suffix='.xml',delete=False) as f:
                f.write(xml); temporary=Path(f.name)
            command=['schtasks','/Create','/TN',name,'/XML',str(temporary)]
            if existing.returncode==0: command.append('/F')
            subprocess.run(command,check=True)
            verify=subprocess.run(['schtasks','/Query','/TN',name,'/XML'],capture_output=True,check=True)
            _,errors=inspect_task(verify.stdout,name,cfg)
            if errors:
                record_diagnostic(cfg,name,errors)
                raise CollectorError('TASK_VERIFY_FAILED','등록 후 작업 내용을 확인하지 못했습니다. 예약 진단 항목을 확인하세요.')
        finally:
            if temporary: temporary.unlink(missing_ok=True)
        print('[예약 등록 완료] PC가 켜져 있고 Windows에 로그인되어 있어야 합니다.'); return 0
    except (KeyboardInterrupt,EOFError): return 130
    except CollectorError as exc: print(f'[{exc.code}] {exc}'); return 1
    except ValueError: print('[INVALID_TIME] 00:00~23:59 사이 HH:MM 형식으로 입력하세요.'); return 1
    except (OSError,subprocess.CalledProcessError):
        print('[SCHEDULE_ERROR] Windows 작업 명령이 실패했습니다. 위 오류와 사용자 권한을 확인하세요.'); return 1

if __name__=='__main__': raise SystemExit(main())
