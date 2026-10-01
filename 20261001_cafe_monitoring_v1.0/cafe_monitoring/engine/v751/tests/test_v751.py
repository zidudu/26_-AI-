"""V7.5.1 회귀 검사. 회사 PowerPoint의 실제 렌더링 검사를 대신하지 않습니다."""
import copy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from v751.core import DEFAULTS, V7Error, config
from v751.layout import balanced_slices
from v751.powerpoint import (CAPTURE_H, CAPTURE_W, CAPTURE_X, CAPTURE_Y, capture_geometry,
                             com_text_length, describe_plan, insert_tile, plan_article,
                             put_source_link, source_link_text, verify_source_link)
from v751.tests.test_export import PictureShapes


def article(sizes, article_id="101"):
    return {"id": article_id, "display_text": "테스트 요약입니다.", "source_mode": False,
            "captures": [{"index": i, "path": f"post_{i:03}.png", "office_width": w,
                          "office_height": h} for i, (w, h) in enumerate(sizes, 1)]}


class WholeCaptureLayoutTests(unittest.TestCase):
    def test_reported_four_articles_have_expected_default_pages(self):
        # 회사 PC 기록에 있는 크기입니다. PNG 내용을 읽거나 새 이미지를 만든 검사가 아닙니다.
        cases = [("5495700", [(800, 1200)] * 7 + [(800, 441)], 4),
                 ("5490883", [(800, 266)], 1), ("5488055", [(800, 808)], 1),
                 ("5482932", [(800, 340)], 1)]
        self.assertEqual(sum(n for _, _, n in cases), 7)
        for aid, sizes, expected in cases:
            with self.subTest(article=aid):
                a = article(sizes, aid)
                pages = plan_article(a, DEFAULTS)
                self.assertEqual(len(pages), expected)
                self.assertEqual(describe_plan(a, pages)["slides"], expected)

    def test_egr_near_fit_keeps_entire_image_within_single_slide(self):
        pages = plan_article(article([(800, 340)]), DEFAULTS)
        self.assertEqual(len(pages), 1)
        tile = pages[0]["tiles"][0]
        self.assertEqual((tile["start_fraction"], tile["end_fraction"]), (0, 1))
        self.assertAlmostEqual(tile["width"] / tile["full_height"], 800 / 340)
        self.assertGreater(tile["fit_scale"], 0.92)
        self.assertLessEqual(tile["height"], CAPTURE_H + 1e-7)

    def test_short_image_uses_full_width_even_in_three_column_mode(self):
        pages = plan_article(article([(800, 266)]), dict(DEFAULTS, capture_columns=3))
        self.assertEqual(pages[0]["columns"], 1)
        self.assertEqual(pages[0]["tiles"][0]["width"], CAPTURE_W)

    def test_all_modes_preserve_capture_order_and_full_coverage(self):
        sizes = [(800, 1200), (800, 340), (800, 808), (800, 441), (300, 1400)]
        for columns in (1, 2, 3):
            with self.subTest(columns=columns):
                a = article(sizes)
                before = copy.deepcopy(a)
                pages = plan_article(a, dict(DEFAULTS, capture_columns=columns))
                self.assertEqual(a, before)
                tiles = [t for p in pages for t in p["tiles"]]
                ids = [t["capture"]["index"] for t in tiles]
                self.assertEqual(ids, sorted(ids))
                for i, (w, h) in enumerate(sizes, 1):
                    parts = [t for t in tiles if t["capture"]["index"] == i]
                    self.assertEqual(parts[0]["start_fraction"], 0)
                    self.assertEqual(parts[-1]["end_fraction"], 1)
                    self.assertEqual([p["part"] for p in parts], list(range(1, len(parts) + 1)))
                    for left, right in zip(parts, parts[1:]):
                        self.assertGreaterEqual(left["end_fraction"], right["start_fraction"])
                        self.assertAlmostEqual(left["height"], right["height"])
                    for part in parts:
                        self.assertAlmostEqual(part["width"] / part["full_height"], w / h)
                        self.assertGreater(part["fit_scale"], 0)
                        self.assertEqual(part["total_parts"], 1)
                self.assertEqual(len(tiles), len(sizes))
                self.assertEqual(ids, list(range(1, len(sizes) + 1)))

    def test_columns_stay_within_area_and_crop_offsets_survive_frame_movement(self):
        # 두 번째 캡처는 열 폭에서 조금 축소되는 비율입니다. 다음 열 위치는 앞 그림 폭과 무관합니다.
        for columns in (2, 3):
            pages = plan_article(article([(800, 665), (800, 1020), (800, 260)]),
                                 dict(DEFAULTS, capture_columns=columns))
            for page in pages:
                previous_right = CAPTURE_X - 16
                for col, tile in enumerate(page["tiles"]):
                    slide = SimpleNamespace(Shapes=PictureShapes())
                    pic = insert_tile(slide, tile, col)
                    crop = pic.PictureFormat.Crop
                    self.assertGreaterEqual(crop.ShapeLeft + 1e-7, previous_right + 16)
                    self.assertLessEqual(crop.ShapeLeft + crop.ShapeWidth, CAPTURE_X + CAPTURE_W + 1e-7)
                    self.assertLessEqual(crop.ShapeTop + crop.ShapeHeight, CAPTURE_Y + CAPTURE_H + 1e-7)
                    self.assertAlmostEqual((crop.ShapeHeight - crop.PictureHeight) / 2 + crop.PictureOffsetY,
                                           -tile["start"])
                    self.assertAlmostEqual(crop.PictureOffsetX, 0)
                    previous_right = crop.ShapeLeft + crop.ShapeWidth

    def test_balanced_parts_remove_tiny_last_strip_without_omitting_content(self):
        parts = balanced_slices(800, 441, CAPTURE_W, CAPTURE_H, 14, 0.85)
        self.assertEqual(len(parts), 2)
        self.assertAlmostEqual(parts[0]["height"], parts[1]["height"])
        self.assertEqual(parts[-1]["end_fraction"], 1)

    def test_fractional_and_extreme_dimensions_are_bounded(self):
        for dimensions in ((800, 0.01), (396.83331298828125, 595.25), (589.3564453125, 595.25),
                           (1200, 10000), (697, 548), (697, 548.0000001)):
            for overlap in (0, 14, 40):
                parts = balanced_slices(*dimensions, CAPTURE_W, CAPTURE_H, overlap, 0.85)
                for p in parts:
                    self.assertGreater(p["height"], 0)
                    self.assertLessEqual(p["height"], CAPTURE_H + 1e-7)
                self.assertEqual(parts[-1]["end_fraction"], 1)

    def test_invalid_or_unbounded_input_fails_explicitly(self):
        for w, h in ((0, 200), (True, 10), (800, float("nan")), (800, float("inf")), (1, 1e15)):
            with self.subTest(dimensions=(w, h)), self.assertRaises(V7Error):
                balanced_slices(w, h, CAPTURE_W, CAPTURE_H, 14, 0.85)

    def test_no_capture_keeps_article_and_does_not_invent_image(self):
        pages = plan_article(article([]), DEFAULTS)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["tiles"], [])

    def test_one_two_three_eight_files_are_not_split_or_duplicated(self):
        for count, expected in ((1, [1]), (2, [2]), (3, [2, 1]), (8, [2, 2, 2, 2])):
            with self.subTest(count=count):
                pages = plan_article(article([(800, 1200)] * count), DEFAULTS)
                self.assertEqual([len(p["tiles"]) for p in pages], expected)
                for p in pages:
                    for t in p["tiles"]:
                        self.assertEqual((t["start_fraction"], t["end_fraction"]), (0, 1))
                        self.assertEqual(t["height"], t["full_height"])

    def test_single_remainder_is_centered_in_entire_area(self):
        for last in ((800, 1200), (1600, 200), (800, 340)):
            pages = plan_article(article([(800, 1200), (800, 1200), last]), DEFAULTS)
            tile = pages[-1]["tiles"][0]
            geometry = capture_geometry(tile, 0)
            self.assertEqual(pages[-1]["columns"], 1)
            self.assertAlmostEqual(geometry["ShapeLeft"] + geometry["ShapeWidth"] / 2,
                                   CAPTURE_X + CAPTURE_W / 2)
            self.assertAlmostEqual(geometry["ShapeTop"] + geometry["ShapeHeight"] / 2,
                                   CAPTURE_Y + CAPTURE_H / 2)

    def test_whole_image_insertion_keeps_picture_and_frame_same_size(self):
        # 프레임 이동으로 그림 위치가 바뀌는 회사 Office 동작을 모사합니다.
        for page in plan_article(article([(800, 1200), (800, 441), (800, 340)]), DEFAULTS):
            for col, tile in enumerate(page["tiles"]):
                slide = SimpleNamespace(Shapes=PictureShapes())
                crop = insert_tile(slide, tile, col).PictureFormat.Crop
                self.assertAlmostEqual(crop.PictureWidth, crop.ShapeWidth)
                self.assertAlmostEqual(crop.PictureHeight, crop.ShapeHeight)
                self.assertAlmostEqual(crop.PictureOffsetX, 0)
                self.assertAlmostEqual(crop.PictureOffsetY, 0)

    def test_old_three_column_setting_uses_at_most_two(self):
        pages = plan_article(article([(800, 1200)] * 3), dict(DEFAULTS, capture_columns=3))
        self.assertEqual([len(p["tiles"]) for p in pages], [2, 1])


