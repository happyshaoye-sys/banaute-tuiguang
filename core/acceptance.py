"""Helpers for data-source acceptance summaries."""

DATA_SOURCE_COL = "\u6570\u636e\u6e90"
STATUS_COL = "\u72b6\u6001"
FILE_COUNT_COL = "\u6587\u4ef6\u6570"
PROVIDED_STATUS = "\u2713 \u5df2\u63d0\u4f9b"
MISSING_STATUS = "\u2717 \u672a\u63d0\u4f9b"


def build_source_status(files_info, expected_sources):
    """Build the visible data-source acceptance table from loaded current files."""
    source_files = {}
    expected = set(expected_sources)
    for entry in files_info or []:
        source = entry.get("source")
        if source not in expected:
            continue
        source_files.setdefault(source, []).append(entry.get("name", ""))

    status_rows = []
    for source in expected_sources:
        count = len(source_files.get(source, []))
        status_rows.append(
            {
                DATA_SOURCE_COL: source,
                STATUS_COL: PROVIDED_STATUS if count > 0 else MISSING_STATUS,
                FILE_COUNT_COL: count,
            }
        )
    return status_rows
