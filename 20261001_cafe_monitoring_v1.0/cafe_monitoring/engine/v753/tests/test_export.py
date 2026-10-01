import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from v753.core import (DEFAULTS, PNG_SIGNATURE, V7Error, capture_files, digest, load_articles,
                     run_choices, slices, validate_draft, wrap_text)
from v753.powerpoint import (CAPTURE_H, CAPTURE_W, CAPTURE_X, CAPTURE_Y, COLS, ROWS,
                           SLIDE_H, SLIDE_W, TABLE_X, TABLE_Y, insert_tile, plan_article)
from v753.powerpoint import capture_geometry, format_text, verify_capture_geometry


def fixture(root, article_id="101", summary="작성자가 경고등 발생을 문의했습니다."):
    body = "출고 후 경고등이 나타나 정비 예약을 문의했습니다."
    a = {"cafe_id": "20179506", "article_id": article_id, "title": "경고등 문의",
         "body": body, "written_at": "2026-09-15T10:00:00+09:00",
         "url": f"https://cafe.naver.com/f-e/cafes/20179506/articles/{article_id}",
         "source_file": str(root / "output_v5" / "run" / "article.json"),
         "metadata": {"view_count": 0, "comment_count": 0}}
    a["input_sha256"] = digest({k: a[k] for k in ("title", "body", "written_at")})
    d = {"draft_version": "monitoring-1", "source": a,
         "monitoring": {"vehicle": {"model": None, "model_year": None, "mileage": None},
                        "summary_suppressed": False, "display_text": summary, "display_basis": "ai_summary"},
         "capture": {"status": "captured", "source_body_sha256": hashlib.sha256(body.encode()).hexdigest(), "files": []},
         "review": {"notes": []}, "ai_candidate": {"summary": "이 항목은 직접 사용하면 안 됩니다."}}
    seal(d)
    return d


def seal(d):
    d["artifact_sha256"] = digest({k: v for k, v in d.items() if k != "artifact_sha256"})


def test_key(article_id="101", token="a"):
    # 실제 V6 저장 형식에 맞춥니다. 해시 단독의 가상 파일명을 쓰지 않습니다.
    return f"20179506_{article_id}_{token * 64}"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class ObservedOfficeCrop:
    """사용자 실측처럼 프레임 이동 시 그림의 절대 위치가 유지되는 모사 객체."""
    def __init__(self):
        self.PictureOffsetX = self.PictureOffsetY = 0.0
        self.ShapeLeft = self.ShapeTop = 0.0

    def __setattr__(self, key, value):
        offset = {"ShapeLeft": "PictureOffsetX", "ShapeTop": "PictureOffsetY"}.get(key)
        if offset:
            old = getattr(self, key, 0.0)
            object.__setattr__(self, offset, getattr(self, offset, 0.0) - (value - old))
        object.__setattr__(self, key, value)


