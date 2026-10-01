# Input and metrics
Helper input: JSON array with source, id, title, first_collected and published (ISO aware timestamps, published may be null), keywords (array of strings), optional url. One row per logical record. Duplicate source/id pairs are rejected rather than silently choosing a version. Preselect source/keyword/drill filters before invoking; record them via `--scope` text. The helper implements only the date filter, using the chosen date field converted to the requested IANA zone. A missing chosen timestamp is excluded and counted in metadata.

Sheets: Records, Sources, Keywords, Query. Total records equals unique exported rows. Source counts sum to total records. Keyword counts sum to memberships and can exceed total records. Duplicate keywords within one record count once. Records with no keywords still count as records. Source+id remains the identity even if two sources share a numerical ID.

Excel text is written with explicit string cell type so leading `=`, `+`, `-`, `@` are not formulas. Do not silently truncate raw bodies into Excel; this helper exports title/metadata only and rejects strings over Excel's 32767-character cell limit. Retain full raw data in its canonical store if a larger export is needed. For CSV outputs, separately address spreadsheet formula interpretation.

Metadata should identify applied source/keyword filters, date field, timezone, half-open range, selected drill scope, total input rows, excluded missing-date rows, returned rows and generation time. If a comparison removes one dimension, label it and avoid presenting its denominator as the filtered detail total.

Origin: V10 database statistics and web/base.js Excel/query export behavior. The bundled workbook is newly generalized and not a byte-for-byte copy of the original browser exporter.
