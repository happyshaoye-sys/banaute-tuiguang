"""MetoX 大模型建议、人工反馈与后续结果的本地持久化。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4


FEEDBACK_DECISIONS = ("采纳", "修改后采纳", "驳回", "暂缓")
_WRITE_LOCK = Lock()
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")


class FeedbackValidationError(ValueError):
    """人工反馈或结果字段不符合本地存储契约。"""


def default_feedback_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = str(env.get("METOX_FEEDBACK_DIR", "") or "").strip()
    if explicit:
        return Path(explicit)
    local_app_data = str(env.get("LOCALAPPDATA", "") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "MetoX" / "feedback"
    return Path.home() / "AppData" / "Local" / "MetoX" / "feedback"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FeedbackValidationError(f"预算字段必须是数字：{value}") from exc
    if not math.isfinite(number) or number < 0:
        raise FeedbackValidationError("预算字段必须是大于等于0的有限数字")
    return number


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                continue
            clean[key_text] = _sanitize(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class FeedbackStore:
    """以JSONL追加保存，避免把用户数据写入程序安装目录。"""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root) if root is not None else default_feedback_root()

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def _append(self, name: str, record: Mapping[str, Any]) -> dict[str, Any]:
        clean = _sanitize(record)
        line = json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._path(name).open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        return clean

    def _list(self, name: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self._path(name)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
        if limit is not None and limit >= 0:
            return records[-limit:]
        return records

    def record_recommendation(
        self,
        recommendation: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        recommendation_id = str(recommendation.get("recommendation_id", "")).strip()
        if not recommendation_id:
            raise FeedbackValidationError("建议缺少recommendation_id")
        record = {
            "event": "recommendation",
            "saved_at": _timestamp(),
            "recommendation_id": recommendation_id,
            "task_key": recommendation.get("task_key") or task_payload.get("task_key"),
            "provider": recommendation.get("provider"),
            "requested_model": recommendation.get("requested_model"),
            "response_model": recommendation.get("response_model"),
            "response_id": recommendation.get("response_id"),
            "prompt_version": recommendation.get("prompt_version"),
            "usage": recommendation.get("usage", {}),
            "advice": dict(recommendation),
            "task_payload": dict(task_payload),
        }
        return self._append("recommendations", record)

    def record_feedback(
        self,
        recommendation: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        *,
        decision: str,
        actual_action: str = "",
        budget_before: Any = None,
        budget_after: Any = None,
        reason: str = "",
        note: str = "",
        operator: str = "",
    ) -> dict[str, Any]:
        if decision not in FEEDBACK_DECISIONS:
            raise FeedbackValidationError(f"不支持的反馈选项：{decision}")
        recommendation_id = str(recommendation.get("recommendation_id", "")).strip()
        if not recommendation_id:
            raise FeedbackValidationError("反馈缺少recommendation_id")
        before = _safe_number(budget_before)
        after = _safe_number(budget_after)
        reason_text = str(reason or "").strip()
        actual_action_text = str(actual_action or "").strip()
        if (before is None) != (after is None):
            raise FeedbackValidationError("调整前预算和调整后预算必须同时填写")
        if decision in {"修改后采纳", "驳回"} and not reason_text:
            raise FeedbackValidationError("修改后采纳或驳回时必须填写原因")
        if decision == "修改后采纳" and not actual_action_text and before is None:
            raise FeedbackValidationError("修改后采纳时必须填写实际动作或调整前后预算")
        change = None if before is None or after is None else after - before
        change_percent = None
        if change is not None and before not in (None, 0):
            change_percent = change / before * 100.0
        product = task_payload.get("product", {}) if isinstance(task_payload, Mapping) else {}
        rule_task = task_payload.get("rule_task", {}) if isinstance(task_payload, Mapping) else {}
        context = task_payload.get("context", {}) if isinstance(task_payload, Mapping) else {}
        record = {
            "event": "feedback",
            "saved_at": _timestamp(),
            "feedback_id": f"fb_{uuid4().hex}",
            "recommendation_id": recommendation_id,
            "task_key": recommendation.get("task_key") or task_payload.get("task_key"),
            "provider": recommendation.get("provider"),
            "model": recommendation.get("response_model") or recommendation.get("requested_model"),
            "prompt_version": recommendation.get("prompt_version"),
            "store": context.get("store"),
            "period": context.get("period"),
            "product_id": product.get("product_id"),
            "style_code": product.get("style_code"),
            "category": product.get("category"),
            "rule_task_type": rule_task.get("rule_task_type"),
            "rule_strategy": rule_task.get("rule_strategy"),
            "model_recommendation": recommendation.get("recommendation"),
            "model_budget_action": recommendation.get("budget_action"),
            "model_budget_change_percent": recommendation.get("budget_change_percent"),
            "decision": decision,
            "actual_action": actual_action_text,
            "budget_before": before,
            "budget_after": after,
            "actual_budget_change": change,
            "actual_budget_change_percent": change_percent,
            "reason": reason_text,
            "note": str(note or "").strip(),
            "operator": str(operator or "").strip(),
            "task_payload": dict(task_payload),
        }
        return self._append("feedback", record)

    def record_outcome(
        self,
        *,
        feedback_id: str,
        recommendation_id: str,
        horizon_days: int,
        metrics: Mapping[str, Any],
        note: str = "",
    ) -> dict[str, Any]:
        if not str(feedback_id).strip() or not str(recommendation_id).strip():
            raise FeedbackValidationError("后续结果必须关联反馈ID和建议ID")
        try:
            horizon = int(horizon_days)
        except (TypeError, ValueError) as exc:
            raise FeedbackValidationError("结果观察天数必须是整数") from exc
        if not 1 <= horizon <= 90:
            raise FeedbackValidationError("结果观察天数必须在1至90天之间")
        if not isinstance(metrics, Mapping) or not metrics:
            raise FeedbackValidationError("后续结果必须包含至少一个指标")
        clean_metrics: dict[str, float] = {}
        for key, value in metrics.items():
            metric_name = str(key or "").strip()
            if not metric_name:
                raise FeedbackValidationError("后续结果指标名称不能为空")
            try:
                metric_value = float(value)
            except (TypeError, ValueError) as exc:
                raise FeedbackValidationError(f"后续结果指标必须是数字：{metric_name}") from exc
            if not math.isfinite(metric_value):
                raise FeedbackValidationError(f"后续结果指标必须是有限数字：{metric_name}")
            clean_metrics[metric_name] = metric_value
        record = {
            "event": "outcome",
            "saved_at": _timestamp(),
            "outcome_id": f"out_{uuid4().hex}",
            "feedback_id": str(feedback_id).strip(),
            "recommendation_id": str(recommendation_id).strip(),
            "horizon_days": horizon,
            "metrics": clean_metrics,
            "note": str(note or "").strip(),
        }
        return self._append("outcomes", record)

    def list_recommendations(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._list("recommendations", limit=limit)

    def list_feedback(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._list("feedback", limit=limit)

    def list_outcomes(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._list("outcomes", limit=limit)

    def build_learning_context(
        self,
        task_payload: Mapping[str, Any],
        *,
        limit: int = 6,
    ) -> dict[str, Any]:
        product = task_payload.get("product", {}) if isinstance(task_payload, Mapping) else {}
        rule_task = task_payload.get("rule_task", {}) if isinstance(task_payload, Mapping) else {}
        category = product.get("category")
        task_type = rule_task.get("rule_task_type")
        matches = [
            record for record in self.list_feedback()
            if record.get("category") == category and record.get("rule_task_type") == task_type
        ]
        outcomes_by_feedback = {
            record.get("feedback_id"): record
            for record in self.list_outcomes()
            if record.get("feedback_id")
        }
        decision_counts = {decision: 0 for decision in FEEDBACK_DECISIONS}
        for record in matches:
            decision = record.get("decision")
            if decision in decision_counts:
                decision_counts[decision] += 1
        examples = []
        for record in matches[-max(0, limit):]:
            outcome = outcomes_by_feedback.get(record.get("feedback_id"), {})
            examples.append({
                "decision": record.get("decision"),
                "rule_strategy": record.get("rule_strategy"),
                "model_recommendation": record.get("model_recommendation"),
                "actual_action": record.get("actual_action"),
                "reason": record.get("reason"),
                "horizon_days": outcome.get("horizon_days"),
                "outcome_metrics": outcome.get("metrics", {}),
            })
        return {
            "match_scope": "same_category_and_rule_task_type",
            "matched_count": len(matches),
            "decision_counts": decision_counts,
            "recent_examples": examples,
        }


__all__ = [
    "FEEDBACK_DECISIONS",
    "FeedbackStore",
    "FeedbackValidationError",
    "default_feedback_root",
]
