import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from v7.core import (DEFAULTS, PNG_SIGNATURE, V7Error, capture_files, digest, load_articles,
                     slices, validate_draft, wrap_text)
from v7.powerpoint import (CAPTURE_H, CAPTURE_W, CAPTURE_X, CAPTURE_Y, COLS, ROWS,
                           SLIDE_H, SLIDE_W, TABLE_X, TABLE_Y, insert_tile, plan_article)


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


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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
        key = "a" * 64
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
        articles, _, _ = self.load(d, [{"cache_key": "a" * 64, "status": "unknown"}], {"b" * 64: fixture(self.root, "102")})
        self.assertEqual(articles, [])

    def test_conversion_summary_is_authoritative(self):
        d = fixture(self.root)
        write(self.folder / "summary.json", {"run_id": "run_1", "saved": 1,
              "items": [{"file": "a" * 64 + ".json", "status": "draft_saved"}]})
        articles, errors, basis = self.load(d, [{"cache_key": "a" * 64, "status": "failed"}])
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
            {"cache_key": "a" * 64, "status": "done"}, {"cache_key": "b" * 64, "status": "done"}
        ], {"b" * 64: copy.deepcopy(d)})
        self.assertEqual(len(articles), 1)
        self.assertEqual(errors[0]["code"], "DUPLICATE_ARTICLE")


class LayoutTests(unittest.TestCase):
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
        class Box:
            pass
        class Shapes:
            def AddPicture(self, *args):
                self.args = args
                self.pic = Box()
                self.pic.PictureFormat = Box()
                self.pic.PictureFormat.Crop = Box()
                return self.pic
        slide = Box()
        slide.Shapes = Shapes()
        part = slices(800, 2400, 697, 274, 14)[2]
        part.update(capture={"path": "protected.png", "index": 1}, part=3, total_parts=8)
        pic = insert_tile(slide, part, 0)
        crop = pic.PictureFormat.Crop
        source_top = (crop.ShapeHeight - crop.PictureHeight) / 2 + crop.PictureOffsetY
        self.assertAlmostEqual(source_top, -part["start"])
        self.assertEqual(slide.Shapes.args[1:3], (0, -1))

    def test_frames_inside_reference_slide_and_capture_cell(self):
        self.assertLess(TABLE_X + sum(COLS), SLIDE_W)
        self.assertLess(TABLE_Y + sum(ROWS), SLIDE_H)
        self.assertGreaterEqual(CAPTURE_X, TABLE_X + COLS[0])
        self.assertGreaterEqual(CAPTURE_Y, TABLE_Y + sum(ROWS[:4]))
        self.assertLessEqual(CAPTURE_X + CAPTURE_W, TABLE_X + sum(COLS))
        self.assertLessEqual(CAPTURE_Y + CAPTURE_H, TABLE_Y + sum(ROWS))


if __name__ == "__main__":
    unittest.main()
