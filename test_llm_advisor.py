from __future__ import annotations

import unittest

from core.llm_advisor import (
    LLMConfigurationError,
    LLMResponseError,
    build_request_payload,
    build_task_payload,
    call_advisor,
    get_provider_spec,
    validate_base_url,
    validate_recommendation,
)


def _task(**overrides):
    row = {
        "宝贝ID": "1001",
        "货号": "SKU-001",
        "商品名称": "不应外发的商品名称",
        "执行优先级": "P1",
        "任务优先分": 96,
        "淘宝类目": "连衣裙",
        "GMV": 3000,
        "商品访客数": 500,
        "链接投产": 4.2,
        "综合等级": "A",
        "商品定位": "冲刺款",
        "商品周期": "成长期",
        "MetoX算法命中": "加预算",
        "AI任务类型": "放量任务",
        "商品级总策略": "逐步放量",
        "主执行动作": "关键词增加10%",
        "计划执行摘要": "2个计划保持，1个计划增加10%",
        "观察周期": "3天",
        "成功标准": "链接投产不低于3.0",
        "停止条件": "链接投产低于2.5",
        "任务触发原因": "MetoX命中加预算",
        "AI可信度": "高",
        "计划数据完整度": "100.0%",
        "库存提醒": "库存充足",
        "secret": "must-not-leak",
    }
    row.update(overrides)
    return row


def _valid_advice(**overrides):
    advice = {
        "recommendation": "增加",
        "budget_action": "增加",
        "budget_change_percent": 10,
        "reason": "规则、投产和库存同时支持小步放量。",
        "evidence": ["MetoX命中加预算", "链接投产4.2"],
        "risks": ["放量后投产可能回落"],
        "observation_days": 3,
        "success_criteria": "链接投产不低于3.0",
        "stop_conditions": "链接投产低于2.5时停止加量",
        "confidence": "高",
        "needs_manual_review": False,
    }
    advice.update(overrides)
    return advice


class ProviderContractTests(unittest.TestCase):
    def test_provider_specs_use_responses_api_and_environment_keys(self):
        openai = get_provider_spec("openai")
        deepseek = get_provider_spec("deepseek")

        self.assertEqual(openai.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(openai.default_model, "gpt-5.6-luna")
        self.assertEqual(openai.endpoint, "https://api.openai.com/v1/responses")
        self.assertEqual(deepseek.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(deepseek.default_model, "deepseek-v4-flash")
        self.assertEqual(deepseek.endpoint, "https://api.deepseek.com/responses")

    def test_only_https_or_local_http_base_urls_are_allowed(self):
        self.assertEqual(validate_base_url("https://gateway.example/v1"), "https://gateway.example/v1")
        self.assertEqual(validate_base_url("http://127.0.0.1:9000/v1"), "http://127.0.0.1:9000/v1")
        with self.assertRaises(LLMConfigurationError):
            validate_base_url("http://gateway.example/v1")


class TaskPayloadTests(unittest.TestCase):
    def test_payload_is_minimized_and_excludes_product_name_and_unknown_fields(self):
        payload = build_task_payload(
            _task(),
            context={"店铺": "女装店", "周期": "近7天", "操作人": "张三"},
        )

        text = str(payload)
        self.assertEqual(payload["context"]["store"], "女装店")
        self.assertEqual(payload["product"]["product_id"], "1001")
        self.assertEqual(payload["rule_task"]["execution_priority"], "P1")
        self.assertNotIn("商品名称", text)
        self.assertNotIn("must-not-leak", text)
        self.assertNotIn("张三", text)

    def test_request_uses_json_schema_and_never_grants_execution(self):
        payload = build_task_payload(_task())
        request = build_request_payload("openai", "gpt-5.6-luna", payload, {"matched_count": 0})

        self.assertFalse(request["store"])
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertFalse(request["text"]["format"]["schema"]["properties"]["needs_manual_review"].get("default", False))
        self.assertIn("不得直接执行", request["instructions"])

    def test_deepseek_request_omits_openai_only_options(self):
        payload = build_task_payload(_task())
        request = build_request_payload("deepseek", "deepseek-v4-flash", payload)

        self.assertNotIn("store", request)
        self.assertNotIn("reasoning", request)
        self.assertEqual(request["text"]["format"]["type"], "json_schema")


class RecommendationValidationTests(unittest.TestCase):
    def test_valid_recommendation_is_normalized_and_marked_read_only(self):
        payload = build_task_payload(_task())
        result = validate_recommendation(_valid_advice(), payload)

        self.assertEqual(result["budget_change_percent"], 10.0)
        self.assertFalse(result["automatic_execution"])
        self.assertEqual(result["guardrail_notes"], [])

    def test_out_of_range_budget_change_is_rejected(self):
        payload = build_task_payload(_task())
        with self.assertRaises(LLMResponseError):
            validate_recommendation(_valid_advice(budget_change_percent=80), payload)

    def test_recommendation_and_budget_action_must_be_consistent(self):
        payload = build_task_payload(_task())
        with self.assertRaises(LLMResponseError):
            validate_recommendation(
                _valid_advice(recommendation="保持", budget_action="增加"),
                payload,
            )

    def test_pause_is_never_represented_as_an_unbounded_budget_cut(self):
        payload = build_task_payload(_task())
        result = validate_recommendation(
            _valid_advice(
                recommendation="暂停",
                budget_action="暂停",
                budget_change_percent=0,
            ),
            payload,
        )

        self.assertTrue(result["needs_manual_review"])
        self.assertFalse(result["automatic_execution"])

    def test_conflict_with_stop_loss_rule_forces_manual_review(self):
        payload = build_task_payload(
            _task(AI任务类型="止损任务", 商品级总策略="暂停推广", 主执行动作="暂停")
        )
        result = validate_recommendation(_valid_advice(recommendation="增加"), payload)

        self.assertTrue(result["needs_manual_review"])
        self.assertTrue(any("规则任务" in note for note in result["guardrail_notes"]))

    def test_call_advisor_parses_responses_output_and_records_usage(self):
        captured = {}

        def transport(url, headers, body, timeout):
            captured.update(url=url, headers=headers, body=body, timeout=timeout)
            return {
                "id": "resp_test",
                "model": "gpt-5.6-luna-2026-08-01",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": __import__("json").dumps(_valid_advice(), ensure_ascii=False)}],
                }],
                "usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
            }

        result = call_advisor(
            provider="openai",
            task=_task(),
            api_key="test-key",
            transport=transport,
        )

        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(result["response_id"], "resp_test")
        self.assertEqual(result["usage"]["total_tokens"], 200)
        self.assertTrue(result["recommendation_id"].startswith("rec_"))

    def test_missing_key_fails_before_transport(self):
        called = False

        def transport(*_args):
            nonlocal called
            called = True
            return {}

        with self.assertRaises(LLMConfigurationError):
            call_advisor(provider="deepseek", task=_task(), api_key="", transport=transport)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
