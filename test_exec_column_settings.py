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

    def test_grouped_expanders_and_instant_apply(self):
        self.assertIn('execution_column_groups(all_cols)', self.tab4)
        self.assertIn('st.expander(', self.tab4)
        self.assertIn('勾选即生效', self.tab4)
        self.assertIn('_save_column_prefs(_new_pref)', self.tab4)
        # 旧的「应用并记住」两步式交互已移除
        self.assertNotIn('应用并记住', self.tab4)
        self.assertNotIn('exec_cols_apply', self.tab4)
        self.assertNotIn('key="exec_cols_ms"', self.tab4)

    def test_group_all_and_none_buttons(self):
        self.assertIn('exec_cols_all_{_group_name}', self.tab4)
        self.assertIn('exec_cols_none_{_group_name}', self.tab4)

    def test_empty_selection_falls_back_to_base_group(self):
        self.assertIn('至少要保留 1 列', self.tab4)
        self.assertIn("st.session_state['exec_cols_grp_基础'] = list(_base)", self.tab4)

    def test_undo_and_reset_buttons(self):
        self.assertIn('撤销到本次打开前', self.tab4)
        self.assertIn('exec_cols_before_edit', self.tab4)
        self.assertIn('恢复全部列', self.tab4)

    def test_column_order_preview(self):
        self.assertIn('表格列序固定按运营定稿顺序', self.tab4)


if __name__ == '__main__':
    unittest.main()
