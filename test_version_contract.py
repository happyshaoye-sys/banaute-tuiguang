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
        self.assertEqual(info["version"], "2.14")
        self.assertEqual(info["release_date"], "2026-08-07")
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
        self.assertEqual(info["rule_versions"]["category_analysis_copy"], "2026-08-05")
        self.assertEqual(info["rule_versions"]["metox_cart_burst_label"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["ai_task_metox_priority"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["execution_cart_rates"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["category_investment_export"], "2026-08-06")
        self.assertEqual(info["rule_versions"]["execution_metric_statuses"], "2026-08-07")
        self.assertEqual(info["rule_versions"]["promotion_cart_rate"], "2026-08-07")
        self.assertEqual(version.VERSION_INFO, info)
        self.assertEqual(version.DISPLAY_VERSION, "MetoX v2.14")

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

    def test_current_operator_documents_show_v2_contract(self):
        documents = {
            "README.md": "MetoX v2.14",
            "README_离线使用说明.txt": "MetoX v2.14",
            "第三方使用说明_先看这个.txt": "MetoX v2.14",
            "半自动推广工具_数据源与指标说明.md": "MetoX v2.14",
            "MetoX2.14_使用说明与运营操作顺序.md": "MetoX v2.14",
            "MetoX2.14_新运营培训手册.html": "MetoX2.14",
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

    def test_release_packager_replaces_both_versioned_manuals(self):
        source = (ROOT / "scripts" / "release_metox.py").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
