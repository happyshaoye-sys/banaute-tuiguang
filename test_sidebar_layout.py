"""Regression contract for the compact daily-loading sidebar."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class SidebarLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        sidebar_start = cls.source.index("with st.sidebar:")
        main_start = cls.source.index("# 主内容区", sidebar_start)
        cls.sidebar = cls.source[sidebar_start:main_start]
        cls.main = cls.source[main_start:]

    def test_daily_controls_precede_one_collapsed_advanced_panel_and_load(self):
        date_pos = self.sidebar.index('"日期"')
        period_pos = self.sidebar.index('"时间周期"')
        advanced_pos = self.sidebar.index('with st.expander("高级设置", expanded=False):')
        load_pos = self.sidebar.index('load_requested = st.button(')
        self.assertLess(date_pos, period_pos)
        self.assertLess(period_pos, load_pos)
        self.assertLess(load_pos, advanced_pos)
        self.assertEqual(self.sidebar.count('with st.expander("高级设置"'), 1)

    def test_low_frequency_controls_are_inside_advanced_settings(self):
        advanced_pos = self.sidebar.index('with st.expander("高级设置", expanded=False):')
        processing_pos = self.sidebar.index('if load_requested:')
        advanced = self.sidebar[advanced_pos:processing_pos]
        for label in (
            "数据根目录", "路径配置", "数据源目录", "自定义分界点",
            "重新应用分界点(不重新读数)", "周期环比范围与完整性",
            "性能耗时", "Git 版本管理",
        ):
            with self.subTest(label=label):
                self.assertIn(label, advanced)

    def test_old_parallel_expanders_and_main_top_diagnostics_are_removed(self):
        for old_expander in (
            'with st.expander("路径配置',
            'with st.expander("数据源目录',
            'with st.expander("自定义分界点',
            'with st.expander("Git 版本管理',
        ):
            with self.subTest(old_expander=old_expander):
                self.assertNotIn(old_expander, self.sidebar)
        self.assertNotIn("周期环比范围与完整性", self.main)
        self.assertNotIn("性能耗时", self.main)

    def test_streamlit_width_api_uses_current_parameter(self):
        self.assertNotIn("use_container_width=", self.source)
        self.assertIn("width=\"stretch\"", self.source)


if __name__ == "__main__":
    unittest.main()
