"""Retain the original source/grounding report and display both provider counts."""
from v754.comparison import write_comparison as legacy_write_comparison


def write_comparison(folder, report):
    legacy_write_comparison(folder, report)
    path = folder / 'comparison.html'
    text = path.read_text(encoding='utf-8')
    text = text.replace('V7.5.4.1 원문·분석 비교', 'V9.5 원문·분석 비교')
    text = text.replace(' · 캐시 ', f" · Codex 호출 {int(report.get('codex_calls', 0))}회 · 캐시 ", 1)
    path.write_text(text, encoding='utf-8')