class ConfigTests(unittest.TestCase):
    def test_side_by_side_config_keeps_v7_files_unchanged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = {"output_dir": "output_v7", "capture_columns": 1,
                        "v6_output_dir": "custom_v6", "font_name": "Arial", "cafe_names": {"1": "카페명"}}
            path = root / "config_v7.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            before = path.read_bytes()
            cfg = config(root)
            self.assertEqual(cfg["out_root"], root / "output_v751")
            self.assertEqual(cfg["v6_root"], root / "custom_v6")
            self.assertEqual(cfg["capture_columns"], 2)
            self.assertEqual(cfg["cafe_names"], {"1": "카페명"})
            self.assertEqual(path.read_bytes(), before)

    def test_v751_override_and_invalid_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config_v751.json"
            path.write_text('{"capture_columns": 3}', encoding="utf-8")
            self.assertEqual(config(root)["capture_columns"], 3)
            for values in ({"capture_columns": True}, {"capture_columns": 4},
                           {"capture_fit_min_scale": 0.5}, {"capture_fit_min_scale": float("nan")},
                           {"output_dir": "output_v6/exports"}, {"no_such_key": 1}):
                path.write_text(json.dumps(values), encoding="utf-8")
                with self.subTest(values=values), self.assertRaises(V7Error):
                    config(root)


