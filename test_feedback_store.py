from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.feedback_store import FeedbackStore, FeedbackValidationError, default_feedback_root
from core.llm_advisor import build_task_payload


def _payload(category="连衣裙", task_type="放量任务"):
    return build_task_payload({
        "宝贝ID": "1001",
        "货号": "SKU-001",
        "淘宝类目": category,
        "AI任务类型": task_type,
        "商品级总策略": "逐步放量",
        "主执行动作": "增加10%",
        "GMV": 3000,
        "商品访客数": 500,
        "链接投产": 4.2,
    })


def _recommendation(payload=None):
    return {
        "recommendation_id": "rec_001",
        "task_key": (payload or _payload())["task_key"],
        "provider": "openai",
        "requested_model": "gpt-5.6-luna",
        "response_model": "gpt-5.6-luna-2026-08-01",
        "prompt_version": "test-v1",
        "recommendation": "增加",
        "budget_action": "增加",
        "budget_change_percent": 10,
        "reason": "测试",
        "evidence": ["链接投产4.2"],
        "risks": [],
        "observation_days": 3,
        "success_criteria": "投产保持",
        "stop_conditions": "投产下降",
        "confidence": "中",
        "needs_manual_review": False,
        "automatic_execution": False,
        "authorization": "sensitive-auth-value",
    }


class FeedbackStoreTests(unittest.TestCase):
    def test_default_root_honors_explicit_environment_directory(self):
        root = default_feedback_root({"METOX_FEEDBACK_DIR": "D:/metox-feedback"})
        self.assertEqual(root, Path("D:/metox-feedback"))

    def test_recommendation_and_one_click_feedback_are_persisted_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            payload = _payload()
            recommendation = _recommendation(payload)
            store.record_recommendation(recommendation, payload)
            feedback = store.record_feedback(recommendation, payload, decision="采纳")

            self.assertEqual(feedback["decision"], "采纳")
            self.assertEqual(len(store.list_recommendations()), 1)
            self.assertEqual(len(store.list_feedback()), 1)
            persisted = (Path(tmp) / "recommendations.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("sensitive-auth-value", persisted)
            self.assertNotIn("authorization", persisted.lower())

    def test_modified_feedback_calculates_actual_budget_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            payload = _payload()
            feedback = store.record_feedback(
                _recommendation(payload),
                payload,
                decision="修改后采纳",
                actual_action="增加5%",
                budget_before=1000,
                budget_after=1050,
                reason="库存只支持小幅增加",
            )

            self.assertEqual(feedback["actual_budget_change"], 50.0)
            self.assertEqual(feedback["actual_budget_change_percent"], 5.0)

    def test_learning_context_uses_matching_feedback_and_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            payload = _payload()
            recommendation = _recommendation(payload)
            store.record_feedback(recommendation, payload, decision="采纳")
            store.record_feedback(
                {**recommendation, "recommendation_id": "rec_002"},
                payload,
                decision="驳回",
                reason="大促前不调整",
            )
            store.record_feedback(
                {**recommendation, "recommendation_id": "rec_other"},
                _payload(category="半身裙"),
                decision="采纳",
            )

            context = store.build_learning_context(payload)
            self.assertEqual(context["matched_count"], 2)
            self.assertEqual(context["decision_counts"]["采纳"], 1)
            self.assertEqual(context["decision_counts"]["驳回"], 1)
            self.assertTrue(any(example["reason"] == "大促前不调整" for example in context["recent_examples"]))

    def test_outcome_can_be_appended_for_later_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            outcome = store.record_outcome(
                feedback_id="fb_001",
                recommendation_id="rec_001",
                horizon_days=7,
                metrics={"GMV": 4200, "链接投产": 4.6},
            )

            self.assertEqual(outcome["horizon_days"], 7)
            self.assertEqual(store.list_outcomes()[0]["metrics"]["GMV"], 4200)

    def test_outcome_rejects_non_numeric_or_non_finite_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            for metrics in ({"GMV": "未知"}, {"ROI": float("inf")}):
                with self.subTest(metrics=metrics):
                    with self.assertRaises(FeedbackValidationError):
                        store.record_outcome(
                            feedback_id="fb_001",
                            recommendation_id="rec_001",
                            horizon_days=7,
                            metrics=metrics,
                        )

    def test_invalid_decision_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            with self.assertRaises(FeedbackValidationError):
                store.record_feedback(_recommendation(), _payload(), decision="自动执行")

    def test_learning_feedback_rejects_partial_or_non_finite_budget_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            payload = _payload()
            recommendation = _recommendation(payload)
            with self.assertRaises(FeedbackValidationError):
                store.record_feedback(
                    recommendation,
                    payload,
                    decision="采纳",
                    budget_before=1000,
                )
            with self.assertRaises(FeedbackValidationError):
                store.record_feedback(
                    recommendation,
                    payload,
                    decision="采纳",
                    budget_before="NaN",
                    budget_after=1000,
                )

    def test_modified_or_rejected_feedback_requires_actionable_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(tmp)
            payload = _payload()
            recommendation = _recommendation(payload)
            with self.assertRaises(FeedbackValidationError):
                store.record_feedback(
                    recommendation,
                    payload,
                    decision="修改后采纳",
                    actual_action="增加5%",
                )
            with self.assertRaises(FeedbackValidationError):
                store.record_feedback(
                    recommendation,
                    payload,
                    decision="驳回",
                )


if __name__ == "__main__":
    unittest.main()
