"""界面级冒烟：执行表「列设置」取消勾选后必须真的不显示（v2.68）。

为什么需要它
------------
v2.68 之前 app.py 有一段硬编码「强制显示」白名单，每次 rerun 都把用户勾掉的
状态/判定列塞回偏好 —— 纯函数单测覆盖不到，因为它发生在页面脚本里。
这里用 Streamlit 官方 AppTest **真渲染页面**：加载真实数据 → 取消勾选
「链接投产状态」→ 取消复选框（实时生效）→ 断言表格列里没有它，再勾回可单列恢复，并且多次 rerun 不复活，
点「恢复全部列」能回来。

运行方式（默认跳过，避免发布门禁依赖共享盘）
--------------------------------------------
    $env:METOX_UI_REAL_DATA=1; python -m unittest test_ui_smoke_exec_columns -v

可选环境变量：
    METOX_UI_DATE=2026-11-05   分析日
    METOX_UI_SHOP=集团店       店铺
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest

try:
    from streamlit.testing.v1 import AppTest
except Exception:  # pragma: no cover - 旧版 streamlit 无 testing API
    AppTest = None

from core.schema import EXPORT_COLS

ROOT = Path(__file__).resolve().parent
ENABLED = os.environ.get("METOX_UI_REAL_DATA") == "1"
DATE = os.environ.get("METOX_UI_DATE", "2026-11-05")
SHOP = os.environ.get("METOX_UI_SHOP", "集团店")
TARGET_COLUMN = "链接投产状态"
KEEP_ALWAYS = ("宝贝ID", "GMV")


def _exception_text(at):
    parts = []
    for exc in list(getattr(at, "exception", []) or []):
        value = getattr(exc, "value", None) or getattr(exc, "message", None) or str(exc)
        parts.append(str(value))
    return " | ".join(parts)


def _frames(at):
    out = []
    for element in at.dataframe:
        value = element.value
        frame = getattr(value, "data", value)
        if hasattr(frame, "columns"):
            out.append(frame)
    return out


def _execution_frame(at):
    """执行表 = 含 宝贝ID/货号/链接投产/可售天数 的那张 dataframe。

    不能用状态列定位：本测试正是要把状态列取消勾选，取消后状态列就不在了。
    """
    marker = {"宝贝ID", "货号", "链接投产", "可售天数"}
    for frame in _frames(at):
        if marker.issubset(set(frame.columns)):
            return frame
    return None


@unittest.skipIf(AppTest is None, "当前 streamlit 版本无 testing.AppTest")
@unittest.skipUnless(ENABLED, "需要 METOX_UI_REAL_DATA=1 才跑真实数据界面冒烟")
class ExecutionColumnPrefsSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefs_path = ROOT / "cache" / "column_prefs.json"
        cls.prefs_backup = (
            cls.prefs_path.read_bytes() if cls.prefs_path.is_file() else None
        )
        cls.at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=1800)
        cls.at.session_state["date_input"] = DATE
        cls.at.session_state["shop_input"] = SHOP
        cls.at.run()
        load_button = next(
            (button for button in cls.at.sidebar.button if button.label == "加载并处理数据"),
            None,
        )
        if load_button is None:  # pragma: no cover - 侧边栏结构变化时直接失败
            raise AssertionError("侧边栏缺少「加载并处理数据」按钮")
        load_button.click()
        cls.at.run()
        cls.first = _execution_frame(cls.at)

    @classmethod
    def tearDownClass(cls):
        # 还原用户真实列偏好，避免冒烟测试改动覆盖工作区配置
        if cls.prefs_backup is not None:
            cls.prefs_path.write_bytes(cls.prefs_backup)
        elif cls.prefs_path.is_file():
            cls.prefs_path.unlink()

    def test_00_render_without_exception(self):
        self.assertEqual(_exception_text(self.at), "", "加载真实数据后页面抛异常")
        self.assertIsNotNone(self.first, "未找到执行表 dataframe")

    def test_01_target_column_visible_by_default(self):
        self.assertIn(TARGET_COLUMN, list(self.first.columns))

    def test_02_unchecked_column_disappears_and_stays_hidden(self):
        # v2.76：列设置改成复选框网格，取消勾选 = 关掉该列的复选框，立刻实时生效
        checkbox_key = f"exec_col_chk_{TARGET_COLUMN}"
        self.assertTrue(self.at.checkbox(key=checkbox_key).value, "该列默认应勾选")
        self.at.checkbox(key=checkbox_key).uncheck().run()
        self.assertEqual(_exception_text(self.at), "", "取消勾选后页面抛异常")

        frame = _execution_frame(self.at)
        self.assertIsNotNone(frame, "取消勾选后执行表消失了")
        self.assertNotIn(TARGET_COLUMN, list(frame.columns), "取消勾选的列仍然显示")
        for column in KEEP_ALWAYS:
            self.assertIn(column, list(frame.columns))
        before = len(frame.columns)
        # 相对初始列数少 1 列（初始列数取决于本机已保存偏好，不硬编码 80）
        self.assertEqual(before, len(self.first.columns) - 1, "取消 1 列后应比初始少 1 列")

        # 关键回归：再 rerun 两次，被取消的列不得复活，复选框也要保持未勾选
        for round_index in range(2):
            self.at.run()
            again = _execution_frame(self.at)
            self.assertIsNotNone(again)
            self.assertNotIn(
                TARGET_COLUMN,
                list(again.columns),
                f"第 {round_index + 1} 次 rerun 后取消勾选的列又出现了",
            )
            self.assertEqual(len(again.columns), before, "列数在 rerun 后发生变化")
            self.assertFalse(self.at.checkbox(key=checkbox_key).value,
                             "复选框状态在 rerun 后丢失")

    def test_02b_recheck_restores_single_column(self):
        # 单独把这一列勾回来（旧版只能整组恢复，这里验证"逐列恢复"）
        checkbox_key = f"exec_col_chk_{TARGET_COLUMN}"
        self.at.checkbox(key=checkbox_key).check().run()
        frame = _execution_frame(self.at)
        self.assertIsNotNone(frame)
        self.assertIn(TARGET_COLUMN, list(frame.columns), "重新勾选后该列未恢复")

    def test_03_restore_all_columns(self):
        self.at.button(key="exec_cols_reset").click().run()
        frame = _execution_frame(self.at)
        self.assertIsNotNone(frame)
        self.assertIn(TARGET_COLUMN, list(frame.columns), "恢复全部列后该列未回来")
        self.assertEqual(len(frame.columns), len(EXPORT_COLS), "恢复全部列后应为全部 80 列")


if __name__ == "__main__":
    unittest.main()
