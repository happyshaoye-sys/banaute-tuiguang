"""Release identity and source-of-truth contract tests."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from core import version


ROOT = Path(__file__).resolve().parent


class VersionContractTests(unittest.TestCase):
    def test_version_json_is_the_application_identity(self):
        info = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))
        self.assertEqual(info["product"], "MetoX")
        self.assertEqual(info["version"], "2.39")
        self.assertEqual(info["release_date"], "2026-09-04")
        self.assertEqual(info["external_strategy_version"], "v9.3")
        self.assertEqual(info["rule_versions"]["refund_risk"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["startup_launcher"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["runtime_packaging"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["sidebar_layout"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["inventory_action_columns"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["ai_operating_daily_report"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["ai_task_view"], "2026-08-04")
        self.assertEqual(info["rule_versions"]["ai_task_period_alignment"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["direct_purchase_metrics"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["ai_task_experience"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["ai_task_no_plan_semantics"], "2026-08-04")
        self.assertEqual(info["rule_versions"]["ai_task_plan_pipeline"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["data_contract_validation"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["category_analysis_copy"], "2026-08-10")
        self.assertEqual(info["rule_versions"]["execution_column_order"], "2026-08-10")
        self.assertEqual(info["rule_versions"]["metox_cart_burst_label"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["ai_task_metox_priority"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["execution_cart_rates"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["category_investment_export"], "2026-08-17")
        self.assertEqual(info["rule_versions"]["execution_metric_statuses"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["promotion_cart_rate"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["metric_status_rule_export"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["link_roi_refund_breakeven"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["strict_same_group_baseline"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["sellable_days_status"], "2026-08-09")
        self.assertEqual(info["rule_versions"]["first_run_email_prompt"], "2026-08-09")
        self.assertEqual(info["rule_versions"]["execution_table_styler_capacity"], "2026-08-13")
        self.assertEqual(info["rule_versions"]["execution_top_category_products"], "2026-08-13")
        self.assertEqual(info["rule_versions"]["execution_top_status_colors"], "2026-08-14")
        self.assertEqual(info["rule_versions"]["daily_fact_cache"], "2026-08-27")
        self.assertEqual(info["rule_versions"]["shared_dimension_cache"], "2026-08-27")
        self.assertEqual(info["rule_versions"]["deferred_all_sheets_download"], "2026-08-18")
        self.assertEqual(info["rule_versions"]["dashboard_kpi_daily_trend"], "2026-08-20")
        self.assertEqual(info["rule_versions"]["category_visitor_metrics"], "2026-08-24")
        self.assertEqual(
            info["rule_versions"]["category_cart_conversion_metrics"],
            "2026-08-26",
        )
        self.assertEqual(info["rule_versions"]["new_product_source_ingestion"], "2026-08-27")
        self.assertEqual(info["rule_versions"]["new_product_first_seen"], "2026-08-27")
        self.assertEqual(info["rule_versions"]["new_product_lifecycle_rules"], "2026-09-04")
        self.assertEqual(info["rule_versions"]["new_product_material_ctr"], "2026-08-31")
        self.assertEqual(info["rule_versions"]["new_product_backtest"], "2026-09-04")
        self.assertEqual(info["rule_versions"]["new_product_view"], "2026-09-04")
        self.assertEqual(info["rule_versions"]["new_product_lazy_history_load"], "2026-08-29")
        self.assertEqual(info["rule_versions"]["loading_speed_optimization"], "2026-08-28")
        self.assertEqual(info["rule_versions"]["llm_advisor"], "2026-09-02")
        self.assertEqual(info["rule_versions"]["llm_feedback_learning"], "2026-09-02")
        self.assertEqual(version.VERSION_INFO, info)
        self.assertEqual(version.DISPLAY_VERSION, "MetoX v2.39")

    def test_app_imports_version_instead_of_assigning_one(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        hardcoded = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in targets):
                    hardcoded.append(node.lineno)
        self.assertEqual(hardcoded, [])
        self.assertIn("from core.version import APP_VERSION, DISPLAY_VERSION, PRODUCT_NAME", source)

    def test_core_init_contains_no_business_rule_copy(self):
        source = (ROOT / "core" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .segmentation import", source)
        self.assertNotIn("refund_rate", source)
        self.assertNotIn("0.7", source)
        self.assertNotIn("0.8", source)

    def test_new_product_history_load_is_lazy(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        load_folder = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_load_folder_cached":
                load_folder = ast.get_source_segment(source, node) or ""
                break
        self.assertNotIn("_load_new_product_history(", load_folder)
        self.assertIn("加载新品历史", source)
        self.assertIn("刷新新品历史", source)

    def test_current_operator_documents_show_v2_contract(self):
        documents = {
            "README.md": "MetoX v2.39",
            "README_离线使用说明.txt": "MetoX v2.39",
            "第三方使用说明_先看这个.txt": "MetoX v2.39",
            "半自动推广工具_数据源与指标说明.md": "MetoX v2.39",
            "MetoX2.39_使用说明与运营操作顺序.md": "MetoX v2.39",
            "MetoX2.39_新运营培训手册.html": "MetoX2.39",
        }
        for relative, marker in documents.items():
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8-sig")
                self.assertIn(marker, text)

        rule_text = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8-sig")
            for relative in ("README.md", "README_离线使用说明.txt", "第三方使用说明_先看这个.txt")
        )
        self.assertIn(">80%", rule_text)
        self.assertIn(">70%", rule_text)
        self.assertIn("40%", rule_text)
        self.assertIn("TOP15", rule_text)
        self.assertIn("TOP50", rule_text)
        self.assertIn("其他单元格", rule_text)
        self.assertIn("#1F2937", rule_text)
        self.assertIn("#DDEBF7", rule_text)
        self.assertIn("#FCE4EC", rule_text)
        self.assertIn("状态判定", rule_text)
        self.assertIn("新品视图", rule_text)
        self.assertIn("商品-整体", rule_text)
        self.assertIn("无界-创意", rule_text)
        self.assertIn("加载新品历史", rule_text)
        self.assertIn("刷新新品历史", rule_text)
        self.assertIn("主流程不主动读取新品历史", rule_text)
        self.assertIn("D15", rule_text)
        self.assertIn("D30", rule_text)
        self.assertIn("15 天", rule_text)
        self.assertIn("D1 至当前判定节点内任意成交", rule_text)
        self.assertIn("300", rule_text)
        self.assertIn("有效数据天数", rule_text)
        self.assertIn("同层样本", rule_text)
        self.assertIn("累计加购件数为 0", rule_text)
        self.assertIn("流量验证只根据", rule_text)
        self.assertIn("商品名称", rule_text)
        self.assertIn("动销保护", rule_text)
        self.assertIn("链接/库存现状", rule_text)
        self.assertIn("素材表现表", rule_text)
        self.assertIn("向量化", rule_text)
        self.assertIn("14.179", rule_text)
        self.assertIn("商品-整体退款分布", rule_text)
        self.assertIn("90天", rule_text)
        self.assertIn("45天", rule_text)

    def test_release_packager_replaces_both_versioned_manuals(self):
        source = (ROOT / "scripts" / "release_metox.py").read_text(encoding="utf-8")
        self.assertIn('package_name = f"MetoX{version}_{compact_date}"', source)
        self.assertNotIn("MetoX半自动推广工具_v{version}_第三方版", source)
        self.assertIn(
            'operator_manual_name = f"MetoX{version}_使用说明与运营操作顺序.md"',
            source,
        )
        self.assertIn(
            'training_manual_name = f"MetoX{version}_新运营培训手册.html"',
            source,
        )
        self.assertIn('stage.glob("MetoX*_使用说明与运营操作顺序.md")', source)
        self.assertIn('stage.glob("MetoX*_新运营培训手册.html")', source)
        self.assertIn("args.baseline_package.absolute()", source)
        self.assertIn("args.launcher_runtime.absolute()", source)
        self.assertIn("args.output_root.absolute()", source)
        self.assertNotIn("args.launcher_runtime.resolve()", source)
        self.assertIn('".agents"', source)
        self.assertIn('".perf_cache"', source)
        self.assertIn('"secrets.toml"', source)
        self.assertIn('FORBIDDEN_SUFFIXES = {".pyc", ".log"}', source)
        self.assertIn('path.name.startswith(".env.")', source)
        self.assertIn('"first_run_email_prompt": "disabled"', source)
        self.assertIn('"sellable_days_status":', source)
        self.assertIn('"execution_top_categories": 15', source)
        self.assertIn('"execution_top_products_per_category": 50', source)
        self.assertIn('"execution_top_products_positive_gmv_only": True', source)
        self.assertIn('"execution_non_status_background": None', source)
        self.assertIn('"execution_status_text_color": "#1F2937"', source)
        self.assertIn('"execution_status_high_background": "#DDEBF7"', source)
        self.assertIn('"execution_status_low_background": "#FCE4EC"', source)
        self.assertIn('"daily_fact_cache": "metox2.30-daily-facts-v2"', source)
        self.assertIn('"shared_dimension_cache": "metox2.30-shared-dimensions-v2"', source)
        self.assertIn('"new_product_view":', source)
        self.assertIn('"decision_checkpoints": ["D15", "D30"]', source)
        self.assertIn('"decision_checkpoint_semantics": "fixed result snapshots only"', source)
        self.assertIn('"current_decision_range": "D15-D45"', source)
        self.assertIn('"current_decision_frequency": "daily"', source)
        self.assertIn('"decision_window_days": 45', source)
        self.assertIn('"decision_periods": ["D1-D15", "D16-D30"]', source)
        self.assertIn('"main_promotion_load": "does not load new-product history"', source)
        self.assertIn('"lazy_tab_button": "加载新品历史 / 刷新新品历史"', source)
        self.assertIn("from D1 through the analysis date, capped at D45, protects the style", source)
        self.assertNotIn("current 15-day period protects that period only", source)
        self.assertIn('"status_inventory_context":', source)
        self.assertIn("display context only", source)
        self.assertIn('"context_fields": ["动作可执行", "库存提示", "保护证据", "下架阻断原因"]', source)
        self.assertIn('"visitor_gate": 300', source)
        self.assertIn('"visitor_gate_scope": "rolling cumulative product visitors from D1 through the analysis date, capped at D45"', source)
        self.assertIn('"traffic_validation_basis": "cumulative visitors only; effective-day sufficiency is evaluated separately"', source)
        self.assertIn('"data_min_valid_days": 12', source)
        self.assertIn('"peer_min_styles": 20', source)
        self.assertIn("at D45 cumulative no sale and visitors<300 forces down", source)
        self.assertIn('"removed_decision_columns": ["商品名称", "商品支付转化率", "访客平均价值"]', source)
        self.assertIn('"removed_lifecycle_columns": ["left_censored", "D14/D15/D21 status and stock", "D15_D21_*", "D1_D21_*"]', source)
        self.assertIn('"hidden_columns": ["商品名称", "商品支付转化率", "访客平均价值"]', source)
        self.assertIn('"material_hidden_columns": ["商品名称"]', source)
        self.assertNotIn('"diagnostic_only"', source)
        self.assertIn('"verified_28day_cold_seconds": 352.067', source)
        self.assertIn('"verified_28day_warm_seconds": 14.179', source)
        self.assertIn('"verified_speedup": 24.831', source)

    def test_llm_user_agent_uses_application_version(self):
        source = (ROOT / "core" / "llm_advisor.py").read_text(encoding="utf-8")
        self.assertIn("from .version import APP_VERSION", source)
        self.assertIn('f"MetoX-LLM-Advisor/{APP_VERSION}"', source)
        self.assertNotIn("MetoX-LLM-Advisor/2.38", source)


if __name__ == "__main__":
    unittest.main()
