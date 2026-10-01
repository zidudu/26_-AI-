"""Windows의 PowerPoint COM을 사용합니다. 보호 파일을 추출하거나 해제하지 않습니다."""
from __future__ import annotations

import math
from pathlib import Path

from .core import V7Error, slices, wrap_text

PPT_BUILD = "20260915_crop_position_1"
SLIDE_W, SLIDE_H = 841.89, 595.28  # 참고 슬라이드와 같은 A4 가로 비율
TABLE_X, TABLE_Y = 28.0, 78.0
COLS = [64.0, 132.0, 68.0, 224.0, 63.0, 117.0, 117.0]
ROWS = [39.0, 24.0, 31.0, 68.0, 306.0]
CAPTURE_X, CAPTURE_Y, CAPTURE_W, CAPTURE_H = 104.0, 251.0, 697.0, 274.0


def rgb(r, g, b):
    return r + (g << 8) + (b << 16)


def ensure_windows():
    import sys
    if sys.platform != "win32":
        raise V7Error("WINDOWS_REQUIRED", "이 생성기는 Windows와 설치된 PowerPoint가 필요합니다.")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise V7Error("PYWIN32_MISSING", "01_install_v7.bat를 먼저 실행하세요.") from exc


def check_office():
    ensure_windows()
    import pywintypes
    # pywin32의 IID 생성자는 ProgID를 받아 등록된 CLSID로 변환합니다.
    # Windows API 이름인 CLSIDFromProgID는 pythoncom의 공개 함수가 아닙니다.
    try:
        pywintypes.IID("PowerPoint.Application")
    except pywintypes.com_error as exc:
        raise V7Error("POWERPOINT_NOT_REGISTERED", "PowerPoint COM 등록을 확인하지 못했습니다. 설치된 데스크톱 PowerPoint를 확인하세요.") from exc
    return "PowerPoint COM 등록 확인"


def format_text(shape, text, font, size=11.0, align=1, min_size=9.0, middle=True, *, table_cell=False):
    tf = shape.TextFrame
    # 사용자 Office에서 표 셀의 AutoSize와 WordWrap 설정이 모두 거부됐습니다.
    # 표는 행·열 크기와 기존 줄바꿈을 사용하고 두 속성은 텍스트 상자에만 적용합니다.
    if not table_cell:
        tf.AutoSize = 0
        tf.WordWrap = -1
    tf.MarginLeft = 5
    tf.MarginRight = 5
    tf.MarginTop = 3
    tf.MarginBottom = 3
    tf.VerticalAnchor = 3 if middle else 1
    tr = tf.TextRange
    tr.Text = str(text).replace("\n", "\r")
    tr.Font.Name = font
    tr.Font.NameFarEast = font
    tr.Font.Bold = 0
    tr.Font.Color.RGB = 0
    tr.ParagraphFormat.Alignment = align
    tr.ParagraphFormat.SpaceBefore = 0
    tr.ParagraphFormat.SpaceAfter = 0
    tr.ParagraphFormat.SpaceWithin = 1.0
    current = size
    while True:
        tr.Font.Size = current
        if tr.BoundHeight <= shape.Height - 5 and tr.BoundWidth <= shape.Width - 8:
            return tr
        current -= 0.5
        if current < min_size:
            raise V7Error("TEXT_OVERFLOW", "표 또는 텍스트가 표시 영역을 넘었습니다. 결과 기록의 게시글 ID를 확인하세요.")


def textbox(slide, name, text, x, y, w, h, font, size=11, align=1, min_size=9):
    shape = slide.Shapes.AddTextbox(1, x, y, w, h)
    shape.Name = name
    format_text(shape, text, font, size, align, min_size, False)
    return shape


def blank_slide(deck):
    slide = deck.Slides.Add(deck.Slides.Count + 1, 12)
    slide.FollowMasterBackground = 0
    slide.Background.Fill.Solid()
    slide.Background.Fill.ForeColor.RGB = rgb(255, 255, 255)
    return slide


