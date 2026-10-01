"""Verify the saved presentation against the content inserted this run."""
from v754.core import V7Error
from v754.powerpoint import (
    verify_source_link, verify_metadata_table, verify_analysis_slide,
    verify_capture_geometry,
)
from v9.ppt_notes import verify_notes
from v9.ppt_images import verify_inserted_ppi


def verify_reopened(reopened, details, prefix_count, report):
    expected = details.names
    expected_crops = details.crops
    expected_articles = details.articles
    expected_pages = details.pages
    expected_notes = details.notes
    expected_picture_texts = details.picture_texts
    if reopened.Slides.Count != len(expected)+prefix_count:
        raise V7Error("REOPEN_MISMATCH", f"슬라이드 수 불일치: 계획 {len(expected)+prefix_count} / "
                      f"저장 전 {report['slides']} / 재열기 {reopened.Slides.Count}")
    if prefix_count:
        from v9.dashboard_ppt import verify_dashboard
        verify_dashboard(reopened,report,'reopen')
        report['dashboard_reopen_checked']=prefix_count
        print(f'[요약 재열기 검증] 통계·색상·1건 눈금·순서·노트·상세 링크 {prefix_count}장',flush=True)
    for i, names in enumerate(expected, 1):
        slide = reopened.Slides.Item(i+prefix_count)
        for name in names:
            shape = slide.Shapes.Item(name)
            if name.startswith("V7_CAPTURE_") and shape.Type != 13:
                raise V7Error("REOPEN_MISMATCH", f"슬라이드 {i}의 그림 개체를 확인하지 못했습니다.")
        if not slide.Shapes.Item("V7_INFO_TABLE").HasTable:
            raise V7Error("REOPEN_MISMATCH", "편집 가능한 표를 확인하지 못했습니다.")
        verify_source_link(slide.Shapes.Item("V7_INFO_TABLE").Table.Cell(2, 4).Shape,
                           expected_articles[i - 1])
        report["source_url_reopen_checked"] += 1
        verify_metadata_table(slide.Shapes.Item('V7_INFO_TABLE').Table, expected_articles[i - 1])
        report['metadata_reopen_checked'] += 1
        verify_analysis_slide(slide, expected_articles[i - 1], expected_pages[i - 1])
        report['analysis_reopen_checked'] += 1
        verify_notes(slide, expected_notes[i - 1])
        report["notes_reopen_checked"] += 1
        for name, geometry in expected_crops[i - 1].items():
            verify_capture_geometry(slide.Shapes.Item(name), geometry)
            if slide.Shapes.Item(name).AlternativeText != expected_picture_texts[i - 1][name]:
                raise V7Error('PPT_IMAGE_LABEL_MISMATCH', '저장된 그림의 원본·축소본 식별 기록이 다릅니다.')
            report["capture_geometry_reopen_checked"] += 1
        for tile in expected_pages[i - 1]['tiles']:
            name = f"V7_CAPTURE_{tile['capture']['index']}_{tile['part']}"
            measurement = verify_inserted_ppi(slide.Shapes.Item(name), tile)
            if measurement is not None:
                report['ppt_ppi_reopen_measurements'].append(dict(measurement, cafe_id=expected_articles[i - 1]['cafe_id'], article_id=expected_articles[i - 1]['id'], capture_index=tile['capture']['index'], slide=i+prefix_count))
    if len(report['ppt_ppi_insert_measurements']) != len(report['ppt_ppi_reopen_measurements']):
        raise V7Error('PPT_IMAGE_PPI_MISMATCH', '삽입·재열기 PPI 검증 개수가 다릅니다.')
    print(f"[PPT 유효 PPI 실측] 삽입 {len(report['ppt_ppi_insert_measurements'])}개 / 재열기 {len(report['ppt_ppi_reopen_measurements'])}개", flush=True)
    print(f"[캡처 배치 검증] 삽입 {report['capture_geometry_insert_checked']}개 / "
          f"재열기 {report['capture_geometry_reopen_checked']}개", flush=True)
    print(f"[원문 URL 검증] 표시·링크 삽입 {report['source_url_insert_checked']}개 / "
          f"재열기 {report['source_url_reopen_checked']}개", flush=True)
