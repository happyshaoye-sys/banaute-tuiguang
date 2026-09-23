"""Regression coverage for the category-analysis copy action."""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from core.dataframe_clipboard import (
    build_dataframe_copy_button_html,
    dataframe_to_clipboard_text,
)


ROOT = Path(__file__).resolve().parent


class DataframeClipboardTests(unittest.TestCase):
    def test_clipboard_text_is_tab_separated_with_headers_and_no_index(self):
        frame = pd.DataFrame([{"品类": "连衣裙", "GMV": 288392.96}])

        text = dataframe_to_clipboard_text(frame)

        self.assertEqual(text, "品类\tGMV\n连衣裙\t288392.96\n")

    def test_copy_component_extends_the_native_hover_toolbar(self):
        frame = pd.DataFrame([{"品类": "连衣裙"}])

        html = build_dataframe_copy_button_html(frame, button_key="category-basic")

        self.assertIn("window.frameElement", html)
        self.assertIn("window.parent", html)
        self.assertIn('button.setAttribute("aria-label", copyLabel)', html)
        self.assertIn('"Download as CSV"', html)
        self.assertIn("lowestCommonAncestor", html)
        self.assertIn("templateSlot.cloneNode(true)", html)
        self.assertIn("renderNativeSizedIcon", html)
        self.assertIn("toolbar.insertBefore(copySlot, firstSlot)", html)
        self.assertIn("MutationObserver", html)
        self.assertIn("clipboard.writeText", html)
        self.assertIn('parentDocument.execCommand("copy")', html)
        self.assertIn("metox-copy-category-basic", html)
        self.assertNotIn('"position:absolute"', html)
        self.assertNotIn('"right:6.25rem"', html)
        self.assertNotIn('removeAttribute("data-testid")', html)

    def test_legacy_copy_runs_inside_the_original_click_activation(self):
        frame = pd.DataFrame([{"category": "dress"}])

        html = build_dataframe_copy_button_html(frame, button_key="category-basic")
        copy_function = html.split("async function copyToClipboard()", 1)[1].split(
            "function findTargetDataframe()", 1
        )[0]

        self.assertLess(
            copy_function.index("fallbackCopy(clipboardText)"),
            copy_function.index("clipboard.writeText"),
        )
        self.assertIn('textarea.focus({ preventScroll: true })', html)
        self.assertIn("textarea.setSelectionRange(0, textarea.value.length)", html)
        self.assertIn('data-metox-copy-state", "copied"', html)
        self.assertIn('data-metox-copy-state", "failed"', html)

    def test_copy_component_reclaims_stale_button_after_rerun(self):
        frame = pd.DataFrame([{"category": "dress"}])

        html = build_dataframe_copy_button_html(frame, button_key="category-basic")

        self.assertIn("const owner =", html)
        self.assertIn("data-metox-copy-owner", html)
        self.assertIn('current.getAttribute("data-metox-copy-owner") === owner', html)
        self.assertIn("removeOwnedButton", html)
        self.assertIn('window.addEventListener("pagehide"', html)

    def test_category_tables_use_distinct_copy_buttons(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

        self.assertIn('button_key="category-basic"', source)
        self.assertIn('button_key="category-investment"', source)
        self.assertEqual(source.count("st.iframe("), 2)
        self.assertEqual(source.count("height=1,"), 2)
        self.assertNotIn("height=0,", source)
        self.assertNotIn("streamlit.components.v1", source)
        self.assertIn(
            "st.dataframe(category_investment_df, width=\"stretch\", hide_index=True)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