def add_notes(slide, article, cfg, page_label, report):
    lines = [
        "V7 직원 검토 전 초안", f"원문 제목: {article['title']}",
        f"카페/게시글: {article['cafe_id']}/{article['id']}", f"원문: {article['url']}",
        f"작성일: {article['written_at']}", f"수집시각: {article['collected_at']}",
        f"V6 초안: {article['draft_path']}", f"V6 초안 해시: {article['draft_sha256']}",
        f"페이지: {page_label}", "이미지와 댓글을 AI가 분석한 결과가 아닙니다.",
        "\nV6 표시 내용 전체", article["display_text"], "\n검토 메모",
        *article["review_notes"], *article["capture_notes"],
    ]
    try:
        for i in range(1, slide.NotesPage.Shapes.Count + 1):
            shp = slide.NotesPage.Shapes.Item(i)
            if shp.Type == 14 and shp.PlaceholderFormat.Type == 2:
                shp.TextFrame.TextRange.Text = "\r".join(lines)
                return
        raise RuntimeError("슬라이드 노트 본문 자리표시자 없음")
    except Exception as exc:
        report["warnings"].append({"article_id": article["id"], "code": "NOTES_FAILED", "message": str(exc)})


def make_frame(deck, article, summary, cfg, page_label, detail=False):
    slide = blank_slide(deck)
    slide.Name = f"V7_{article['id']}_{deck.Slides.Count}"
    font = cfg["font_name"]
    title_lines = wrap_text(article["title"], 49)
    title = "\n".join(title_lines[:2])
    if len(title_lines) > 2:
        title += "…"
    textbox(slide, "V7_TITLE", "■ " + title, 24, 12, 789, 49, font, 22, min_size=14)
    line = slide.Shapes.AddLine(28, 60, 813, 60)
    line.Name = "V7_BLUE_LINE"
    line.Line.ForeColor.RGB = rgb(83, 126, 213)
    line.Line.Weight = 0.65

    table_shape = slide.Shapes.AddTable(5, 7, TABLE_X, TABLE_Y, sum(COLS), sum(ROWS))
    table_shape.Name = "V7_INFO_TABLE"
    table = table_shape.Table
    for c, width in enumerate(COLS, 1):
        table.Columns.Item(c).Width = width
    for r, height in enumerate(ROWS, 1):
        table.Rows.Item(r).Height = height
    for r in range(1, 6):
        for c in range(1, 8):
            cell = table.Cell(r, c)
            cell.Shape.Fill.Solid()
            cell.Shape.Fill.ForeColor.RGB = rgb(255, 255, 255)
            for border in range(1, 5):
                cell.Borders.Item(border).ForeColor.RGB = rgb(165, 165, 165)
                cell.Borders.Item(border).Weight = 0.6
    # 병합 전의 논리 행·열 번호를 사용해 고정 양식을 만듭니다.
    table.Cell(5, 2).Merge(table.Cell(5, 7))
    table.Cell(4, 2).Merge(table.Cell(4, 7))
    table.Cell(1, 6).Merge(table.Cell(1, 7))
    for c in range(1, 6):
        table.Cell(2, c).Merge(table.Cell(3, c))

    def put(r, c, value, label=False, align=2, size=11):
        cell = table.Cell(r, c)
        if label:
            cell.Shape.Fill.ForeColor.RGB = rgb(243, 243, 243)
        return format_text(cell.Shape, value, font, size, align, 9, table_cell=True)

    put(1, 1, "차 종", True)
    put(1, 2, article["vehicle"])
    put(1, 3, "제 원", True)
    put(1, 4, article["specs"])
    put(1, 5, "불만 구분", True)
    put(1, 6, article["complaint"])
    put(2, 1, "매체", True)
    put(2, 2, article["cafe_name"])
    put(2, 3, "게시글\n위치", True)
    date = article["written_at"][:10] or "-"
    link = put(2, 4, "원문 게시글 열기\n" + date)
    link.Characters(1, len("원문 게시글 열기")).ActionSettings.Item(1).Hyperlink.Address = article["url"]
    link.Characters(1, len("원문 게시글 열기")).Font.Color.RGB = rgb(0, 91, 179)
    link.Characters(1, len("원문 게시글 열기")).Font.Underline = -1
    put(2, 5, "버즈량", True)
    put(2, 6, "동일건수")
    put(2, 7, "조회수/댓글")
    put(3, 6, article["same_count"])
    put(3, 7, article["views"] + "/" + article["comments"])
    put(4, 1, "원문\n표시" if article["source_mode"] else "내용\n요약", True)
    put(4, 2, summary, align=1, size=12)
    put(5, 1, "내용\n상세" if detail else "게시글\n예시", True)
    # 내용과 이미지는 개별 편집 가능한 개체로 둡니다.
    textbox(slide, "V7_FOOTER", f"직원 검토 전 초안 · 게시글 {article['id']} · {page_label}",
            28, 555, 655, 20, font, 9, min_size=8)
    return slide


