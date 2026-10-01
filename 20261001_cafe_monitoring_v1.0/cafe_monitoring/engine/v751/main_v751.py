"""V7.5.1 실행 진입점. 기존 V5/V6 데이터는 읽기만 합니다."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v751 import VERSION
from v751.core import V7Error, config, load_articles, run_choices, write_json


@contextmanager
def export_lock(out_root):
    # 동일 출력 폴더에서 V7.5.1을 동시에 생성하지 않게 하는 OS 잠금입니다.
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "v751_export.lock"
    with path.open("a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise V7Error("ALREADY_RUNNING", "다른 V7.5.1 생성 작업이 실행 중입니다.") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def choose_run(cfg, requested, prompt):
    choices = run_choices(cfg)
    if not choices:
        raise V7Error("NO_V6_DRAFTS", "V6 초안이 없습니다. V6 분석 또는 13_convert_saved_v6.bat 결과를 확인하세요.")
    if requested:
        selected = next((p for p in choices if p.name == requested), None)
        if selected is None:
            raise V7Error("RUN_NOT_FOUND", "선택한 V6 실행의 초안 폴더가 없습니다.")
        return selected
    if not prompt:
        raise V7Error("RUN_REQUIRED", "--run 실행ID 또는 --prompt를 지정하세요.")
    print("\n사용 가능한 V6 실행 (최근 순)")
    for i, p in enumerate(choices[:20], 1):
        print(f" {i:2}. {p.name}")
    answer = input("번호 또는 실행 ID (Enter: 1, 취소: /q): ").strip()
    if answer.lower() == "/q":
        raise V7Error("CANCELLED", "취소했습니다.")
    if not answer:
        return choices[0]
    if answer.isdigit() and 1 <= int(answer) <= min(20, len(choices)):
        return choices[int(answer)-1]
    return choose_run(cfg, answer, False)


def do_export(args, cfg):
    from v751.powerpoint import check_office, render
    check_office()
    source_folder = choose_run(cfg, args.run, args.prompt)
    articles, errors, basis = load_articles(source_folder, cfg, ROOT)
    if not articles:
        for e in errors:
            print(f"[제외] {e['code']} / {e['file']}")
        raise V7Error("NO_VALID_DRAFTS", "변환할 유효한 V6 초안이 없습니다.")
    print(f"[V6 원본] 유효 {len(articles)}개 / 제외 {len(errors)}개")
    count = args.count
    if args.command == "test":
        count = 1
    elif count is None and args.prompt:
        answer = input(f"PPT로 만들 글 수 1~{len(articles)} (Enter: 전체, 취소: /q): ").strip()
        if answer.lower() == "/q":
            raise V7Error("CANCELLED", "취소했습니다.")
        if answer and not answer.isdigit():
            raise V7Error("COUNT_INVALID", "글 수는 정수로 입력하세요.")
        count = int(answer) if answer else len(articles)
    count = len(articles) if count is None else count
    if count < 1 or count > len(articles):
        raise V7Error("COUNT_INVALID", f"글 수는 1~{len(articles)}입니다.")
    articles = articles[:count]
    print(f"[이번 선정] {len(articles)}개 / AI 호출 0 / 댓글 캡처 수집 없음")
    print(f"[배치] 캡처 분할 없음 / 슬라이드당 최대 {min(cfg['capture_columns'], 2)}장 / 1장은 가운데 배치")
    for a in articles:
        print(f" {a['id']} / 캡처 {len(a['captures'])}장 / {a['title']}")
    with export_lock(cfg["out_root"]):
        tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        prefix = "test_" if args.command == "test" else ""
        folder = cfg["out_root"] / (prefix + tag)
        folder.mkdir(exist_ok=False)
        report = {
            "version": VERSION, "v6_run_id": source_folder.name, "selection_basis": basis,
            "status": "running", "stage": "PREPARE", "created_at": datetime.now().astimezone().isoformat(),
            "selected_articles": len(articles), "api_calls": 0, "source_errors": errors,
            "capture_failures": [], "missing_capture_articles": [], "warnings": [],
            "visual_check": "not_run", "reopened_structure_verified": False,
            "layout": "a4_landscape_whole_capture", "original_company_template_used": False,
            "items": articles,
        }
        report_path = folder / "summary.json"
        checkpoint = lambda: write_json(report_path, report)
        checkpoint()
        print(f"[출력 폴더] {folder}", flush=True)
        try:
            render(articles, cfg, folder, report, checkpoint)
            partial = bool(errors or report["capture_failures"] or report["missing_capture_articles"] or
                           report["warnings"] or any(a["capture_problem"] for a in articles))
            report["status"] = "partial" if partial else "completed"
            checkpoint()
            print(f"\n[PPT 저장] {report['pptx']}")
            print(f"[다시 열기 확인] {report['slides']}장 / 그림·편집 가능한 표 개체 확인")
            print("[직접 확인] 열린 PPT의 이미지 표시, 분할 경계, 글자 크기와 요약을 확인하세요.")
            print("저장 성공은 직원 검토 완료가 아닙니다. 기존 PPT의 직원 수정값은 새 생성본에 자동 반영되지 않습니다.")
            if partial:
                print("[일부 확인 필요] 누락·삽입 오류·검토 사항은 summary.json에 기록했습니다.")
            print(f"[결과 기록] {report_path}")
            return 2 if partial else 0
        except BaseException as exc:
            report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            report["error"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
            # 원문·변수 값은 덤프하지 않고 V7 내부의 실패 코드 위치만 기록합니다.
            frames = [
                {"file": Path(f.filename).name, "line": f.lineno,
                 "function": f.name, "statement": f.line}
                for f in traceback.extract_tb(exc.__traceback__)
                if Path(f.filename).resolve().parent == ROOT / "v751"
            ]
            report["error"]["frames"] = frames
            checkpoint()
            print(f"[실패 단계] {report['stage']}")
            for f in frames:
                print(f"[오류 위치] {f['file']}:{f['line']} / {f['function']} / {f['statement']}")
            print(f"[결과 기록] {report_path}")
            raise


def main():
    parser = argparse.ArgumentParser(description="V6 초안을 PowerPoint 보고서로 변환합니다. AI 호출 없음.")
    parser.add_argument("command", choices=("check", "preview", "test", "export"))
    parser.add_argument("--run", help="V6 실행 ID")
    parser.add_argument("--count", type=int, help="작성일 최신 순으로 선정할 게시글 수")
    parser.add_argument("--columns", type=int, choices=(1, 2, 3), help="슬라이드당 최대 캡처 수 (기본 2, 이전 값 3은 2로 적용)")
    parser.add_argument("--prompt", action="store_true")
    args = parser.parse_args()
    print(f"[V{VERSION}] {args.command}")
    try:
        cfg = config(ROOT)
        cfg["project_root"] = ROOT
        if args.columns is not None:
            cfg["capture_columns"] = args.columns
        if cfg["capture_columns"] == 3:
            print("[배치 안내] 이전 3열 설정은 이번 수정본에서 최대 2장으로 적용합니다.")
        if args.command == "check":
            from v751.powerpoint import check_office
            print(check_office())
            print(f"V6 출력: {cfg['v6_root']}")
            print(f"초안 실행 폴더: {len(run_choices(cfg))}개")
            print(f"V7.5.1 출력: {cfg['out_root']}")
            print(f"캡처 전체 맞춤 / 슬라이드당 최대 {min(cfg['capture_columns'], 2)}장 / 분할 없음")
            print("CHECK_OK: 설정과 PowerPoint 등록 확인. 실제 삽입은 02_test_ppt_v751.bat로 확인하세요.")
            return 0
        if args.command == "preview":
            folder = choose_run(cfg, args.run, args.prompt)
            articles, errors, basis = load_articles(folder, cfg, ROOT)
            print(f"선정 기준: {basis} / 유효 {len(articles)}개 / 제외 {len(errors)}개")
            for a in articles:
                mode = "원문 표시" if a["source_mode"] else "AI 요약"
                print(f"{a['id']} / {mode} / 캡처 {len(a['captures'])}장 / {a['title']}")
                from v751.powerpoint import plan_article
                try:
                    preview = dict(a, captures=[dict(c, office_width=c.get('width'), office_height=c.get('height'))
                                                for c in a['captures']])
                    print(f"  V5 크기 기록 기준 예상 {len(plan_article(preview, cfg))}장 (Office 실측 전)")
                except V7Error as exc:
                    print(f"  배치 예측 불가: {exc.code} / {exc}")
                for note in a["capture_notes"]:
                    print("  " + note)
            for e in errors:
                print(f"[제외] {e['code']} / {e['file']}")
            return 2 if errors else 0
        return do_export(args, cfg)
    except (KeyboardInterrupt, EOFError):
        print("취소했습니다. 기존 파일은 보존됩니다.")
        return 130
    except Exception as exc:
        print(f"[중단: {getattr(exc, 'code', type(exc).__name__)}] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
