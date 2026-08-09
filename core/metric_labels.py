"""执行表核心指标的同层基准与状态标签。"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd


HISTORY_DAYS = 28
RATE_SAMPLE_MIN_VISITORS = 100

STATUS_HIGH = "高"
STATUS_NORMAL = "正常"
STATUS_LOW = "低"
STATUS_INSUFFICIENT = "样本不足"

METRIC_STATUS_RULE_SHEET = "指标状态规则"
METRIC_STATUS_RULE_COLUMNS = (
    "状态字段",
    "指标口径",
    "基准或参考线",
    "判定标准",
    "特殊情况",
    "运营解释",
)
METRIC_STATUS_RULES = (
    {
        "状态字段": "链接投产状态",
        "指标口径": (
            "链接投产=GMV÷付费；退款后GSV=GMV×(1-退款率)；"
            "退款后销售成本=退款后GSV÷件单价×成本价（含税）；"
            "可投放毛利额=退款后GSV-退款后销售成本。"
        ),
        "基准或参考线": (
            "盈亏平衡线=GMV÷可投放毛利额；同组P75=过去28日当前所选店铺内，"
            "同淘宝类目且同价格段（0-100/100-200/200-500/500+）商品的链接投产P75；"
            "同组无数据时不跨类目或价格段回退。"
        ),
        "判定标准": (
            "链接投产≥max(盈亏平衡线×1.2，同组P75)：高；"
            "盈亏平衡线≤链接投产<高线：正常；链接投产<盈亏平衡线：低。"
        ),
        "特殊情况": (
            "无付费，或缺GMV、退款率、件单价、成本价：样本不足；"
            "退款率不在0%~100%：样本不足；可投放毛利额≤0：低；"
            "同组P75缺失时高线只取盈亏平衡线×1.2；不使用链接实际投产过滤。"
        ),
        "运营解释": "高=退款后仍明显高于不亏线且达到同组优秀水平；正常=退款后达到不亏线；低=退款后尚未盈亏平衡。",
    },
    {
        "状态字段": "商品支付转化率状态",
        "指标口径": "商品支付转化率=支付买家数÷商品访客数。",
        "基准或参考线": (
            "B=过去28日同店铺、同淘宝类目、同价格段累计支付买家数÷累计商品访客数；"
            "同组无数据时不跨类目或价格段回退。"
        ),
        "判定标准": "指标≥1.2B：高；0.8B≤指标<1.2B：正常；指标<0.8B：低。",
        "特殊情况": "当前访客数<100、B缺失或B≤0：样本不足。",
        "运营解释": "高=商品支付承接强；正常=与同层商品接近；低=进店后成交承接偏弱。",
    },
    {
        "状态字段": "商品加购率状态",
        "指标口径": "商品加购率=商品加购件数÷商品访客数。",
        "基准或参考线": (
            "B=过去28日同店铺、同淘宝类目、同价格段累计商品加购件数÷累计商品访客数；"
            "同组无数据时不跨类目或价格段回退。"
        ),
        "判定标准": "指标≥1.2B：高；0.8B≤指标<1.2B：正常；指标<0.8B：低。",
        "特殊情况": "当前访客数<100、B缺失或B≤0：样本不足。",
        "运营解释": "高=商品兴趣承接好；正常=与同层商品接近；低=主图、价格或详情承接偏弱。",
    },
    {
        "状态字段": "可售天数状态",
        "指标口径": "可售天数=在仓库存÷日销7（近7日日均销量）。",
        "基准或参考线": "按库存交期风险固定阈值判定，不使用同层基准。",
        "判定标准": (
            "可售天数<14：紧急；14≤可售天数<28：需关注；"
            "28≤可售天数≤40：充足；可售天数>40：积压。"
        ),
        "特殊情况": "在仓库存≤0：紧急；在仓库存>0且日销7≤0：积压；必要字段不足：样本不足。",
        "运营解释": "紧急=优先补货；需关注=结合交期安排；充足=库存健康；积压=控货并关注去化。",
    },
    {
        "状态字段": "推广加购率状态",
        "指标口径": "推广加购率=推广加购件数÷推广访客数。",
        "基准或参考线": (
            "B=过去28日同店铺、同淘宝类目、同价格段累计推广加购件数÷累计推广访客数；"
            "同组无数据时不跨类目或价格段回退。"
        ),
        "判定标准": "指标≥1.2B：高；0.8B≤指标<1.2B：正常；指标<0.8B：低。",
        "特殊情况": "当前访客数<100（使用推广访客数）、B缺失或B≤0：样本不足。",
        "运营解释": "高=推广流量兴趣质量好；正常=与同层推广接近；低=推广流量或素材匹配效率偏低。",
    },
)


def metric_status_rule_frame():
    """返回与状态算法同源的 Excel 规则说明表。"""
    return pd.DataFrame(METRIC_STATUS_RULES, columns=METRIC_STATUS_RULE_COLUMNS)


def _numeric(frame, column):
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _nullable_numeric(frame, column):
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _safe_ratio(numerator, denominator):
    return (numerator / denominator.where(denominator > 0)).fillna(0.0)


def _price_band(values):
    price = pd.to_numeric(values, errors="coerce").fillna(0.0)
    return pd.cut(
        price,
        bins=[-np.inf, 100, 200, 500, np.inf],
        labels=["0-100", "100-200", "200-500", "500+"],
        right=False,
    ).astype(str)


def _day_frame(entry):
    if entry is None:
        return None
    if isinstance(entry, pd.DataFrame):
        return entry
    if isinstance(entry, (tuple, list)) and entry:
        return entry[0]
    return None


def _history_frame(day_map, date_str):
    if not day_map or not date_str:
        return pd.DataFrame()
    end = datetime.strptime(str(date_str), "%Y-%m-%d").date()
    start = end - timedelta(days=HISTORY_DAYS - 1)
    frames = []
    current = start
    while current <= end:
        frame = _day_frame(day_map.get(current.isoformat()))
        if frame is not None and len(frame) > 0 and "宝贝ID" in frame.columns:
            frames.append(frame)
        current += timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _current_segments(frame):
    segments = pd.DataFrame(index=frame.index)
    segments["宝贝ID"] = frame["宝贝ID"].astype(str).str.strip()
    segments["淘宝类目"] = (
        frame["淘宝类目"].fillna("其他").astype(str).str.strip().replace("", "其他")
        if "淘宝类目" in frame.columns else "其他"
    )
    price = _numeric(frame, "件单价")
    fallback_price = _safe_ratio(_numeric(frame, "GMV"), _numeric(frame, "支付件数"))
    price = price.where(price > 0, fallback_price)
    segments["价格段"] = _price_band(price)
    return segments


def _history_by_product(history):
    if history.empty:
        return pd.DataFrame()
    work = history.copy()
    work["宝贝ID"] = work["宝贝ID"].astype(str).str.strip()
    metrics = [
        "GMV", "付费", "商品访客数", "商品加购件数", "支付买家数", "支付件数",
        "MetoX推广访客数", "MetoX推广加购件数",
    ]
    for column in metrics:
        work[column] = _numeric(work, column)
    work["件单价"] = _numeric(work, "件单价")
    work["淘宝类目"] = (
        work["淘宝类目"].fillna("其他").astype(str).str.strip().replace("", "其他")
        if "淘宝类目" in work.columns else "其他"
    )
    totals = work.groupby("宝贝ID", as_index=False)[metrics].sum()
    dimensions = work.groupby("宝贝ID", as_index=False).agg({
        "淘宝类目": "last",
        "件单价": "last",
    })
    totals = totals.merge(dimensions, on="宝贝ID", how="left")
    weighted_price = _safe_ratio(totals["GMV"], totals["支付件数"])
    totals["件单价"] = weighted_price.where(weighted_price > 0, totals["件单价"])
    totals["价格段"] = _price_band(totals["件单价"])
    return totals


def _weighted_baseline_lookup(history, segments, numerator_col, denominator_col):
    exact = {}
    if not history.empty:
        exact_totals = history.groupby(["淘宝类目", "价格段"])[[numerator_col, denominator_col]].sum()
        exact = {
            key: row[numerator_col] / row[denominator_col]
            for key, row in exact_totals.iterrows()
            if row[denominator_col] > 0
        }

    values = []
    for _, row in segments.iterrows():
        value = exact.get((row["淘宝类目"], row["价格段"]))
        values.append(value)
    return pd.Series(values, index=segments.index, dtype=float)


def _p75_lookup(history, segments):
    exact = {}
    if not history.empty:
        eligible = history[history["付费"] > 0].copy()
        eligible["_链接投产"] = eligible["GMV"] / eligible["付费"]
        for key, part in eligible.groupby(["淘宝类目", "价格段"]):
            exact[key] = float(part["_链接投产"].quantile(0.75))

    values = []
    for _, row in segments.iterrows():
        value = exact.get((row["淘宝类目"], row["价格段"]))
        values.append(value)
    return pd.Series(values, index=segments.index, dtype=float)


def _relative_status(rate, baseline, sample):
    result = pd.Series(STATUS_INSUFFICIENT, index=rate.index, dtype=object)
    eligible = (sample >= RATE_SAMPLE_MIN_VISITORS) & baseline.notna() & (baseline > 0)
    result.loc[eligible & (rate >= baseline * 1.2)] = STATUS_HIGH
    result.loc[eligible & (rate < baseline * 0.8)] = STATUS_LOW
    result.loc[eligible & (rate >= baseline * 0.8) & (rate < baseline * 1.2)] = STATUS_NORMAL
    return result


def _link_roi_status(frame, group_p75):
    spend = _numeric(frame, "付费")
    roi = _numeric(frame, "链接投产")
    gmv = _nullable_numeric(frame, "GMV")
    refund_rate = _nullable_numeric(frame, "退款率")
    price = _numeric(frame, "件单价")
    cost = _numeric(frame, "成本价（含税）")

    refund_after_gsv = gmv * (1 - refund_rate)
    estimated_units = refund_after_gsv / price.where(price > 0)
    refund_after_cost = estimated_units * cost
    available_gross_profit = refund_after_gsv - refund_after_cost
    breakeven = gmv / available_gross_profit.where(available_gross_profit > 0)
    high_line = pd.concat([breakeven * 1.2, group_p75], axis=1).max(axis=1, skipna=True)

    result = pd.Series(STATUS_INSUFFICIENT, index=frame.index, dtype=object)
    valid_inputs = (
        (spend > 0)
        & gmv.notna()
        & (gmv >= 0)
        & refund_rate.notna()
        & refund_rate.between(0.0, 1.0, inclusive="both")
        & (price > 0)
        & (cost > 0)
    )
    no_available_profit = valid_inputs & (available_gross_profit <= 0)
    eligible = valid_inputs & (available_gross_profit > 0)
    result.loc[no_available_profit] = STATUS_LOW
    result.loc[eligible & (roi < breakeven)] = STATUS_LOW
    result.loc[eligible & (roi >= breakeven) & (roi < high_line)] = STATUS_NORMAL
    result.loc[eligible & (roi >= high_line)] = STATUS_HIGH
    return result


def _stock_status(frame):
    stock = _numeric(frame, "在仓库存")
    daily_sales = _numeric(frame, "日销7")
    sellable_days = (
        pd.to_numeric(frame["可售天数"], errors="coerce")
        if "可售天数" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
    result = pd.Series(STATUS_INSUFFICIENT, index=frame.index, dtype=object)

    result.loc[stock <= 0] = "紧急"
    result.loc[(stock > 0) & (daily_sales <= 0)] = "积压"
    valid = (stock > 0) & (daily_sales > 0) & sellable_days.notna()
    result.loc[valid & (sellable_days < 14)] = "紧急"
    result.loc[valid & (sellable_days >= 14) & (sellable_days < 28)] = "需关注"
    result.loc[valid & (sellable_days >= 28) & (sellable_days <= 40)] = "充足"
    result.loc[valid & (sellable_days > 40)] = "积压"
    return result


def apply_metric_labels(df, day_map=None, date_str=None):
    """追加展示用比率和状态列，不覆盖退款、实际投产或既有动作结果。"""
    if df is None or len(df) == 0 or "宝贝ID" not in df.columns:
        return df

    out = df.copy()
    visitors = _numeric(out, "商品访客数")
    carts = _numeric(out, "商品加购件数")
    buyers = _numeric(out, "支付买家数")
    ad_visitors = _numeric(out, "MetoX推广访客数")
    ad_carts = _numeric(out, "MetoX推广加购件数")

    out["商品加购率"] = _safe_ratio(carts, visitors)
    out["商品支付转化率"] = _safe_ratio(buyers, visitors)
    out["推广加购率"] = _safe_ratio(ad_carts, ad_visitors)

    segments = _current_segments(out)
    history = _history_by_product(_history_frame(day_map, date_str))
    if history.empty:
        history = _history_by_product(out)

    product_cart_b = _weighted_baseline_lookup(history, segments, "商品加购件数", "商品访客数")
    product_pay_b = _weighted_baseline_lookup(history, segments, "支付买家数", "商品访客数")
    ad_cart_b = _weighted_baseline_lookup(history, segments, "MetoX推广加购件数", "MetoX推广访客数")
    peer_p75 = _p75_lookup(history, segments)

    out["链接投产状态"] = _link_roi_status(out, peer_p75)
    out["可售天数状态"] = _stock_status(out)
    out["商品加购率状态"] = _relative_status(out["商品加购率"], product_cart_b, visitors)
    out["商品支付转化率状态"] = _relative_status(out["商品支付转化率"], product_pay_b, visitors)
    out["推广加购率状态"] = _relative_status(out["推广加购率"], ad_cart_b, ad_visitors)
    return out
