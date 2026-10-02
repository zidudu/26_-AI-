"""Add private HTTPS :8443 without resetting or replacing the existing Funnel :443.

Executed only by the user on the server PC; no automatic public exposure.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from configure_beta import CONFIG, tailscale_exe, private_permissions
from connect_remote import agree
from yme.remote_security import ServerConfig, https_origin, atomic_private_json


def run_json(exe, *args):
    result = subprocess.run([exe, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-1600:])
    value = json.loads(result.stdout.decode('utf-8-sig'))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError('Tailscale 상태 JSON 형식이 예상과 다릅니다.')
    return value


def public_443(config: dict, host: str):
    # Include all public 443 entries, not only the current DNS name.
    return {
        'tcp': config.get('TCP', {}).get('443'),
        'web': {k: v for k, v in config.get('Web', {}).items() if k.endswith(':443')},
        'funnel': {k: v for k, v in config.get('AllowFunnel', {}).items() if k.endswith(':443')},
    }


def private_command(exe: str, port: int):
    return [exe, 'serve', '--bg', '--https=8443', f'http://127.0.0.1:{port}']


def main(remove=False):
    cfg = ServerConfig.load(CONFIG)
    exe = tailscale_exe()
    if not exe:
        raise RuntimeError('Tailscale이 설치된 서버 PC에서 실행해 주세요. 기존 Funnel 설치를 그대로 사용합니다.')
    status = run_json(exe, 'status', '--json')
    if status.get('BackendState') != 'Running':
        raise RuntimeError('PC의 Tailscale을 연결한 뒤 다시 실행하세요.')
    host = status.get('Self', {}).get('DNSName', '').rstrip('.')
    fast = https_origin('https://' + host + ':8443', fast=True)
    existing = run_json(exe, 'serve', 'status', '--json')
    if remove:
        if not agree('개인 :8443 경로만 해제할까요? 기존 :443 경로는 변경하지 않습니다.'):
            return 2
        subprocess.run([exe, 'serve', '--https=8443', 'off'], check=True)
        cfg.fast_url = ''
        cfg.save(CONFIG)
        print('개인 경로 해제 완료. V8 서버를 재시작해 주세요.')
        return 0
    print('\n기존 443 Funnel/Serve는 그대로 둡니다. reset 또는 funnel 명령을 실행하지 않습니다.')
    print('추가할 개인 주소:', fast)
    print('대상:', f'http://127.0.0.1:{cfg.port}')
    print('휴대폰에서 Tailscale 연결이 필요합니다. 가능한 경우 직접 연결되지만 속도 보장은 아닙니다.')
    if existing.get('TCP', {}).get('8443'):
        print('[주의] 8443에 기존 설정이 있습니다. 계속하면 이 포트의 / 경로를 V8에 연결합니다.')
    if not agree('위 개인 경로를 추가할까요?'):
        return 2
    private_permissions(CONFIG)
    backup = CONFIG / ('tailscale-before-fast-' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json')
    atomic_private_json(backup, existing)
    subprocess.run(private_command(exe, cfg.port), check=True)
    after = run_json(exe, 'serve', 'status', '--json')
    if public_443(existing, host) != public_443(after, host):
        raise RuntimeError('443 설정이 예상과 달라졌습니다. 자동 reset은 하지 않았습니다. tailscale serve status와 config의 설정 백업을 확인하세요.')
    if after.get('AllowFunnel', {}).get(host + ':8443'):
        raise RuntimeError('8443이 공개 Funnel로 남아 있습니다. tailscale serve status를 확인하세요. 개인 주소 설정은 저장하지 않았습니다.')
    cfg.fast_url = fast
    cfg.save(CONFIG)
    print('\n개인 경로 설정 완료:', fast)
    print('기존 443 설정 보존 확인 완료. 이미 열려 있는 공개 주소는 그대로 사용할 수 있습니다.')
    print('stop_v8_beta.bat → run_v8_beta.bat 순서로 서버를 다시 시작하세요.')
    print('휴대폰: Tailscale 앱 연결 → 위 :8443 주소 → 라이브러리 계정 로그인')
    print('공개 443 주소와 개인 8443 주소의 로그인 세션은 분리됩니다.')
    print('PC에서 tailscale status를 보면 active peer가 direct인지 relay인지 확인할 수 있습니다.')
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    try:
        raise SystemExit(main(args.remove))
    except (Exception, KeyboardInterrupt) as exc:
        print('[설정 중단]', str(exc) or '사용자 취소')
        raise SystemExit(1)
