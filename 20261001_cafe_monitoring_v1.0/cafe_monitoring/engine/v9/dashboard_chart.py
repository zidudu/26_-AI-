"""Owned embedded chart lifecycle; never closes an unrelated Excel workbook.

Office may refresh chart defaults when its data workbook closes. Bind and read
the data first, close that workbook, then format a fresh Shape.Chart reference.
Cleanup errors must not replace the original data error. A cleanup warning is
acceptable only with strict immediate AND saved/reopened chart verification.
"""
from time import sleep
from v754.core import V7Error

READ_ATTEMPTS = 20
READ_DELAY = 0.15
BUSY_HRESULTS = {-2147418111, -2147417846, -2146777998}
FINALIZE_ATTEMPTS = 3
# Office.MsoChartElementType: explicit layout commands, not only display flags.
MSO_ELEMENT_CHART_TITLE_NONE = 0
MSO_ELEMENT_LEGEND_NONE = 100


def pause():
    try:
        import pythoncom
    except ImportError:
        pass
    else:
        pythoncom.PumpWaitingMessages()
    sleep(READ_DELAY)


def transient(exc):
    return isinstance(exc, AttributeError) or getattr(exc, 'hresult', None) in BUSY_HRESULTS


def read_ready(read, accept=lambda value: True, message='차트 준비 상태를 확인하지 못했습니다.'):
    """Bounded reads only; never repeats a possibly completed mutation."""
    for attempt in range(READ_ATTEMPTS):
        try:
            value = read()
            if accept(value):
                return value
        except Exception as exc:
            if not transient(exc) or attempt == READ_ATTEMPTS - 1:
                raise
        if attempt < READ_ATTEMPTS - 1:
            pause()
    raise V7Error('DASHBOARD_CHART_DATA_NOT_READY', message)


def data_values(chart):
    return (chart.SeriesCollection().Count,
            tuple(chart.SeriesCollection(1).XValues),
            tuple(chart.SeriesCollection(1).Values))


def expected_values(item):
    return (1, tuple(item['categories']),
            tuple(v if v is not None else 0 for v in item['values']))


def error_record(exc):
    return {'type': type(exc).__name__, 'message': str(exc),
            'code': getattr(exc, 'code', None), 'hresult': getattr(exc, 'hresult', None)}


def populate_chart(shape, item, font, slide_name, audit):
    entry = {'slide': slide_name, 'chart': item['name'], 'stage': 'activate',
             'rows': len(item['values']), 'verified': False}
    audit.append(entry)
    wb = sheet = close = None
    try:
        try:
            shape.Chart.ChartData.Activate()
            entry['stage'] = 'workbook_ready'
            wb = read_ready(lambda: shape.Chart.ChartData.Workbook,
                            lambda value: value is not None)
            sheet = read_ready(lambda: wb.Worksheets.Item(1), lambda value: value is not None)
            entry['stage'] = 'write_cells'
            sheet.Cells.Clear()
            rows = [('항목', '게시글 수', '확정 게시글 수', '상태')]
            rows += [(n, v if v is not None else 0, v, '집계' if v is not None else '미확인')
                     for n, v in zip(item['categories'], item['values'])]
            rows = tuple(rows)
            address = 'A1:D' + str(len(rows))
            sheet.Range(address).Value = rows
            entry['stage'] = 'read_cells'
            read_ready(lambda: tuple(tuple(r) for r in sheet.Range(address).Value),
                       lambda actual: actual == rows, '차트 통합문서의 항목·값이 입력 자료와 다릅니다.')
            entry['stage'] = 'bind_series'
            source = "='" + str(sheet.Name).replace("'", "''") + "'!$A$1:$B$" + str(len(rows))
            shape.Chart.SetSourceData(source, 2)
            entry['stage'] = 'read_series'
            read_ready(lambda: data_values(shape.Chart), lambda actual: actual == expected_values(item),
                       '차트 계열의 항목·값이 입력 자료와 다릅니다. 기본 예제 차트는 저장하지 않습니다.')
        except BaseException as exc:
            entry['primary_error'] = error_record(exc)
            entry['failed_stage'] = entry['stage']
            raise
        finally:
            if wb is not None:
                # Resolve Close on the current owned chart workbook, rather than
                # retaining a dynamic wrapper through SetSourceData/Excel updates.
                # Invoke once: an ambiguous failure is never blindly retried.
                try:
                    close = read_ready(lambda: shape.Chart.ChartData.Workbook.Close, callable)
                    close(True)
                    entry['workbook_close'] = 'closed'
                except Exception as exc:
                    entry['workbook_close'] = 'warning'
                    entry['cleanup_error'] = error_record(exc)
                    print(f'[차트 통합문서 닫기 경고] {slide_name} / {item["name"]} / '
                          f'{type(exc).__name__}: {exc} / 차트 저장·재열기 검증 필수', flush=True)
        # Release embedded Excel wrappers before the final chart formatting.
        close = sheet = wb = None
        entry['stage'] = 'refresh_after_close'
        shape.Chart.Refresh()
        read_ready(lambda: data_values(shape.Chart), lambda actual: actual == expected_values(item))
        entry['stage'] = 'format_after_close'
        style(shape.Chart, item, font)
        entry['stage'] = 'verify_after_format'
        verify_chart(shape.Chart, item)
        entry.update(stage='verified', verified=True)
        print(f'[요약 차트 검증] {slide_name} / {item["name"]} / {entry["rows"]}개 항목·값 일치', flush=True)
    except BaseException as exc:
        entry.setdefault('primary_error', error_record(exc))
        entry.setdefault('failed_stage', entry['stage'])
        print(f'[요약 차트 실패] {slide_name} / {item["name"]} / {entry["failed_stage"]} / '
              f'{type(exc).__name__}: {exc}', flush=True)
        raise


