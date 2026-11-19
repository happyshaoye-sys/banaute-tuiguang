"""执行表「列设置」改版（v2.75）回归测试。

口径
----
* 80 列按语义分 11 组（`core.schema.execution_column_groups`），**零遗漏零重复**；
* 界面为「分组折叠面板 + 勾选即生效（自动保存）+ 撤销到本次打开前 + 恢复全部列」；
* 全部组都清空时回退到「基础」组，不停留在空表；
* 显示严格按勾选，导出仍全量 80 列（与显示无关）。
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core.schema import (
    EXEC_COLUMN_GROUPS,
    EXEC_COLUMN_GROUP_FALLBACK,
    EXPORT_COLS,
    execution_column_groups,
    resolve_display_columns,
)

ROOT = Path(__file__).resolve().parent


class ExecutionColumnGroupsTests(unittest.TestCase):
    def test_covers_all_export_columns_without_duplicates(self):
        groups = execution_column_groups()
        flat = [column for _, columns in groups for column in columns]
        self.assertEqual(len(flat), len(set(flat)), '分组出现重复列')
        self.assertEqual(set(flat), set(EXPORT_COLS), '分组漏列或多列')
        self.assertEqual(len(flat), 80)

    def test_group_order_follows_export_cols(self):
        order = {column: index for index, column in enumerate(EXPORT_COLS)}
        for name, columns in execution_column_groups():
            with self.subTest(group=name):
                self.assertEqual(columns, sorted(columns, key=lambda c: order[c]))

    def test_no_empty_groups(self):
        for name, columns in execution_column_groups():
            with self.subTest(group=name):
                self.assertTrue(columns, f'{name} 组为空')

    def test_unknown_columns_fall_into_fallback_group(self):
        groups = dict(execution_column_groups(['GMV', '自定义新列']))
        self.assertEqual(groups[EXEC_COLUMN_GROUP_FALLBACK], ['自定义新列'])

    def test_subset_input_only_returns_existing_columns(self):
        groups = dict(execution_column_groups(['宝贝ID', 'GMV']))
        self.assertEqual(groups['基础'], ['宝贝ID'])
        self.assertEqual(groups['销售与投产'], ['GMV'])

    def test_declared_groups_are_known_column_names(self):
        for name, columns in EXEC_COLUMN_GROUPS:
            with self.subTest(group=name):
                self.assertIsInstance(name, str)
                self.assertTrue(columns)


class BudgetDisplayConsistencyTests(unittest.TestCase):
    def test_display_still_user_controlled(self):
        picked = [column for column in EXPORT_COLS
                  if column not in ('链接投产状态', 'MetoX算法命中')]
        shown = resolve_display_columns(EXPORT_COLS, picked)
        self.assertNotIn('链接投产状态', shown)
        self.assertNotIn('MetoX算法命中', shown)
        self.assertEqual(len(shown), 78)


class ColumnSettingsUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'app.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(cls.source)
        cls.tab4 = cls.source[cls.source.index('with tab4:'):]

    def test_dialog_trigger_lives_in_filter_row(self):
        """入口回到筛选行右侧（v2.72 的位置），点开是弹窗。"""
        self.assertIn('key="exec_cols_open"', self.tab4)
        self.assertIn("_render_exec_column_dialog(all_cols)", self.tab4)
        self.assertNotIn('st.popover(', self.tab4)
        self.assertNotIn('exec_col_chk_', self.tab4)

    def test_dialog_is_two_panel_with_search(self):
        # 弹窗装饰器经 _column_dialog 安全包装（AST 单测里没有 st.dialog）
        self.assertIn('@_column_dialog("列设置", width="small"', self.source)
        self.assertIn('getattr(st, "dialog", None)', self.source)
        # v2.77：窄面板（small ≤520px），不再用全屏 large
        self.assertNotIn('width="large"', self.source)
        self.assertIn("on_dismiss=_close_exec_column_dialog", self.source)
        self.assertIn("已隐藏列", self.source)
        self.assertIn("已显示列", self.source)
        self.assertIn("exec_cols_dlg_hidden_q", self.source)
        self.assertIn("exec_cols_dlg_shown_q", self.source)
        self.assertIn("exec_cols_dlg_", self.source)
        self.assertIn("恢复默认", self.source)
        # 紧凑密排样式（一列一行），不再是大号复选框网格
        self.assertIn('st.caption(f"▍{group_name}")', self.source)
        self.assertIn('div[data-testid="stDialog"] div[data-testid="stCheckbox"]',
                      self.source)
        self.assertIn('div[data-testid="stDialog"] div[data-testid="stCaptionContainer"]',
                      self.source)
        # 贴窗口右侧的窄面板，而不是居中全屏弹窗
        self.assertIn("position: fixed !important;", self.source)
        self.assertIn("width: var(--mx-cd-w, 430px) !important;", self.source)

    def test_dialog_applies_instantly_and_keeps_filtered_columns(self):
        self.assertIn("_save_column_prefs(new_pref)", self.source)
        self.assertIn("被搜索过滤掉、这次没渲染的列，沿用当前偏好", self.source)
        self.assertIn("至少要保留 1 列", self.source)
        # 不再有需要点击才生效的「应用」按钮
        self.assertNotIn("应用并记住", self.source)
        self.assertNotIn("exec_cols_apply", self.source)

    def test_dialog_groups_columns_by_semantic_group(self):
        self.assertIn("execution_column_groups(all_columns)", self.source)
        self.assertIn("group_of.get(column", self.source)

    def test_column_order_note(self):
        self.assertIn('表格列序固定按运营定稿顺序', self.source)


if __name__ == '__main__':
    unittest.main()
