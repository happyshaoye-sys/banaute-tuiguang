import ast
import unittest
from pathlib import Path

import pandas as pd

from core.analysis import KPI_TREND_KEYS, build_daily_kpi_trends


ROOT = Path(__file__).resolve().parent


class DashboardKpiTrendDataTests(unittest.TestCase):
    def test_builds_daily_series_from_first_loaded_day_and_keeps_missing_gaps(self):
        first = pd.DataFrame({
            "GMV": [100, 200],
            "付费": [10, 20],
            "商品加购件数": [3, 7],
        })
        last = pd.DataFrame({
            "GMV": [120, 80],
            "付费": [20, 0],
            "商品加购件数": [5, 1],
        })
        after_end = pd.DataFrame({
            "GMV": [999],
            "付费": [1],
            "商品加购件数": [1],
        })
        day_map = {
            "2026-08-01": (None, [], 0, 0, set()),
            "2026-08-02": (first, [], 0, 0, {"P1", "P2"}),
            "2026-08-03": (None, [], 0, 0, set()),
            "2026-08-04": (last, [], 0, 0, {"P1"}),
            "2026-08-05": (after_end, [], 0, 0, {"P9"}),
        }

        result = build_daily_kpi_trends(day_map, end_date="2026-08-04")

        self.assertEqual(
            result["日期"].tolist(),
            ["2026-08-02", "2026-08-03", "2026-08-04"],
        )
        self.assertEqual(
            result.loc[0, list(KPI_TREND_KEYS)].tolist(),
            [300.0, 30.0, 2.0, 10.0, 2.0, 10.0],
        )
        self.assertTrue(result.loc[1, list(KPI_TREND_KEYS)].isna().all())
        self.assertEqual(
            result.loc[2, list(KPI_TREND_KEYS)].tolist(),
            [200.0, 20.0, 1.0, 10.0, 1.0, 6.0],
        )

    def test_empty_history_returns_stable_columns(self):
        result = build_daily_kpi_trends({}, end_date="2026-08-04")

        self.assertTrue(result.empty)
        self.assertEqual(result.columns.tolist(), ["日期", *KPI_TREND_KEYS])


class DashboardKpiTrendUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        cls.tree = ast.parse(cls.source)

    def test_uses_native_popover_and_line_chart_without_custom_component(self):
        render_function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_kpi_metric_with_trend"
        )
        rendered = ast.get_source_segment(self.source, render_function)

        self.assertIn("st.metric(", rendered)
        self.assertIn("st.popover(", rendered)
        self.assertIn("st.line_chart(", rendered)
        self.assertNotIn("components.html", rendered)

    def test_all_six_dashboard_metrics_render_a_daily_trend(self):
        trend_calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_render_kpi_metric_with_trend"
        ]

        self.assertEqual(len(trend_calls), 6)
        trend_keys = {
            keyword.value.value
            for call in trend_calls
            for keyword in call.keywords
            if keyword.arg == "trend_key" and isinstance(keyword.value, ast.Constant)
        }
        self.assertEqual(trend_keys, set(KPI_TREND_KEYS))


if __name__ == "__main__":
    unittest.main()
