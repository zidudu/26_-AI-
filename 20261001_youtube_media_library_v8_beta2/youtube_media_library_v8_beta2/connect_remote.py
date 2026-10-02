"""사용자 PC에서 외부 연결 도구를 설정합니다. 계정/터널을 원격으로 생성하지 않습니다."""
from __future__ import annotations
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from configure_beta import CONFIG, private_permissions, tailscale_exe
from yme.remote_security import ROOT, ServerConfig, https_origin


def agree(message: str) -> bool:
    print(message)
    return input('진행하려면 y 입력, 취소는 Enter: ').strip().lower() == 'y'


def find_cloudflared():
    p = shutil.which('cloudflared')
    if p: return p
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if base:
            for sub in ('cloudflared/cloudflared.exe','Cloudflare/cloudflared.exe'):
                p=Path(base)/sub
                if p.is_file():return str(p)
    # PATH 갱신 전에도 winget의 안정 링크를 먼저 확인합니다.
    p=Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/WinGet/Links/cloudflared.exe'
    return str(p) if p.is_file() else None


def install_with_winget(package: str):
    if os.name != 'nt' or not shutil.which('winget'):
        raise RuntimeError('winget을 찾지 못했습니다. README의 공식 설치 안내를 사용한 뒤 이 창을 다시 실행하세요.')
    if not agree(f'{package}를 Windows winget으로 설치합니다. 관리자 권한 창이 표시될 수 있습니다.'):
        raise RuntimeError('설치를 취소했습니다.')
    # 기존에 설치된 앱을 임의로 업그레이드하거나 약관을 자동 수락하지 않습니다.
    subprocess.run(['winget','install','--id',package,'--exact','--source','winget'],check=True)


def tailscale_setup():
    cfg=ServerConfig.load(CONFIG)
    exe=tailscale_exe()
    if not exe:
        install_with_winget('Tailscale.Tailscale');exe=tailscale_exe()
    if not exe:raise RuntimeError('설치 후 이 창을 닫고 다시 실행해 주세요. Tailscale 실행 파일을 아직 찾지 못했습니다.')
    def status():
        r=subprocess.run([exe,'status','--json'],capture_output=True,timeout=20)
        try:return json.loads(r.stdout.decode('utf-8'))
        except (ValueError,UnicodeDecodeError):return {}
    current=status()
    if current.get('BackendState')!='Running':
        print('브라우저에서 Tailscale 로그인을 완료해 주세요. 휴대폰에도 같은 개인 네트워크 계정을 사용합니다.')
        subprocess.run([exe,'login'],check=True)
        current=status()
    if current.get('BackendState')!='Running':
        raise RuntimeError('Tailscale이 아직 연결되지 않았습니다. PC의 Tailscale 앱을 연결한 후 다시 실행하세요.')
    host=current.get('Self',{}).get('DNSName','').rstrip('.')
    url=https_origin('https://'+host,private=True)
    existing=subprocess.run([exe,'serve','status','--json'],capture_output=True,timeout=15)
    try:
        existing_routes = json.loads(existing.stdout.decode('utf-8')) or {}
        has_routes = bool(existing_routes)
    except ValueError:
        raise RuntimeError('기존 Tailscale 경로를 읽지 못했습니다. 기존 연결을 보호하기 위해 중단합니다.')
    if any(bool(value) and str(key).endswith(':443') for key, value in existing_routes.get('AllowFunnel', {}).items()):
        raise RuntimeError('443 포트의 공개 Funnel이 이미 있습니다. 이 설정은 변경하지 않았습니다. '
                           '05_connect_private_fast.bat으로 별도 개인 8443 연결을 추가하세요.')
    print('\n개인 접속 주소:',url)
    print('연결 대상:',f'http://127.0.0.1:{cfg.port}')
    print('이 주소는 Tailscale에 연결된 기기에서만 열립니다. Funnel(인터넷 전체 공개)은 사용하지 않습니다.')
    print('HTTPS 활성화 시 기기 이름과 .ts.net 이름이 인증서 공개 기록에 남을 수 있습니다.')
    if has_routes:print('[주의] 기존 Tailscale Serve 설정이 있습니다. 이 기기의 HTTPS 443 / 경로를 beta 서버로 설정합니다.')
    if not agree('Tailscale Serve를 위 서버에 연결하시겠습니까?'):
        return 2
    # --bg는 종료 후에도 Serve 설정을 유지합니다. HTTPS 활성화가 필요하면 CLI 안내를 따릅니다.
    subprocess.run([exe,'serve','--bg','--https=443',f'http://127.0.0.1:{cfg.port}'],check=True)
    private_permissions(CONFIG)
    cfg.private_url=url;cfg.save(CONFIG)
    print('\n설정 저장 완료:',url)
    print('run_v8_beta.bat을 실행하세요. 이미 켜져 있다면 stop_v8_beta.bat으로 종료한 뒤 다시 실행하세요.')
    print('휴대폰: Tailscale 앱 설치 → 같은 네트워크 계정 로그인 → 연결 → 위 HTTPS 주소를 브라우저에서 열기')
    print('외부 확인: 휴대폰 Wi-Fi를 끄고 LTE/5G에서 로그인과 영상 재생을 테스트하세요.')
    print('PC 부팅 후에도 연결하려면 Tailscale의 Run unattended와 03_install_autostart.bat을 설정하세요.')
    return 0


