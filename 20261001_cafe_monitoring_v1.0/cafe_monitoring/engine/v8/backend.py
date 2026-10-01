"""V7.5.4.1 수집/분석/PPT 기능을 그대로 호출하는 어댑터입니다."""
from pathlib import Path
import hashlib
from .configuration import V8Error, read_json, write_json


def verify_base(root):
    manifest = read_json(Path(__file__).with_name("base_manifest.json"))
    changed = [name for name, expected in manifest.items()
               if not (root / name).is_file()
               or hashlib.sha256((root / name).read_bytes()).hexdigest() != expected]
    if changed:
        raise V8Error("BASE_VERSION_MISMATCH", "검증한 V7.5.4.1 코드와 다릅니다. 기준 패치를 먼저 적용하세요: " + ", ".join(changed[:3]))


class Backend:
    def __init__(self, root):
        self.root = Path(root).resolve()
        verify_base(self.root)
        from v754.core import config
        from v754.period_config import load_period_config
        from v754.refresh import browser_config
        self.cfg = config(self.root)
        self.cfg["project_root"] = self.root
        self.legacy_out = self.cfg["out_root"]
        self.cfg["out_root"] = self.root / "output_v8"
        if (self.legacy_out == self.cfg["out_root"] or self.legacy_out in self.cfg["out_root"].parents
                or self.cfg["out_root"] in self.legacy_out.parents):
            raise V8Error("OUTPUT_OVERLAP", "V7.5.4와 V8 출력 폴더를 분리하세요.")
        self.settings = load_period_config(self.root)
        self.webcfg = browser_config(self.root, self.cfg)
        self.cafe_id = self.webcfg["cafe_id"]
        self.words = self.settings["keywords"]

    def check(self):
        from v754.analysis_config import load_analysis_config, load_api_key
        from v754.powerpoint import check_office
        from v754.api_transport import certificate_policy, windows_ssl_context
        import playwright.sync_api
        import openai
        check_office()
        ai = load_analysis_config(self.root)
        if not load_api_key():
            raise V8Error("API_KEY_REQUIRED", "기존 OPENAI_API_KEY 설정을 확인하세요.")
        if certificate_policy() == "windows_system_truststore":
            windows_ssl_context()
        return {"cafe_id": self.cafe_id, "keywords": self.words, "model": ai["model"],
                "browser": self.webcfg["browser"]}

    def collect(self, folder, start, end):
        from v754.main_v754 import report_base, timed
        from v754.period import Window
        from v8.period_collect import collect_with_browser
        window = Window(start, end)
        report = report_base("period_collection")
        report.update(window=window.record(), keywords=self.words, cafe_id=self.cafe_id,
                      search_scope="title", search_board="all", search_sort="latest")
        checkpoint = lambda: write_json(folder / "summary.json", report)
        checkpoint()
        try:
            with timed(report, "collection", checkpoint):
                collect_with_browser(self.cfg, self.settings, self.webcfg, folder,
                                     report, checkpoint, window, self.words)
            if not report["collection_complete"]:
                raise V8Error("COLLECTION_INCOMPLETE", "검색·작성 시각 확인이 끝나지 않았습니다. 이번 구간은 완료 처리하지 않습니다.")
            report["status"] = "collected" if report["items"] else "collected_empty"
            checkpoint()
            return folder
        except BaseException:
            report["status"] = "failed"
            checkpoint()
            raise

    def collection_ok(self, folder, start, end):
        from v754.period_sources import load_collection
        data, _ = load_collection(folder)
        return (data["cafe_id"] == self.cafe_id and set(data["keywords"]) == set(self.words)
                and data["window"]["start"] == start.isoformat() and data["window"]["end"] == end.isoformat())

    def export(self, collection, folder):
        from v754.main_v754 import report_base
        from v8.exporting import export_items
        from v754.period_sources import load_collection
        data, items = load_collection(collection)
        report = report_base("saved_collection_export")
        report.update(collection_from=str(collection), collection_complete=True,
                      range_search_complete=True, articles_verified_complete=True,
                      window=data["window"], keywords=data["keywords"], cafe_id=data["cafe_id"],
                      selected_articles=len(items))
        checkpoint = lambda: write_json(folder / "summary.json", report)
        checkpoint()
        try:
            code = export_items(items, self.cfg, folder, report, checkpoint)
            return report, code
        except BaseException:
            report["status"] = "failed"
            checkpoint()
            raise
