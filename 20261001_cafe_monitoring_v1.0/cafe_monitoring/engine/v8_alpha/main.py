"""기존 alpha BAT를 위한 V8.2 호환 진입점."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from v8.period_run import main, run_test, prompt_test_mail
if __name__ == "__main__":
    raise SystemExit(main())
