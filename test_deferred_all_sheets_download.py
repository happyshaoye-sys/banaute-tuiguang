"""Regression contract for the crash-safe all-sheets download flow."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class DeferredAllSheetsDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        block_start = source.index("    with ce2:")
        block_end = source.index("    st.caption(", block_start)
        cls.export_block = source[block_start:block_end]

    def test_all_sheets_is_generated_by_deferred_download_callable(self):
        self.assertIn("all_sheets_download = partial(", self.export_block)
        self.assertIn("export_all_sheets,", self.export_block)
        self.assertIn("data=all_sheets_download", self.export_block)

    def test_large_workbook_is_not_retained_in_session_state(self):
        self.assertNotIn('st.session_state["all_xlsx"] =', self.export_block)
        self.assertNotIn('st.session_state["all_xlsx_meta"] =', self.export_block)
        self.assertIn('st.session_state.pop("all_xlsx", None)', self.export_block)
        self.assertIn('st.session_state.pop("all_xlsx_meta", None)', self.export_block)

    def test_download_does_not_trigger_full_page_rerun(self):
        self.assertIn('on_click="ignore"', self.export_block)
        self.assertNotIn('st.button("📦 生成全部 Sheet Excel"', self.export_block)


if __name__ == "__main__":
    unittest.main()