class PictureShapes:
    def AddPicture(self, *args):
        self.args = args
        self.pic = SimpleNamespace(PictureFormat=SimpleNamespace(Crop=ObservedOfficeCrop()))
        return self.pic


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "output_v6"
        self.folder = self.out / "drafts" / "run_1"
        self.cfg = dict(DEFAULTS, v6_root=self.out)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, d, statuses=None, extra=None):
        key = test_key(d["source"]["article_id"])
        write(self.folder / (key + ".json"), d)
        jobs = statuses or [{"cache_key": key, "status": "done"}]
        write(self.out / "runs" / "run_1" / "manifest.json", {"run_id": "run_1", "jobs": jobs})
        if extra:
            for k, v in extra.items():
                write(self.folder / (k + ".json"), v)
        return load_articles(self.folder, self.cfg, self.root)

    def test_valid_zero_counts_and_unknown_fields(self):
        articles, errors, _ = self.load(fixture(self.root))
        self.assertFalse(errors)
        self.assertEqual((articles[0]["views"], articles[0]["comments"]), ("0", "0"))
        self.assertEqual(articles[0]["complaint"], "-")
        self.assertEqual(articles[0]["same_count"], "-")

    def test_tampering_is_rejected(self):
        d = fixture(self.root)
        d["monitoring"]["display_text"] = "다른 결론"
        with self.assertRaisesRegex(V7Error, "저장 당시"):
            validate_draft(d)

    def test_source_hash_checked_even_with_new_outer_checksum(self):
        d = fixture(self.root)
        d["source"]["body"] += "새 내용"
        seal(d)
        with self.assertRaisesRegex(V7Error, "원문 입력"):
            validate_draft(d)

    def test_suppressed_summary_never_revives_candidate(self):
        d = fixture(self.root)
        d["monitoring"].update(summary_suppressed=True, display_basis="source_body", display_text=d["source"]["body"])
        seal(d)
        articles, errors, _ = self.load(d)
        self.assertTrue(articles[0]["source_mode"])
        self.assertEqual(articles[0]["display_text"], d["source"]["body"])
        self.assertNotIn("사용하면", articles[0]["display_text"])

    def test_only_completed_jobs_and_no_stale_drafts(self):
        d = fixture(self.root)
        articles, _, _ = self.load(d, [{"cache_key": test_key(), "status": "unknown"}], {test_key("102", "b"): fixture(self.root, "102")})
        self.assertEqual(articles, [])

    def test_conversion_summary_is_authoritative(self):
        d = fixture(self.root)
        write(self.folder / "summary.json", {"run_id": "run_1", "saved": 1,
              "items": [{"file": test_key() + ".json", "status": "draft_saved"}]})
        articles, errors, basis = self.load(d, [{"cache_key": test_key(), "status": "failed"}])
        self.assertEqual((len(articles), basis), (1, "converted_summary"))

    def test_missing_capture_does_not_drop_text(self):
        d = fixture(self.root)
        d["capture"]["files"] = [{"path": "captures/missing.png", "sha256": "0" * 64}]
        seal(d)
        articles, errors, _ = self.load(d)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["captures"], [])
        self.assertTrue(articles[0]["capture_problem"])

    def test_drm_is_passed_to_office_without_claiming_hash_match(self):
        d = fixture(self.root)
        p = Path(d["source"]["source_file"]).parent / "captures/post_001.png"
        p.parent.mkdir(parents=True)
        original = b"\x9b DRMONE  This Document is encrypted and protected by Fasoo DRM " + b"0" * 64
        p.write_bytes(original)
        d["capture"]["files"] = [{"path": "captures/post_001.png", "sha256": "0" * 64}]
        caps, notes = capture_files(d, self.root)
        self.assertEqual(len(caps), 1)
        self.assertIn("unverified", caps[0]["verification"])
        self.assertEqual(p.read_bytes(), original)

    def test_plain_png_hash_mismatch_and_traversal_rejected(self):
        d = fixture(self.root)
        base = Path(d["source"]["source_file"]).parent
        base.mkdir(parents=True)
        (base / "post.png").write_bytes(PNG_SIGNATURE + b"not a real image")
        d["capture"]["files"] = [
            {"path": "post.png", "sha256": "0" * 64},
            {"path": "../outside.png", "sha256": "0" * 64},
            {"path": "C:\\outside.png", "sha256": "0" * 64},
        ]
        caps, notes = capture_files(d, self.root)
        self.assertEqual(caps, [])
        self.assertEqual(len(notes), 3)

    def test_duplicate_cafe_article_is_not_silently_duplicated(self):
        d = fixture(self.root)
        articles, errors, _ = self.load(d, [
            {"cache_key": test_key(), "status": "done"}, {"cache_key": test_key(token="b"), "status": "done"}
        ], {test_key(token="b"): copy.deepcopy(d)})
        self.assertEqual(len(articles), 1)
        self.assertEqual(errors[0]["code"], "DUPLICATE_ARTICLE")

    def test_reported_v6_filenames_discovered_and_loaded(self):
        # 사용자 로그의 실제 파일명입니다. 내용은 테스트용 가상 초안입니다.
        names = [
            "20179506_5482932_1930f61e558a3abc3ca63cf078b3af3047aecd8dada3eb78f60f46df0216e1cf.json",
            "20179506_5488055_ca0e186c06c0cbfbcaf8f662fcd46476931ef6b06fad2cefa5391e58789c8668.json",
            "20179506_5490883_877b8eadd4c821fcd5528f3b67442ad5b1fd8bb11f963f178a3881c737827004.json",
        ]
        for name in names:
            write(self.folder / name, fixture(self.root, name.split("_")[1]))
        write(self.folder / "summary.json", {"run_id": "run_1", "saved": 3,
              "items": [{"file": n, "status": "draft_saved"} for n in names]})
        self.assertEqual(run_choices(self.cfg), [self.folder])
        articles, errors, basis = load_articles(self.folder, self.cfg, self.root)
        self.assertFalse(errors)
        self.assertEqual({a["id"] for a in articles}, {"5482932", "5488055", "5490883"})
        self.assertEqual(basis, "converted_summary")

    def test_filename_ids_must_match_source(self):
        d = fixture(self.root)
        name = test_key("999") + ".json"
        write(self.folder / name, d)
        write(self.folder / "summary.json", {"run_id": "run_1", "saved": 1,
              "items": [{"file": name, "status": "draft_saved"}]})
        articles, errors, _ = load_articles(self.folder, self.cfg, self.root)
        self.assertEqual(articles, [])
        self.assertEqual(errors[0]["code"], "DRAFT_ID_MISMATCH")

    def test_discovery_ignores_html_summary_and_unrelated_json(self):
        write(self.folder / "summary.json", {"saved": 0})
        write(self.folder / "other.json", {})
        (self.folder / "monitoring.html").write_text("<html></html>")
        self.assertEqual(run_choices(self.cfg), [])