def get_dimensions(deck, articles, report):
    probe = blank_slide(deck)
    probe.Name = "V7_INTERNAL_IMAGE_CHECK"
    try:
        for article in articles:
            usable = []
            for cap in article["captures"]:
                picture = None
                try:
                    report["active_article"] = article["id"]
                    report["active_capture"] = cap["index"]
                    picture = probe.Shapes.AddPicture(cap["path"], 0, -1, 0, 0, -1, -1)
                    w, h = float(picture.Width), float(picture.Height)
                    if not (math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0):
                        raise V7Error("IMAGE_DIMENSIONS", "PowerPoint가 반환한 이미지 크기가 잘못됐습니다.")
                    rw, rh = cap.get("width"), cap.get("height")
                    if type(rw) in (int, float) and type(rh) in (int, float) and rw > 0 and rh > 0:
                        if abs((w / h) / (rw / rh) - 1) > 0.04:
                            raise V7Error("IMAGE_RATIO_MISMATCH", "V5 기록과 PowerPoint 이미지 비율이 다릅니다.")
                    cap = dict(cap, office_width=w, office_height=h)
                    usable.append(cap)
                except Exception as exc:
                    article["capture_problem"] = True
                    article["capture_notes"].append(f"캡처 {cap['index']}: PowerPoint 삽입 실패")
                    report["capture_failures"].append({"article_id": article["id"], "capture_index": cap["index"],
                                                       "code": getattr(exc, "code", "INSERT_IMAGE"), "message": str(exc)})
                finally:
                    if picture is not None:
                        picture.Delete()
            article["captures"] = usable
    finally:
        probe.Delete()


def plan_article(article, cfg):
    summary_lines = wrap_text(article["display_text"], 53)
    main_summary = "\n".join(summary_lines[:3])
    remaining = summary_lines[3:]
    pages, tiles = [], []
    ncols = cfg["capture_columns"]
    col_width = (CAPTURE_W - (ncols - 1) * 16) / ncols
    for cap in article["captures"]:
        parts = slices(cap["office_width"], cap["office_height"], col_width, CAPTURE_H,
                       cfg["capture_overlap_points"])
        for i, part in enumerate(parts, 1):
            tiles.append(dict(part, capture=cap, part=i, total_parts=len(parts)))
    for i in range(0, len(tiles), ncols):
        pages.append({"kind": "captures", "summary": main_summary, "tiles": tiles[i:i+ncols],
                      "summary_continues": bool(remaining)})
    if not pages:
        pages.append({"kind": "captures", "summary": main_summary, "tiles": [], "summary_continues": bool(remaining)})
    extra = [{"kind": "text", "summary": "V6 원문 표시의 계속입니다." if article["source_mode"] else "V6 내용 요약의 계속입니다.",
              "text": "\n".join(remaining[i:i+18]), "tiles": [], "summary_continues": False}
             for i in range(0, len(remaining), 18)]
    # 첫 근거 장 다음에 긴 요약/원문을 보존합니다. 캡처 순서는 그대로 이어집니다.
    pages[1:1] = extra
    return pages


def capture_geometry(tile, column):
    return {
        "PictureWidth": tile["width"], "PictureHeight": tile["full_height"],
        "ShapeWidth": tile["width"], "ShapeHeight": tile["height"],
        "ShapeLeft": CAPTURE_X + column * (tile["width"] + 16), "ShapeTop": CAPTURE_Y,
        "PictureOffsetX": 0.0,
        "PictureOffsetY": (tile["full_height"] - tile["height"]) / 2 - tile["start"],
    }


