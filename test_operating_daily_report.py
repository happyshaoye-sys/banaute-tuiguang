"""AI operating daily report reconciliation and UI-contract tests."""
from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from core.analysis import (
    build_operating_daily_report,
    calc_kpi_mom_percent,
    calc_kpis,
)
from core.plan_metrics import summarize_plan_records


ROOT = Path(__file__).resolve().parent
COMPLETE_7D = {
    "周期": "7日环比",
    "完整性": "当前 7/7 天；对比 7/7 天",
    "缺失日期": "当前: 无 | 对比: 无",
}


class OperatingDailyReportTests(unittest.TestCase):
    def setUp(self):
        self.current = pd.DataFrame([
            {
                "宝贝ID": "1", "综合等级": "A", "淘宝类目": "羽绒服",
                "GMV": 300.0, "付费": 120.0,
                "商品访客数": 30.0, "访客平均价值": 10.0,
                "关键词": 20.0, "关键词归因成交": 100.0,
                "人群": 10.0, "人群归因成交": 60.0,
                "全站": 10.0, "全站归因成交": 40.0,
            },
            {
                "宝贝ID": "2", "综合等级": "B", "淘宝类目": "针织衫",
                "GMV": 100.0, "付费": 100.0,
                "商品访客数": 20.0, "访客平均价值": 5.0,
                "关键词": 30.0, "关键词归因成交": 20.0,
                "人群": 10.0, "人群归因成交": 0.0,
                "全站": 10.0, "全站归因成交": 10.0,
            },
            {
                "宝贝ID": "3", "综合等级": "A", "淘宝类目": "羽绒服",
                "GMV": 100.0, "付费": 0.0,
                "商品访客数": 10.0, "访客平均价值": 10.0,
                "关键词": 0.0, "关键词归因成交": 30.0,
                "人群": 0.0, "人群归因成交": 0.0,
                "全站": 0.0, "全站归因成交": 0.0,
            },
        ])
        plan_metadata = []
        for row in self.current.to_dict('records'):
            spend = float(row['付费'])
            plans = [] if spend <= 0 else [{
                '计划ID': f"P{row['宝贝ID']}", '当前花费': spend,
                '归因成交': row['GMV'], '加购': 1, '点击': 10,
                '所属渠道': '关键词',
            }]
            plan_metadata.append(summarize_plan_records(
                plans, period_days=7, actual_days=7,
                period_start='2026-07-25', period_end='2026-07-31',
            ))
        for column in plan_metadata[0]:
            self.current[column] = [metadata[column] for metadata in plan_metadata]
        self.previous = pd.DataFrame([
            {
                "宝贝ID": "1", "淘宝类目": "羽绒服",
                "GMV": 100.0, "付费": 20.0,
                "商品访客数": 20.0, "访客平均价值": 5.0,
                "关键词": 10.0, "关键词归因成交": 50.0,
                "人群": 5.0, "人群归因成交": 30.0,
                "全站": 5.0, "全站归因成交": 20.0,
            },
            {
                "宝贝ID": "2", "淘宝类目": "针织衫",
                "GMV": 200.0, "付费": 20.0,
                "商品访客数": 20.0, "访客平均价值": 10.0,
                "关键词": 10.0, "关键词归因成交": 40.0,
                "人群": 5.0, "人群归因成交": 20.0,
                "全站": 5.0, "全站归因成交": 20.0,
            },
            {
                "宝贝ID": "4", "淘宝类目": "连衣裙",
                "GMV": 50.0, "付费": 10.0,
                "商品访客数": 10.0, "访客平均价值": 5.0,
                "关键词": 5.0, "关键词归因成交": 10.0,
                "人群": 0.0, "人群归因成交": 0.0,
                "全站": 0.0, "全站归因成交": 0.0,
            },
        ])

    def _report(self):
        return build_operating_daily_report(
            self.current,
            self.previous,
            period_choice="7天",
            period_log=COMPLETE_7D,
            risk_spend_floor=100.0,
        )

    def test_grade_and_category_deltas_reconcile_to_store_gmv_delta(self):
        report = self._report()

        self.assertEqual(report["overview"]["gmv_delta"], 150.0)
        self.assertEqual(sum(row["delta"] for row in report["grades"]["all"]), 150.0)
        self.assertEqual(sum(row["delta"] for row in report["categories"]["all"]), 150.0)
        self.assertEqual(report["grades"]["growth"]["label"], "A")
        self.assertEqual(report["grades"]["growth"]["delta"], 300.0)
        grade_deltas = {row["label"]: row["delta"] for row in report["grades"]["all"]}
        self.assertEqual(grade_deltas["流失商品"], -50.0)
        self.assertEqual(report["categories"]["growth"]["label"], "羽绒服")

    def test_three_channels_use_attributed_sales_and_keep_spend_separate(self):
        channels = {row["name"]: row for row in self._report()["channels"]}

        self.assertEqual(channels["关键词"]["attributed_sales_delta"], 50.0)
        self.assertEqual(channels["关键词"]["spend_delta"], 25.0)
        self.assertEqual(channels["人群"]["attributed_sales_delta"], 10.0)
        self.assertEqual(channels["全站"]["attributed_sales_delta"], 10.0)

    def test_roi_risk_uses_ratio_of_category_totals_and_spend_floor(self):
        risk = self._report()["roi_risk"]

        self.assertEqual(risk["label"], "针织衫")
        self.assertEqual(risk["previous_roi"], 10.0)
        self.assertEqual(risk["current_roi"], 1.0)
        self.assertEqual(risk["roi_delta"], -9.0)

    def test_zero_previous_baseline_is_labelled_new(self):
        report = build_operating_daily_report(
            self.current.iloc[[0]],
            pd.DataFrame(),
            period_choice="当日",
            period_log={"完整性": "当前 1/1 天；对比 1/1 天"},
        )

        self.assertEqual(report["overview"]["gmv_change_label"], "新增")

    def test_incomplete_window_is_flagged_and_ui_suppresses_driver_lines(self):
        report = build_operating_daily_report(
            self.current,
            self.previous,
            period_choice="7天",
            period_log={
                "完整性": "当前 6/7 天；对比 7/7 天",
                "缺失日期": "当前: 2026-07-31 | 对比: 无",
            },
        )
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

        self.assertEqual(report["status"], "incomplete")
        self.assertIn("2026-07-31", report["completeness"])
        self.assertLess(source.index('st.subheader("🧠 AI经营日报")'), source.index("# 等级分布卡片"))
        incomplete_branch = source[source.index("if operating_report['status'] != 'complete':"):
                                   source.index("st.divider()", source.index("if operating_report['status'] != 'complete':"))]
        self.assertIn("总览（暂估）", incomplete_branch)
        self.assertIn("for section in ('总览', '等级', '品类', '渠道', '风险')", incomplete_branch)

    def test_streamlit_dashboard_renders_data_backed_report(self):
        plans = {"p1"}
        previous_plans = {"p0"}
        current_kpis = calc_kpis(self.current, plans)
        previous_kpis = calc_kpis(self.previous, previous_plans)
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
        state = {
            "loaded": True,
            "df": self.current,
            "prev_df": self.previous,
            "kpis": current_kpis,
            "prev_kpis": previous_kpis,
            "kpi_mom": calc_kpi_mom_percent(current_kpis, previous_kpis),
            "plan_ids": plans,
            "prev_plan_ids": previous_plans,
            "cur_date": "2026-07-31",
            "prev_date": None,
            "sources_info": [],
            "timings": [],
            "total_time": 0,
            "period_logs": [COMPLETE_7D],
            "cat_ctx": {"period_choice": "7天", "sel_label": "7日环比"},
            "traffic_bounds": None,
            "value_bounds": None,
        }
        for key, value in state.items():
            app.session_state[key] = value

        app.run()

        self.assertEqual(list(app.exception), [])
        self.assertIn("🧠 AI经营日报", [item.value for item in app.subheader])
        rendered = "\n".join(item.value for item in app.markdown)
        self.assertIn("**总览：**", rendered)
        self.assertIn("增长集中在A", rendered)
        self.assertIn("关键词归因", rendered)


if __name__ == "__main__":
    unittest.main()