def style(chart, item, font):
    from v9.dashboard_ppt import rgb, INK, GRAY
    values, colors, limits = item['values'], item['colors'], item['axis']
    chart.HasTitle = False
    chart.HasLegend = False
    series = chart.SeriesCollection(1)
    series.ApplyDataLabels()
    series.DataLabels().Position = 2
    series.DataLabels().Font.Name = font
    series.DataLabels().Font.Size = 9
    series.DataLabels().Font.Color = rgb(INK)
    chart.ChartGroups(1).GapWidth = 50
    for i, (v, color) in enumerate(zip(values, colors), 1):
        pt = series.Points(i)
        pt.Format.Fill.Solid()
        pt.Format.Fill.ForeColor.RGB = rgb(color)
        pt.Format.Line.Visible = 0
        if v is None:
            pt.DataLabel.Text = '—'
    category = chart.Axes(1)
    category.ReversePlotOrder = True
    category.TickLabelSpacing = 1
    category.TickMarkSpacing = 1
    category.TickLabels.Font.Name = font
    category.TickLabels.Font.Size = 8.5
    category.TickLabels.Font.Color = rgb(GRAY)
    value = chart.Axes(2)
    value.MinimumScale = 0
    value.MaximumScale = limits['maximum']
    value.MajorUnit = 1
    value.TickLabels.NumberFormat = '0'
    value.TickLabels.Font.Name = font
    value.TickLabels.Font.Size = 8 if limits['maximum'] <= 20 else 6
    if limits['maximum'] > 20:
        value.TickLabels.Orientation = 90
    value.TickLabels.Font.Color = rgb(GRAY)
    category.Crosses = 2
    chart.ChartArea.Format.Fill.Visible = 0
    chart.ChartArea.Format.Line.Visible = 0
    chart.PlotArea.Format.Fill.Visible = 0
    chart.PlotArea.Format.Line.Visible = 0


def decorations(chart):
    return {'has_title': bool(chart.HasTitle), 'has_legend': bool(chart.HasLegend)}


def audit_entry(report, slide_name, chart_name):
    return next(r for r in report['dashboard_chart_audit']
                if r['slide'] == slide_name and r['chart'] == chart_name)


def finalize_decorations(shape, item, entry, attempt):
    """Only finalize decorations; never repair counts, order, axes or colors."""
    record = {'attempt': attempt, 'stage': 'refresh'}
    entry.setdefault('finalization', []).append(record)
    try:
        shape.Chart.Refresh()
        record['before'] = decorations(shape.Chart)
        record['stage'] = 'verify_content'
        verify_chart_content(shape.Chart, item)
        record['stage'] = 'remove_title_legend'
        shape.Chart.SetElement(MSO_ELEMENT_CHART_TITLE_NONE)
        shape.Chart.SetElement(MSO_ELEMENT_LEGEND_NONE)
        shape.Chart.HasTitle = False
        shape.Chart.HasLegend = False
        record['after'] = decorations(shape.Chart)
        record['stage'] = 'applied'
    except BaseException as exc:
        record['error'] = error_record(exc)
        print(f'[요약 차트 마무리 실패] {entry["slide"]} / {entry["chart"]} / '
              f'{record["stage"]} / {exc}', flush=True)
        raise


def verify_chart(chart, item):
    verify_chart_content(chart, item)
    flags = decorations(chart)
    if flags['has_title'] or flags['has_legend']:
        raise V7Error('DASHBOARD_CHART_TITLE_MISMATCH',
                      f'요약 차트 {item["name"]}: 자동 제목={flags["has_title"]}, '
                      f'중복 범례={flags["has_legend"]}가 남았습니다.')


def verify_chart_content(chart, item):
    from v9.dashboard_ppt import rgb
    if data_values(chart) != expected_values(item):
        raise V7Error('DASHBOARD_CHART_MISMATCH', '요약 차트의 계열 수·항목·집계가 저장 자료와 다릅니다.')
    value = chart.Axes(2)
    if (value.MinimumScale != 0 or value.MaximumScale != item['axis']['maximum'] or value.MajorUnit != 1
            or not chart.Axes(1).ReversePlotOrder or str(value.TickLabels.NumberFormat) != '0'):
        raise V7Error('DASHBOARD_AXIS_MISMATCH', '요약 차트 범위·순서 또는 1건 눈금이 다릅니다.')
    series = chart.SeriesCollection(1)
    for i, (v, color) in enumerate(zip(item['values'], item['colors']), 1):
        point = series.Points(i)
        if (point.Format.Fill.ForeColor.RGB != rgb(color)
                or (v is None and point.DataLabel.Text != '—')):
            raise V7Error('DASHBOARD_CHART_MISMATCH', '요약 차트의 색상 또는 미완료 표시가 다릅니다.')
