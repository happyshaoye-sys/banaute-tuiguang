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

    def test_copy_component_renders_a_visible_toolbar_button(self):
        frame = pd.DataFrame([{"品类": "连衣裙"}])

        html = build_dataframe_copy_button_html(frame, button_key="category-basic")

        self.assertIn("window.frameElement", html)
        self.assertIn("window.parent", html)
        self.assertIn('button.setAttribute("aria-label", copyLabel)', html)
        self.assertIn('"right:6.25rem"', html)
        self.assertIn("clipboard.writeText", html)
        self.assertIn('parentDocument.execCommand("copy")', html)
        self.assertIn("metox-copy-category-basic", html)
        self.assertNotIn('button[aria-label="Download as CSV"]', html)


if __name__ == "__main__":
    unittest.main()
