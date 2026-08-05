"""Regression coverage for the category-analysis copy action."""
from __future__ import annotations

import unittest

import pandas as pd

from core.dataframe_clipboard import (
    build_dataframe_copy_button_html,
    dataframe_to_clipboard_text,
)


class DataframeClipboardTests(unittest.TestCase):
    def test_clipboard_text_is_tab_separated_with_headers_and_no_index(self):
        frame = pd.DataFrame([{"品类": "连衣裙", "GMV": 288392.96}])

        text = dataframe_to_clipboard_text(frame)

        self.assertEqual(text, "品类\tGMV\n连衣裙\t288392.96\n")

    def test_copy_component_targets_the_native_dataframe_toolbar(self):
        frame = pd.DataFrame([{"品类": "连衣裙"}])

        html = build_dataframe_copy_button_html(frame, button_key="category-basic")

        self.assertIn('button[aria-label="Download as CSV"]', html)
        self.assertIn("一键复制", html)
        self.assertIn("document.currentScript", html)
        self.assertIn("navigator.clipboard.writeText", html)
        self.assertIn('document.execCommand("copy")', html)
        self.assertIn("MutationObserver", html)
        self.assertIn("metox-copy-category-basic", html)


if __name__ == "__main__":
    unittest.main()
