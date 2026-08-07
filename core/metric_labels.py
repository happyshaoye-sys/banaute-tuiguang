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


def _numeric(frame, column):
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


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
    band = {}
    overall = np.nan
    if not history.empty:
        exact_totals = history.groupby(["淘宝类目", "价格段"])[[numerator_col, denominator_col]].sum()
        exact = {
            key: row[numerator_col] / row[denominator_col]
            for key, row in exact_totals.iterrows()
            if row[denominator_col] > 0
        }
        band_totals = history.groupby("价格段")[[numerator_col, denominator_col]].sum()
        band = {
            key: row[numerator_col] / row[denominator_col]
            for key, row in band_totals.iterrows()
            if row[denominator_col] > 0
        }
        total_denominator = history[denominator_col].sum()
        if total_denominator > 0:
            overall = history[numerator_col].sum() / total_denominator

    values = []
    for _, row in segments.iterrows():
        value = exact.get((row["淘宝类目"], row["价格段"]))
        if value is None:
            value = band.get(row["价格段"], overall)
        values.append(value)
    return pd.Series(values, index=segments.index, dtype=float)


def _p75_lookup(history, segments):
    exact = {}
    band = {}
    overall = np.nan
    if not history.empty:
        eligible = history[history["付费"] > 0].copy()
        eligible["_链接投产"] = eligible["GMV"] / eligible["付费"]
        for key, part in eligible.groupby(["淘宝类目", "价格段"]):
            exact[key] = float(part["_链接投产"].quantile(0.75))
        for key, part in eligible.groupby("价格段"):
            band[key] = float(part["_链接投产"].quantile(0.75))
        if len(eligible) > 0:
            overall = float(eligible["_链接投产"].quantile(0.75))

    values = []
    for _, row in segments.iterrows():
        value = exact.get((row["淘宝类目"], row["价格段"]))
        if value is None:
            value = band.get(row["价格段"], overall)
        values.append(value)
    return pd.Series(values, index=segments.index, dtype=float)


def _relative_status(rate, baseline, sample):
    result = pd.Series(STATUS_INSUFFICIENT, index=rate.index, dtype=object)
    eligible = (sample >= RATE_SAMPLE_MIN_VISITORS) & baseline.notna() & (baseline > 0)
    result.loc[eligible & (rate >= baseline * 1.2)] = STATUS_HIGH
    result.loc[eligible & (rate < baseline * 0.8)] = STATUS_LOW
    result.loc[eligible & (rate >= baseline * 0.8) & (rate < baseline * 1.2)] = STATUS_NORMAL
    return result


def _link_roi_status(frame, peer_p75):
    spend = _numeric(frame, "付费")
    roi = _numeric(frame, "链接投产")
    price = _numeric(frame, "件单价")
    cost = _numeric(frame, "成本价（含税）")
    margin = (price - cost) / price.where(price > 0)
    breakeven = 1 / margin.where(margin > 0)
    high_line = pd.concat([breakeven * 1.2, peer_p75], axis=1).max(axis=1, skipna=True)

    result = pd.Series(STATUS_INSUFFICIENT, index=frame.index, dtype=object)
    impossible_margin = (spend > 0) & (price > 0) & (cost >= price)
    eligible = (spend > 0) & (price > 0) & (cost > 0) & (margin > 0)
    result.loc[impossible_margin] = STATUS_LOW
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
    result.loc[valid & (sellable_days >= 28) & (sellable_days <= 60)] = "充足"
    result.loc[valid & (sellable_days > 60)] = "积压"
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
