"""执行表「列设置」回归测试（v2.68）。

背景
----
`app.py` 里曾有一段硬编码「强制显示」白名单：链接投产状态 / 可售天数状态 /
商品加购率状态 / 商品支付转化率状态 / 推广加购率状态 / v9.3信号 /
MetoX算法命中 / MetoX算法分 / MetoX算法原因 / 商品加购率 / 推广加购率 /
当前有效投放计划数 —— 每次 rerun 都把用户勾掉的这些列塞回偏好，
表现为「取消了勾选、表格里还在，而且列设置计数比勾选的还多」。

v2.68 的处理：显示严格按勾选（`core.schema.resolve_display_columns`），
导出仍全量（`execution_export_frame` -> `EXPORT_COLS`）。

本文件守住三件事
----------------
1. `resolve_display_columns` 不注入任何列，且顺序恒按 EXPORT_COLS；
2. 反复 rerun（多次调用）也不会把取消勾选的列"复活"；
3. 显示被子集限制时，导出帧仍是全量 EXPORT_COLS。
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

import pandas as pd

from core.schema import (
    EXPORT_COLS,
    execution_export_frame,
    resolve_display_columns,
)

ROOT = Path(__file__).resolve().parent

# v2.68 之前被硬编码强制显示的 12 列（回归样本）
EX_FORCED_COLUMNS = [
    "链接投产状态", "可售天数状态",
    "商品加购率状态", "商品支付转化率状态", "推广加购率状态",
    "v9.3信号", "MetoX算法命中", "MetoX算法分", "MetoX算法原因",
    "商品加购率", "推广加购率", "当前有效投放计划数",
]


class ResolveDisplayColumnsTests(unittest.TestCase):
    def test_no_forced_columns_are_injected(self):
        """用户明确不勾的状态/判定列，绝不能出现。"""
        picked = [c for c in EXPORT_COLS if c not in EX_FORCED_COLUMNS]
        shown = resolve_display_columns(EXPORT_COLS, picked)
        for column in EX_FORCED_COLUMNS:
            with self.subTest(column=column):
                self.assertNotIn(column, shown)
        self.assertEqual(shown, picked)

    def test_order_always_follows_export_cols(self):
        """展示顺序按 EXPORT_COLS，不按勾选顺序。"""
        picked = ["GMV", "宝贝ID", "链接投产状态", "货号"]
        shown = resolve_display_columns(EXPORT_COLS, picked)
        self.assertEqual(shown, [c for c in EXPORT_COLS if c in set(picked)])

    def test_unchecking_survives_repeated_reruns(self):
        """连续多次 rerun 不得把取消勾选的列塞回来（旧实现的核心缺陷）。"""
        picked = [c for c in EXPORT_COLS if c not in ("链接投产状态", "可售天数状态")]
        shown = resolve_display_columns(EXPORT_COLS, picked)
        for _ in range(5):
            shown = resolve_display_columns(EXPORT_COLS, shown)
            self.assertNotIn("链接投产状态", shown)
            self.assertNotIn("可售天数状态", shown)
        self.assertEqual(len(shown), len(EXPORT_COLS) - 2)

    def test_unknown_and_stale_columns_are_dropped(self):
        picked = ["宝贝ID", "不存在的列", "GMV"]
        self.assertEqual(resolve_display_columns(EXPORT_COLS, picked), ["宝贝ID", "GMV"])

    def test_empty_pick_means_all_columns(self):
        """界面约定：清空=全部列。"""
        self.assertEqual(resolve_display_columns(EXPORT_COLS, []), list(EXPORT_COLS))
        self.assertEqual(resolve_display_columns(EXPORT_COLS, None), list(EXPORT_COLS))
        self.assertEqual(
            resolve_display_columns(EXPORT_COLS, ["不存在的列"]), list(EXPORT_COLS)
        )

    def test_subset_of_available_columns_keeps_relative_order(self):
        """all_columns 只含实际存在的列时（execution_df 缺列）也不出岔子。"""
        available = [c for c in EXPORT_COLS if c not in ("款式主图", "销售排名")]
        picked = ["GMV", "宝贝ID", "款式主图"]
        self.assertEqual(
            resolve_display_columns(available, picked), ["宝贝ID", "GMV"]
        )


class ExportStaysCompleteTests(unittest.TestCase):
    def test_export_frame_is_always_full_even_when_display_is_trimmed(self):
        frame = pd.DataFrame(
            {
                "宝贝ID": ["A", "B"],
                "GMV": [100.0, 200.0],
                "链接投产": [3.0, 4.0],
                "链接投产状态": ["高", "正常"],
            }
        )
        displayed = resolve_display_columns(EXPORT_COLS, ["宝贝ID", "GMV"])
        self.assertEqual(displayed, ["宝贝ID", "GMV"])
        exported = execution_export_frame(frame)
        self.assertEqual(list(exported.columns), list(EXPORT_COLS))
        self.assertEqual(len(exported.columns), 81)
        self.assertIn("链接投产状态", exported.columns)
        self.assertEqual(exported.loc[0, "链接投产状态"], "高")


class StylingWithHiddenColumnsTests(unittest.TestCase):
    """把状态列/关键列全部取消勾选后，着色链路不得报错（v2.68 放开勾选后的新边界）。"""

    def _frame(self):
        rows = {
            "宝贝ID": ["A", "B"],
            "淘宝类目": ["T恤", "T恤"],
            "GMV": [1000.0, 500.0],
            "链接投产状态": ["高", "低"],
            "可售天数状态": ["紧急", "充足"],
            "商品加购率状态": ["正常", "高"],
        }
        frame = pd.DataFrame(rows)
        for column in EXPORT_COLS:
            if column not in frame.columns:
                frame[column] = ""
        return frame

    def test_style_survives_single_visible_column(self):
        from core.exporter import style_execution_table

        frame = self._frame()
        shown = resolve_display_columns(EXPORT_COLS, ["宝贝ID"])
        self.assertEqual(shown, ["宝贝ID"])
        styled = style_execution_table(frame[shown], ranking_df=frame)
        self.assertEqual(list(styled.data.columns), ["宝贝ID"])

    def test_style_survives_all_status_columns_hidden(self):
        from core.exporter import style_execution_table

        frame = self._frame()
        kept = [c for c in EXPORT_COLS if c not in EX_FORCED_COLUMNS]
        shown = resolve_display_columns(EXPORT_COLS, kept)
        styled = style_execution_table(frame[shown], ranking_df=frame)
        self.assertEqual(list(styled.data.columns), shown)


class AppWiringTests(unittest.TestCase):
    """app.py 侧的结构守护（AST + 源码断言）。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

    def test_app_uses_shared_helper(self):
        self.assertIn("resolve_display_columns", self.source)
        tree = ast.parse(self.source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core.schema":
                imported.update(alias.name for alias in node.names)
        self.assertIn("resolve_display_columns", imported)

    def test_forced_column_append_loop_is_gone(self):
        self.assertNotIn('st.session_state["exec_cols_pref"].append', self.source)
        self.assertNotIn(
            'if c in all_cols and c not in st.session_state["exec_cols_pref"]',
            self.source,
        )
        self.assertNotIn('"链接投产状态", "可售天数状态",', self.source)
        # 旧白名单里成串出现的「状态列组」不得再以列表形式存在
        self.assertNotIn('"商品加购率状态", "商品支付转化率状态"', self.source)
        self.assertNotIn('"MetoX算法命中", "MetoX算法分", "MetoX算法原因"', self.source)
        self.assertNotIn('"商品加购率", "推广加购率", "当前有效投放计划数"', self.source)


if __name__ == "__main__":
    unittest.main()
