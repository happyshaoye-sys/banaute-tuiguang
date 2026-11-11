"""界面级冒烟测试：用 Streamlit AppTest 真跑 app.py，逐个切换「时间周期」下拉档位。

为什么需要它
------------
2026-11-09 出现过的真实缺陷：切到「自定义」时 `NameError: name 'timedelta' is not defined`。
单元测试只覆盖 `core/periods.py` 纯函数、端到端脚本直接调 `_load_folder_cached` 绕过侧边栏、
打包自检默认档位是「当日」不进入自定义分支 —— 三层都拦不住。
本测试直接渲染页面并切换每个档位，任何异常都算失败。

覆盖
----
* 7 个档位（当日/3天/7天/15天/30天/按月/自定义）切换后页面无异常；
* 自定义：默认值可渲染，且「超 31 天」时给出错误提示并降级；
* 按月：月份下拉出现且可切换；
* 侧边栏控件齐全（日期、时间周期、加载按钮）。
"""
import unittest

try:
    from streamlit.testing.v1 import AppTest
except Exception:  # pragma: no cover - 旧版 streamlit 无 testing API
    AppTest = None

PERIOD_OPTIONS = ["当日", "3天", "7天", "15天", "30天", "按月", "自定义"]


def _new_app(timeout=120):
    path = str(__import__("pathlib").Path(__file__).resolve().parent / "app.py")
    at = AppTest.from_file(path, default_timeout=timeout)
    at.run()
    return at


def _exception_text(at):
    """把 AppTest 捕获到的异常拼成可读文本。"""
    parts = []
    for exc in list(getattr(at, "exception", []) or []):
        value = getattr(exc, "value", None) or getattr(exc, "message", None) or str(exc)
        parts.append(str(value))
    return " | ".join(parts)


@unittest.skipIf(AppTest is None, "当前 streamlit 版本无 testing.AppTest")
class PeriodSwitcherSmokeTest(unittest.TestCase):
    """逐个档位切换时间周期，页面不得抛异常。"""

    def test_all_period_options_render_without_exception(self):
        at = _new_app()
        self.assertEqual(_exception_text(at), "", "初始加载即出现异常")
        labels = [box.label for box in at.sidebar.selectbox]
        self.assertIn("时间周期", labels, f"侧边栏缺少时间周期控件: {labels}")

        for option in PERIOD_OPTIONS:
            with self.subTest(period=option):
                at.sidebar.selectbox(key="period_choice_input").set_value(option).run()
                err = _exception_text(at)
                self.assertEqual(err, "", f"切换到「{option}」时页面抛异常：{err}")

    def test_month_option_exposes_month_picker(self):
        at = _new_app()
        at.sidebar.selectbox(key="period_choice_input").set_value("按月").run()
        self.assertEqual(_exception_text(at), "")
        keys = [box.key for box in at.sidebar.selectbox]
        self.assertIn("period_month_input", keys, f"按月未出现月份选择器: {keys}")
        # 切到上一个自然月（列表第二项）
        picker = at.sidebar.selectbox(key="period_month_input")
        options = list(picker.options)
        self.assertGreaterEqual(len(options), 2, f"月份候选过少: {options}")
        picker.set_value(options[1]).run()
        self.assertEqual(_exception_text(at), "", "切换月份后页面抛异常")

    def test_custom_option_exposes_two_date_inputs(self):
        at = _new_app()
        at.sidebar.selectbox(key="period_choice_input").set_value("自定义").run()
        self.assertEqual(_exception_text(at), "", "切换到自定义时页面抛异常")
        keys = [box.key for box in at.sidebar.date_input]
        self.assertIn("period_custom_start", keys, f"缺少开始日期控件: {keys}")
        self.assertIn("period_custom_end", keys, f"缺少结束日期控件: {keys}")

    def test_custom_over_31_days_shows_error(self):
        at = _new_app()
        at.sidebar.selectbox(key="period_choice_input").set_value("自定义").run()
        import datetime as _dt
        start = _dt.date(2026, 10, 1)
        end = _dt.date(2026, 11, 5)      # 36 天，超上限
        at.sidebar.date_input(key="period_custom_start").set_value(start).run()
        at.sidebar.date_input(key="period_custom_end").set_value(end).run()
        err = _exception_text(at)
        self.assertEqual(err, "", f"超 31 天时不应抛异常，而应给出提示：{err}")
        messages = " ".join(str(item.value) for item in (at.sidebar.error or []))
        self.assertIn("31", messages, f"未给出 31 天上限提示：{messages}")

    def test_sidebar_has_core_controls(self):
        at = _new_app()
        texts = [box.label for box in at.sidebar.text_input]
        self.assertIn("日期", texts, f"侧边栏缺少日期控件: {texts}")
        buttons = [btn.label for btn in at.sidebar.button]
        self.assertIn("加载并处理数据", buttons, f"侧边栏缺少加载按钮: {buttons}")


if __name__ == "__main__":
    unittest.main()