def verify_capture_geometry(pic, expected):
    """COM에서 적용된 값을 다시 읽습니다. Office의 소수점 반올림은 0.5pt까지 허용합니다."""
    crop = pic.PictureFormat.Crop
    actual = {key: float(getattr(crop, key)) for key in expected}
    differences = [
        f"{key}: 계획 {value:.2f} / 실제 {actual[key]:.2f}"
        for key, value in expected.items()
        if not math.isfinite(actual[key]) or abs(actual[key] - value) > 0.5
    ]
    if differences:
        raise V7Error("CAPTURE_GEOMETRY_MISMATCH", f"{pic.Name} 그림 배치 불일치: " + "; ".join(differences))


def insert_tile(slide, tile, column):
    cap = tile["capture"]
    pic = slide.Shapes.AddPicture(cap["path"], 0, -1, 0, 0, -1, -1)
    pic.Name = f"V7_CAPTURE_{cap['index']}_{tile['part']}"
    pic.AlternativeText = f"실제 게시글 캡처 {cap['index']} / 구간 {tile['part']}/{tile['total_parts']}"
    pic.LockAspectRatio = 0
    crop = pic.PictureFormat.Crop
    expected = capture_geometry(tile, column)
    crop.PictureWidth = tile["width"]
    crop.PictureHeight = tile["full_height"]
    crop.ShapeWidth = tile["width"]
    crop.ShapeHeight = tile["height"]
    # 사용자 실측: 프레임을 (104, 251)로 옮기면 그림 오프셋에 (-104, -251)이 생깁니다.
    # 프레임 위치를 먼저 정하고, 그 프레임의 중심 기준으로 그림 위치를 마지막에 맞춥니다.
    crop.ShapeLeft = expected["ShapeLeft"]
    crop.ShapeTop = expected["ShapeTop"]
    crop.PictureOffsetX = expected["PictureOffsetX"]
    crop.PictureOffsetY = expected["PictureOffsetY"]
    verify_capture_geometry(pic, expected)
    return pic


