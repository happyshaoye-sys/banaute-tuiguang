"""Pure backtest helpers for the new-product lifecycle MVP.

The module intentionally has no Streamlit, loader, cache, or file-system
coupling.  It can be fed either by a future lifecycle module's checkpoint table
or by simple daily age-grain rows for tests and offline validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable

import numpy as np
import pandas as pd


DECISION_DOWN = "下架"
DECISION_OBSERVE = "继续观察"

FUTURE_FAILURE = "未来失败"
FUTURE_RECOVERED = "未来恢复"
FUTURE_UNVERIFIABLE = "不可验证"

RULE_A = "A"
RULE_B = "B"
RULE_C = "C"
RULE_D = "D"
RULES = (RULE_A, RULE_B, RULE_C, RULE_D)

POSITIVE_METOX_LABELS = (
    "明日之星",
    "强主推",
    "近3日潜力",
    "加购爆发",
    "加预算",
    "保护持有",
    "溢出",
)


class BacktestColumns:
    """Column contract shared by lifecycle and backtest code.

    Chinese source-field mapping belongs in source/lifecycle modules.  This
    layer uses stable English names so tests and later UI/export code can pass
    a normalized frame without depending on raw platform headers.
    """

    style_key = "style_key"
    age_day = "age_day"
    store = "store"
    category = "category"
    price_band = "price_band"
    checkpoint = "checkpoint"

    visitors = "visitors"
    carts = "carts"
    buyers = "buyers"
    units = "units"
    gmv = "gmv"
    profit = "profit"
    valid_data_day = "valid_data_day"
    is_online = "is_online"
    stock_ok = "stock_ok"
    manual_removed = "manual_removed"
    data_missing = "data_missing"
    historical_truncated = "historical_truncated"

    main_ctr = "main_ctr"
    creative_impressions = "creative_impressions"
    creative_clicks = "creative_clicks"
    metox_labels = "metox_labels"

    baseline_cart_rate = "baseline_cart_rate"
    baseline_pay_cvr = "baseline_pay_cvr"
    baseline_uv_value = "baseline_uv_value"
    baseline_cumulative_cart_rate = "baseline_cumulative_cart_rate"
    baseline_cumulative_pay_cvr = "baseline_cumulative_pay_cvr"
    baseline_cumulative_uv_value = "baseline_cumulative_uv_value"
    baseline_main_ctr = "baseline_main_ctr"
    baseline_creative_ctr = "baseline_creative_ctr"
    ctr_positive_signal = "ctr_positive_signal"
    peer_count = "peer_count"


C = BacktestColumns


@dataclass(frozen=True)
class BacktestConfig:
    checkpoints: tuple[int, ...] = (15, 30)
    decision_min_visitors: int = 300
    decision_min_valid_days: int = 12
    min_peer_count: int = 20
    validation_min_visitors: int = 100
    validation_min_valid_days: int = 12
    low_multiplier: float = 0.8
    ctr_positive_multiplier: float = 1.2
    creative_ctr_min_impressions: float = 100.0
    positive_metox_labels: tuple[str, ...] = POSITIVE_METOX_LABELS
    group_cols: tuple[str, ...] = (C.store, C.category, C.price_band)
    opportunity_profit_col: str = C.profit


@dataclass(frozen=True)
class BacktestRun:
    checkpoint_rows: pd.DataFrame
    summary: pd.DataFrame


def checkpoint_windows(checkpoint: int) -> dict[str, tuple[int, int]]:
    """Return the non-leaking windows for a lifecycle checkpoint."""

    checkpoint = int(checkpoint)
    if checkpoint == 15:
        return {
            "previous": (1, 0),
            "current": (1, 15),
            "cumulative": (1, 15),
            "validation": (16, 30),
        }
    if checkpoint == 30:
        return {
            "previous": (1, 15),
            "current": (16, 30),
            "cumulative": (1, 30),
            "validation": (31, 45),
        }
    raise ValueError(f"Unsupported checkpoint: {checkpoint!r}")


def prepare_checkpoint_rows(
    daily_rows: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    """Aggregate age-grain rows into configured checkpoint rows.

    Decision fields are read only from the checkpoint-visible windows.  Future
    fields are aggregated separately for validation labels, preventing future
    periods from changing current 15-day-cycle rule inputs.
    """

    config = config or BacktestConfig()
    if daily_rows is None or daily_rows.empty:
        return pd.DataFrame()
    required = {C.style_key, C.age_day}
    missing = sorted(required - set(daily_rows.columns))
    if missing:
        raise ValueError(f"Missing required daily columns: {missing}")

    work = daily_rows.copy()
    work[C.style_key] = work[C.style_key].astype(str).str.strip()
    work[C.age_day] = pd.to_numeric(work[C.age_day], errors="coerce")
    work = work[work[C.style_key].ne("") & work[C.age_day].notna()].copy()
    work[C.age_day] = work[C.age_day].astype(int)
    work = _collapse_daily_rows_by_style_age(work, config)
    if work.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for style_key, part in work.groupby(C.style_key, sort=False):
        part = part.sort_values(C.age_day)
        max_observed_age = int(part[C.age_day].max())
        for checkpoint in config.checkpoints:
            if max_observed_age < checkpoint:
                continue
            windows = checkpoint_windows(checkpoint)
            visible = part[part[C.age_day] <= checkpoint]
            if visible.empty:
                continue
            row: dict[str, object] = {
                C.style_key: style_key,
                C.checkpoint: checkpoint,
            }
            _attach_dimensions(row, visible, part, config)
            for name, bounds in windows.items():
                _attach_window(row, name, _slice_window(part, *bounds), bounds)
            _attach_checkpoint_state(row, visible)
            rows.append(row)
    return _attach_peer_baselines(pd.DataFrame(rows), config)


def _collapse_daily_rows_by_style_age(
    frame: pd.DataFrame,
    config: BacktestConfig,
) -> pd.DataFrame:
    """Collapse duplicate product rows into one style-age row.

    The source can contain multiple product IDs for the same style and age day.
    Sale protection depends on buyers, units, or GMV, so every sale metric must
    survive this collapse.
    """

    if frame.empty:
        return frame
    numeric_cols = [
        C.visitors,
        C.carts,
        C.buyers,
        C.units,
        C.gmv,
        C.profit,
        C.creative_impressions,
        C.creative_clicks,
    ]
    bool_cols = [
        C.valid_data_day,
        C.is_online,
        C.manual_removed,
        C.data_missing,
        C.historical_truncated,
        C.ctr_positive_signal,
    ]
    all_bool_cols = [column for column in bool_cols if column in frame.columns]
    rows: list[dict[str, object]] = []
    group_cols = [C.style_key, C.age_day]
    for (style_key, age_day), part in frame.groupby(group_cols, sort=False, dropna=False):
        row: dict[str, object] = {
            C.style_key: style_key,
            C.age_day: age_day,
        }
        for column in config.group_cols:
            row[column] = _last_present(part, column, default="")
        for column in numeric_cols:
            row[column] = _sum(part, column)
        for column in all_bool_cols:
            row[column] = _any_bool(part, column)
        if C.stock_ok in frame.columns:
            row[C.stock_ok] = bool(_bool(part, C.stock_ok, default=True).any())
        row[C.main_ctr] = _last_present(part, C.main_ctr, default=np.nan)
        row[C.metox_labels] = _merge_label_values(part[C.metox_labels]) if C.metox_labels in part.columns else ""
        for column in (
            C.baseline_cart_rate,
            C.baseline_pay_cvr,
            C.baseline_uv_value,
            C.baseline_cumulative_cart_rate,
            C.baseline_cumulative_pay_cvr,
            C.baseline_cumulative_uv_value,
            C.baseline_main_ctr,
            C.baseline_creative_ctr,
        ):
            if column in frame.columns:
                row[column] = _last_present(part, column, default=np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def _merge_label_values(values: Iterable[object]) -> str:
    labels: list[str] = []
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        for part in re.split(r"[\s,，；、/]+", str(value)):
            label = part.strip()
            if label and label not in labels:
                labels.append(label)
    return " ".join(labels)


def apply_backtest_rules(
    checkpoint_rows: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    """Apply A-D lifecycle backtest decisions and future labels."""

    config = config or BacktestConfig()
    if checkpoint_rows is None or checkpoint_rows.empty:
        return pd.DataFrame()

    result = checkpoint_rows.copy()
    result["future_label"] = classify_future(result, config)
    result["future_label_method"] = _future_label_methods(result)
    result["ctr_positive"] = _ctr_positive(result, config)
    result["metox_positive"] = result.get("metox_labels_at_checkpoint", pd.Series("", index=result.index)).map(
        lambda value: has_positive_metox_label(value, config.positive_metox_labels)
    )

    a_down = _rule_a_down_mask(result, config)
    result["rule_A_decision"] = np.where(a_down, DECISION_DOWN, DECISION_OBSERVE)
    result["rule_A_status"] = _rule_a_statuses(result, a_down, config)
    result["rule_A_reason"] = _rule_a_reasons(result, a_down, config)

    result["ctr_protected"] = a_down & result["ctr_positive"]
    result["metox_protected"] = a_down & result["metox_positive"]

    result["rule_B_decision"] = np.where(
        a_down & ~result["ctr_protected"],
        DECISION_DOWN,
        DECISION_OBSERVE,
    )
    result["rule_C_decision"] = np.where(
        a_down & ~result["metox_protected"],
        DECISION_DOWN,
        DECISION_OBSERVE,
    )
    result["rule_D_decision"] = np.where(
        a_down & ~(result["ctr_protected"] | result["metox_protected"]),
        DECISION_DOWN,
        DECISION_OBSERVE,
    )
    return result


def classify_future(
    checkpoint_rows: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> pd.Series:
    """Classify future validation windows without using current decisions.

    D15/D30 use the current Rule A validation model: no paid sale and low
    validation cart rate in the following 15-day cycle.
    """

    config = config or BacktestConfig()
    index = checkpoint_rows.index
    label = pd.Series(FUTURE_UNVERIFIABLE, index=index, dtype=object)
    active_checkpoint = _rule_a_active_checkpoint(checkpoint_rows)
    validation_context_ok = _future_validation_context_ok(checkpoint_rows, config)

    active_verifiable = (
        active_checkpoint
        & validation_context_ok
        & _number(checkpoint_rows, "baseline_cart_rate").gt(0)
    )
    active_future_failure = (
        active_verifiable
        & _validation_no_paid_sale(checkpoint_rows)
        & _low_vs_baseline(checkpoint_rows, "validation_cart_rate", "baseline_cart_rate", config)
    )
    label.loc[active_verifiable] = FUTURE_RECOVERED
    label.loc[active_future_failure] = FUTURE_FAILURE

    return label


def _future_label_methods(frame: pd.DataFrame) -> pd.Series:
    method = pd.Series("unsupported_checkpoint", index=frame.index, dtype=object)
    method.loc[_rule_a_active_checkpoint(frame)] = "rule_a_cart_only_validation"
    return method


def _future_validation_context_ok(frame: pd.DataFrame, config: BacktestConfig) -> pd.Series:
    return (
        _number(frame, "validation_valid_days").ge(config.validation_min_valid_days)
        & _number(frame, "validation_visitors").ge(config.validation_min_visitors)
        & _bool(frame, "validation_data_missing", default=False).eq(False)
    )


def _validation_no_paid_sale(frame: pd.DataFrame) -> pd.Series:
    buyers = _number(frame, "validation_buyers").fillna(0)
    units = _number(frame, "validation_units").fillna(0)
    gmv = _number(frame, "validation_gmv").fillna(0)
    return buyers.le(0) & units.le(0) & gmv.le(0)


def summarize_backtest(
    backtest_rows: pd.DataFrame,
    group_cols: Iterable[str] | None = None,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    """Summarize decision quality for each rule and optional group."""

    config = config or BacktestConfig()
    if backtest_rows is None or backtest_rows.empty:
        return pd.DataFrame()
    group_cols = tuple(group_cols if group_cols is not None else ())
    missing_group_cols = [column for column in group_cols if column not in backtest_rows.columns]
    if missing_group_cols:
        raise ValueError(f"Missing group columns: {missing_group_cols}")

    summaries: list[dict[str, object]] = []
    groups = _iter_groups(backtest_rows, group_cols)
    for group_values, part in groups:
        base_metrics = _relative_gain_metrics(part)
        for rule in RULES:
            row: dict[str, object] = {}
            for column, value in zip(group_cols, group_values):
                row[column] = value
            row["rule"] = rule
            row.update(_rule_summary(part, rule, config))
            row.update(base_metrics)
            summaries.append(row)
    return pd.DataFrame(summaries)


def run_backtest(
    rows: pd.DataFrame,
    *,
    prepared: bool = False,
    group_cols: Iterable[str] | None = None,
    config: BacktestConfig | None = None,
) -> BacktestRun:
    """Prepare rows if needed, apply A-D rules, and return summary metrics."""

    config = config or BacktestConfig()
    checkpoint_rows = rows.copy() if prepared else prepare_checkpoint_rows(rows, config)
    scored = apply_backtest_rules(checkpoint_rows, config)
    summary = summarize_backtest(scored, group_cols=group_cols, config=config)
    return BacktestRun(scored, summary)


def has_positive_metox_label(value: object, labels: Iterable[str] = POSITIVE_METOX_LABELS) -> bool:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    if isinstance(value, (list, tuple, set)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value)
    if not text.strip():
        return False
    parts = set(filter(None, re.split(r"[\s,，;；、|/]+", text)))
    return any(label in parts or label in text for label in labels)


def _attach_dimensions(
    row: dict[str, object],
    visible: pd.DataFrame,
    full_part: pd.DataFrame,
    config: BacktestConfig,
) -> None:
    for column in config.group_cols:
        row[column] = _last_present(visible, column, default=_last_present(full_part, column, default=""))
    for column in (
        C.baseline_cart_rate,
        C.baseline_pay_cvr,
        C.baseline_uv_value,
        C.baseline_cumulative_cart_rate,
        C.baseline_cumulative_pay_cvr,
        C.baseline_cumulative_uv_value,
        C.baseline_main_ctr,
        C.baseline_creative_ctr,
        C.historical_truncated,
        C.ctr_positive_signal,
    ):
        row[column] = _last_present(visible, column, default=np.nan)
    row["metox_labels_at_checkpoint"] = _last_present(visible, C.metox_labels, default="")


def _attach_window(
    row: dict[str, object],
    prefix: str,
    window: pd.DataFrame,
    bounds: tuple[int, int],
) -> None:
    row[f"{prefix}_start_day"], row[f"{prefix}_end_day"] = bounds
    expected_days = bounds[1] - bounds[0] + 1
    row[f"{prefix}_observed_days"] = _observed_day_count(window)
    row[f"{prefix}_valid_days"] = _valid_day_count(window)
    row[f"{prefix}_missing_days"] = max(0, expected_days - row[f"{prefix}_valid_days"])
    row[f"{prefix}_visitors"] = _sum(window, C.visitors)
    row[f"{prefix}_carts"] = _sum(window, C.carts)
    row[f"{prefix}_buyers"] = _sum(window, C.buyers)
    row[f"{prefix}_units"] = _sum(window, C.units)
    row[f"{prefix}_gmv"] = _sum(window, C.gmv)
    row[f"{prefix}_profit"] = _sum(window, C.profit)
    row[f"{prefix}_cart_rate"] = _safe_ratio(row[f"{prefix}_carts"], row[f"{prefix}_visitors"])
    row[f"{prefix}_pay_cvr"] = _safe_ratio(row[f"{prefix}_buyers"], row[f"{prefix}_visitors"])
    row[f"{prefix}_uv_value"] = _safe_ratio(row[f"{prefix}_gmv"], row[f"{prefix}_visitors"])

    row[f"{prefix}_creative_impressions"] = _sum(window, C.creative_impressions)
    row[f"{prefix}_creative_clicks"] = _sum(window, C.creative_clicks)
    row[f"{prefix}_creative_ctr"] = _safe_ratio(
        row[f"{prefix}_creative_clicks"],
        row[f"{prefix}_creative_impressions"],
    )
    row[f"{prefix}_main_ctr"] = _last_present(window, C.main_ctr, default=np.nan)
    row[f"{prefix}_manual_removed"] = _any_bool(window, C.manual_removed)
    row[f"{prefix}_data_missing"] = _any_bool(window, C.data_missing)
    row[f"{prefix}_stockout_days"] = _stockout_days(window)


def _attach_checkpoint_state(row: dict[str, object], visible: pd.DataFrame) -> None:
    row["is_online_at_checkpoint"] = _last_bool(visible, C.is_online, default=True)
    row["stock_ok_at_checkpoint"] = _last_bool(visible, C.stock_ok, default=True)
    row["data_missing_at_checkpoint"] = _last_bool(visible, C.data_missing, default=False)


def _attach_peer_baselines(frame: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    if frame.empty:
        return frame

    result = frame.copy()
    group_cols = [column for column in config.group_cols if column in result.columns]
    baseline_cols = [
        C.baseline_cart_rate,
        C.baseline_pay_cvr,
        C.baseline_uv_value,
        C.baseline_main_ctr,
        C.baseline_creative_ctr,
        C.peer_count,
    ]
    for column in baseline_cols:
        if column not in result.columns:
            result[column] = np.nan

    for checkpoint, part in result.groupby(C.checkpoint, sort=False):
        part_index = part.index
        eligible = _peer_baseline_eligible(part, config)
        if not bool(eligible.any()):
            continue

        if group_cols:
            grouped = part.loc[eligible].groupby(group_cols, dropna=False, sort=False)
        else:
            grouped = [("_ALL", part.loc[eligible])]

        metrics = []
        for key, peer in grouped:
            if not isinstance(key, tuple):
                key = (key,)
            visitors = _number(peer, "current_visitors").fillna(0).sum()
            carts = _number(peer, "current_carts").fillna(0).sum()
            buyers = _number(peer, "current_buyers").fillna(0).sum()
            gmv = _number(peer, "current_gmv").fillna(0).sum()
            creative_clicks = _number(peer, "current_creative_clicks").fillna(0).sum()
            creative_impressions = _number(peer, "current_creative_impressions").fillna(0).sum()
            main_ctr = _number(peer, "current_main_ctr").dropna()
            peer_count = int(peer[C.style_key].nunique()) if C.style_key in peer.columns else int(len(peer))
            row = {column: value for column, value in zip(group_cols, key)}
            row[C.checkpoint] = checkpoint
            row[C.peer_count] = peer_count
            row[C.baseline_cart_rate] = carts / visitors if visitors > 0 else np.nan
            row[C.baseline_pay_cvr] = buyers / visitors if visitors > 0 else np.nan
            row[C.baseline_uv_value] = gmv / visitors if visitors > 0 else np.nan
            row[C.baseline_creative_ctr] = (
                creative_clicks / creative_impressions if creative_impressions > 0 else np.nan
            )
            row[C.baseline_main_ctr] = float(main_ctr.mean()) if not main_ctr.empty else np.nan
            metrics.append(row)
        if not metrics:
            continue

        metric_frame = pd.DataFrame(metrics)
        if group_cols:
            aligned = part.loc[part_index, group_cols + [C.checkpoint]].merge(
                metric_frame,
                on=group_cols + [C.checkpoint],
                how="left",
            )
            result.loc[part_index, baseline_cols] = aligned[baseline_cols].to_numpy()
        else:
            row = metric_frame.iloc[0]
            for column in baseline_cols:
                result.loc[part_index, column] = row[column]

    return result


def _peer_baseline_eligible(frame: pd.DataFrame, config: BacktestConfig) -> pd.Series:
    return (
        _number(frame, "current_valid_days").ge(config.decision_min_valid_days)
        & _number(frame, "current_visitors").gt(0)
        & _bool(frame, "data_missing_at_checkpoint", default=False).eq(False)
        & _bool(frame, C.historical_truncated, default=False).eq(False)
    )


def _rule_a_down_mask(frame: pd.DataFrame, config: BacktestConfig) -> pd.Series:
    active_checkpoint = _rule_a_active_checkpoint(frame)
    decision_visitors = _rule_a_cumulative_metric(frame, "visitors")
    action_valid_days = _rule_a_current_metric(frame, "valid_days")
    action_cart_rate = _rule_a_current_metric(frame, "cart_rate")
    baseline_cart_rate = _rule_a_cart_baseline(frame)

    sample_ok = (
        decision_visitors.ge(config.decision_min_visitors)
        & action_valid_days.ge(config.decision_min_valid_days)
    )
    cart_rate_low = (
        action_cart_rate.notna()
        & baseline_cart_rate.gt(0)
        & action_cart_rate.lt(baseline_cart_rate * config.low_multiplier)
    )
    no_cart_improvement = _rule_a_not_improved(frame)
    return (
        active_checkpoint
        & sample_ok
        & _rule_a_context_ok(frame)
        & _rule_a_peer_count_ok(frame, config)
        & _rule_a_no_paid_sale(frame)
        & cart_rate_low
        & no_cart_improvement
    )


def _rule_a_reasons(frame: pd.DataFrame, down_mask: pd.Series, config: BacktestConfig) -> pd.Series:
    status = _rule_a_statuses(frame, down_mask, config)
    reasons = pd.Series("继续观察", index=frame.index, dtype=object)
    reasons.loc[status.eq("legacy_checkpoint")] = "仅支持D15/D30回放"
    reasons.loc[status.eq("context_abnormal")] = "库存/状态/数据异常"
    reasons.loc[status.eq("paid_sale_protected")] = "D1至当前判定节点累计已有成交，保护观察"
    reasons.loc[status.eq("valid_days_insufficient")] = "有效数据天数不足"
    reasons.loc[status.eq("traffic_insufficient")] = "访客样本不足"
    reasons.loc[status.eq("baseline_insufficient")] = "同层同期加购率基准不足"
    reasons.loc[status.eq("cart_rate_improving")] = "加购率较前窗改善"
    reasons.loc[status.eq("cart_rate_not_low")] = "加购率未低于同层同期基准"
    reasons.loc[down_mask] = "无成交且加购率低于同层同期基准，且未较前窗改善"
    reasons.loc[status.eq("legacy_checkpoint")] = "仅支持D15/D30回放"
    reasons.loc[status.eq("context_abnormal")] = "历史截断或数据异常"
    reasons.loc[status.eq("paid_sale_protected")] = "D1至当前判定节点累计已有成交，保护观察"
    reasons.loc[status.eq("valid_days_insufficient")] = "当前15天周期有效数据天数不足"
    reasons.loc[status.eq("traffic_insufficient")] = "累计访客未达300"
    reasons.loc[status.eq("baseline_insufficient")] = "同层样本不足或加购率基准不可用"
    reasons.loc[status.eq("cart_rate_improving")] = "D30当前周期加购率高于第一周期"
    reasons.loc[status.eq("cart_rate_not_low")] = "当前周期加购率未低于同层同期基准"
    reasons.loc[down_mask] = "无成交且累计流量达标，当前周期加购率弱于同层基准"
    return reasons


def _rule_a_statuses(frame: pd.DataFrame, down_mask: pd.Series, config: BacktestConfig) -> pd.Series:
    status = pd.Series("observe", index=frame.index, dtype=object)
    active_checkpoint = _rule_a_active_checkpoint(frame)
    decision_visitors = _rule_a_cumulative_metric(frame, "visitors")
    action_valid_days = _rule_a_current_metric(frame, "valid_days")
    action_cart_rate = _rule_a_current_metric(frame, "cart_rate")
    baseline_cart_rate = _rule_a_cart_baseline(frame)
    baseline_bad = (
        baseline_cart_rate.isna()
        | baseline_cart_rate.le(0)
        | ~_rule_a_peer_count_ok(frame, config)
    )
    cart_rate_low = (
        action_cart_rate.notna()
        & baseline_cart_rate.gt(0)
        & action_cart_rate.lt(baseline_cart_rate * config.low_multiplier)
    )
    no_cart_improvement = _rule_a_not_improved(frame)

    status.loc[~active_checkpoint] = "legacy_checkpoint"
    status.loc[active_checkpoint & ~_rule_a_context_ok(frame)] = "context_abnormal"
    status.loc[active_checkpoint & _rule_a_has_paid_sale(frame)] = "paid_sale_protected"
    undecided = status.eq("observe") & active_checkpoint
    status.loc[
        undecided & action_valid_days.fillna(0).lt(config.decision_min_valid_days)
    ] = "valid_days_insufficient"
    undecided = status.eq("observe") & active_checkpoint
    status.loc[
        undecided & decision_visitors.fillna(0).lt(config.decision_min_visitors)
    ] = "traffic_insufficient"
    undecided = status.eq("observe") & active_checkpoint
    status.loc[undecided & baseline_bad] = "baseline_insufficient"
    undecided = status.eq("observe") & active_checkpoint
    status.loc[undecided & cart_rate_low & ~no_cart_improvement] = "cart_rate_improving"
    undecided = status.eq("observe") & active_checkpoint
    status.loc[undecided & ~cart_rate_low] = "cart_rate_not_low"
    status.loc[down_mask] = "down_eligible"
    return status


def _rule_a_active_checkpoint(frame: pd.DataFrame) -> pd.Series:
    return _number(frame, C.checkpoint).isin([15, 30])


def _rule_a_context_ok(frame: pd.DataFrame) -> pd.Series:
    return (
        _bool(frame, "data_missing_at_checkpoint", default=False).eq(False)
        & _bool(frame, C.historical_truncated, default=False).eq(False)
    )


def _rule_a_current_metric(frame: pd.DataFrame, suffix: str) -> pd.Series:
    return _number(frame, f"current_{suffix}").where(_rule_a_active_checkpoint(frame), np.nan)


def _rule_a_cumulative_metric(frame: pd.DataFrame, suffix: str) -> pd.Series:
    return _number(frame, f"cumulative_{suffix}").where(_rule_a_active_checkpoint(frame), np.nan)


def _rule_a_cart_baseline(frame: pd.DataFrame) -> pd.Series:
    return _number(frame, C.baseline_cart_rate).where(_rule_a_active_checkpoint(frame), np.nan)


def _rule_a_has_paid_sale(frame: pd.DataFrame) -> pd.Series:
    buyers = _rule_a_cumulative_metric(frame, "buyers").fillna(0)
    units = _rule_a_cumulative_metric(frame, "units").fillna(0)
    gmv = _rule_a_cumulative_metric(frame, "gmv").fillna(0)
    return buyers.gt(0) | units.gt(0) | gmv.gt(0)


def _rule_a_no_paid_sale(frame: pd.DataFrame) -> pd.Series:
    return ~_rule_a_has_paid_sale(frame)


def _rule_a_peer_count_ok(frame: pd.DataFrame, config: BacktestConfig) -> pd.Series:
    if C.peer_count not in frame.columns:
        return pd.Series(True, index=frame.index, dtype=bool)
    return _number(frame, C.peer_count).fillna(0).ge(config.min_peer_count)


def _rule_a_not_improved(frame: pd.DataFrame) -> pd.Series:
    checkpoint = _number(frame, C.checkpoint)
    d15 = checkpoint.eq(15)
    d30 = checkpoint.eq(30) & _not_improved(frame, "current_cart_rate", "previous_cart_rate")
    return d15 | d30


def _ctr_positive(frame: pd.DataFrame, config: BacktestConfig) -> pd.Series:
    explicit = _bool(frame, C.ctr_positive_signal, default=False)
    creative_positive = (
        _number(frame, "current_creative_impressions").ge(config.creative_ctr_min_impressions)
        & _number(frame, "baseline_creative_ctr").gt(0)
        & _number(frame, "current_creative_ctr").ge(
            _number(frame, "baseline_creative_ctr") * config.ctr_positive_multiplier
        )
    )
    main_positive = (
        _number(frame, "baseline_main_ctr").gt(0)
        & _number(frame, "current_main_ctr").ge(
            _number(frame, "baseline_main_ctr") * config.ctr_positive_multiplier
        )
    )
    return explicit | creative_positive | main_positive


def _low_vs_baseline(
    frame: pd.DataFrame,
    value_col: str,
    baseline_col: str,
    config: BacktestConfig,
) -> pd.Series:
    value = _number(frame, value_col)
    baseline = _number(frame, baseline_col)
    return value.notna() & baseline.gt(0) & value.lt(baseline * config.low_multiplier)


def _not_improved(frame: pd.DataFrame, current_col: str, previous_col: str) -> pd.Series:
    current = _number(frame, current_col)
    previous = _number(frame, previous_col)
    return current.notna() & previous.notna() & current.le(previous)


def _rule_summary(part: pd.DataFrame, rule: str, config: BacktestConfig) -> dict[str, object]:
    decision_col = f"rule_{rule}_decision"
    predicted_down = part[decision_col].eq(DECISION_DOWN)
    verifiable = part["future_label"].ne(FUTURE_UNVERIFIABLE)
    future_failure = part["future_label"].eq(FUTURE_FAILURE)
    future_recovered = part["future_label"].eq(FUTURE_RECOVERED)
    down_verifiable = predicted_down & verifiable

    return {
        "total": int(len(part)),
        "predicted_down": int(predicted_down.sum()),
        "predicted_observe": int((~predicted_down).sum()),
        "verifiable": int(verifiable.sum()),
        "future_failure": int(future_failure.sum()),
        "future_recovered": int(future_recovered.sum()),
        "unverifiable": int((~verifiable).sum()),
        "down_hit_rate": _divide((predicted_down & future_failure).sum(), down_verifiable.sum()),
        "false_kill_rate": _divide((predicted_down & future_recovered).sum(), down_verifiable.sum()),
        "recall": _divide((predicted_down & future_failure).sum(), future_failure.sum()),
        "down_coverage_rate": _divide(predicted_down.sum(), len(part)),
        "opportunity_loss_gmv": float(_number(part.loc[predicted_down & future_recovered], "validation_gmv").sum()),
        "opportunity_loss_profit": float(
            _number(part.loc[predicted_down & future_recovered], f"validation_{config.opportunity_profit_col}").sum()
        ),
    }


def _relative_gain_metrics(part: pd.DataFrame) -> dict[str, object]:
    def false_kill(rule: str) -> float:
        decision_col = f"rule_{rule}_decision"
        predicted_down = part[decision_col].eq(DECISION_DOWN)
        verifiable = part["future_label"].ne(FUTURE_UNVERIFIABLE)
        future_recovered = part["future_label"].eq(FUTURE_RECOVERED)
        return _divide((predicted_down & future_recovered).sum(), (predicted_down & verifiable).sum())

    def precision(rule: str) -> float:
        decision_col = f"rule_{rule}_decision"
        predicted_down = part[decision_col].eq(DECISION_DOWN)
        verifiable = part["future_label"].ne(FUTURE_UNVERIFIABLE)
        failure = part["future_label"].eq(FUTURE_FAILURE)
        return _divide((predicted_down & failure).sum(), (predicted_down & verifiable).sum())

    metox_protected = part.get("metox_protected", pd.Series(False, index=part.index)).astype(bool)
    verifiable_protected = metox_protected & part["future_label"].ne(FUTURE_UNVERIFIABLE)
    recovered_protected = metox_protected & part["future_label"].eq(FUTURE_RECOVERED)
    return {
        "ctr_gain_false_kill_rate": _nan_subtract(false_kill(RULE_A), false_kill(RULE_B)),
        "ctr_gain_down_hit_rate": _nan_subtract(precision(RULE_B), precision(RULE_A)),
        "metox_protection_effectiveness": _divide(recovered_protected.sum(), verifiable_protected.sum()),
        "metox_protected_count": int(metox_protected.sum()),
    }


def _iter_groups(frame: pd.DataFrame, group_cols: tuple[str, ...]):
    if not group_cols:
        yield (), frame
        return
    grouped = frame.groupby(list(group_cols), dropna=False, sort=False)
    for key, part in grouped:
        if not isinstance(key, tuple):
            key = (key,)
        yield key, part


def _slice_window(frame: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    return frame[(frame[C.age_day] >= start) & (frame[C.age_day] <= end)]


def _sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _observed_day_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(pd.to_numeric(frame[C.age_day], errors="coerce").dropna().nunique())


def _valid_day_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if C.valid_data_day not in frame.columns:
        return _observed_day_count(frame)
    valid = _bool(frame, C.valid_data_day, default=True)
    days = frame.loc[valid, C.age_day]
    return int(pd.to_numeric(days, errors="coerce").dropna().nunique())


def _stockout_days(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if C.stock_ok not in frame.columns:
        return 0
    stock_ok = _bool(frame, C.stock_ok, default=True)
    days = frame.loc[~stock_ok, C.age_day]
    return int(pd.to_numeric(days, errors="coerce").dropna().nunique())


def _any_bool(frame: pd.DataFrame, column: str) -> bool:
    if frame.empty or column not in frame.columns:
        return False
    return bool(_bool(frame, column, default=False).any())


def _last_present(frame: pd.DataFrame, column: str, default: object = np.nan) -> object:
    if frame.empty or column not in frame.columns:
        return default
    values = frame[column].dropna()
    if values.empty:
        return default
    return values.iloc[-1]


def _last_bool(frame: pd.DataFrame, column: str, default: bool) -> bool:
    value = _last_present(frame, column, default=default)
    return bool(_coerce_bool(value, default=default))


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _bool(frame: pd.DataFrame, column: str, default: bool) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    return frame[column].map(lambda value: _coerce_bool(value, default=default)).astype(bool)


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "当前在线", "online"}:
        return True
    if text in {"0", "false", "no", "n", "否", "已下架", "offline"}:
        return False
    return default


def _safe_ratio(numerator: object, denominator: object) -> float:
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return np.nan
    if denominator <= 0:
        return np.nan
    return numerator / denominator


def _divide(numerator: object, denominator: object) -> float:
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return np.nan
    if denominator <= 0:
        return np.nan
    return numerator / denominator


def _nan_subtract(left: float, right: float) -> float:
    if pd.isna(left) or pd.isna(right):
        return np.nan
    return float(left - right)


__all__ = [
    "BacktestColumns",
    "BacktestConfig",
    "BacktestRun",
    "DECISION_DOWN",
    "DECISION_OBSERVE",
    "FUTURE_FAILURE",
    "FUTURE_RECOVERED",
    "FUTURE_UNVERIFIABLE",
    "POSITIVE_METOX_LABELS",
    "RULES",
    "RULE_A",
    "RULE_B",
    "RULE_C",
    "RULE_D",
    "apply_backtest_rules",
    "checkpoint_windows",
    "classify_future",
    "has_positive_metox_label",
    "prepare_checkpoint_rows",
    "run_backtest",
    "summarize_backtest",
]
