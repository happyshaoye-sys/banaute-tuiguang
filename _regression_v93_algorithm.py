# -*- coding: utf-8 -*-
"""Regression checks for the built-in v9.3 threshold layer."""
from datetime import date, timedelta

import pandas as pd

from core.schema import EXPORT_COLS
from core.v93_algorithm import apply_v93_algorithm


def check(name, condition, detail):
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def _day_map(latest, rows_by_pid):
    latest_day = date.fromisoformat(latest)
    out = {}
    for offset in range(7):
        day = (latest_day - timedelta(days=offset)).isoformat()
        records = []
        for pid, daily_rows in rows_by_pid.items():
            row = daily_rows[offset] if offset < len(daily_rows) else {}
            record = {
                "宝贝ID": pid,
                "商品名称": f"商品{pid}",
                "淘宝类目": "连衣裙",
                "GMV": row.get("gmv", 0.0),
                "付费": row.get("spend", 0.0),
                "推广成交金额": row.get("ad_gmv", row.get("gmv", 0.0) * 0.5),
                "推广加购数": row.get("ad_carts", 0.0),
                "商品访客数": row.get("vis", 0.0),
                "商品加购件数": row.get("carts", 0.0),
                "件单价": row.get("price", 120.0),
                "推广明细": row.get("detail", "计划ID:1"),
            }
            for source, target in {
                "v93_spend": "v9.3推广花费",
                "v93_ad_gmv": "v9.3推广成交金额",
                "v93_ad_carts": "v9.3推广加购数",
                "v93_ad_vis": "v9.3推广访客数",
            }.items():
                if source in row:
                    record[target] = row[source]
            records.append(record)
        out[day] = (pd.DataFrame(records), [], [], 0, set())
    return out


def test_builtin_v93_threshold_labels():
    latest = "2026-07-09"
    rows_by_pid = {
        "focus": [
            {"vis": 30, "carts": 5, "gmv": 500, "spend": 10},
            {"vis": 10, "carts": 3, "gmv": 200, "spend": 20},
            {"vis": 10, "carts": 3, "gmv": 200, "spend": 20},
            {"vis": 10, "carts": 2, "gmv": 100, "spend": 20},
            {"vis": 10, "carts": 2, "gmv": 100, "spend": 10},
            {"vis": 10, "carts": 1, "gmv": 100, "spend": 10},
            {"vis": 10, "carts": 1, "gmv": 100, "spend": 10},
        ],
        "recent": [
            {"vis": 100, "carts": 2, "gmv": 100, "spend": 20},
            {"vis": 100, "carts": 11, "gmv": 200, "spend": 40},
            {"vis": 100, "carts": 11, "gmv": 200, "spend": 40},
        ],
        "scale": [
            {"vis": 100, "carts": 30, "gmv": 900, "spend": 300},
            {"vis": 80, "carts": 15, "gmv": 300, "spend": 150},
            {"vis": 80, "carts": 15, "gmv": 300, "spend": 150},
            {"vis": 80, "carts": 15, "gmv": 300, "spend": 150},
        ],
        "reb": [
            {"vis": 60, "carts": 5, "gmv": 1400, "spend": 70, "ad_gmv": 300},
        ],
        "base": [
            {"vis": 20, "carts": 1, "gmv": 100, "spend": 100, "ad_gmv": 90},
        ],
    }
    current = _day_map(latest, rows_by_pid)[latest][0]
    scored = apply_v93_algorithm(current, _day_map(latest, rows_by_pid), latest)
    by_pid = scored.set_index("宝贝ID")

    check(
        "focus threshold produces 明日之星",
        "明日之星" in by_pid.loc["focus", "v9.3算法命中"],
        by_pid.loc["focus", "v9.3算法命中"],
    )
    check(
        "recent threshold supplements non-focus item",
        "近3日潜力" in by_pid.loc["recent", "v9.3算法命中"],
        by_pid.loc["recent", "v9.3算法命中"],
    )
    check(
        "scale threshold detects burst item",
        "爆发" in by_pid.loc["scale", "v9.3算法命中"],
        by_pid.loc["scale", "v9.3算法命中"],
    )
    check(
        "rebalance threshold detects add-budget item",
        "加预算" in by_pid.loc["reb", "v9.3算法命中"],
        by_pid.loc["reb", "v9.3算法命中"],
    )


