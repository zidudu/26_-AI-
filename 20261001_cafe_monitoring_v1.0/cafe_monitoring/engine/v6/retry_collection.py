"""V6 분석에 넣을 본문을 기존 V5 이력에서 재수집합니다. 새 검색은 하지 않습니다."""
from pathlib import Path
import subprocess
import sys


def retry_command(root, keyword):
    root = Path(root).resolve()
    python = root / ".venv" / "Scripts" / "python.exe"
    script = root / "v5" / "main_v5.py"
    return [str(python), str(script), "retry", "--keywords", keyword]


def main():
    root = Path(__file__).resolve().parent.parent
    try:
        print("[V6 본문 재수집] 오류 이력의 글을 다시 읽어 output_v5에 저장합니다.")
        keyword = input("실패 당시 검색어 (Enter: 확인, 취소: /q): ").strip() or "확인"
        if keyword.lower() == "/q":
            return 0
        command = retry_command(root, keyword)
        if not Path(command[0]).is_file() or not Path(command[1]).is_file():
            print("[V5_NOT_FOUND] 기존 프로그램의 v5 폴더와 Python 환경이 필요합니다. 수정본을 기존 프로그램 폴더에 넣으세요.")
            return 1
        code = subprocess.call(command, cwd=root)
        print("\n재수집에 성공한 글은 V6의 입력 대상에 포함됩니다. 03_analyze_v6.bat로 분석하세요.")
        return code
    except (KeyboardInterrupt, EOFError):
        print("\n재수집을 중단했습니다. 저장된 V5 이력은 유지됩니다.")
        return 130
    except OSError:
        print("[COLLECTOR_START_FAILED] 수집기를 실행하지 못했습니다. v5 폴더와 Python 설치를 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
