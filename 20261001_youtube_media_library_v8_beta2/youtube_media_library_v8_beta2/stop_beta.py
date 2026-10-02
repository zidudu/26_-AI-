"""인터넷 종료 API 없이 로컬 소유자만 정상 종료 요청 파일을 만듭니다."""
import json
import time
from pathlib import Path
from yme.remote_security import ROOT, atomic_private_json

folder=ROOT/'config';runtime=folder/'runtime.json'
if not runtime.exists():
    print('실행 중인 beta 서버 기록이 없습니다.')
    raise SystemExit(0)
try:
    data=json.loads(runtime.read_text())
    atomic_private_json(folder/'stop.request',{'id':data['id']})
    print('작업 정리와 정상 종료를 요청했습니다. 최대 40초 기다립니다.')
    for _ in range(80):
        if not runtime.exists():
            print('서버가 종료되었습니다.');raise SystemExit(0)
        time.sleep(.5)
    print('응답이 없습니다. Windows 작업 관리자에서 해당 beta 서버를 확인하세요. 다른 Python 작업은 종료하지 마세요.')
    raise SystemExit(1)
except (ValueError,KeyError) as exc:
    print('실행 기록을 읽지 못했습니다:',exc);raise SystemExit(1)
