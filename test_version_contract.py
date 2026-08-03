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
        self.assertEqual(info["version"], "2.6")
        self.assertEqual(info["release_date"], "2026-08-03")
        self.assertEqual(info["external_strategy_version"], "v9.3")
        self.assertEqual(info["rule_versions"]["refund_risk"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["startup_launcher"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["runtime_packaging"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["sidebar_layout"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["inventory_action_columns"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["ai_operating_daily_report"], "2026-08-02")
        self.assertEqual(info["rule_versions"]["ai_task_view"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["ai_task_period_alignment"], "2026-08-03")
        self.assertEqual(info["rule_versions"]["direct_purchase_metrics"], "2026-08-03")
        self.assertEqual(version.VERSION_INFO, info)
        self.assertEqual(version.DISPLAY_VERSION, "MetoX v2.6")

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
            "README.md": "MetoX v2.6",
            "README_离线使用说明.txt": "MetoX v2.6",
            "第三方使用说明_先看这个.txt": "MetoX v2.6",
            "半自动推广工具_数据源与指标说明.md": "MetoX v2.6",
            "MetoX2.6_新运营培训手册.html": "MetoX2.6",
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


if __name__ == "__main__":
    unittest.main()