def cloudflare_setup():
    cfg=ServerConfig.load(CONFIG)
    print('Cloudflare는 이 beta에서 URL 등록/자막/태그 관리용입니다. 영상·음원·대용량 전달은 차단합니다.')
    print('도메인, Cloudflare Tunnel, 본인 이메일만 허용하는 Access 정책은 먼저 Cloudflare 계정에서 설정해야 합니다.')
    print(f'공개 호스트의 서비스 주소: http://127.0.0.1:{cfg.port}')
    print('HTTP Host Header는 덮어쓰지 말고 원래 공개 호스트를 유지하세요. Cache Everything 규칙을 사용하지 마세요.')
    if not agree('계정에서 위 설정을 완료했고, 관리 주소를 추가하시겠습니까?'):return 2
    url=https_origin(input('관리용 HTTPS 주소: ').strip())
    if not url:raise ValueError('관리 주소가 비어 있습니다.')
    if url==cfg.private_url:raise ValueError('개인 접속 주소와 관리 주소는 달라야 합니다.')
    exe=find_cloudflared()
    if not exe:
        install_with_winget('Cloudflare.cloudflared');exe=find_cloudflared()
    if not exe:raise RuntimeError('설치 후 이 창을 다시 실행하세요. cloudflared를 찾지 못했습니다.')
    print('터널 토큰은 이 PC에만 입력하세요. ChatGPT나 로그 공유에 포함하지 마세요.')
    token=getpass.getpass('Tunnel 토큰 (화면에 표시되지 않음): ').strip()
    if not 30<=len(token)<=8192 or any(c.isspace() for c in token):
        raise ValueError('토큰 형식을 확인하세요. 전체 설치 명령이 아니라 토큰 값만 입력해야 합니다.')
    private_permissions(CONFIG)
    target=CONFIG/'tunnel.token';tmp=CONFIG/'tunnel.token.tmp'
    with tmp.open('w',encoding='utf-8') as f:
        os.chmod(tmp,0o600);f.write(token)
    os.replace(tmp,target)
    cfg.cloudflare_url=url;cfg.save(CONFIG)
    print('터널 토큰을 로컬 제한 폴더에 저장했습니다. 값은 로그에 남기지 않았습니다.')
    print('서버를 다시 시작하고 OPTIONAL_run_cloudflare.bat을 실행하세요.')
    print('자동 시작에도 포함하려면 03_install_autostart.bat을 다시 실행하세요.')
    return 0


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(errors='replace')
    parser=argparse.ArgumentParser();parser.add_argument('--cloudflare',action='store_true');args=parser.parse_args()
    try:raise SystemExit(cloudflare_setup() if args.cloudflare else tailscale_setup())
    except (Exception,KeyboardInterrupt) as exc:
        # 토큰을 비롯한 stdin은 예외에 포함하지 않습니다.
        print('\n[설정 중단]',str(exc) if not isinstance(exc,KeyboardInterrupt) else '사용자 취소')
        raise SystemExit(1)
