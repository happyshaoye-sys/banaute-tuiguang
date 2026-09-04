"""新品生命周期 15 天周期聚合与规则A判定。

本模块只接受标准化 DataFrame，不读取文件、不依赖 UI，也不修改 MetoX 算法。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .new_product_first_seen import (
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    IS_HISTORY_TRUNCATED,
    MATCH_CONFIDENCE,
    STYLE_KEY,
    build_style_key_series,
    normalize_identifier,
)


RESULT_DOWN = "下架"
RESULT_OBSERVE = "继续观察"

CHECKPOINT_D15 = "D15"
CHECKPOINT_D30 = "D30"
RULE_VERSION_A = "A-商品经营核心规则"

REASON_BEFORE_D15 = "未到第一轮判定日"
REASON_BEFORE_D30 = "未到第二轮判定日"
REASON_HISTORY_TRUNCATED = "历史截断"
REASON_DATA_INCOMPLETE = "有效数据天数不足"
REASON_TRAFFIC_INSUFFICIENT = "流量验证不足"
REASON_CURRENT_TRAFFIC_INSUFFICIENT = "D1至分析日累计访客不足300"
REASON_PEER_INSUFFICIENT = "同层样本不足"
REASON_IMPROVING = "指标正在改善"
REASON_PERFORMANCE_NORMAL = "表现未达下架条件"
REASON_D15_WEAK = "D15加购弱且证据充分"
REASON_D30_WEAK = "D30加购弱且证据充分"
REASON_D15_SALE_PROTECTED = "D15累计已有成交，动销保护"
REASON_D30_SALE_PROTECTED = "D30累计已有成交，动销保护"
REASON_CURRENT_SALE_PROTECTED = "D1至分析日累计已有成交，动销保护"
REASON_D45_TRAFFIC_EXPIRED = "D45累计访客未达300且无成交，周期到期下架"

PRODUCT_ID = "宝贝ID"
SKU = "货号"
PRODUCT_STATUS = "商品状态"
ONLINE_STATUS = "当前在线"
DATA_DATE = "_数据日期"
STAT_DATE = "统计日期"

SUM_COLUMNS = ["商品访客数", "商品加购件数", "支付买家数", "支付件数", "GMV"]
DIMENSION_COLUMNS = ["店铺", "淘宝类目", "价格段", "货号", "商品名称", "款式主图"]
WINDOW_SPECS = {
    "D1_D15": (1, 15),
    "D16_D30": (16, 30),
}
CUMULATIVE_SPECS = {
    "D1_D30": ("D1_D15", "D16_D30"),
}
CART_COUNT_COLUMN = "商品加购件数"
CART_RATE_COLUMN = "商品加购率"
CURRENT_CUMULATIVE_PREFIX = "__D1_AS_OF"
CURRENT_CUMULATIVE_ATTR = "new_product_current_cumulative_metrics"


@dataclass(frozen=True)
class LifecycleDecisionConfig:
    """规则A集中阈值。"""

    decision_visitor_min: int = 300
    period_min_valid_days: int = 12
    min_peer_count: int = 20
    low_ratio: float = 0.8


def _to_num(series, index=None) -> pd.Series:
    if series is None:
        return pd.Series(0.0, index=index, dtype=float)
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _parse_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    return parsed.normalize()


def _date_series(frame: pd.DataFrame) -> pd.Series:
    if DATA_DATE in frame.columns:
        return frame[DATA_DATE].map(_parse_date)
    if STAT_DATE in frame.columns:
        return frame[STAT_DATE].map(_parse_date)
    return pd.Series(pd.NaT, index=frame.index)


def _first_non_empty(values) -> str:
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return text
    return ""


def _last_non_empty(values) -> str:
    for value in reversed(list(values)):
        text = _first_non_empty([value])
        if text:
            return text
    return ""


def _price_band(value) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        price = 0.0
    if price < 100:
        return "0-100"
    if price < 200:
        return "100-200"
    if price < 500:
        return "200-500"
    return "500+"


def lifecycle_window(age_days) -> str:
    """按新品年龄返回 15 天生命周期周期，正式判定周期外返回空字符串。"""
    try:
        age = int(age_days)
    except (TypeError, ValueError):
        return ""
    for label, (start, end) in WINDOW_SPECS.items():
        if start <= age <= end:
            return label
    return ""


def _prepare_facts(frame: pd.DataFrame, first_seen_index: pd.DataFrame) -> pd.DataFrame:
    if frame is None or len(frame) == 0 or first_seen_index is None or len(first_seen_index) == 0:
        return pd.DataFrame()
    work = frame.copy()
    keys = build_style_key_series(work)
    work[STYLE_KEY] = keys[STYLE_KEY]
    if PRODUCT_ID in work.columns:
        pid = work[PRODUCT_ID].map(normalize_identifier)
        pid_map = first_seen_index.set_index(FIRST_SEEN_PRODUCT_ID)[STYLE_KEY].to_dict()
        mapped = pid.map(pid_map).fillna("")
        work[STYLE_KEY] = work[STYLE_KEY].where(mapped == "", mapped)
    index_cols = [
        STYLE_KEY,
        FIRST_SEEN_DATE,
        IS_HISTORY_TRUNCATED,
        MATCH_CONFIDENCE,
    ]
    keep = [col for col in index_cols if col in first_seen_index.columns]
    work = work.merge(first_seen_index[keep].drop_duplicates(STYLE_KEY), on=STYLE_KEY, how="inner")
    if work.empty:
        return work

    work["_data_date"] = _date_series(work)
    work["_first_seen_date"] = work[FIRST_SEEN_DATE].map(_parse_date)
    work = work[work["_data_date"].notna() & work["_first_seen_date"].notna()].copy()
    work["新品年龄"] = (work["_data_date"] - work["_first_seen_date"]).dt.days + 1
    work = work[(work["新品年龄"] >= 1) & (work["新品年龄"] <= 45)].copy()
    work["生命周期窗口"] = work["新品年龄"].map(lifecycle_window)
    for column in SUM_COLUMNS + ["在仓库存", "件单价", "成本价（含税）"]:
        if column in work.columns:
            work[column] = _to_num(work[column], work.index)
    for column in SUM_COLUMNS:
        if column not in work.columns:
            work[column] = 0.0
    return work


def aggregate_lifecycle_windows(
    facts: pd.DataFrame,
    first_seen_index: pd.DataFrame,
    as_of_date: str | date | None = None,
) -> pd.DataFrame:
    """按款式聚合两个 15 天周期与关键累计窗口。"""
    work = _prepare_facts(facts, first_seen_index)
    if work.empty:
        return pd.DataFrame()

    as_of = _parse_date(as_of_date) if as_of_date else pd.NaT
    if pd.notna(as_of):
        work = work[work["_data_date"] <= as_of].copy()
        if work.empty:
            return pd.DataFrame()
    rows = []
    current_metrics = {}
    for style_key, part in work.groupby(STYLE_KEY, sort=False):
        part = part.sort_values("_data_date")
        first_seen = part["_first_seen_date"].iloc[0]
        age = (
            int((as_of - first_seen).days + 1)
            if pd.notna(as_of)
            else int(part["新品年龄"].max())
        )
        row = {
            STYLE_KEY: style_key,
            FIRST_SEEN_DATE: first_seen.date().isoformat(),
            "新品年龄": age,
            IS_HISTORY_TRUNCATED: bool(part[IS_HISTORY_TRUNCATED].any())
            if IS_HISTORY_TRUNCATED in part.columns
            else False,
            MATCH_CONFIDENCE: _last_non_empty(part[MATCH_CONFIDENCE])
            if MATCH_CONFIDENCE in part.columns
            else "",
            "关联宝贝ID": ",".join(
                dict.fromkeys(
                    pid
                    for pid in (
                        part[PRODUCT_ID].map(normalize_identifier)
                        if PRODUCT_ID in part.columns
                        else pd.Series([], dtype=object)
                    )
                    if pid
                )
            ),
        }
        for column in DIMENSION_COLUMNS:
            if column in part.columns:
                row[column] = _last_non_empty(part[column])
        if "价格段" not in row or not row.get("价格段"):
            price = 0.0
            if "件单价" in part.columns and float(part["件单价"].max()) > 0:
                price = float(part["件单价"].max())
            else:
                paid_units = part["支付件数"].sum()
                price = float(part["GMV"].sum() / paid_units) if paid_units > 0 else 0.0
            row["价格段"] = _price_band(price)
        latest = part.iloc[-1]
        if PRODUCT_STATUS in part.columns:
            row["当前商品状态"] = _last_non_empty(part[PRODUCT_STATUS])
        if "在仓库存" in part.columns:
            row["当前在仓库存"] = float(latest.get("在仓库存", 0.0))
        _write_checkpoint_state(row, part, CHECKPOINT_D30, max_age=30)

        for label, (start, end) in WINDOW_SPECS.items():
            window_part = part[(part["新品年龄"] >= start) & (part["新品年龄"] <= end)]
            _write_window_metrics(row, label, window_part)
        for label, source_windows in CUMULATIVE_SPECS.items():
            _write_cumulative_metrics(row, label, source_windows)
        current_end_age = max(0, min(int(age), 45))
        current_part = part[
            (part["新品年龄"] >= 1)
            & (part["新品年龄"] <= current_end_age)
        ]
        current_row = {STYLE_KEY: style_key}
        _write_window_metrics(current_row, CURRENT_CUMULATIVE_PREFIX, current_part)
        current_metrics[style_key] = current_row
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs[CURRENT_CUMULATIVE_ATTR] = current_metrics
    return out


def _write_window_metrics(row: dict, prefix: str, part: pd.DataFrame) -> None:
    for column in SUM_COLUMNS:
        row[f"{prefix}_{column}"] = float(part[column].sum()) if not part.empty else 0.0
    row[f"{prefix}_有效数据天数"] = (
        int(part["_data_date"].nunique()) if not part.empty else 0
    )
    _write_cart_rate(row, prefix)


def _write_cumulative_metrics(row: dict, prefix: str, source_windows: tuple[str, ...]) -> None:
    for column in SUM_COLUMNS:
        row[f"{prefix}_{column}"] = sum(
            float(row.get(f"{source}_{column}", 0.0)) for source in source_windows
        )
    row[f"{prefix}_有效数据天数"] = sum(
        int(row.get(f"{source}_有效数据天数", 0)) for source in source_windows
    )
    _write_cart_rate(row, prefix)


def _write_cart_rate(row: dict, prefix: str) -> None:
    visitors = float(row.get(f"{prefix}_商品访客数", 0.0))
    row[f"{prefix}_商品加购率"] = (
        float(row.get(f"{prefix}_商品加购件数", 0.0)) / visitors if visitors > 0 else np.nan
    )


def _write_checkpoint_state(row: dict, part: pd.DataFrame, checkpoint: str, max_age: int) -> None:
    eligible = part[part["新品年龄"] <= max_age].sort_values("_data_date")
    if eligible.empty:
        return
    latest = eligible.iloc[-1]
    if PRODUCT_STATUS in eligible.columns:
        row[f"{checkpoint}商品状态"] = _last_non_empty(eligible[PRODUCT_STATUS])
    if "在仓库存" in eligible.columns:
        row[f"{checkpoint}在仓库存"] = float(latest.get("在仓库存", 0.0))


def peer_group_columns(frame: pd.DataFrame) -> list[str]:
    cols = []
    for column in ("店铺", "淘宝类目", "价格段"):
        if column in frame.columns:
            cols.append(column)
    return cols


def compute_peer_baselines(
    peer_frame: pd.DataFrame,
    checkpoint: str,
    config: LifecycleDecisionConfig | None = None,
) -> pd.DataFrame:
    """按同店铺/同类目/同价格段计算同层基准。"""
    config = config or LifecycleDecisionConfig()
    if peer_frame is None or len(peer_frame) == 0:
        return pd.DataFrame()
    work = peer_frame.copy()
    work = _eligible_peer_frame(work, checkpoint, config)
    if work.empty:
        return pd.DataFrame()
    group_cols = peer_group_columns(work)
    if not group_cols:
        work["_peer_group"] = "ALL"
        group_cols = ["_peer_group"]

    prefix = _decision_spec(checkpoint)["prefix"]
    prefixes = [prefix]
    rows = []
    for key, part in work.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        row = dict(zip(group_cols, key))
        peer_count = int(part[STYLE_KEY].nunique()) if STYLE_KEY in part.columns else int(len(part))
        row["同层样本数"] = peer_count
        for prefix in prefixes:
            visitors = _to_num(part.get(f"{prefix}_商品访客数"), part.index).sum()
            carts = _to_num(part.get(f"{prefix}_商品加购件数"), part.index).sum()
            eligible = peer_count >= config.min_peer_count and visitors > 0
            row[f"{prefix}_商品加购率_B"] = carts / visitors if eligible else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.drop(columns=["_peer_group"], errors="ignore")


def attach_peer_baselines(
    lifecycle_frame: pd.DataFrame,
    peer_frame: pd.DataFrame,
    checkpoint: str,
    config: LifecycleDecisionConfig | None = None,
) -> pd.DataFrame:
    config = config or LifecycleDecisionConfig()
    out = lifecycle_frame.copy()
    baselines = compute_peer_baselines(peer_frame, checkpoint, config)
    if baselines.empty:
        out["同层样本数"] = 0
        return out
    group_cols = peer_group_columns(out)
    baseline_group_cols = peer_group_columns(baselines)
    group_cols = [col for col in group_cols if col in baseline_group_cols]
    if group_cols:
        return out.merge(baselines, on=group_cols, how="left")
    out["_peer_group"] = "ALL"
    baselines["_peer_group"] = "ALL"
    merged = out.merge(baselines, on="_peer_group", how="left")
    return merged.drop(columns=["_peer_group"], errors="ignore")


def _eligible_peer_frame(
    frame: pd.DataFrame,
    checkpoint: str,
    config: LifecycleDecisionConfig,
) -> pd.DataFrame:
    work = frame.copy()
    spec = _decision_spec(checkpoint)
    min_age = spec["min_age"]
    prefix = spec["prefix"]
    age = pd.to_numeric(
        work.get("新品年龄", pd.Series(np.nan, index=work.index)),
        errors="coerce",
    )
    valid = age >= min_age
    if IS_HISTORY_TRUNCATED in work.columns:
        valid &= ~work[IS_HISTORY_TRUNCATED].fillna(False).astype(bool)
    valid &= (
        pd.to_numeric(
            work.get(f"{prefix}_有效数据天数", pd.Series(0, index=work.index)),
            errors="coerce",
        ).fillna(0)
        >= config.period_min_valid_days
    )
    valid &= (
        pd.to_numeric(
            work.get(f"{prefix}_商品访客数", pd.Series(0, index=work.index)),
            errors="coerce",
        ).fillna(0)
        > 0
    )
    return work.loc[valid].copy()


def apply_lifecycle_decisions(
    lifecycle_frame: pd.DataFrame,
    checkpoint: str,
    peer_frame: pd.DataFrame | None = None,
    config: LifecycleDecisionConfig | None = None,
) -> pd.DataFrame:
    """应用规则A，输出仅为“下架/继续观察”。"""
    config = config or LifecycleDecisionConfig()
    if lifecycle_frame is None or len(lifecycle_frame) == 0:
        return pd.DataFrame()
    peer_frame = lifecycle_frame if peer_frame is None else peer_frame
    out = attach_peer_baselines(lifecycle_frame, peer_frame, checkpoint, config)
    rows = []
    for _, row in out.iterrows():
        decision = _decide_row(row, checkpoint, config)
        rows.append(decision)
    decisions = pd.DataFrame(rows, index=out.index)
    return pd.concat([out, decisions], axis=1)


def apply_current_lifecycle_decisions(
    lifecycle_frame: pd.DataFrame,
    peer_frame: pd.DataFrame | None = None,
    config: LifecycleDecisionConfig | None = None,
) -> pd.DataFrame:
    """按分析日输出当前判定：性能沿用D15/D30，流量/成交使用D1至分析日滚动累计。"""
    config = config or LifecycleDecisionConfig()
    if lifecycle_frame is None or len(lifecycle_frame) == 0:
        return pd.DataFrame()
    peer_frame = lifecycle_frame if peer_frame is None else peer_frame
    lifecycle_with_current = _attach_current_metric_columns(lifecycle_frame)
    peer_with_current = _attach_current_metric_columns(peer_frame)
    pieces = []
    checkpoint_series = _current_checkpoint_series(lifecycle_with_current)
    for checkpoint in (CHECKPOINT_D15, CHECKPOINT_D30):
        part = lifecycle_with_current[checkpoint_series.eq(checkpoint)].copy()
        if part.empty:
            continue
        scored = attach_peer_baselines(part, peer_with_current, checkpoint, config)
        decisions = pd.DataFrame(
            [
                _decide_row(
                    row,
                    checkpoint,
                    config,
                    current_prefix=CURRENT_CUMULATIVE_PREFIX,
                )
                for _, row in scored.iterrows()
            ],
            index=scored.index,
        )
        pieces.append(pd.concat([scored, decisions], axis=1))
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, axis=0).sort_index()
    return _drop_current_metric_columns(out)


def _current_checkpoint_series(frame: pd.DataFrame) -> pd.Series:
    age = pd.to_numeric(
        frame.get("新品年龄", pd.Series(0, index=frame.index)),
        errors="coerce",
    ).fillna(0)
    return pd.Series(
        np.where(age >= 30, CHECKPOINT_D30, CHECKPOINT_D15),
        index=frame.index,
        dtype=object,
    )


def _attach_current_metric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    metrics_by_style = getattr(frame, "attrs", {}).get(CURRENT_CUMULATIVE_ATTR, {})
    if metrics_by_style and STYLE_KEY in out.columns:
        current_frame = pd.DataFrame(list(metrics_by_style.values()))
        if STYLE_KEY in current_frame.columns:
            out = out.merge(current_frame, on=STYLE_KEY, how="left")

    for column in _current_metric_columns():
        if column not in out.columns:
            out[column] = np.nan
    for idx, row in out.iterrows():
        if not pd.isna(row.get(f"{CURRENT_CUMULATIVE_PREFIX}_商品访客数")):
            continue
        source_prefix = "D1_D30" if _num(row, "新品年龄") >= 30 else "D1_D15"
        for field in SUM_COLUMNS:
            out.at[idx, f"{CURRENT_CUMULATIVE_PREFIX}_{field}"] = _num(row, f"{source_prefix}_{field}")
        out.at[idx, f"{CURRENT_CUMULATIVE_PREFIX}_有效数据天数"] = _num(
            row, f"{source_prefix}_有效数据天数"
        )
        visitors = out.at[idx, f"{CURRENT_CUMULATIVE_PREFIX}_商品访客数"]
        carts = out.at[idx, f"{CURRENT_CUMULATIVE_PREFIX}_商品加购件数"]
        out.at[idx, f"{CURRENT_CUMULATIVE_PREFIX}_商品加购率"] = (
            carts / visitors if visitors > 0 else np.nan
        )
    return out


def _current_metric_columns() -> list[str]:
    return [
        *[f"{CURRENT_CUMULATIVE_PREFIX}_{field}" for field in SUM_COLUMNS],
        f"{CURRENT_CUMULATIVE_PREFIX}_有效数据天数",
        f"{CURRENT_CUMULATIVE_PREFIX}_商品加购率",
    ]


def _drop_current_metric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    internal_cols = [
        column for column in frame.columns
        if str(column).startswith(f"{CURRENT_CUMULATIVE_PREFIX}_")
    ]
    return frame.drop(columns=internal_cols, errors="ignore")


def _decide_row(
    row: pd.Series,
    checkpoint: str,
    config: LifecycleDecisionConfig,
    *,
    current_prefix: str | None = None,
) -> dict:
    spec = _decision_spec(checkpoint)
    min_age = spec["min_age"]
    prefix = spec["prefix"]
    sale_prefix = current_prefix or spec["sale_prefix"]
    traffic_prefix = current_prefix or spec["traffic_prefix"]
    sales_summary_prefix = current_prefix or prefix
    trend_prefix = spec["trend_prefix"]
    prev_prefix = spec["prev_prefix"]
    weak_reason = spec["weak_reason"]
    before_reason = spec["before_reason"]
    sale_protected_reason = (
        REASON_CURRENT_SALE_PROTECTED
        if current_prefix
        else spec["sale_protected_reason"]
    )
    traffic_insufficient_reason = (
        REASON_CURRENT_TRAFFIC_INSUFFICIENT
        if current_prefix
        else REASON_TRAFFIC_INSUFFICIENT
    )

    reason = None
    result = RESULT_OBSERVE
    sample_status = "充分"
    protection_status = "无保护"
    protection_factor = ""
    protection_evidence = ""
    traffic_visitors = _num(row, f"{traffic_prefix}_商品访客数")
    traffic_validation = (
        "已验证"
        if traffic_visitors >= config.decision_visitor_min
        else "待流量验证"
    )
    sales_summary = _sales_summary(row, sales_summary_prefix)
    protection_sales_summary = _sales_summary(row, sale_prefix)
    if _num(row, "新品年龄") < min_age:
        reason = before_reason
    elif _has_paid_sale(row, sale_prefix):
        reason = sale_protected_reason
        protection_status = "动销保护"
        protection_factor = reason
        protection_evidence = protection_sales_summary
    elif bool(row.get(IS_HISTORY_TRUNCATED, False)):
        reason = REASON_HISTORY_TRUNCATED
    elif current_prefix and _num(row, "新品年龄") >= 45 and traffic_visitors < config.decision_visitor_min:
        result = RESULT_DOWN
        reason = REASON_D45_TRAFFIC_EXPIRED
        sample_status = "不足"
    elif traffic_visitors < config.decision_visitor_min:
        reason = traffic_insufficient_reason
        sample_status = "不足"
    elif not _decision_data_complete(row, checkpoint, config):
        reason = REASON_DATA_INCOMPLETE
        sample_status = "不足"
    elif not _has_cart_peer_baseline(row, prefix, config):
        reason = REASON_PEER_INSUFFICIENT
        sample_status = "同层不足"
    else:
        cart_count_zero = _cart_count_zero(row, prefix)
        is_low = _cart_rate_low(row, prefix, config)
        no_improve = not _cart_rate_improved(row, trend_prefix, prev_prefix)
        if cart_count_zero or (is_low and no_improve):
            result = RESULT_DOWN
            reason = weak_reason
        elif is_low and not no_improve:
            reason = REASON_IMPROVING
        else:
            reason = REASON_PERFORMANCE_NORMAL

    return {
        "判定轮次": checkpoint,
        "判定结果": result,
        "判定原因": reason,
        "样本充分性": sample_status,
        "保护状态": protection_status,
        "流量验证": traffic_validation,
        "本轮成交": sales_summary,
        "保护证据": protection_evidence,
        "下架阻断原因": "" if result == RESULT_DOWN else (reason or ""),
        "动作可执行": _action_executable_tag(row),
        "库存提示": _stock_hint(row),
        "保护因素": protection_factor,
        "下架规则版本": RULE_VERSION_A,
    }


def _decision_spec(checkpoint: str) -> dict:
    if checkpoint == CHECKPOINT_D15:
        return {
            "min_age": 15,
            "prefix": "D1_D15",
            "sale_prefix": "D1_D15",
            "traffic_prefix": "D1_D15",
            "trend_prefix": "D1_D15",
            "prev_prefix": "",
            "weak_reason": REASON_D15_WEAK,
            "before_reason": REASON_BEFORE_D15,
            "sale_protected_reason": REASON_D15_SALE_PROTECTED,
        }
    if checkpoint == CHECKPOINT_D30:
        return {
            "min_age": 30,
            "prefix": "D16_D30",
            "sale_prefix": "D1_D30",
            "traffic_prefix": "D1_D30",
            "trend_prefix": "D16_D30",
            "prev_prefix": "D1_D15",
            "weak_reason": REASON_D30_WEAK,
            "before_reason": REASON_BEFORE_D30,
            "sale_protected_reason": REASON_D30_SALE_PROTECTED,
        }
    raise ValueError(f"Unsupported lifecycle checkpoint: {checkpoint}")


def _decision_data_complete(
    row: pd.Series,
    checkpoint: str,
    config: LifecycleDecisionConfig,
) -> bool:
    checks = [(_decision_spec(checkpoint)["prefix"], config.period_min_valid_days)]
    return all(_num(row, f"{prefix}_有效数据天数") >= min_days for prefix, min_days in checks)


def _num(row: pd.Series, field: str) -> float:
    value = pd.to_numeric(row.get(field, 0.0), errors="coerce")
    return 0.0 if pd.isna(value) else float(value)


def _has_paid_sale(row: pd.Series, prefix: str) -> bool:
    return any(
        _num(row, f"{prefix}_{metric}") > 0
        for metric in ("支付买家数", "支付件数", "GMV")
    )


def _format_amount(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _sales_summary(row: pd.Series, prefix: str) -> str:
    buyers = _num(row, f"{prefix}_支付买家数")
    units = _num(row, f"{prefix}_支付件数")
    gmv = _num(row, f"{prefix}_GMV")
    return f"支付买家{_format_amount(buyers)}人，支付件数{_format_amount(units)}件，GMV{gmv:.2f}"


def _action_executable_tag(row: pd.Series) -> str:
    status = str(row.get("当前商品状态", "") or "").strip()
    if not status:
        return "链接状态未知"
    if status == ONLINE_STATUS:
        return "当前有在线链接"
    return f"当前无在线链接（{status}）"


def _stock_hint(row: pd.Series) -> str:
    if "当前在仓库存" not in row.index:
        return "库存未知"
    stock = pd.to_numeric(row.get("当前在仓库存"), errors="coerce")
    if pd.isna(stock):
        return "库存未知"
    formatted = _format_amount(float(stock))
    if stock > 0:
        return f"有库存（{formatted}）"
    return f"无库存（{formatted}）"


def _has_cart_peer_baseline(row: pd.Series, prefix: str, config: LifecycleDecisionConfig) -> bool:
    peer_count = pd.to_numeric(row.get("同层样本数", 0), errors="coerce")
    if pd.isna(peer_count) or peer_count < config.min_peer_count:
        return False
    baseline = pd.to_numeric(row.get(f"{prefix}_{CART_RATE_COLUMN}_B"), errors="coerce")
    return not (pd.isna(baseline) or baseline <= 0)


def _cart_count_zero(row: pd.Series, prefix: str) -> bool:
    return _num(row, f"{prefix}_{CART_COUNT_COLUMN}") <= 0


def _cart_rate_low(row: pd.Series, prefix: str, config: LifecycleDecisionConfig) -> bool:
    value = pd.to_numeric(row.get(f"{prefix}_{CART_RATE_COLUMN}"), errors="coerce")
    baseline = pd.to_numeric(row.get(f"{prefix}_{CART_RATE_COLUMN}_B"), errors="coerce")
    if pd.isna(value) or pd.isna(baseline) or baseline <= 0:
        return False
    return bool(value < baseline * config.low_ratio)


def _cart_rate_improved(row: pd.Series, prefix: str, prev_prefix: str) -> bool:
    if not prev_prefix:
        return False
    current = pd.to_numeric(row.get(f"{prefix}_{CART_RATE_COLUMN}"), errors="coerce")
    previous = pd.to_numeric(row.get(f"{prev_prefix}_{CART_RATE_COLUMN}"), errors="coerce")
    if pd.isna(current):
        current = 0.0
    if pd.isna(previous):
        previous = 0.0
    if previous <= 0 and current > 0:
        return True
    return bool(previous > 0 and current > previous)


__all__ = [
    "CHECKPOINT_D15",
    "CHECKPOINT_D30",
    "LifecycleDecisionConfig",
    "REASON_BEFORE_D15",
    "REASON_BEFORE_D30",
    "REASON_CURRENT_SALE_PROTECTED",
    "REASON_CURRENT_TRAFFIC_INSUFFICIENT",
    "REASON_D15_WEAK",
    "REASON_D15_SALE_PROTECTED",
    "REASON_D30_WEAK",
    "REASON_D30_SALE_PROTECTED",
    "REASON_D45_TRAFFIC_EXPIRED",
    "REASON_DATA_INCOMPLETE",
    "REASON_HISTORY_TRUNCATED",
    "REASON_IMPROVING",
    "REASON_PEER_INSUFFICIENT",
    "REASON_PERFORMANCE_NORMAL",
    "REASON_TRAFFIC_INSUFFICIENT",
    "RESULT_DOWN",
    "RESULT_OBSERVE",
    "RULE_VERSION_A",
    "aggregate_lifecycle_windows",
    "apply_current_lifecycle_decisions",
    "apply_lifecycle_decisions",
    "compute_peer_baselines",
    "lifecycle_window",
]
