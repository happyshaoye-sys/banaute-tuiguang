from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class LLMUIContractTests(unittest.TestCase):
    def test_main_page_no_longer_exposes_llm_feedback_controls(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

        self.assertNotIn("大模型复核与人工反馈", source)
        self.assertNotIn("生成 / 刷新大模型建议", source)
        self.assertNotIn("一键反馈给MetoX", source)
        self.assertNotIn("回填后续结果（可选）", source)
        self.assertNotIn("导出全部人工反馈 CSV", source)
        self.assertNotIn("PROVIDER_SPECS", source)
        self.assertNotIn("FeedbackStore", source)
        self.assertNotIn("call_advisor", source)
        self.assertNotIn("provider_key_available", source)
        self.assertNotIn("llm_recommendations", source)
        self.assertNotIn("llm_feedback_status", source)

    def test_core_llm_advisor_keeps_manual_execution_and_env_key_boundaries(self):
        source = (ROOT / "core" / "llm_advisor.py").read_text(encoding="utf-8-sig")

        self.assertIn('"automatic_execution": False', source)
        self.assertIn("env.get(spec.api_key_env", source)
        self.assertIn("未配置环境变量", source)
        self.assertIn("api_key_env", source)
        self.assertNotIn('"automatic_execution": True', source)


if __name__ == "__main__":
    unittest.main()
