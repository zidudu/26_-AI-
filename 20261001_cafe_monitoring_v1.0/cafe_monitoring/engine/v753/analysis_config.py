"""V6 모델 설정을 이어받는 독립 분석 설정."""
import os
import re
from .core import V7Error, read_json

AI_KEYS = {'model', 'reasoning_effort', 'max_output_tokens', 'timeout_seconds',
           'max_body_chars', 'focus_topics', 'complaint_definition'}
DEFAULT_DEFINITION = (
    '원문에 드러난 불만의 대상·증상을 짧게 정리합니다. 질문에도 실제 증상 호소가 있으면 포함합니다. '
    '회사 공식 분류가 아닌 검토용 증상 분류입니다. 홍보의 일반적인 설명은 실제 소비자 불만으로 분류하지 않습니다.'
)


def load_analysis_config(root):
    old_path = root / 'config_v6.json'
    old = read_json(old_path) if old_path.is_file() else {}
    custom_path = root / 'config_v753.json'
    custom = read_json(custom_path) if custom_path.is_file() else {}
    if not isinstance(old, dict) or not isinstance(custom, dict):
        raise V7Error('CONFIG_ERROR', '설정 파일은 JSON 객체여야 합니다.')
    cfg = {k: old[k] for k in AI_KEYS if k in old}
    cfg.setdefault('reasoning_effort', None)
    cfg.setdefault('max_output_tokens', 6000)
    cfg.setdefault('timeout_seconds', 180)
    cfg.setdefault('max_body_chars', 100000)
    cfg.setdefault('focus_topics', ['자동차 불량·고장·경고·정비 사례'])
    cfg['complaint_definition'] = DEFAULT_DEFINITION
    cfg.update({k: v for k, v in custom.items() if k in AI_KEYS})
    if not isinstance(cfg.get('model'), str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,100}', cfg['model']):
        raise V7Error('MODEL_REQUIRED', 'config_v6.json 또는 config_v753.json에 기존 사용 모델을 지정하세요.')
    if cfg['reasoning_effort'] not in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
        raise V7Error('CONFIG_ERROR', 'reasoning_effort 설정을 확인하세요.')
    for k, lo, hi in [('max_output_tokens', 2000, 16000), ('timeout_seconds', 10, 300), ('max_body_chars', 1000, 100000)]:
        if type(cfg[k]) is not int or not lo <= cfg[k] <= hi:
            raise V7Error('CONFIG_ERROR', f'{k}는 {lo}~{hi} 정수여야 합니다.')
    if not isinstance(cfg['focus_topics'], list) or not 1 <= len(cfg['focus_topics']) <= 30 or any(
        not isinstance(v, str) or not v.strip() or len(v) > 250 for v in cfg['focus_topics']):
        raise V7Error('CONFIG_ERROR', 'focus_topics는 비어 있지 않은 문자열 목록이어야 합니다.')
    if not isinstance(cfg['complaint_definition'], str) or not 10 <= len(cfg['complaint_definition'].strip()) <= 2000:
        raise V7Error('CONFIG_ERROR', 'complaint_definition은 10~2000자 문자열이어야 합니다.')
    return cfg


def load_api_key():
    value = os.environ.get('OPENAI_API_KEY', '').strip()
    if not value and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = str(winreg.QueryValueEx(key, 'OPENAI_API_KEY')[0]).strip()
        except OSError:
            pass
    return value
