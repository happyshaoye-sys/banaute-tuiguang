# -*- coding: utf-8 -*-
"""Regression checks for export and period denominator contracts."""
import io

import pandas as pd

from core.exporter import export_all_sheets
from core.periods import aggregate_period_frame


def check(name, condition, detail):
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def test_export_category_refund_uses_erp_rate():
    df = pd.DataFrame([
        {
            "宝贝ID": "p1",
            "淘宝类目": "连衣裙",
            "GMV": 100.0,
            "付费": 10.0,
            "成功退款金额": 10.0,
            "退款率": 0.50,
        },
        {
            "宝贝ID": "p2",
            "淘宝类目": "连衣裙",
            "GMV": 300.0,
            "付费": 30.0,
            "成功退款金额": 0.0,
            "退款率": 0.10,
        },
    ])

    workbook = export_all_sheets(df)
    cat = pd.read_excel(io.BytesIO(workbook), sheet_name="品类分析")
    actual = float(cat.loc[0, "退款率"])
    expected = (100.0 * 0.50 + 300.0 * 0.10) / 400.0
    check(
        "导出品类退款率使用ERP/cohort加权口径",
        abs(actual - expected) < 1e-9,
        f"actual={actual}, expected={expected}",
    )


def test_period_daily_spend_uses_selected_days():
    day_rows = pd.DataFrame([
        {"宝贝ID": "p1", "GMV": 100.0, "付费": 30.0, "商品访客数": 10, "支付买家数": 1, "支付件数": 1},
        {"宝贝ID": "p1", "GMV": 200.0, "付费": 60.0, "商品访客数": 20, "支付买家数": 2, "支付件数": 2},
        {"宝贝ID": "p1", "GMV": 300.0, "付费": 90.0, "商品访客数": 30, "支付买家数": 3, "支付件数": 3},
    ])

    agg = aggregate_period_frame(day_rows, period_days=3)
    actual = float(agg.loc[0, "日均消耗"])
    expected = 180.0 / 3.0
    check(
        "周期日均消耗按所选天数计算",
        abs(actual - expected) < 1e-9,
        f"actual={actual}, expected={expected}",
    )


if __name__ == "__main__":
    test_export_category_refund_uses_erp_rate()
    test_period_daily_spend_uses_selected_days()
    print("regression checks passed")
