# -*- coding: utf-8 -*-
"""Built-in v9.3-style threshold labels for the execution table.

This module intentionally does not import daily_review_v9.3.py.  The offline
tool owns execution-table fields, so this layer recreates the four business
gates from fields already present in the merged table/history.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
import re

import pandas as pd


PID = "宝贝ID"
NAME = "商品名称"
CATEGORY = "淘宝类目"
GMV = "GMV"
AD_GMV = "推广成交金额"
AD_CARTS = "推广加购数"
AD_VISITORS = "推广访客数"
V93_SPEND = "v9.3推广花费"
V93_AD_GMV = "v9.3推广成交金额"
V93_AD_CARTS = "v9.3推广加购数"
V93_AD_VISITORS = "v9.3推广访客数"
SPEND = "付费"
VISITORS = "商品访客数"
CARTS = "商品加购件数"
PRICE = "件单价"
DETAIL = "推广明细"

OUT_LABELS = "v9.3算法命中"
OUT_SCORE = "v9.3算法分"
OUT_REASON = "v9.3算法原因"


FOCUS_GATE = {
    "soft_ceiling": 100,
    "min_7d_org_vis": 50,
    "min_7d_carts": 15,
    "min_yd_org_vis": 20,
    "mid_yd_org_vis": 30,
    "strong_yd_org_vis": 50,
    "min_yd_carts": 3,
    "mid_yd_carts": 5,
    "strong_yd_carts": 8,
    "min_3d_carts": 20,
    "max_spend_7d": 200,
    "strong_spend_7d": 150,
    "min_rate_floor": 0.06,
    "strong_rate_floor": 0.08,
}
FOCUS_WEIGHTS = {
    "yd_org_vis_strong": 3,
    "yd_org_vis_mid": 2,
    "yd_org_vis_min": 1,
    "yd_carts_strong": 3,
    "yd_carts_mid": 2,
    "yd_carts_min": 1,
    "yd_rate_strong": 3,
    "yd_rate_exec": 2,
    "carts_3d_base": 1,
    "growth_3d_carts_strong": 2,
    "growth_3d_carts_mid": 1,
    "growth_3d_gmv": 1,
    "low_spend_bonus": 3,
    "plan_count_low": 1,
    "plan_count_high_penalty": -1,
    "high_plan_roi": 1,
    "growth_3d_carts_ratio": 1.15,
    "growth_3d_carts_absolute": 5,
    "growth_3d_gmv_ratio": 1.05,
    "growth_3d_gmv_absolute": 100,
    "plan_count_high_threshold": 5,
    "high_plan_roi_threshold": 10,
    "plan_count_low_threshold": 3,
}
PRICE_CPA_THRESHOLDS = {"500": 15, "200": 12, "100": 5, "0": 3}
PRICE_CART_RATE_THRESHOLDS = {"500": 0.03, "200": 0.035, "100": 0.05, "0": 0.08}
DYNAMIC_THRESHOLDS = {
    "enabled": True,
    "min_segment_vis": 500,
    "min_segment_ad_carts": 50,
    "cpa_factor": 1.2,
    "segment_window_days": 7,
    "tiers": [
        {"min": 500, "key": "500+"},
        {"min": 200, "key": "200-500"},
        {"min": 100, "key": "100-200"},
        {"min": 0, "key": "0-100"},
    ],
}
LOSS_HOLE_GATE = {
    "min_spend": 200,
    "roi_bad_factor": 0.15,
    "roi_exempt_factor": 0.5,
    "spillover_exempt": 10,
}
RECENT_GATE = {"min_carts_3d": 20, "max_spend_3d": 200, "min_cart_rate": 0.08}
SCALE_GATE = {
    "min_prev_carts": 10,
    "min_current_carts": 20,
    "total_growth": 0.3,
    "organic_growth_strong": 0.3,
    "organic_growth_mid": 0.2,
    "low_spend": 200,
    "organic_baseline_factor": 0.5,
}
REBALANCE_GATE = {
    "min_spend": 50,
    "roi_high_factor": 1.2,
    "roi_ok_factor": 0.9,
    "roi_low_factor": 0.3,
    "roi_high_pctl": 75,
    "roi_ok_pctl": 50,
    "org_share_pctl": 90,
    "cart_rate_pctl": 90,
    "min_samples_for_percentile": 30,
    "org_share_default": 0.75,
    "cart_rate_default": 0.06,
    "saturation_spend_3d_min": 100,
    "saturation_spend": 50,
    "saturation_mroi": 3,
    "min_spend_change": 50,
    "cpa_good_factor": 0.8,
    "cpa_ok_factor": 1.0,
    "min_budget": 300,
    "budget_mult_good": 1.5,
    "budget_mult_normal": 1.3,
    "reduce_mult": 0.5,
    "store_roi_window_days": 7,
    "store_roi_min_valid_days": 4,
    "store_roi_floor": 5.0,
}
SPILLOVER_GATE = {"min_spend": 100, "top_min_spend": 200, "top_n": 999}
PLAN_ID_RE = re.compile(r"计划ID[:：]\s*([^\s]+)")
PLAN_ROI_RE = re.compile(r"投产[:：]\s*([0-9.]+)")
PLAN_CART_RE = re.compile(r"加购[:：]\s*([0-9.]+)")


def apply_v93_algorithm(df, day_map=None, date_str=None):
    """Append built-in v9.3 threshold labels to an execution table.

    `day_map` is the app's existing `{date: (day_df, ...)}` history cache.  When
    it is absent, the function falls back to the current frame and rolling
    columns such as `花费3/花费7`, so manual tests and exported snapshots still
    get deterministic labels.
    """
    if df is None or len(df) == 0:
        return df
    if PID not in df.columns:
        out = df.copy()
        out[OUT_LABELS] = ""
        out[OUT_SCORE] = 0
        out[OUT_REASON] = ""
        return out

    out = df.copy()
    out[PID] = out[PID].astype(str).str.strip()
    pids = out[PID].tolist()
    latest = _latest_frame(out, day_map, date_str)

    ctx = _MetricContext(out, latest, day_map, date_str)
    thresholds = _ThresholdContext(ctx, pids)

    labels = {pid: [] for pid in pids}
    scores = {pid: 0 for pid in pids}
    reasons = {pid: [] for pid in pids}

    focus_pids = set()
    for pid in pids:
        item = ctx.item(pid)
        focus = _focus_result(item, thresholds.cart_threshold(item["price"], item["category"]))
        if focus:
            focus_pids.add(pid)
            labels[pid].append("明日之星")
            scores[pid] += focus["score"]
            reasons[pid].append(focus["reason"])
            if focus["strong"]:
                labels[pid].append("强主推")

    recent_pids = set()
    for pid in pids:
        if pid in focus_pids:
            continue
        item = ctx.item(pid)
        rate3 = _safe_div(item["carts_3d"], item["vis_3d"])
        if (
            item["carts_3d"] >= RECENT_GATE["min_carts_3d"]
            and item["spend_3d"] < RECENT_GATE["max_spend_3d"]
            and rate3 >= RECENT_GATE["min_cart_rate"]
        ):
            recent_pids.add(pid)
            labels[pid].append("近3日潜力")
            scores[pid] += 20
            reasons[pid].append(
                f"近3日加购{item['carts_3d']:.0f},花费{item['spend_3d']:.0f},加购率{rate3:.1%}"
            )

    scale_pids = set()
    for pid in pids:
        item = ctx.item(pid)
        if item["baseline_carts"] < SCALE_GATE["min_prev_carts"]:
            continue
        growth = _safe_div(item["yd_carts"] - item["baseline_carts"], item["baseline_carts"])
        if growth > SCALE_GATE["total_growth"] and item["yd_carts"] >= SCALE_GATE["min_current_carts"]:
            scale_pids.add(pid)
            labels[pid].append("爆发")
            scores[pid] += 30
            org_growth = _safe_div(item["yd_org_carts"] - item["baseline_org_carts"], item["baseline_org_carts"])
            if org_growth > SCALE_GATE["organic_growth_strong"] and item["yd_spend"] < SCALE_GATE["low_spend"]:
                signal = "自然爆发"
            elif org_growth > SCALE_GATE["organic_growth_mid"]:
                signal = "潜力爆发"
            else:
                signal = "投放拉动"
            reasons[pid].append(f"{signal}:昨日加购{item['yd_carts']:.0f},较基线+{growth:.0%}")

    risk_ctx = _loss_hole_context(ctx, pids)
    for pid in pids:
        risk = _loss_hole_result(ctx.item(pid), risk_ctx, thresholds)
        if risk:
            labels[pid].append("亏损风险")
            scores[pid] -= 20
            reasons[pid].append(risk)

    blocked_for_rebalance = focus_pids | recent_pids | scale_pids
    rebalance_ctx = _rebalance_context(ctx, pids, thresholds)
    for pid in pids:
        if pid in blocked_for_rebalance:
            continue
        item = ctx.item(pid)
        reb = _rebalance_result(item, rebalance_ctx, thresholds)
        if reb:
            labels[pid].append(reb["label"])
            scores[pid] += reb["score"]
            reasons[pid].append(reb["reason"])

    for pid in pids:
        spill = _spillover_result(ctx.item(pid))
        if spill:
            labels[pid].append("溢出")
            scores[pid] += 10
            reasons[pid].append(spill)

    out[OUT_LABELS] = ["|".join(labels[pid]) for pid in pids]
    out[OUT_SCORE] = [int(scores[pid]) for pid in pids]
    out[OUT_REASON] = ["; ".join(reasons[pid]) for pid in pids]
    return out


class _MetricContext:
    def __init__(self, current, latest, day_map, date_str):
        self.current = _by_pid(current)
        self.latest = _by_pid(latest)
        self.day_map = day_map or {}
        self.date_str = date_str
        self.has_history = bool(day_map and date_str)
        self._cache = {}
        self._item_cache = {}
        self._window_cache = {}
        self._window_day_counts = {}

    def item(self, pid):
        if pid in self._item_cache:
            return self._item_cache[pid]
        row = self.current.get(pid, {})
        yd = self.latest.get(pid, row)
        spend7 = self.sum_preferred(pid, V93_SPEND, SPEND, 7, 0, fallback_col="花费7")
        spend3 = self.sum_preferred(pid, V93_SPEND, SPEND, 3, 0, fallback_col="花费3")
        gmv3 = self.sum(pid, GMV, 3, 0)
        prev_gmv3 = self.sum(pid, GMV, 3, 3)
        spend3_prev = self.sum_preferred(pid, V93_SPEND, SPEND, 3, 3)
        carts7 = self.sum(pid, CARTS, 7, 0)
        ad_carts7 = self.sum_preferred(pid, V93_AD_CARTS, AD_CARTS, 7, 0)
        ad_vis7 = self.sum_preferred(pid, V93_AD_VISITORS, AD_VISITORS, 7, 0)
        yd_vis = _num(_val(yd, VISITORS, _val(row, VISITORS, 0)))
        yd_carts = _num(_val(yd, CARTS, _val(row, CARTS, 0)))
        yd_ad_vis = self.value_preferred(yd, row, V93_AD_VISITORS, AD_VISITORS)
        yd_ad_carts = self.value_preferred(yd, row, V93_AD_CARTS, AD_CARTS)
        org_vis7 = max(0.0, self.sum(pid, VISITORS, 7, 0) - ad_vis7)
        org_carts7 = max(0.0, carts7 - ad_carts7)
        yd_org_vis = max(0.0, yd_vis - yd_ad_vis)
        yd_org_carts = max(0.0, yd_carts - yd_ad_carts)
        plan_count, best_plan_roi, detail_ad_carts = _parse_plan_detail(_val(row, DETAIL, ""))
        if ad_carts7 <= 0 and detail_ad_carts > 0:
            ad_carts7 = detail_ad_carts
        ad_carts3 = self.sum_preferred(pid, V93_AD_CARTS, AD_CARTS, 3, 0)
        prev_org_carts = max(
            0.0,
            self.avg(pid, CARTS, 3, 1)
            - self.avg_preferred(pid, V93_AD_CARTS, AD_CARTS, 3, 1),
        )
        price = _num(_val(row, PRICE, _val(yd, PRICE, 0)))
        if price <= 0:
            units = _num(_val(row, "支付件数", _val(yd, "支付件数", 0)))
            price = _safe_div(_val(row, GMV, _val(yd, GMV, 0)), units)
        item = {
            "pid": pid,
            "category": str(_val(row, CATEGORY, _val(yd, CATEGORY, "")) or ""),
            "price": price,
            "yd_vis": yd_vis,
            "yd_carts": yd_carts,
            "yd_org_vis": yd_org_vis,
            "yd_org_carts": yd_org_carts,
            "yd_rate": _safe_div(yd_org_carts, yd_org_vis),
            "yd_spend": self.value_preferred(yd, row, V93_SPEND, SPEND),
            "yd_gmv": _num(_val(yd, GMV, _val(row, GMV, 0))),
            "vis_7d": self.sum(pid, VISITORS, 7, 0),
            "org_vis_7d": org_vis7,
            "org_carts_7d": org_carts7,
            "carts_7d": carts7,
            "gmv_7d": self.sum(pid, GMV, 7, 0),
            "spend_7d": spend7,
            "ad_gmv_7d": self.sum_preferred(pid, V93_AD_GMV, AD_GMV, 7, 0),
            "ad_carts_7d": ad_carts7,
            "vis_3d": self.sum(pid, VISITORS, 3, 0),
            "carts_3d": self.sum(pid, CARTS, 3, 0),
            "ad_carts_3d": ad_carts3,
            "gmv_3d": gmv3,
            "spend_3d": spend3,
            "prev_carts_3d": self.sum(pid, CARTS, 3, 3),
            "prev_gmv_3d": prev_gmv3,
            "spend_change": spend3 - spend3_prev,
            "mroi": _safe_div(gmv3 - prev_gmv3, spend3 - spend3_prev) if spend3 > spend3_prev else 999,
            "baseline_carts": self.avg(pid, CARTS, 3, 1),
            "baseline_org_carts": prev_org_carts,
            "active_plan_count": plan_count if plan_count > 0 else int(_num(_val(row, SPEND, 0)) > 0),
            "best_plan_roi": best_plan_roi,
            "has_plan": self.value_preferred(row, row, V93_SPEND, SPEND) > 0 or str(_val(row, DETAIL, "")).strip() != "",
        }
        self._item_cache[pid] = item
        return item

    def sum(self, pid, metric, days, offset=0, fallback_col=None):
        key = (pid, metric, days, offset, fallback_col)
        if key in self._cache:
            return self._cache[key]
        if self.has_history:
            value = self._window_metric_values(metric, days, offset).get(str(pid), 0.0)
        else:
            row = self.current.get(pid, {})
            source = fallback_col if fallback_col and fallback_col in row else metric
            value = _num(_val(row, source, 0))
        self._cache[key] = value
        return value

    def sum_preferred(self, pid, preferred, fallback, days, offset=0, fallback_col=None):
        if self.has_metric(preferred, days, offset):
            return self.sum(pid, preferred, days, offset)
        return self.sum(pid, fallback, days, offset, fallback_col=fallback_col)

    def avg(self, pid, metric, days, offset=0):
        if not self.has_history:
            return 0.0
        count = self._window_day_count(metric, days, offset)
        if count <= 0:
            return 0.0
        return self._window_metric_values(metric, days, offset).get(str(pid), 0.0) / count

    def avg_preferred(self, pid, preferred, fallback, days, offset=0):
        metric = preferred if self.has_metric(preferred, days, offset) else fallback
        return self.avg(pid, metric, days, offset)

    def value_preferred(self, primary_row, fallback_row, preferred, fallback):
        if preferred in primary_row:
            return _num(_val(primary_row, preferred, 0))
        return _num(_val(primary_row, fallback, _val(fallback_row, fallback, 0)))

    def has_metric(self, metric, days, offset=0):
        if self.has_history:
            return self._window_has_metric(metric, days, offset)
        return any(metric in row for row in self.current.values())

    def _window_metric_values(self, metric, days, offset):
        key = (metric, days, offset)
        if key in self._window_cache:
            return self._window_cache[key]

        frames = []
        day_count = 0
        for day in _window_days(self.date_str, days, offset):
            frame = _day_frame(self.day_map, day)
            if frame is None or PID not in frame.columns or metric not in frame.columns:
                continue
            day_count += 1
            part = frame[[PID, metric]].copy()
            part[PID] = part[PID].astype(str).str.strip()
            part[metric] = pd.to_numeric(part[metric], errors="coerce").fillna(0.0)
            frames.append(part)

        self._window_day_counts[key] = day_count
        if not frames:
            self._window_cache[key] = {}
            return self._window_cache[key]

        grouped = (
            pd.concat(frames, ignore_index=True)
            .groupby(PID, as_index=True)[metric]
            .sum()
        )
        self._window_cache[key] = grouped.to_dict()
        return self._window_cache[key]

    def _window_day_count(self, metric, days, offset):
        key = (metric, days, offset)
        if key not in self._window_day_counts:
            self._window_metric_values(metric, days, offset)
        return self._window_day_counts.get(key, 0)

    def _window_has_metric(self, metric, days, offset):
        for day in _window_days(self.date_str, days, offset):
            frame = _day_frame(self.day_map, day)
            if frame is not None and metric in frame.columns:
                return True
        return False

    def current_frame(self):
        if self.current:
            return pd.DataFrame(self.current.values())
        return pd.DataFrame()


def _focus_result(item, category_rate_line):
    if not item["has_plan"]:
        return None
    if item["org_vis_7d"] < FOCUS_GATE["min_7d_org_vis"]:
        return None
    if item["carts_7d"] < FOCUS_GATE["min_7d_carts"]:
        return None
    if item["yd_org_vis"] < FOCUS_GATE["min_yd_org_vis"]:
        return None
    if item["yd_carts"] < FOCUS_GATE["min_yd_carts"]:
        return None
    if item["spend_7d"] > FOCUS_GATE["max_spend_7d"]:
        return None
    exec_rate_line = max(category_rate_line, FOCUS_GATE["min_rate_floor"])
    strong_rate_line = max(category_rate_line, FOCUS_GATE["strong_rate_floor"])
    if item["yd_rate"] < exec_rate_line:
        return None
    if item["carts_3d"] <= item["prev_carts_3d"] and item["gmv_3d"] <= item["prev_gmv_3d"]:
        return None

    score = 0
    reasons = [
        f"7日自然访客{item['org_vis_7d']:.0f}≥{FOCUS_GATE['min_7d_org_vis']}",
        f"7日加购{item['carts_7d']:.0f}≥{FOCUS_GATE['min_7d_carts']}",
    ]

    if item["yd_org_vis"] >= FOCUS_GATE["strong_yd_org_vis"]:
        score += FOCUS_WEIGHTS["yd_org_vis_strong"]
        reasons.append(f"昨日自然访客{item['yd_org_vis']:.0f}≥50")
    elif item["yd_org_vis"] >= FOCUS_GATE["mid_yd_org_vis"]:
        score += FOCUS_WEIGHTS["yd_org_vis_mid"]
        reasons.append(f"昨日自然访客{item['yd_org_vis']:.0f}≥30")
    else:
        score += FOCUS_WEIGHTS["yd_org_vis_min"]
        reasons.append(f"昨日自然访客{item['yd_org_vis']:.0f}≥20")

    if item["yd_carts"] >= FOCUS_GATE["strong_yd_carts"]:
        score += FOCUS_WEIGHTS["yd_carts_strong"]
        reasons.append(f"昨日加购{item['yd_carts']:.0f}≥8")
    elif item["yd_carts"] >= FOCUS_GATE["mid_yd_carts"]:
        score += FOCUS_WEIGHTS["yd_carts_mid"]
        reasons.append(f"昨日加购{item['yd_carts']:.0f}≥5")
    else:
        score += FOCUS_WEIGHTS["yd_carts_min"]
        reasons.append(f"昨日加购{item['yd_carts']:.0f}≥3")

    if item["yd_rate"] >= strong_rate_line:
        score += FOCUS_WEIGHTS["yd_rate_strong"]
        reasons.append(f"昨日自然加购率{item['yd_rate']:.1%}≥强阈值{strong_rate_line:.1%}")
    else:
        score += FOCUS_WEIGHTS["yd_rate_exec"]
        reasons.append(f"昨日自然加购率{item['yd_rate']:.1%}≥执行阈值{exec_rate_line:.1%}")

    if item["carts_3d"] >= FOCUS_GATE["min_3d_carts"]:
        score += FOCUS_WEIGHTS["carts_3d_base"]
    if item["carts_3d"] >= max(
        item["prev_carts_3d"] * FOCUS_WEIGHTS["growth_3d_carts_ratio"],
        item["prev_carts_3d"] + FOCUS_WEIGHTS["growth_3d_carts_absolute"],
    ):
        score += FOCUS_WEIGHTS["growth_3d_carts_strong"]
        reasons.append("近3日加购明显高于前3日")
    elif item["carts_3d"] > item["prev_carts_3d"]:
        score += FOCUS_WEIGHTS["growth_3d_carts_mid"]
    if item["gmv_3d"] >= max(
        item["prev_gmv_3d"] * FOCUS_WEIGHTS["growth_3d_gmv_ratio"],
        item["prev_gmv_3d"] + FOCUS_WEIGHTS["growth_3d_gmv_absolute"],
    ):
        score += FOCUS_WEIGHTS["growth_3d_gmv"]
    if item["spend_7d"] <= FOCUS_GATE["strong_spend_7d"]:
        score += FOCUS_WEIGHTS["low_spend_bonus"]
    if item["active_plan_count"] <= FOCUS_WEIGHTS["plan_count_low_threshold"]:
        score += FOCUS_WEIGHTS["plan_count_low"]
    elif item["active_plan_count"] > FOCUS_WEIGHTS["plan_count_high_threshold"]:
        score += FOCUS_WEIGHTS["plan_count_high_penalty"]
    if item["best_plan_roi"] >= FOCUS_WEIGHTS["high_plan_roi_threshold"]:
        score += FOCUS_WEIGHTS["high_plan_roi"]

    strong = (
        item["yd_carts"] >= FOCUS_GATE["mid_yd_carts"]
        and item["yd_org_vis"] >= FOCUS_GATE["mid_yd_org_vis"]
        and item["yd_rate"] >= strong_rate_line
        and item["spend_7d"] <= FOCUS_GATE["strong_spend_7d"]
    )
    return {
        "score": score,
        "strong": strong,
        "reason": (
            f"明日之星:7日自然访客{item['org_vis_7d']:.0f},7日加购{item['carts_7d']:.0f},"
            f"昨日自然加购率{item['yd_rate']:.1%},7日花费{item['spend_7d']:.0f},"
            f"执行分{score},原因:{'；'.join(reasons)}"
        ),
    }


class _ThresholdContext:
    def __init__(self, ctx, pids):
        self.ctx = ctx
        self.pids = pids
        self.segment_thresholds = {}
        self.segment_cpa_thresholds = {}
        self._build_dynamic_thresholds()

    def cart_threshold(self, price, category):
        base = _threshold_by_price(price, PRICE_CART_RATE_THRESHOLDS)
        if not DYNAMIC_THRESHOLDS["enabled"]:
            return base
        seg_rate = self.segment_thresholds.get(self._seg_key(price, category))
        return max(base, seg_rate) if seg_rate is not None else base

    def cpa_line(self, price, category):
        price_line = _threshold_by_price(price, PRICE_CPA_THRESHOLDS)
        seg_cpa = self.segment_cpa_thresholds.get(self._seg_key(price, category))
        return _compute_cpa_line(price_line, seg_cpa, DYNAMIC_THRESHOLDS["cpa_factor"])

    def _seg_key(self, price, category):
        return (category if category else "其他", _tier_key(price))

    def _build_dynamic_thresholds(self):
        if not DYNAMIC_THRESHOLDS["enabled"]:
            return
        stats = {}
        ad_stats = {}
        for pid in self.pids:
            item = self.ctx.item(pid)
            key = self._seg_key(item["price"], item["category"])
            cur = stats.setdefault(key, {"org_vis": 0.0, "org_carts": 0.0})
            cur["org_vis"] += item["org_vis_7d"]
            cur["org_carts"] += item["org_carts_7d"]
            ad = ad_stats.setdefault(key, {"ad_spend": 0.0, "ad_carts": 0.0})
            ad["ad_spend"] += item["spend_7d"]
            ad["ad_carts"] += item["ad_carts_7d"]
        for key, value in stats.items():
            if value["org_vis"] >= DYNAMIC_THRESHOLDS["min_segment_vis"]:
                self.segment_thresholds[key] = _safe_div(value["org_carts"], value["org_vis"])
        for key, value in ad_stats.items():
            if value["ad_carts"] >= DYNAMIC_THRESHOLDS["min_segment_ad_carts"]:
                self.segment_cpa_thresholds[key] = _safe_div(value["ad_spend"], value["ad_carts"])


def _loss_hole_context(ctx, pids):
    latest = _latest_frame(ctx.current_frame(), ctx.day_map, ctx.date_str)
    spend = _frame_sum_preferred(latest, V93_SPEND, SPEND)
    gmv = _frame_sum(latest, GMV)
    return {"store_roi": _safe_div(gmv, spend)}


def _loss_hole_result(item, risk_ctx, thresholds):
    store_roi = risk_ctx["store_roi"]
    if item["yd_spend"] < LOSS_HOLE_GATE["min_spend"] or store_roi <= 0:
        return None
    roi = _safe_div(item["yd_gmv"], item["yd_spend"])
    cart_rate = _safe_div(item["yd_carts"], item["yd_vis"])
    cart_line = thresholds.cart_threshold(item["price"], item["category"])
    roi_bad_line = store_roi * LOSS_HOLE_GATE["roi_bad_factor"]
    roi_exempt_line = store_roi * LOSS_HOLE_GATE["roi_exempt_factor"]
    roi_3d = _safe_div(item["gmv_3d"], item["spend_3d"])
    roi_7d = _safe_div(item["gmv_7d"], item["spend_7d"])
    org_gmv = max(0.0, item["gmv_7d"] - item["ad_gmv_7d"])
    spillover_roi = _safe_div(org_gmv, item["spend_7d"])
    if (
        (roi < roi_bad_line or cart_rate < cart_line)
        and roi_3d < roi_bad_line
        and roi_7d < roi_bad_line
        and not (roi > roi_exempt_line or spillover_roi > LOSS_HOLE_GATE["spillover_exempt"])
    ):
        parts = []
        if roi < roi_bad_line:
            parts.append(f"亏损风险:昨日ROI{roi:.1f}<{roi_bad_line:.1f}")
        if cart_rate < cart_line:
            parts.append(f"加购率{cart_rate:.1%}<{cart_line:.1%}")
        parts.append(f"3d ROI{roi_3d:.1f}/7d ROI{roi_7d:.1f}双低")
        return ",".join(parts)
    return None


def _rebalance_context(ctx, pids, thresholds):
    rows = [ctx.item(pid) for pid in pids]
    valid = [r for r in rows if r["spend_7d"] >= REBALANCE_GATE["min_spend"] and r["spend_7d"] > 0]
    rois = [_safe_div(r["gmv_7d"], r["spend_7d"]) for r in valid]
    store_roi, valid_days = _store_roi_basis(ctx, pids)
    use_percentile = len(rois) >= REBALANCE_GATE["min_samples_for_percentile"]
    org_shares = [_safe_div(max(0, r["gmv_7d"] - r["ad_gmv_7d"]), r["gmv_7d"]) for r in valid if r["gmv_7d"] > 0]
    cart_rates = [_safe_div(r["carts_7d"], r["vis_7d"]) for r in valid if r["vis_7d"] > 0]
    basis_ok = (
        store_roi > 0
        and valid_days >= REBALANCE_GATE["store_roi_min_valid_days"]
        and store_roi >= REBALANCE_GATE["store_roi_floor"]
    )
    return {
        "basis_ok": basis_ok,
        "store_roi": store_roi,
        "roi_high": max(
            store_roi * REBALANCE_GATE["roi_high_factor"],
            _percentile(rois, REBALANCE_GATE["roi_high_pctl"]) if use_percentile else 0,
        ),
        "roi_ok": max(
            store_roi * REBALANCE_GATE["roi_ok_factor"],
            _percentile(rois, REBALANCE_GATE["roi_ok_pctl"]) if use_percentile else 0,
        ),
        "org_share_high": (
            _percentile(org_shares, REBALANCE_GATE["org_share_pctl"])
            if use_percentile and org_shares
            else REBALANCE_GATE["org_share_default"]
        ),
        "cart_rate_high": (
            _percentile(cart_rates, REBALANCE_GATE["cart_rate_pctl"])
            if use_percentile and cart_rates
            else REBALANCE_GATE["cart_rate_default"]
        ),
        "thresholds": thresholds,
    }


def _rebalance_result(item, ctx, thresholds):
    if not ctx["basis_ok"]:
        return None
    if item["spend_7d"] < REBALANCE_GATE["min_spend"] or item["spend_7d"] <= 0:
        return None
    roi7 = _safe_div(item["gmv_7d"], item["spend_7d"])
    org_share = _safe_div(max(0, item["gmv_7d"] - item["ad_gmv_7d"]), item["gmv_7d"])
    cart_rate = _safe_div(item["carts_7d"], item["vis_7d"])
    cpa = _safe_div(item["spend_7d"], item["ad_carts_7d"]) if item["ad_carts_7d"] > 0 else 999
    cpa_line = thresholds.cpa_line(item["price"], item["category"])
    saturated = (
        item["spend_3d"] > REBALANCE_GATE["saturation_spend_3d_min"]
        and item["spend_change"] > REBALANCE_GATE["saturation_spend"]
        and item["mroi"] < REBALANCE_GATE["saturation_mroi"]
    )
    if roi7 >= ctx["roi_high"] and not saturated:
        return {"label": "加预算", "score": 40, "reason": f"高ROI:7日ROI{roi7:.1f}≥高线{ctx['roi_high']:.1f}"}
    if (
        roi7 >= ctx["roi_ok"]
        and not saturated
        and org_share >= ctx["org_share_high"]
        and cpa <= cpa_line
    ):
        return {"label": "加预算", "score": 40, "reason": f"有机强+CPA优:ROI{roi7:.1f}≥良线{ctx['roi_ok']:.1f},自然GMV占比{org_share:.0%},CPA{cpa:.1f}≤{cpa_line:.1f}"}
    if saturated and (roi7 >= ctx["roi_ok"] or org_share >= ctx["org_share_high"]):
        return {"label": "饱和观察", "score": 5, "reason": f"饱和观察:mROI{item['mroi']:.1f}<3,3日增投{item['spend_change']:.0f},7日ROI{roi7:.1f}"}
    if cpa <= cpa_line * REBALANCE_GATE["cpa_good_factor"] and cart_rate >= ctx["cart_rate_high"]:
        return {"label": "保护持有", "score": 15, "reason": f"低CPA:CPA{cpa:.1f}≤红线{cpa_line:.1f}*0.8,加购率{cart_rate:.1%}"}
    if org_share >= ctx["org_share_high"]:
        return {"label": "保护持有", "score": 10, "reason": f"有机贡献:自然GMV占比{org_share:.0%}≥{ctx['org_share_high']:.0%}"}
    if roi7 < ctx["store_roi"] * REBALANCE_GATE["roi_low_factor"] and cpa > cpa_line * REBALANCE_GATE["cpa_ok_factor"]:
        return {"label": "削减", "score": -30, "reason": f"低ROI高CPA:7日ROI{roi7:.1f}<店铺基准{ctx['store_roi']:.1f}*0.3,CPA{cpa:.1f}>{cpa_line:.1f}"}
    return {"label": "保护持有", "score": 0, "reason": f"稳定持有:7日ROI{roi7:.1f},自然GMV占比{org_share:.0%},CPA{_format_cpa(cpa)}"}


def _spillover_result(item):
    if item["spend_7d"] < SPILLOVER_GATE["top_min_spend"]:
        return None
    org_gmv = max(0.0, item["gmv_7d"] - item["ad_gmv_7d"])
    spill = _safe_div(org_gmv, item["spend_7d"])
    return f"溢出:{spill:.1f},自然GMV{org_gmv:.0f},7日花费{item['spend_7d']:.0f}"


def _store_roi_basis(ctx, pids):
    if not ctx.has_history:
        spend = sum(ctx.item(pid)["spend_7d"] for pid in pids)
        gmv = sum(ctx.item(pid)["gmv_7d"] for pid in pids)
        return _safe_div(gmv, spend), 7 if spend > 0 else 0
    rois = []
    for day in _window_days(ctx.date_str, 7, 0):
        frame = _day_frame(ctx.day_map, day)
        if frame is None or GMV not in frame.columns:
            continue
        spend = _frame_sum_preferred(frame, V93_SPEND, SPEND)
        gmv = float(pd.to_numeric(frame[GMV], errors="coerce").fillna(0.0).sum())
        if spend > 0:
            rois.append(gmv / spend)
    return (median(rois) if rois else 0.0), len(rois)


def _category_cart_threshold(frame):
    if frame is None or len(frame) == 0 or CATEGORY not in frame.columns:
        return {}
    work = frame.copy()
    if VISITORS not in work.columns or CARTS not in work.columns:
        return {}
    work["_vis"] = pd.to_numeric(work[VISITORS], errors="coerce").fillna(0.0)
    work["_carts"] = pd.to_numeric(work[CARTS], errors="coerce").fillna(0.0)
    work = work[work["_vis"] > 0]
    if work.empty:
        return {}
    work["_rate"] = work["_carts"] / work["_vis"]
    out = {}
    for cat, part in work.groupby(CATEGORY):
        out[str(cat)] = max(FOCUS_GATE["min_rate_floor"], float(part["_rate"].median()))
    return out


def _parse_plan_detail(detail):
    text = str(detail or "")
    if not text.strip():
        return 0, 0.0, 0.0
    plan_count = len(PLAN_ID_RE.findall(text))
    rois = [_num(m.group(1)) for m in PLAN_ROI_RE.finditer(text)]
    carts = [_num(m.group(1)) for m in PLAN_CART_RE.finditer(text)]
    return plan_count, max(rois) if rois else 0.0, sum(carts)


def _threshold_by_price(price, thresholds):
    for pv, key in sorted(((float(k), k) for k in thresholds.keys()), reverse=True):
        if _num(price) >= pv:
            return thresholds[key]
    return thresholds.get("0", 0)


def _tier_key(price):
    tiers = DYNAMIC_THRESHOLDS["tiers"]
    for tier in sorted(tiers, key=lambda x: x["min"], reverse=True):
        if _num(price) >= tier["min"]:
            return tier["key"]
    return tiers[-1]["key"] if tiers else "NA"


def _compute_cpa_line(price_line, seg_cpa, cpa_factor):
    if seg_cpa and seg_cpa > 0:
        return max(price_line, seg_cpa * cpa_factor)
    return price_line


def _frame_sum(frame, metric):
    if frame is None or len(frame) == 0 or metric not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[metric], errors="coerce").fillna(0.0).sum())


def _frame_sum_preferred(frame, preferred, fallback):
    if frame is None or len(frame) == 0:
        return 0.0
    metric = preferred if preferred in frame.columns else fallback
    return _frame_sum(frame, metric)


def _format_cpa(cpa):
    return "N/A" if cpa == 999 else f"{cpa:.1f}"


def _latest_frame(current, day_map, date_str):
    frame = _day_frame(day_map or {}, date_str) if date_str else None
    return frame if frame is not None and len(frame) > 0 else current


def _day_frame(day_map, day):
    entry = day_map.get(day) if day_map and day else None
    if not entry:
        return None
    return entry[0]


def _window_days(end_date, days, offset=0):
    end = datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days=offset)
    start = end - timedelta(days=days - 1)
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _by_pid(frame):
    if frame is None or len(frame) == 0 or PID not in frame.columns:
        return {}
    work = frame.copy()
    work[PID] = work[PID].astype(str).str.strip()
    return {str(row[PID]): row for _, row in work.iterrows()}


def _val(row, key, default=0):
    try:
        return row.get(key, default)
    except AttributeError:
        return default


def _num(value):
    if value is None:
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    try:
        if isinstance(value, str):
            value = (
                value.strip()
                .replace(",", "")
                .replace("¥", "")
                .replace("￥", "")
                .replace("%", "")
            )
            if not value:
                return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_div(a, b):
    a = _num(a)
    b = _num(b)
    return a / b if b else 0.0


def _percentile(values, pct):
    vals = sorted(_num(v) for v in values if pd.notna(v))
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * max(0, min(100, pct)) / 100
    low = int(rank)
    high = min(low + 1, len(vals) - 1)
    weight = rank - low
    return vals[low] * (1 - weight) + vals[high] * weight
