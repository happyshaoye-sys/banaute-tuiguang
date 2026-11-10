"""v2.66：时间周期「按月」「自定义」的区间与校验测试。

覆盖点
------
1. ``month_bounds`` 解析月末（含闰年 2 月）；
2. ``resolve_month_range``：历史月取整月、当月取月初~分析日、未来月退化为分析日；
3. ``validate_custom_range``：合法区间、31 天边界、超 31 天、起止倒置、格式错误；
4. ``build_period_ranges``：原有 5 档不变，追加「月环比」「自定义环比」且对比窗口等长相邻；
5. 标签映射 ``PERIOD_LABEL_MAP`` 与新档位一致（避免界面选了但取不到区间）。
"""
import unittest
from datetime import date

from core.periods import (
    CUSTOM_LABEL,
    MAX_PERIOD_DAYS,
    MONTH_LABEL,
    PERIOD_SPECS,
    build_period_ranges,
    month_bounds,
    resolve_month_range,
    validate_custom_range,
)


class MonthBoundsTest(unittest.TestCase):
    def test_normal_month(self):
        self.assertEqual(month_bounds("2026-10"), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(month_bounds("2026-11"), (date(2026, 11, 1), date(2026, 11, 30)))

    def test_december_rolls_year(self):
        self.assertEqual(month_bounds("2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_leap_february(self):
        self.assertEqual(month_bounds("2028-02"), (date(2028, 2, 1), date(2028, 2, 29)))
        self.assertEqual(month_bounds("2027-02"), (date(2027, 2, 1), date(2027, 2, 28)))


class ResolveMonthRangeTest(unittest.TestCase):
    def test_history_month_is_full_month(self):
        self.assertEqual(resolve_month_range("2026-10", date(2026, 11, 9)),
                         (date(2026, 10, 1), date(2026, 10, 31)))

    def test_current_month_is_month_to_date(self):
        self.assertEqual(resolve_month_range("2026-11", date(2026, 11, 9)),
                         (date(2026, 11, 1), date(2026, 11, 9)))

    def test_future_month_clamped_to_base_date(self):
        start, end = resolve_month_range("2026-12", date(2026, 11, 9))
        self.assertEqual(start, date(2026, 12, 1))
        self.assertEqual(end, date(2026, 12, 1))     # 不越界到未来


class ValidateCustomRangeTest(unittest.TestCase):
    def test_valid(self):
        start, end, days, err = validate_custom_range("2026-10-01", "2026-10-07")
        self.assertEqual((start, end, days, err), (date(2026, 10, 1), date(2026, 10, 7), 7, ""))

    def test_exact_max_days(self):
        start, end, days, err = validate_custom_range("2026-10-01", "2026-10-31")
        self.assertEqual(days, MAX_PERIOD_DAYS)
        self.assertEqual(err, "")

    def test_over_max_days_rejected(self):
        start, end, days, err = validate_custom_range("2026-10-01", "2026-11-01")
        self.assertIsNone(start)
        self.assertEqual(days, 32)
        self.assertIn("最长 31 天", err)

    def test_reversed_rejected(self):
        _s, _e, _d, err = validate_custom_range("2026-10-07", "2026-10-01")
        self.assertIn("不能早于", err)

    def test_bad_format_rejected(self):
        _s, _e, _d, err = validate_custom_range("2026/10/01", "2026-10-07")
        self.assertIn("YYYY-MM-DD", err)

    def test_single_day_ok(self):
        _s, _e, days, err = validate_custom_range("2026-10-01", "2026-10-01")
        self.assertEqual(days, 1)
        self.assertEqual(err, "")


class BuildPeriodRangesTest(unittest.TestCase):
    def test_legacy_periods_unchanged(self):
        periods = build_period_ranges("2026-11-09")
        self.assertEqual(set(periods), {label for _n, label in PERIOD_SPECS})
        self.assertEqual(periods["7日环比"]["current_start"], "2026-11-03")
        self.assertEqual(periods["7日环比"]["current_end"], "2026-11-09")
        self.assertEqual(periods["7日环比"]["prev_start"], "2026-10-27")
        self.assertEqual(periods["7日环比"]["prev_end"], "2026-11-02")

    def test_month_period_appended(self):
        periods = build_period_ranges("2026-11-09", month="2026-10")
        self.assertIn(MONTH_LABEL, periods)
        month = periods[MONTH_LABEL]
        self.assertEqual(month["current_start"], "2026-10-01")
        self.assertEqual(month["current_end"], "2026-10-31")
        self.assertEqual(month["days"], 31)
        # 对比窗口 = 紧邻之前等长
        self.assertEqual(month["prev_end"], "2026-09-30")
        self.assertEqual(month["prev_start"], "2026-08-31")

    def test_month_to_date_when_current_month(self):
        periods = build_period_ranges("2026-11-09", month="2026-11")
        month = periods[MONTH_LABEL]
        self.assertEqual(month["current_start"], "2026-11-01")
        self.assertEqual(month["current_end"], "2026-11-09")
        self.assertEqual(month["days"], 9)

    def test_custom_period_appended(self):
        periods = build_period_ranges("2026-11-09", custom=("2026-10-05", "2026-10-11"))
        self.assertIn(CUSTOM_LABEL, periods)
        custom = periods[CUSTOM_LABEL]
        self.assertEqual(custom["current_start"], "2026-10-05")
        self.assertEqual(custom["current_end"], "2026-10-11")
        self.assertEqual(custom["days"], 7)
        self.assertEqual(custom["prev_end"], "2026-10-04")
        self.assertEqual(custom["prev_start"], "2026-09-28")

    def test_invalid_custom_not_appended(self):
        periods = build_period_ranges("2026-11-09", custom=("2026-10-01", "2026-11-05"))
        self.assertNotIn(CUSTOM_LABEL, periods)
        # 原有周期仍然齐全，界面已降级为 7天
        self.assertIn("7日环比", periods)

    def test_month_and_custom_together(self):
        periods = build_period_ranges("2026-11-09", month="2026-10",
                                      custom=("2026-10-01", "2026-10-31"))
        self.assertIn(MONTH_LABEL, periods)
        self.assertIn(CUSTOM_LABEL, periods)


class PeriodLabelMapTest(unittest.TestCase):
    """界面下拉的每一档都必须能映射到 periods 里的标签，否则选了就会 KeyError。"""

    def test_every_option_maps_to_existing_period(self):
        from test_new_product_view import _load_app_helpers
        app = _load_app_helpers()
        label_map = app.PERIOD_LABEL_MAP
        periods = build_period_ranges("2026-11-09", month="2026-10",
                                      custom=("2026-10-01", "2026-10-31"))
        for option in ("当日", "3天", "7天", "15天", "30天", "按月", "自定义"):
            with self.subTest(option=option):
                self.assertIn(option, label_map, f"下拉选项 {option} 未映射")
                self.assertIn(label_map[option], periods, f"{option} → {label_map[option]} 不在 periods")


if __name__ == "__main__":
    unittest.main()
