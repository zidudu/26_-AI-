"""비밀번호/세션/터널 토큰을 표시하지 않는 로컬 상태 진단."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from yme.remote_security import ROOT,ServerConfig
from configure_beta import tailscale_exe


def main():
    cfg=ServerConfig.load(ROOT/'config')
    print('V8 beta 상태 진단')
    print('Python:',sys.version.split()[0])
    print('서버 URL:',f'http://127.0.0.1:{cfg.port}')
    print('개인 접속:',cfg.private_url or '(미설정)')
    print('개인 연결 8443:',cfg.fast_url or '(05_connect_private_fast.bat에서 설정)')
    print('Cloudflare 관리:',cfg.cloudflare_url or '(미사용)')
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{cfg.port}/api/health',timeout=3) as r:
            state=json.load(r)
        print('서버 응답:',state.get('application'),state.get('version'),state.get('status'))
    except Exception as exc:print('서버 응답 없음:',type(exc).__name__)
    exe=tailscale_exe()
    if exe:
        r=subprocess.run([exe,'status','--json'],capture_output=True,timeout=10)
        try:
            s=json.loads(r.stdout.decode('utf-8'));print('Tailscale:',s.get('BackendState'))
            print('이 기기 DNS:',s.get('Self',{}).get('DNSName',''))
        except ValueError:print('Tailscale 상태를 읽지 못했습니다.')
        print('Serve 확인: tailscale serve status')
    else:print('Tailscale: 설치 확인 필요')
    if os.name=='nt':
        # 사용자 이름/비밀번호를 출력하지 않고 정해진 두 작업의 상태만 표시.
        cmd="Get-ScheduledTask -TaskName YME_V8Beta_Server,YME_V8Beta_Tunnel -ErrorAction SilentlyContinue | Select-Object TaskName,State | Format-Table -AutoSize"
        subprocess.run(['powershell.exe','-NoProfile','-Command',cmd],check=False)
    print('서버 로그: logs/server.log · 수집 상세 로그: 웹의 작업 대기열')
    print('이 진단은 실제 LTE 접속 성공을 보장하지 않습니다. 휴대폰 Wi-Fi를 끄고 직접 확인하세요.')
    print('config 폴더는 공유하지 마세요. 세션/터널 자격 증명이 포함됩니다.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:print('[진단 오류]',exc);raise SystemExit(1)
