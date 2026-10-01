"""Select the provider explicitly; checking the CLI never invokes a model."""
from v8.main import ask
from v9.ai_provider import load_settings, save_settings, check_codex, show_settings, ProviderError


def setup(root):
    settings = load_settings(root)
    show_settings(settings)
    print('1: Codex CLI (ChatGPT 로그인) / 2: 기존 OpenAI API')
    print('Codex는 게시글마다 실행합니다. 출력 토큰 상한은 생성 후 확인하며 이미 사용한 토큰을 취소하지 않습니다.')
    print('Codex의 임시 세션 설정은 API store=False와 동일한 보관 정책을 보장하지 않습니다.')
    choice = ask('사용할 AI 방식', '1' if settings['provider'] == 'codex' else '2')
    if choice not in ('1', '2'):
        raise ProviderError('AI_CONFIG_ERROR', '1 또는 2를 입력하세요.')
    settings['provider'] = 'codex' if choice == '1' else 'openai'
    if choice == '1':
        opts = settings['codex']
        opts['model'] = ask('Codex 모델', opts['model'])
        opts['reasoning_effort'] = ask('추론 수준', opts['reasoning_effort'])
        value = ask('Codex 동시 실행 최대 수 1·2·3 / 첫 시험은 1', str(opts['max_parallel']))
        if value not in ('1', '2', '3'):
            raise ProviderError('AI_CONFIG_ERROR', '동시 실행 수는 1·2·3입니다.')
        opts['max_parallel'] = int(value)
        opts['acknowledge_cli_differences'] = True
        status = check_codex(settings)
        print('[Codex 사전 확인]', status)
        print('모델 호출은 하지 않았습니다. 로그인 계정 소유자는 Codex 화면에서 확인하세요.')
    save_settings(root, settings)
    show_settings(settings)
    print('[AI 설정 저장] 새 실행부터 적용됩니다. 진행 중·재시도 실행은 기록된 AI 설정을 유지합니다.')
    if choice == '1':
        print('[다음 단계] 45_test_codex_saved_v95.bat / 저장된 게시글 1건 분석 시험')