def test_builtin_v93_columns_are_exported():
    for col in ["v9.3算法命中", "v9.3算法分", "v9.3算法原因"]:
        check("export includes built-in v9.3 column", col in EXPORT_COLS, col)


def test_original_v93_rebalance_buckets_and_spillover():
    latest = "2026-07-09"
    rows_by_pid = {
        "add": [
            {"vis": 500, "carts": 20, "ad_carts": 10, "gmv": 3000, "ad_gmv": 500, "spend": 100, "price": 120},
        ] * 7,
        "observe": [
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 1000, "ad_gmv": 500, "spend": 100, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 1000, "ad_gmv": 500, "spend": 100, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 1000, "ad_gmv": 500, "spend": 100, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 950, "ad_gmv": 500, "spend": 10, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 950, "ad_gmv": 500, "spend": 10, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 950, "ad_gmv": 500, "spend": 10, "price": 120},
            {"vis": 600, "carts": 10, "ad_carts": 5, "gmv": 950, "ad_gmv": 500, "spend": 10, "price": 120},
        ],
        "protect": [
            {"vis": 100, "carts": 7, "ad_carts": 7, "gmv": 100, "ad_gmv": 90, "spend": 20, "price": 120},
        ] * 7,
        "reduce": [
            {"vis": 100, "carts": 5, "ad_carts": 2, "gmv": 120, "ad_gmv": 100, "spend": 100, "price": 120},
        ] * 7,
        "base": [
            {"vis": 100, "carts": 5, "ad_carts": 5, "gmv": 1000, "ad_gmv": 900, "spend": 100, "price": 120},
        ] * 7,
    }
    scored = apply_v93_algorithm(_day_map(latest, rows_by_pid)[latest][0], _day_map(latest, rows_by_pid), latest)
    by_pid = scored.set_index("宝贝ID")

    check("rebalance high ROI uses original 加预算 bucket", "加预算" in by_pid.loc["add", "v9.3算法命中"], by_pid.loc["add", "v9.3算法命中"])
    check("saturated good item uses original 饱和观察 bucket", "饱和观察" in by_pid.loc["observe", "v9.3算法命中"], by_pid.loc["observe", "v9.3算法命中"])
    check("low add-to-cart-cost item uses original 保护持有 bucket", "保护持有" in by_pid.loc["protect", "v9.3算法命中"], by_pid.loc["protect", "v9.3算法命中"])
    check("low ROI high add-to-cart-cost item uses original 削减 bucket", "削减" in by_pid.loc["reduce", "v9.3算法命中"], by_pid.loc["reduce", "v9.3算法命中"])
    check("spillover section uses fixed 7 day spend and organic GMV", "溢出" in by_pid.loc["add", "v9.3算法命中"], by_pid.loc["add", "v9.3算法命中"])
    reasons = " | ".join(by_pid["v9.3算法原因"].astype(str).tolist())
    check("algorithm reasons use 加购成本 label", "加购成本" in reasons, reasons)
    check("algorithm reasons no longer expose CPA label", "CPA" not in reasons, reasons)


def test_original_v93_fields_override_wujie_attribution_fields():
    current = pd.DataFrame([{
        "宝贝ID": "strict",
        "商品名称": "严格口径",
        "淘宝类目": "休闲裤",
        "GMV": 3000.0,
        "付费": 500.0,
        "推广成交金额": 2500.0,
        "推广加购数": 10.0,
        "v9.3推广花费": 300.0,
        "v9.3推广成交金额": 300.0,
        "v9.3推广加购数": 100.0,
        "v9.3推广访客数": 50.0,
        "商品访客数": 500.0,
        "商品加购件数": 60.0,
        "件单价": 120.0,
        "推广明细": "",
    }])
    scored = apply_v93_algorithm(current)
    reason = scored.loc[0, "v9.3算法原因"]
    check("strict v9.3 ad GMV overrides wujie attribution GMV", "自然GMV占比90%" in reason, reason)


if __name__ == "__main__":
    test_builtin_v93_threshold_labels()
    test_builtin_v93_columns_are_exported()
    test_original_v93_rebalance_buckets_and_spillover()
    test_original_v93_fields_override_wujie_attribution_fields()
    print("v9.3 algorithm regression checks passed")
