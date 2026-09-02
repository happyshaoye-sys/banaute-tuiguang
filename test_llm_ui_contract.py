from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class LLMUIContractTests(unittest.TestCase):
    def test_ai_task_view_exposes_optional_advice_and_one_click_feedback(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

        self.assertIn("大模型复核与人工反馈", source)
        self.assertIn("生成 / 刷新大模型建议", source)
        for decision in ("采纳", "修改后采纳", "驳回", "暂缓"):
            self.assertIn(f'"{decision}"', source)
        self.assertIn("回填后续结果（可选）", source)
        self.assertIn("导出全部人工反馈 CSV", source)

    def test_ui_reads_environment_keys_and_never_collects_api_keys(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

        self.assertIn("provider_key_available(llm_provider)", source)
        self.assertNotIn('st.text_input("API Key', source)
        self.assertNotIn('st.text_input("API密钥', source)
        self.assertIn("不会自动执行预算调整", source)


if __name__ == "__main__":
    unittest.main()
