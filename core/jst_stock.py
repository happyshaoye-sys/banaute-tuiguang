"""聚水潭库存快照直连适配器(2026-07-07 新增)。

输入 = 聚水潭每日快照 CSV(SKU×仓级,`聚水潭/库存快照/YYYY-MM/库存快照_YYYY-MM-DD.csv`),
输出 = 款式(货号=i_id)级库存维度表,作为「库存」数据源注入合并管线,
由既有的 货号↔款式编码 关联挂到商品(需同时提供店铺商品资料以获得 商品ID→货号 映射)。
执行表字段「在仓库存」= 本表「在仓库存」列,语义为整款聚水潭经营库存(多链接共享同一池)。

口径与 v9.3 聚水潭链路(build_jst_stock.py)完全一致:
- 经营库存 = GREATEST(0, qty+in_qty+return_qty+customize_1/2/4+allocate+purchase+sale_refund+customize_5 − order_lock),
  先按 SKU 跨仓求和再截断负值;
- 排次品仓 12994511 / 12097444;
- 采购在途 = purchase_qty 求和。
注意:快照不含销量,此路径不产生日销7/可售天数(要可售天数请另喂 jst_stock.csv)。
"""
import os
from datetime import date, timedelta

import pandas as pd

DEFECT_WMS = {"12994511", "12097444"}
_OPER_PLUS = ["qty", "in_qty", "return_qty", "customize_qty_1", "customize_qty_2",
              "customize_qty_4", "allocate_qty", "purchase_qty", "sale_refund_qty",
              "customize_qty_5"]


def find_snapshot(root, date_str, max_back_days=7):
    """优先取分析日当天快照;缺失时向前回退最多 max_back_days 天。
    同一天优先原始 SKU×仓镜像(`库存快照_日期.csv`),其次款式级补数
    (`库存快照_日期_款式级.csv`,由 MCP/数据库聚合导出)。
    返回 (路径, 实际快照日期) 或 (None, None)。"""
    if not root or not os.path.isdir(str(root)):
        return None, None
    d = date.fromisoformat(date_str)
    for i in range(max_back_days + 1):
        cur = d - timedelta(days=i)
        month_dir = os.path.join(str(root), cur.strftime("%Y-%m"))
        for name in (f"库存快照_{cur.isoformat()}.csv",
                     f"库存快照_{cur.isoformat()}_款式级.csv"):
            p = os.path.join(month_dir, name)
            if os.path.isfile(p):
                return p, cur.isoformat()
    return None, None


def load_snapshot_as_stock(path):
    """快照 → 款式级库存维度表(列: 货号/在仓库存/在途)。
    自动识别两种输入:① SKU×仓原始镜像;② 款式级预聚合(i_id/oper/onway,
    聚合口径与本模块一致,由数据库端 SQL 产出)。"""
    head = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
    if "oper" in head.columns and "i_id" in head.columns:
        out = pd.read_csv(path, encoding="utf-8-sig", dtype={"i_id": str})
        out = out.rename(columns={"i_id": "货号", "oper": "在仓库存", "onway": "在途"})
        out["货号"] = out["货号"].astype(str).str.strip()
        out["在仓库存"] = pd.to_numeric(out["在仓库存"], errors="coerce").fillna(0.0)
        out["在途"] = pd.to_numeric(out.get("在途"), errors="coerce").fillna(0.0)
        return out[out["货号"] != ""][["货号", "在仓库存", "在途"]].reset_index(drop=True)

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str,
                     usecols=lambda c: c in set(_OPER_PLUS) | {"order_lock", "wms_co_id",
                                                               "sku_id", "i_id"})
    for col in _OPER_PLUS + ["order_lock"]:
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)
    df = df[~df["wms_co_id"].astype(str).str.strip().isin(DEFECT_WMS)]
    # 编码先 strip 再分组:同一 SKU 的空白变体若分进不同组,GREATEST 截断口径会漂
    df["sku_id"] = df["sku_id"].astype(str).str.strip()
    df["i_id"] = df["i_id"].astype(str).str.strip()

    contrib = df[_OPER_PLUS[0]].copy()
    for col in _OPER_PLUS[1:]:
        contrib = contrib + df[col]
    df["_contrib"] = contrib - df["order_lock"]

    sku = df.groupby("sku_id").agg(_contrib=("_contrib", "sum"),
                                   onway=("purchase_qty", "sum"),
                                   i_id=("i_id", "first"))
    sku["oper"] = sku["_contrib"].clip(lower=0)

    out = (sku.groupby("i_id").agg(在仓库存=("oper", "sum"), 在途=("onway", "sum"))
           .reset_index().rename(columns={"i_id": "货号"}))
    out["货号"] = out["货号"].astype(str).str.strip()
    return out[out["货号"] != ""].reset_index(drop=True)
