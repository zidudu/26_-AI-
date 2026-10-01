"""V9.5 AI provider wiring; legacy prompts, validators and engine files stay intact."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re

from v754.core import V7Error, read_json, write_json
from v754.analysis_api import Analyzer, response_usage

SETTINGS_FILE = 'config_ai_v95.json'
DEFAULTS = {'version': '9.5.0', 'provider': 'openai', 'codex': {
    'model': 'gpt-6-astra', 'reasoning_effort': 'medium',
    'timeout_seconds': 120, 'max_parallel': 1, 'command': 'codex',
    'credentials_store': 'auto', 'acknowledge_cli_differences': False}}


class ProviderError(V7Error):
    def __init__(self, code, message, *, stop=True, uncertain=False, diagnostics=None):
        super().__init__(code, message)
        self.stop = stop
        self.uncertain = uncertain
        self.diagnostics = deepcopy(diagnostics or {})


def map_codex_error(exc):
    return ProviderError(exc.code, str(exc), stop=exc.stop,
                         uncertain=exc.uncertain, diagnostics=exc.diagnostics)


def validate_settings(data):
    cfg = deepcopy(data)
    try:
        if not isinstance(cfg, dict) or set(cfg) != set(DEFAULTS):
            raise ValueError('version/provider/codex 항목이 필요합니다')
        if cfg['version'] != '9.5.0' or cfg['provider'] not in ('openai', 'codex'):
            raise ValueError('provider는 openai 또는 codex')
        opts = cfg['codex']
        if not isinstance(opts, dict) or set(opts) != set(DEFAULTS['codex']):
            raise ValueError('Codex 설정 항목')
        if not isinstance(opts['model'], str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,100}', opts['model']):
            raise ValueError('Codex 모델 ID')
        if opts['reasoning_effort'] not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            raise ValueError('Codex 추론 수준')
        if type(opts['timeout_seconds']) is not int or not 10 <= opts['timeout_seconds'] <= 300:
            raise ValueError('Codex 제한 시간은 10~300초 정수')
        if type(opts['max_parallel']) is not int or opts['max_parallel'] not in (1, 2, 3):
            raise ValueError('Codex 동시 실행 수는 1·2·3')
        if (not isinstance(opts['command'], str) or not opts['command'].strip()
                or any(c in opts['command'] for c in '\r\n\x00')):
            raise ValueError('Codex 실행 파일 경로')
        if opts['credentials_store'] not in ('auto', 'file', 'keyring'):
            raise ValueError('Codex 로그인 저장 방식')
        if type(opts['acknowledge_cli_differences']) is not bool:
            raise ValueError('CLI 차이 확인 값은 true/false')
        if cfg['provider'] == 'codex' and not opts['acknowledge_cli_differences']:
            raise ValueError('44_ai_provider_v95.bat에서 Codex 방식을 선택하세요')
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError('AI_CONFIG_ERROR', f'{SETTINGS_FILE}을 확인하세요: {exc}') from exc
    return cfg


def load_settings(root):
    path = Path(root) / SETTINGS_FILE
    return validate_settings(read_json(path) if path.exists() else DEFAULTS)


def save_settings(root, settings):
    cfg = validate_settings(settings)
    path = Path(root) / SETTINGS_FILE
    if path.exists():
        backup = path.with_name('config_ai_v95_before_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
        backup.write_bytes(path.read_bytes())
    write_json(path, cfg)
    return cfg


def settings_for_record(record):
    # Pre-V9.5 runs used the API. A retry must never silently switch provider.
    return validate_settings(record.get('ai_settings', DEFAULTS))


def adapter_module():
    try:
        from v9 import codex_adapter
    except ImportError as exc:
        raise ProviderError('CODEX_DEPENDENCY_MISSING',
            'Codex 추가 패키지가 필요합니다. 46_install_codex_dependency_v95.bat를 실행하세요.',
            diagnostics={'error_type': type(exc).__name__}) from exc
    return codex_adapter


def codex_options(settings, *, audit_log=None):
    module = adapter_module()
    return module.CodexOptions(**validate_settings(settings)['codex'], audit_log=audit_log)


def check_codex(settings):
    module = adapter_module()
    factory = module.CodexAnalyzerFactory(codex_options(settings), error_mapper=map_codex_error)
    try:
        return factory.preflight()
    except module.CodexAdapterError as exc:
        raise map_codex_error(exc) from exc
    finally:
        factory.close()


@contextmanager
def analysis_binding(root, settings, folder):
    """Prepare cache identity before any cache lookup; share one factory for repairs."""
    from v754.analysis_config import load_analysis_config
    from v8.analysis_prompts import make_spec
    settings = validate_settings(settings)
    spec = make_spec(load_analysis_config(Path(root)))
    if settings['provider'] == 'openai':
        yield spec, Analyzer
        return
    module = adapter_module()
    options = codex_options(settings, audit_log=Path(folder) / 'codex_usage.jsonl')
    factory = module.CodexAnalyzerFactory(options, error_mapper=map_codex_error)
    try:
        preflight = factory.preflight()
        spec = module.prepare_codex_spec(spec, options, cli_version=preflight['cli_version'])
        yield spec, factory
    except module.CodexAdapterError as exc:
        raise map_codex_error(exc) from exc
    finally:
        factory.close()


def observed_usage(response):
    usage = response_usage(response)
    raw = response.get('usage') or {}
    for name in ('input_tokens_details', 'output_tokens_details'):
        if isinstance(raw.get(name), dict):
            usage[name] = deepcopy(raw[name])
    return usage


def update_usage(report):
    rows = report.get('analysis_results', []) + report.get('summary_repairs', [])
    report['api_usage'] = [r['usage'] for r in rows if r.get('mode') == 'api' and r.get('usage')]
    report['codex_usage'] = [r['usage'] for r in rows if r.get('mode') == 'codex' and r.get('usage')]
    report['ai_usage'] = report['api_usage'] + report['codex_usage']
    report['ai_calls'] = report.get('api_calls', 0) + report.get('codex_calls', 0)


def show_settings(settings, *, requested_concurrency=None):
    cfg = validate_settings(settings)
    if cfg['provider'] == 'openai':
        print('[AI 방식] OpenAI API / 기존 모델·추론 설정', flush=True)
    else:
        opts = cfg['codex']
        limit = opts['max_parallel'] if requested_concurrency is None else min(opts['max_parallel'], requested_concurrency)
        print(f"[AI 방식] Codex CLI / {opts['model']} / {opts['reasoning_effort']} / "
              f"{opts['timeout_seconds']}초 / 실제 동시 실행 최대 {limit}개", flush=True)