class LayoutTests(unittest.TestCase):
    def test_table_cell_rejecting_autosize_and_wordwrap_still_formats_text(self):
        # 사용자 Office에서 관측한 제약을 재현합니다. 실제 COM 렌더링 시험은 아닙니다.
        class TextFrame:
            def __init__(self, cell):
                self.cell = cell
                self.autosize_calls = []
                self.wordwrap_calls = []
                self.TextRange = SimpleNamespace(
                    Font=SimpleNamespace(Color=SimpleNamespace()),
                    ParagraphFormat=SimpleNamespace(), BoundHeight=12, BoundWidth=32,
                )

            @property
            def AutoSize(self):
                return 0

            @AutoSize.setter
            def AutoSize(self, value):
                if self.cell:
                    raise ValueError("표 셀 AutoSize: 지정한 값이 범위를 벗어났습니다.")
                self.autosize_calls.append(value)

            @property
            def WordWrap(self):
                return -1

            @WordWrap.setter
            def WordWrap(self, value):
                if self.cell:
                    raise ValueError("표 셀 WordWrap: 지정한 값이 범위를 벗어났습니다.")
                self.wordwrap_calls.append(value)

        for cell in (True, False):
            with self.subTest(table_cell=cell):
                tf = TextFrame(cell)
                shape = SimpleNamespace(TextFrame=tf, Width=64, Height=39)
                tr = format_text(shape, "차 종", "맑은 고딕", table_cell=cell)
                self.assertEqual(tr.Text, "차 종")
                self.assertEqual(tr.Font.Name, "맑은 고딕")
                self.assertEqual(tf.autosize_calls, [] if cell else [0])
                self.assertEqual(tf.wordwrap_calls, [] if cell else [-1])

    def test_long_capture_coverage_no_gaps_and_no_distortion(self):
        for width, height in ((800, 1130), (1200, 10000), (1600, 500), (1, 10)):
            parts = slices(width, height, CAPTURE_W, CAPTURE_H, 14)
            self.assertEqual(parts[0]["start_fraction"], 0)
            self.assertAlmostEqual(parts[-1]["end_fraction"], 1)
            for left, right in zip(parts, parts[1:]):
                self.assertLessEqual(right["start_fraction"], left["end_fraction"])
            for p in parts:
                self.assertLessEqual(p["height"], CAPTURE_H)
                self.assertAlmostEqual(p["width"] / p["full_height"], width / height)

    def test_invalid_dimension_rejected(self):
        with self.assertRaises(V7Error):
            slices(0, 100, 500, 100, 10)

    def test_full_summary_preserved_across_continuations(self):
        text = "본문과 요약을 생략 없이 보존해야 합니다. " * 200
        article = {"display_text": text, "source_mode": True, "captures": []}
        pages = plan_article(article, DEFAULTS)
        pieces = pages[0]["summary"] + "".join(p["text"] for p in pages if p["kind"] == "text")
        self.assertEqual(pieces.replace("\n", ""), text)
        self.assertGreater(len(pages), 1)

    def test_crop_offsets_match_requested_source_slice(self):
        # 프레임 이동의 부작용이 없는 단순 Box 검사는 이전 오류를 놓쳤습니다.
        # 실제 사례의 첫/끝 구간 및 두 번째 열도 원래 소스 구간을 보여야 합니다.
        for width, height in ((800, 1200), (800, 441), (1600, 500)):
            for column, box_width in ((0, CAPTURE_W), (1, (CAPTURE_W - 16) / 2)):
                parts = slices(width, height, box_width, CAPTURE_H, 14)
                for i, part in enumerate(parts, 1):
                    with self.subTest(source=(width, height), column=column, part=i):
                        slide = SimpleNamespace(Shapes=PictureShapes())
                        tile = dict(part, capture={"path": "protected.png", "index": 1},
                                    part=i, total_parts=len(parts))
                        pic = insert_tile(slide, tile, column)
                        crop = pic.PictureFormat.Crop
                        source_top = (crop.ShapeHeight - crop.PictureHeight) / 2 + crop.PictureOffsetY
                        self.assertAlmostEqual(source_top, -part["start"])
                        self.assertAlmostEqual(crop.PictureOffsetX, 0)
                        self.assertAlmostEqual(crop.ShapeLeft, CAPTURE_X + column * (box_width + 16))
                        self.assertAlmostEqual(crop.ShapeTop, CAPTURE_Y)
                        self.assertEqual(slide.Shapes.args[1:3], (0, -1))

    def test_frames_inside_reference_slide_and_capture_cell(self):
        self.assertLess(TABLE_X + sum(COLS), SLIDE_W)
        self.assertLess(TABLE_Y + sum(ROWS), SLIDE_H)
        self.assertGreaterEqual(CAPTURE_X, TABLE_X + COLS[0])
        self.assertGreaterEqual(CAPTURE_Y, TABLE_Y + sum(ROWS[:4]))
        self.assertLessEqual(CAPTURE_X + CAPTURE_W, TABLE_X + sum(COLS))
        self.assertLessEqual(CAPTURE_Y + CAPTURE_H, TABLE_Y + sum(ROWS))

    def test_geometry_check_accepts_office_rounding_but_rejects_displacement(self):
        tile = slices(800, 1200, CAPTURE_W, CAPTURE_H, 14)[0]
        expected = capture_geometry(tile, 0)
        # 사용자 실측의 약 0.01~0.02pt 반올림은 정상입니다.
        rounded = dict(expected, PictureWidth=697.01, PictureHeight=1045.52)
        pic = SimpleNamespace(Name="V7_CAPTURE_1_1", PictureFormat=SimpleNamespace(
            Crop=SimpleNamespace(**rounded)))
        verify_capture_geometry(pic, expected)

        for key, value in (("PictureOffsetX", -104), ("PictureOffsetY", 134.75),
                           ("ShapeTop", 0), ("PictureWidth", float("nan"))):
            with self.subTest(property=key):
                pic.PictureFormat.Crop = SimpleNamespace(**dict(rounded, **{key: value}))
                with self.assertRaises(V7Error) as result:
                    verify_capture_geometry(pic, expected)
                self.assertEqual(result.exception.code, "CAPTURE_GEOMETRY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
