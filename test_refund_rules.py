"""Boundary regressions for the 30-day cohort refund-risk contract."""
from __future__ import annotations

import io
import unittest

import pandas as pd

import core
from core.analysis import refund_risk
from core.exporter import export_all_sheets
from core.segmentation import _apply_matrix


class RefundRuleBoundaryTests(unittest.TestCase):
    @staticmethod
    def _matrix_input(rates: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "宝贝ID": [f"P{i}" for i in range(len(rates))],
                "流量定位": ["金牛款"] * len(rates),
                "价值定位": ["金牛款"] * len(rates),
                "退款率": rates,
                "在仓库存": [100] * len(rates),
                "在途": [0] * len(rates),
                "推广投产": [2] * len(rates),
                "商品访客数": [100] * len(rates),
                "付费": [0] * len(rates),
            }
        )

    def test_matrix_uses_strict_70_and_80_percent_boundaries(self):
        result = _apply_matrix(self._matrix_input([0.7, 0.700001, 0.8, 0.800001]))

        self.assertEqual(result.loc[0, "商品周期"], "放量")
        self.assertNotIn("退款", result.loc[0, "风险标签"])

        self.assertEqual(result.loc[1, "商品周期"], "待优化")
        self.assertIn("⚠高退款", result.loc[1, "风险标签"])

        self.assertEqual(result.loc[2, "商品周期"], "待优化")
        self.assertIn("⚠高退款", result.loc[2, "风险标签"])
        self.assertNotIn("🚨极高退款", result.loc[2, "风险标签"])

        self.assertEqual(result.loc[3, "商品周期"], "待优化")
        self.assertIn("🚨极高退款", result.loc[3, "风险标签"])
        self.assertNotIn("⚠高退款", result.loc[3, "风险标签"])

    def test_core_public_entrypoint_reuses_segmentation_implementation(self):
        self.assertIs(core._apply_matrix, _apply_matrix)
        direct = _apply_matrix(self._matrix_input([0.800001]))
        public = core._apply_matrix(self._matrix_input([0.800001]))
        pd.testing.assert_frame_equal(direct, public)

    def test_page_and_excel_share_strict_70_percent_line(self):
        frame = pd.DataFrame(
            {
                "宝贝ID": ["P70", "P7001", "P80", "P8001"],
                "商品名称": ["A", "B", "C", "D"],
                "淘宝类目": ["测试"] * 4,
                "GMV": [100] * 4,
                "成功退款金额": [70, 70.01, 80, 80.01],
                "退款率": [0.7, 0.7001, 0.8, 0.8001],
                "付费": [1] * 4,
                "综合等级": ["A"] * 4,
                "对应操作": ["待优化"] * 4,
                "风险标签": ["", "⚠高退款", "⚠高退款", "🚨极高退款"],
                "在仓库存": [100] * 4,
            }
        )
        expected = ["P8001", "P80", "P7001"]

        self.assertEqual([row["宝贝ID"] for row in refund_risk(frame)], expected)

        workbook = export_all_sheets(frame)
        excel_refund = pd.read_excel(io.BytesIO(workbook), sheet_name="退款风险")
        self.assertEqual(excel_refund["宝贝ID"].astype(str).tolist(), expected)


if __name__ == "__main__":
    unittest.main()
