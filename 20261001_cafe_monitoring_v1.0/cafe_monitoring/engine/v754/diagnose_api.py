"""GET /v1/models 1회로 연결을 확인합니다. Responses API, 원문 전송, AI 분석은 하지 않습니다."""
from datetime import datetime
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v754.core import config, read_json, write_json, V7Error
from v754.analysis_config import load_analysis_config, load_api_key
from v754.api_diagnostics import APIError, BUILD, run_sdk_call
from v754.api_transport import certificate_policy, create_openai_client, TLS_BUILD


def probe_models(key, client_factory=None):
    if client_factory is None:
        client = create_openai_client(key, 15)
    else:
        client = client_factory(api_key=key, base_url='https://api.openai.com/v1',
                                timeout=15, max_retries=0)
    started = time.monotonic()
    with client:
        try:
            run_sdk_call(lambda: client.models.list(), 15)
            return {'status': 'pass', 'code': 'API_CONNECTION_OK',
                    'elapsed_seconds': round(time.monotonic() - started, 3),
                    'meaning': '현재 키로 모델 목록 요청 성공. 이전 분석 실패 원인이나 Responses API 성공을 증명하지는 않습니다.'}
        except APIError as exc:
            return {'status': 'fail', 'code': exc.code, 'message': str(exc), 'diagnostics': exc.diagnostics}


def latest_failure(out_root):
    paths = sorted(out_root.glob('*/summary.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            report = read_json(path)
            if report.get('status') not in ('failed', 'partial', 'interrupted') or not report.get('analysis_results'):
                continue
            spec_path = path.parent / 'analysis_spec.json'
            spec = read_json(spec_path) if spec_path.is_file() else {}
            return {'run_id': path.parent.name, 'analysis_stop_code': report.get('analysis_stop_code'),
                    'recorded_timeout_seconds': spec.get('timeout_seconds'),
                    'results': [{k: row.get(k) for k in ('article_id', 'status', 'code', 'diagnostics')}
                                for row in report['analysis_results']],
                    'note': '기존 API_RESULT_UNKNOWN 기록만으로 timeout과 connection을 복원할 수 없습니다.'}
        except (V7Error, TypeError, AttributeError):
            continue
    return None


def main():
    print('[V7.5.4] API 연결 진단 / AI 분석 호출 0 / 모델 목록 GET 1회 / 자동 재시도 없음', flush=True)
    try:
        cfg = config(ROOT)
        analysis_cfg = load_analysis_config(ROOT)
        key = load_api_key()
        if not key:
            raise V7Error('API_KEY_MISSING', '기존 02_setup_key_v6.bat에서 키를 설정하세요.')
        packages = {}
        for name in ('openai', 'httpx', 'truststore'):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = 'not_installed'
        report = {'build': BUILD, 'created_at': datetime.now().astimezone().isoformat(),
                  'tls_build': TLS_BUILD, 'certificate_policy': certificate_policy(),
                  'python': platform.python_version(), 'platform': platform.system(), 'packages': packages,
                  'model': analysis_cfg['model'], 'analysis_timeout_seconds': analysis_cfg['timeout_seconds'],
                  'inference_calls': 0, 'request': 'GET https://api.openai.com/v1/models',
                  'environment_flags': {name: bool(os.environ.get(name)) for name in (
                      'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR')},
                  'prior_failure': latest_failure(cfg['out_root'])}
        print(f"분석 모델: {report['model']} / 기존 분석 대기 설정: {report['analysis_timeout_seconds']}초", flush=True)
        print(f"인증서 신뢰 설정: {report['certificate_policy']} / {TLS_BUILD}", flush=True)
        if report['certificate_policy'] == 'explicit_ca_environment':
            print('SSL_CERT_FILE 또는 SSL_CERT_DIR이 설정되어 있어 해당 CA 설정을 우선합니다.', flush=True)
        report['probe'] = probe_models(key)
        cfg['out_root'].mkdir(parents=True, exist_ok=True)
        path = cfg['out_root'] / ('api_diagnostic_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
        write_json(path, report)
        print(json.dumps(report['probe'], ensure_ascii=False, indent=2))
        print(f'[진단 파일] {path}')
        print('이 출력 또는 진단 파일을 보내 주세요. 이 실행은 게시글 분석·PPT 생성을 하지 않았습니다.')
        return 0 if report['probe']['status'] == 'pass' else 2
    except V7Error as exc:
        print(f'[진단 중단] {exc.code} / {exc}')
        return 1
    except Exception as exc:
        # 예외 문자열에 키·프록시 주소가 섞일 수 있어 형식명만 표시합니다.
        print(f'[진단 도구 오류] {type(exc).__name__} / 패키지 설치와 파일 복사 위치를 확인하세요.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
