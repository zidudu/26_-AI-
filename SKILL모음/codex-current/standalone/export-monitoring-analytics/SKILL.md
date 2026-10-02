---
name: export-monitoring-analytics
description: 수집 문서의 출처·키워드·기간별 통계, 추이, 상세 목록과 Excel 내보내기를 만들 때 사용한다. 고유 문서 수와 다중 매칭 수, 최초 수집일과 게시일, 현재 적용된 필터와 비교 집계 범위를 구분한다.
---

# 모니터링 통계와 Excel 내보내기

## Workflow
1. Define the entity being counted, date field/timezone, period boundaries and filter state. Read [metric/export contract](references/contracts.md). Do not assume publication date and first collection date mean the same thing.
2. Aggregate distinct source/item identities. Count keyword memberships separately because a record can match several keywords. Label each chart and total with its denominator.
3. Freeze applied filters, drill selection and export scope. Distinguish edited-but-not-applied controls and comparison charts that intentionally remove a filter dimension.
4. Use `scripts/export_records.py --input records.json --output report.xlsx --date-field first_collected --timezone Asia/Seoul` for a minimal auditable export. Requires openpyxl. Optional `--start` and `--end` are local ISO dates with exclusive end. The input schema and remaining filter responsibilities are in the reference.
5. Export details, counts and query metadata together. Preserve user text as text, numeric values as numbers and long identifiers as strings. Do not allow values beginning with `=` to become spreadsheet formulas.
6. Reopen the workbook and reconcile totals with exported rows and memberships. Inspect readability and date display. Explain omitted records and source scope.

Deliver the workbook and a concise statement of its counting/filter rules. The helper is a baseline exporter, not a replacement for a domain dashboard or a financial model.
