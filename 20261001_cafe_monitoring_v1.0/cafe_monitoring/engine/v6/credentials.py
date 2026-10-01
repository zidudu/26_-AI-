"""키는 프로세스 환경 변수 또는 Windows 사용자 환경 변수에서만 읽습니다."""
import getpass
import os
import sys
from .common import V6Error


def load_api_key():
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if not value and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = str(winreg.QueryValueEx(key, "OPENAI_API_KEY")[0]).strip()
        except OSError:
            pass
    return value


def setup_key():
    if os.name != "nt":
        raise V6Error("WINDOWS_ONLY", "이 설정 도구는 Windows용입니다. 다른 OS에서는 OPENAI_API_KEY 환경 변수를 설정하세요.")
    import winreg
    if not sys.stdin.isatty():
        raise V6Error("INTERACTIVE_INPUT_REQUIRED", "02_setup_key_v6.bat를 직접 열어 키를 입력하세요. 리디렉션된 입력에서는 키를 받지 않습니다.")
    if load_api_key():
        choice = input("기존 API 키 설정이 있습니다. 교체하려면 Y, 유지하려면 Enter: ").strip().lower()
        if choice != "y":
            print("[키 유지] 기존 설정을 사용합니다.")
            return
    value = getpass.getpass("OpenAI API 키 붙여넣기 (입력 내용은 표시되지 않음, 취소: Enter): ").strip()
    if not value:
        print("취소했습니다.")
        return
    if not value.startswith("sk-") or len(value) < 20 or any(c.isspace() for c in value):
        raise V6Error("INVALID_KEY_FORMAT", "키 형식을 확인하세요. 앞뒤 따옴표 없이 붙여넣으세요.")
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            winreg.SetValueEx(key, "OPENAI_API_KEY", 0, winreg.REG_SZ, value)
        os.environ["OPENAI_API_KEY"] = value
    except OSError:
        raise V6Error("KEY_SAVE_FAILED", "현재 Windows 사용자의 환경 변수에 저장하지 못했습니다.") from None
    # 새 탐색기 프로세스에 변경을 알립니다. V6는 레지스트리를 직접 읽는 대체 경로도 있습니다.
    try:
        import ctypes
        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(65535, 26, 0, "Environment", 2, 2000, ctypes.byref(result))
    except (OSError, AttributeError, TypeError):
        pass
    print("[키 설정 완료] Windows 사용자 환경 변수 OPENAI_API_KEY에 저장했습니다. 키 값은 출력하지 않습니다.")