class MockLink:
    def __init__(self, owner, start, length):
        self.owner, self.start, self.length = owner, start, length

    @property
    def Address(self):
        targets = self.owner.targets[self.start:self.start + self.length]
        return targets[0] if targets and len(set(targets)) == 1 else ""

    @Address.setter
    def Address(self, value):
        self.owner.targets[self.start:self.start + self.length] = [value] * self.length


class MockRange:
    def __init__(self):
        self.Font = SimpleNamespace(Color=SimpleNamespace())
        self.ParagraphFormat = SimpleNamespace()
        self.BoundHeight, self.BoundWidth = 35, 190
        self.targets = [""] * 1000
        self.Text = ""

    def Characters(self, start, length):
        actions = SimpleNamespace(Item=lambda _: SimpleNamespace(Hyperlink=MockLink(self, start - 1, length)))
        return SimpleNamespace(ActionSettings=actions, Font=self.Font)


class SourceLinkTests(unittest.TestCase):
    def setUp(self):
        self.a = {"url": "https://cafe.naver.com/f-e/cafes/20179506/articles/5482932", "written_at": "2026-08-25"}
        self.tr = MockRange()
        self.shape = SimpleNamespace(TextFrame=SimpleNamespace(TextRange=self.tr), Width=224, Height=55)

    def test_full_url_preserved_and_every_url_character_links_to_original(self):
        put_source_link(self.shape, self.a, "맑은 고딕")
        visible, text = source_link_text(self.a["url"], self.a["written_at"])
        self.assertEqual(visible.replace("\r", ""), self.a["url"])
        self.assertEqual(self.tr.Text, text)
        count = com_text_length(visible)
        self.assertEqual(set(self.tr.targets[:count]), {self.a["url"]})
        self.assertEqual(self.tr.targets[count], "")
        self.assertNotIn("원문 게시글 열기", text)
        verify_source_link(self.shape, self.a)

    def test_reopen_verifier_rejects_changed_text_or_wrong_link(self):
        put_source_link(self.shape, self.a, "맑은 고딕")
        text = self.tr.Text
        self.tr.Text = text.replace("5482932", "0000000")
        with self.assertRaises(V7Error):
            verify_source_link(self.shape, self.a)
        self.tr.Text = text
        self.tr.targets[0] = "https://cafe.naver.com/wrong"
        with self.assertRaises(V7Error):
            verify_source_link(self.shape, self.a)

    def test_wrap_preserves_query_and_unicode_without_shortening(self):
        for url in (self.a["url"], self.a["url"] + "?search=a%20b&order=new", "https://cafe.naver.com/긴주소/😀"):
            visible, text = source_link_text(url, "")
            self.assertEqual(visible.replace("\r", ""), url)
            self.assertTrue(text.endswith("\r-"))
        self.assertEqual(com_text_length("A😀"), 3)


if __name__ == "__main__":
    unittest.main()
