"""MetoX 可选大模型顾问。

该模块只生成结构化建议，不执行推广动作，也不修改输入任务。OpenAI 与
DeepSeek 共用 Responses API 风格的最小请求契约，供应商差异限制在此模块内。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import os
from numbers import Number
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


LLM_PROMPT_VERSION = "metox-llm-advisor-2026-09-02-v1"
ALLOWED_RECOMMENDATIONS = ("保持", "增加", "削减", "暂停", "小额测试", "人工复核")
ALLOWED_BUDGET_ACTIONS = ("不调整", "增加", "削减", "暂停", "小额测试")
ALLOWED_CONFIDENCE = ("高", "中", "低")
MAX_BUDGET_INCREASE_PERCENT = 30.0
MAX_BUDGET_DECREASE_PERCENT = -50.0


class LLMAdvisorError(RuntimeError):
    """大模型顾问基础错误。"""


class LLMConfigurationError(LLMAdvisorError):
    """供应商、地址或密钥配置错误。"""


class LLMTransportError(LLMAdvisorError):
    """网络或服务端请求失败。"""


class LLMResponseError(LLMAdvisorError):
    """响应无法解析或未通过安全校验。"""


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    api_key_env: str
    base_url_env: str
    default_base_url: str
    default_model: str
    models: tuple[str, ...]

    @property
    def endpoint(self) -> str:
        return _responses_endpoint(self.default_base_url)


PROVIDER_SPECS = {
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        base_url_env="METOX_OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-5.6-luna",
        models=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"),
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="METOX_DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
    ),
}


_TASK_FIELD_MAP = {
    "执行优先级": "execution_priority",
    "任务优先分": "task_priority_score",
    "款式主图": "main_image_url",
    "货号": "style_code",
    "链接数": "link_count",
    "宝贝ID": "product_id",
    "淘宝类目": "category",
    "GMV": "gmv",
    "商品访客数": "product_visitors",
    "链接投产": "link_roi",
    "综合等级": "grade",
    "商品定位": "product_position",
    "商品周期": "product_cycle",
    "MetoX算法命中": "metox_hits",
    "优先级": "metox_priority",
    "AI任务类型": "rule_task_type",
    "商品级总策略": "rule_strategy",
    "动作对象": "action_scope",
    "主执行动作": "rule_main_action",
    "关键词状态": "keyword_status",
    "关键词动作": "keyword_action",
    "人群状态": "audience_status",
    "人群动作": "audience_action",
    "全站状态": "sitewide_status",
    "全站动作": "sitewide_action",
    "推广计划数": "promotion_plan_count",
    "计划总数": "plan_total",
    "计划展示数": "plan_displayed",
    "计划覆盖率": "plan_coverage",
    "计划数据完整度": "plan_completeness",
    "计划数据说明": "plan_data_note",
    "AI可信度": "rule_confidence",
    "AI可信度分": "rule_confidence_score",
    "AI可信度说明": "rule_confidence_note",
    "商品策略数据源": "product_strategy_source",
    "渠道动作数据源": "channel_action_source",
    "计划动作数据源": "plan_action_source",
    "计划执行摘要": "plan_summary",
    "计划执行明细": "plan_details",
    "非投放动作": "non_promotion_action",
    "观察周期": "rule_observation_period",
    "成功标准": "rule_success_criteria",
    "停止条件": "rule_stop_conditions",
    "任务触发原因": "rule_reason",
    "库存提醒": "inventory_note",
}


ADVICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendation": {"type": "string", "enum": list(ALLOWED_RECOMMENDATIONS)},
        "budget_action": {"type": "string", "enum": list(ALLOWED_BUDGET_ACTIONS)},
        "budget_change_percent": {
            "type": "number",
            "minimum": MAX_BUDGET_DECREASE_PERCENT,
            "maximum": MAX_BUDGET_INCREASE_PERCENT,
        },
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
        "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "observation_days": {"type": "integer", "minimum": 1, "maximum": 30},
        "success_criteria": {"type": "string"},
        "stop_conditions": {"type": "string"},
        "confidence": {"type": "string", "enum": list(ALLOWED_CONFIDENCE)},
        "needs_manual_review": {"type": "boolean"},
    },
    "required": [
        "recommendation",
        "budget_action",
        "budget_change_percent",
        "reason",
        "evidence",
        "risks",
        "observation_days",
        "success_criteria",
        "stop_conditions",
        "confidence",
        "needs_manual_review",
    ],
}


SYSTEM_INSTRUCTIONS = """你是MetoX经营决策顾问，只能基于输入JSON解释和复核现有规则任务。
要求：
1. MetoX规则结果是基线；不得虚构输入中没有的数据、计划、预算或原因。
2. 你只能给建议，不得直接执行、不得声称已修改推广平台、不得绕过人工确认。
3. 预算增加最多30%，预算削减最多50%；暂停不写成-100%，比例填0并标记人工复核；小额测试比例为0%至10%。
4. 建议动作和预算动作必须一致；高风险、规则冲突或证据不足必须标记人工复核。
5. 优先引用输入中的具体证据，并给出观察期、成功标准和停止条件。
6. 历史反馈仅用于参考；不得把不同类目或不同任务类型的经验当成硬规则。
7. 严格返回JSON Schema要求的字段，不要输出额外文本。"""


def get_provider_spec(provider: str) -> ProviderSpec:
    key = str(provider or "").strip().lower()
    try:
        return PROVIDER_SPECS[key]
    except KeyError as exc:
        raise LLMConfigurationError(f"不支持的大模型供应商：{provider}") from exc


def validate_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMConfigurationError("大模型服务地址不能包含账号、密码、查询参数或片段")
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return value
    raise LLMConfigurationError("大模型服务地址必须使用HTTPS；仅本机地址允许HTTP")


def _responses_endpoint(base_url: str) -> str:
    value = validate_base_url(base_url)
    return value if value.endswith("/responses") else f"{value}/responses"


def resolve_provider_config(
    provider: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    spec = get_provider_spec(provider)
    env = os.environ if environ is None else environ
    resolved_model = str(model or spec.default_model).strip()
    if not resolved_model:
        raise LLMConfigurationError("模型名称不能为空")
    resolved_key = str(api_key if api_key is not None else env.get(spec.api_key_env, "")).strip()
    resolved_base = str(base_url or env.get(spec.base_url_env, "") or spec.default_base_url).strip()
    return {
        "provider": spec.key,
        "provider_label": spec.label,
        "model": resolved_model,
        "api_key": resolved_key,
        "api_key_env": spec.api_key_env,
        "base_url": validate_base_url(resolved_base),
        "endpoint": _responses_endpoint(resolved_base),
    }


def provider_key_available(provider: str, environ: Mapping[str, str] | None = None) -> bool:
    config = resolve_provider_config(provider, environ=environ)
    return bool(config["api_key"])


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Number):
        try:
            numeric = float(value)
            return None if math.isnan(numeric) or math.isinf(numeric) else numeric
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    text = str(value).strip()
    return None if text in {"", "nan", "None", "<NA>"} else text


def _mapped_task(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        target: _json_safe(task.get(source))
        for source, target in _TASK_FIELD_MAP.items()
        if source in task and _json_safe(task.get(source)) is not None
    }


def build_task_payload(
    task: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把AI任务行缩减为允许外发的最小字段集。"""
    source = _mapped_task(task)
    context = context or {}
    safe_context = {
        "store": _json_safe(context.get("店铺", context.get("store"))),
        "period": _json_safe(context.get("周期", context.get("period"))),
        "data_date": _json_safe(context.get("数据日期", context.get("data_date"))),
        "app_version": _json_safe(context.get("版本", context.get("app_version"))),
        "rule_version": _json_safe(context.get("规则版本", context.get("rule_version"))),
    }
    safe_context = {key: value for key, value in safe_context.items() if value is not None}
    payload = {
        "context": safe_context,
        "product": {
            key: source[key]
            for key in ("product_id", "style_code", "category", "link_count")
            if key in source
        },
        "metrics": {
            key: source[key]
            for key in ("gmv", "product_visitors", "link_roi")
            if key in source
        },
        "rule_task": {
            key: value
            for key, value in source.items()
            if key not in {
                "product_id", "style_code", "category", "link_count",
                "gmv", "product_visitors", "link_roi", "main_image_url",
            }
        },
    }
    identity = {
        "store": safe_context.get("store", ""),
        "product_id": payload["product"].get("product_id", ""),
        "style_code": payload["product"].get("style_code", ""),
        "category": payload["product"].get("category", ""),
        "rule_task_type": payload["rule_task"].get("rule_task_type", ""),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    payload["task_key"] = f"task_{digest}"
    return payload


def build_request_payload(
    provider: str,
    model: str,
    task_payload: Mapping[str, Any],
    feedback_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider_key = get_provider_spec(provider).key
    dynamic_input = {
        "current_task": _json_safe(task_payload),
        "matching_historical_feedback": _json_safe(feedback_context or {"matched_count": 0}),
    }
    response_format = {
        "type": "json_schema",
        "name": "metox_advice",
        "schema": ADVICE_SCHEMA,
    }
    # DeepSeek Responses API supports json_schema, but does not accept the
    # OpenAI-only `strict` member in text.format.
    if provider_key == "openai":
        response_format["strict"] = True

    body = {
        "model": model,
        "instructions": SYSTEM_INSTRUCTIONS,
        "input": json.dumps(dynamic_input, ensure_ascii=False, separators=(",", ":")),
        "text": {"format": response_format},
        "max_output_tokens": 1400,
    }
    if provider_key == "openai":
        body["store"] = False
        body["reasoning"] = {"effort": "low"}
    return body


def _bounded_text(value: Any, field: str, *, maximum: int = 2000) -> str:
    text = str(value or "").strip()
    if not text:
        raise LLMResponseError(f"大模型响应缺少字段：{field}")
    if len(text) > maximum:
        raise LLMResponseError(f"大模型响应字段过长：{field}")
    return text


def _text_list(value: Any, field: str, *, minimum: int = 0, maximum: int = 6) -> list[str]:
    if not isinstance(value, list):
        raise LLMResponseError(f"大模型响应字段必须是数组：{field}")
    result = [_bounded_text(item, field, maximum=500) for item in value]
    if not minimum <= len(result) <= maximum:
        raise LLMResponseError(f"大模型响应字段数量不符合要求：{field}")
    return result


def validate_recommendation(
    advice: Mapping[str, Any],
    task_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(advice, Mapping):
        raise LLMResponseError("大模型响应不是JSON对象")
    missing = [field for field in ADVICE_SCHEMA["required"] if field not in advice]
    if missing:
        raise LLMResponseError(f"大模型响应缺少字段：{', '.join(missing)}")

    recommendation = _bounded_text(advice.get("recommendation"), "recommendation", maximum=20)
    budget_action = _bounded_text(advice.get("budget_action"), "budget_action", maximum=20)
    confidence = _bounded_text(advice.get("confidence"), "confidence", maximum=10)
    if recommendation not in ALLOWED_RECOMMENDATIONS:
        raise LLMResponseError(f"不允许的建议动作：{recommendation}")
    if budget_action not in ALLOWED_BUDGET_ACTIONS:
        raise LLMResponseError(f"不允许的预算动作：{budget_action}")
    if confidence not in ALLOWED_CONFIDENCE:
        raise LLMResponseError(f"不允许的可信度：{confidence}")

    try:
        budget_percent = float(advice.get("budget_change_percent"))
    except (TypeError, ValueError) as exc:
        raise LLMResponseError("预算调整比例必须是数字") from exc
    if not MAX_BUDGET_DECREASE_PERCENT <= budget_percent <= MAX_BUDGET_INCREASE_PERCENT:
        raise LLMResponseError(
            f"预算调整比例超出安全范围：{MAX_BUDGET_DECREASE_PERCENT}%至{MAX_BUDGET_INCREASE_PERCENT}%"
        )
    if budget_action == "增加" and budget_percent <= 0:
        raise LLMResponseError("预算动作为增加时，调整比例必须大于0")
    if budget_action == "削减" and budget_percent >= 0:
        raise LLMResponseError("预算动作为削减时，调整比例必须小于0")
    if budget_action == "不调整" and budget_percent != 0:
        raise LLMResponseError("预算不调整时，调整比例必须为0")
    if budget_action == "暂停" and budget_percent != 0:
        raise LLMResponseError("预算动作为暂停时，调整比例必须为0并交由人工确认")
    if budget_action == "小额测试" and not 0 <= budget_percent <= 10:
        raise LLMResponseError("小额测试的预算比例必须在0%至10%之间")

    expected_budget_actions = {
        "保持": {"不调整"},
        "增加": {"增加"},
        "削减": {"削减"},
        "暂停": {"暂停"},
        "小额测试": {"小额测试"},
        "人工复核": set(ALLOWED_BUDGET_ACTIONS),
    }
    if budget_action not in expected_budget_actions[recommendation]:
        raise LLMResponseError("模型建议动作与预算动作不一致")

    try:
        observation_days = int(advice.get("observation_days"))
    except (TypeError, ValueError) as exc:
        raise LLMResponseError("观察天数必须是整数") from exc
    if not 1 <= observation_days <= 30:
        raise LLMResponseError("观察天数必须在1至30天之间")
    if not isinstance(advice.get("needs_manual_review"), bool):
        raise LLMResponseError("needs_manual_review必须是布尔值")

    guardrail_notes: list[str] = []
    rule_task = task_payload.get("rule_task", {}) if isinstance(task_payload, Mapping) else {}
    rule_type = str(rule_task.get("rule_task_type", ""))
    rule_strategy = str(rule_task.get("rule_strategy", ""))
    if recommendation == "增加" and ("止损" in rule_type or "暂停" in rule_strategy):
        guardrail_notes.append("模型增加预算建议与MetoX规则任务冲突，必须人工复核")
    if recommendation in {"削减", "暂停"} and ("放量" in rule_type or "放量" in rule_strategy):
        guardrail_notes.append("模型收缩建议与MetoX放量规则冲突，必须人工复核")
    if recommendation == "暂停":
        guardrail_notes.append("暂停属于高影响动作，必须人工复核")
    if recommendation == "人工复核":
        guardrail_notes.append("模型已标记人工复核，不得直接执行")
    if str(rule_task.get("rule_confidence", "")) == "低":
        guardrail_notes.append("原规则可信度低，必须人工复核")
    if str(rule_task.get("plan_completeness", "")) == "无计划" and budget_percent != 0:
        guardrail_notes.append("没有计划级数据，预算比例建议只能作为人工参考")

    result = {
        "recommendation": recommendation,
        "budget_action": budget_action,
        "budget_change_percent": budget_percent,
        "reason": _bounded_text(advice.get("reason"), "reason"),
        "evidence": _text_list(advice.get("evidence"), "evidence", minimum=1),
        "risks": _text_list(advice.get("risks"), "risks"),
        "observation_days": observation_days,
        "success_criteria": _bounded_text(advice.get("success_criteria"), "success_criteria"),
        "stop_conditions": _bounded_text(advice.get("stop_conditions"), "stop_conditions"),
        "confidence": confidence,
        "needs_manual_review": bool(advice.get("needs_manual_review")) or bool(guardrail_notes),
        "automatic_execution": False,
        "guardrail_notes": guardrail_notes,
    }
    return result


def _extract_output_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in response.get("output", []) or []:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, Mapping) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    raise LLMResponseError("大模型响应中没有可解析的output_text")


def _parse_json_output(text: str) -> Mapping[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise LLMResponseError("大模型没有返回JSON对象")
    try:
        parsed = json.loads(value[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"大模型JSON解析失败：{exc.msg}") from exc
    if not isinstance(parsed, Mapping):
        raise LLMResponseError("大模型JSON顶层必须是对象")
    return parsed


def _post_json(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_detail = exc.read().decode("utf-8", errors="replace").strip()
        detail = raw_detail[:1000]
        if raw_detail:
            try:
                error_payload = json.loads(raw_detail)
                error = error_payload.get("error") if isinstance(error_payload, Mapping) else None
                message = error.get("message") if isinstance(error, Mapping) else None
                if message:
                    detail = str(message).strip()[:1000]
            except json.JSONDecodeError:
                pass
        if not detail:
            reason = str(getattr(exc, "reason", "") or "").strip()
            detail = reason or "服务未返回详细错误信息"
        raise LLMTransportError(f"大模型服务返回HTTP {exc.code}：{detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LLMTransportError(f"无法连接大模型服务：{exc}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMTransportError("大模型服务返回了非JSON响应") from exc
    if not isinstance(result, Mapping):
        raise LLMTransportError("大模型服务响应格式无效")
    return result


def call_advisor(
    *,
    provider: str,
    task: Mapping[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    context: Mapping[str, Any] | None = None,
    feedback_context: Mapping[str, Any] | None = None,
    timeout: float = 45.0,
    transport: Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    config = resolve_provider_config(
        provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    if not config["api_key"]:
        raise LLMConfigurationError(f"未配置环境变量 {config['api_key_env']}")
    if timeout <= 0 or timeout > 120:
        raise LLMConfigurationError("请求超时必须在0至120秒之间")

    task_payload = build_task_payload(task, context=context)
    request_body = build_request_payload(
        config["provider"], config["model"], task_payload, feedback_context
    )
    sender = transport or _post_json
    response = sender(
        config["endpoint"],
        {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
            "User-Agent": "MetoX-LLM-Advisor/2.38",
        },
        request_body,
        timeout,
    )
    if response.get("error"):
        error = response.get("error")
        message = error.get("message") if isinstance(error, Mapping) else str(error)
        raise LLMTransportError(f"大模型服务返回错误：{message}")

    advice = validate_recommendation(_parse_json_output(_extract_output_text(response)), task_payload)
    advice.update({
        "recommendation_id": f"rec_{uuid4().hex}",
        "task_key": task_payload["task_key"],
        "provider": config["provider"],
        "requested_model": config["model"],
        "response_model": str(response.get("model") or config["model"]),
        "response_id": str(response.get("id") or ""),
        "prompt_version": LLM_PROMPT_VERSION,
        "usage": _json_safe(response.get("usage") or {}),
        "task_payload": task_payload,
    })
    return advice


__all__ = [
    "ADVICE_SCHEMA",
    "LLM_PROMPT_VERSION",
    "LLMAdvisorError",
    "LLMConfigurationError",
    "LLMResponseError",
    "LLMTransportError",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "build_request_payload",
    "build_task_payload",
    "call_advisor",
    "get_provider_spec",
    "provider_key_available",
    "resolve_provider_config",
    "validate_base_url",
    "validate_recommendation",
]