def render(articles, cfg, folder, report, checkpoint):
    ensure_windows()
    report["renderer_build"] = PPT_BUILD
    print(f"[PPT 모듈] {PPT_BUILD}", flush=True)
    import pythoncom
    from win32com.client import dynamic
    pythoncom.CoInitialize()
    app = deck = None
    try:
        report["stage"] = "START_POWERPOINT"
        checkpoint()
        app = dynamic.Dispatch("PowerPoint.Application")
        app.Visible = -1
        deck = app.Presentations.Add(-1)
        deck.PageSetup.SlideWidth = SLIDE_W
        deck.PageSetup.SlideHeight = SLIDE_H
        report["stage"] = "CHECK_CAPTURE_INSERTION"
        checkpoint()
        get_dimensions(deck, articles, report)
        plans = [(a, plan_article(a, cfg)) for a in articles]
        report["inserted_source_captures"] = sum(len(a["captures"]) for a in articles)
        report["capture_segments"] = sum(len(p["tiles"]) for a, pages in plans for p in pages)
        count = sum(len(p) for a, p in plans)
        if count > cfg["max_slides"]:
            raise V7Error("TOO_MANY_SLIDES", f"예상 {count}장으로 최대 {cfg['max_slides']}장을 넘습니다. 글 수를 줄여 실행하세요.")
        expected = []
        expected_crops = []
        report["capture_geometry_insert_checked"] = 0
        report["capture_geometry_reopen_checked"] = 0
        for article, pages in plans:
            print(f"[PPT 작성] 게시글 {article['id']} / {len(pages)}장", flush=True)
            for page_index, page in enumerate(pages, 1):
                report["stage"] = "CREATE_SLIDE"
                report["active_article"] = article["id"]
                checkpoint()
                slide = make_frame(deck, article, page["summary"], cfg, f"{page_index}/{len(pages)}",
                                   detail=page["kind"] == "text")
                names = ["V7_TITLE", "V7_INFO_TABLE"]
                crops = {}
                if page["kind"] == "text":
                    textbox(slide, "V7_FULL_TEXT", page["text"], CAPTURE_X, CAPTURE_Y, CAPTURE_W, CAPTURE_H,
                            cfg["font_name"], 12, min_size=11)
                    names.append("V7_FULL_TEXT")
                elif not page["tiles"]:
                    textbox(slide, "V7_NO_CAPTURE", "삽입 가능한 캡처 없음\n원문 링크와 결과 기록을 확인하세요.",
                            CAPTURE_X, CAPTURE_Y, CAPTURE_W, 80, cfg["font_name"], 15)
                    report["missing_capture_articles"].append(article["id"])
                for col, tile in enumerate(page["tiles"]):
                    report["stage"] = "INSERT_CAPTURE"
                    report["active_capture"] = tile["capture"]["index"]
                    checkpoint()
                    pic = insert_tile(slide, tile, col)
                    names.append(pic.Name)
                    crops[pic.Name] = capture_geometry(tile, col)
                    report["capture_geometry_insert_checked"] += 1
                if page["tiles"]:
                    caption = " / ".join(f"캡처 {t['capture']['index']} ({t['part']}/{t['total_parts']})" for t in page["tiles"])
                    if page["summary_continues"]:
                        caption += " · 요약/원문 계속: 내용 상세 슬라이드"
                    textbox(slide, "V7_CAPTURE_CAPTION", caption, CAPTURE_X, 528, CAPTURE_W, 18,
                            cfg["font_name"], 8.5, min_size=8)
                add_notes(slide, article, cfg, f"{page_index}/{len(pages)}", report)
                if cfg["logo_path"]:
                    logo_path = Path(cfg["logo_path"])
                    if not logo_path.is_absolute():
                        logo_path = cfg["project_root"] / logo_path
                    logo = slide.Shapes.AddPicture(str(logo_path.resolve()), 0, -1, 0, 0, -1, -1)
                    scale = min(116 / logo.Width, 24 / logo.Height)
                    logo.LockAspectRatio = -1
                    logo.Width *= scale
                    logo.Left, logo.Top = 813 - logo.Width, 555
                    logo.Name = "V7_USER_LOGO"
                expected.append(names)
                expected_crops.append(crops)
        report["stage"] = "SAVE_PPTX"
        report["slides"] = deck.Slides.Count
        checkpoint()
        path = folder / "monitoring.pptx"
        deck.SaveAs(str(path), 24)
        if not path.is_file() or path.stat().st_size == 0:
            raise V7Error("PPT_SAVE_FAILED", "저장된 PPT 파일을 확인하지 못했습니다.")
        report["pptx"] = str(path)
        deck.Close()
        deck = None
        report["stage"] = "REOPEN_PPTX"
        checkpoint()
        reopened = app.Presentations.Open(str(path), 0, 0, -1)
        if reopened.Slides.Count != len(expected):
            raise V7Error("REOPEN_MISMATCH", f"슬라이드 수 불일치: 계획 {len(expected)} / "
                          f"저장 전 {report['slides']} / 재열기 {reopened.Slides.Count}")
        for i, names in enumerate(expected, 1):
            slide = reopened.Slides.Item(i)
            for name in names:
                shape = slide.Shapes.Item(name)
                if name.startswith("V7_CAPTURE_") and shape.Type != 13:
                    raise V7Error("REOPEN_MISMATCH", f"슬라이드 {i}의 그림 개체를 확인하지 못했습니다.")
            if not slide.Shapes.Item("V7_INFO_TABLE").HasTable:
                raise V7Error("REOPEN_MISMATCH", "편집 가능한 표를 확인하지 못했습니다.")
            for name, geometry in expected_crops[i - 1].items():
                verify_capture_geometry(slide.Shapes.Item(name), geometry)
                report["capture_geometry_reopen_checked"] += 1
        print(f"[캡처 배치 검증] 삽입 {report['capture_geometry_insert_checked']}개 / "
              f"재열기 {report['capture_geometry_reopen_checked']}개", flush=True)
        report["reopened_structure_verified"] = True
        report["visual_check"] = "user_required"
        report["stage"] = "COMPLETE"
        # PowerPoint는 열어 두어 사용자가 실제 표시와 분할 경계를 확인합니다.
    finally:
        # 다른 사용자의 PPT를 닫거나 PowerPoint 프로세스를 종료하지 않습니다.
        # 실패한 새 문서도 화면에 남겨 원인과 부분 결과를 확인할 수 있게 합니다.
        app = deck = None
        pythoncom.CoUninitialize()
