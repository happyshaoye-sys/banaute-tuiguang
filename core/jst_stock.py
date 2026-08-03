"""聚水潭库存快照直连适配器(2026-07-07 新增)。

输入 = 聚水潭每日快照 CSV(SKU×仓级,`聚水潭/库存快照/YYYY-MM/库存快照_YYYY-MM-DD.csv`),
输出 = 款式(货号=i_id)级库存维度表,作为「库存」数据源注入合并管线,
由既有的 货号↔款式编码 关联挂到商品(需同时提供店铺商品资料以获得 商品ID→货号 映射)。
执行表字段「在仓库存」= 本表「在仓库存」列,语义为整款聚水潭经营库存(多链接共享同一池)。

口径与 MetoX 聚水潭链路(build_jst_stock.py)完全一致:
- 经营库存 = GREATEST(0, qty+in_qty+return_qty+customize_1/2/4+allocate+purchase+sale_refund+customize_5 − order_lock),
  先按 SKU 跨仓求和再截断负值;
- 排次品仓 12994511 / 12097444;
- 采购在途 = purchase_qty 求和。
注意:快照不含销量,此路径不产生日销7/可售天数(要可售天数请另喂 jst_stock.csv)。
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .data_validation import (
    DataValidationError,
    DataValidationIssue,
    validate_required_columns,
)

DEFECT_WMS = {"12994511", "12097444"}
_OPER_PLUS = ["qty", "in_qty", "return_qty", "customize_qty_1", "customize_qty_2",
              "customize_qty_4", "allocate_qty", "purchase_qty", "sale_refund_qty",
              "customize_qty_5"]
ENRICHED_COLS = ["宝贝ID", "货号", "款式主图", "创建时间", "在仓库存", "在途", "成本价（含税）"]
_PID_ALIASES = ["宝贝ID", "平台店铺款式编码", "平台款式编码", "商品ID", "主体ID"]
_SKU_ALIASES = ["货号", "线上款式编码", "款式编码", "商家编码", "i_id"]
_COST_ALIASES = ["成本价（含税）", "成本价", "初始买断价", "含税成本"]


def _clean_id_series(series):
    return series.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _first_present(columns, names):
    for name in names:
        if name in columns:
            return name
    return None


def _stock_number_for_csv(series):
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)

    def fmt(value):
        if float(value).is_integer():
            return str(int(value))
        return ("%f" % float(value)).rstrip("0").rstrip(".")

    return values.map(fmt)


def _candidate_snapshot_roots(root):
    base = Path(str(root))
    candidates = [
        base,
        base / "库存快照",
        base / "聚水潭" / "库存快照",
        base / "mark0706啊" / "聚水潭" / "库存快照",
    ]
    seen = set()
    out = []
    for cand in candidates:
        key = os.path.normcase(os.path.abspath(str(cand)))
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _normalize_enriched_stock(df):
    out = df.copy()
    pid_col = _first_present(out.columns, _PID_ALIASES)
    sku_col = _first_present(out.columns, _SKU_ALIASES)
    if pid_col and pid_col != "宝贝ID":
        out = out.rename(columns={pid_col: "宝贝ID"})
    if sku_col and sku_col != "货号":
        out = out.rename(columns={sku_col: "货号"})
    cost_col = _first_present(out.columns, _COST_ALIASES)
    if cost_col and cost_col != "成本价（含税）":
        out = out.rename(columns={cost_col: "成本价（含税）"})

    for col in ENRICHED_COLS:
        if col not in out.columns:
            out[col] = "" if col in {"宝贝ID", "货号", "款式主图", "创建时间"} else 0.0

    out["宝贝ID"] = _clean_id_series(out["宝贝ID"])
    out["货号"] = _clean_id_series(out["货号"])
    out["款式主图"] = out["款式主图"].fillna("").astype(str).str.strip()
    out["创建时间"] = out["创建时间"].fillna("").astype(str).str.strip()
    out["在仓库存"] = pd.to_numeric(out["在仓库存"], errors="coerce").fillna(0.0)
    out["在途"] = pd.to_numeric(out["在途"], errors="coerce").fillna(0.0)
    out["成本价（含税）"] = pd.to_numeric(out["成本价（含税）"], errors="coerce").fillna(0.0)
    out = out[(out["宝贝ID"] != "") | (out["货号"] != "")]
    return out[ENRICHED_COLS].reset_index(drop=True)


def find_snapshot(root, date_str, max_back_days=7):
    """优先取分析日当天快照;缺失时向前回退最多 max_back_days 天。
    同一天优先增强/预聚合款式级补数(`库存快照_日期_款式级.csv`),
    其次原始 SKU×仓镜像(`库存快照_日期.csv`)。
    返回 (路径, 实际快照日期) 或 (None, None)。"""
    if not root or not os.path.isdir(str(root)):
        return None, None
    d = date.fromisoformat(date_str)
    roots = _candidate_snapshot_roots(root)
    for i in range(max_back_days + 1):
        cur = d - timedelta(days=i)
        for snap_root in roots:
            month_dir = os.path.join(str(snap_root), cur.strftime("%Y-%m"))
            for name in (f"库存快照_{cur.isoformat()}_款式级.csv",
                         f"库存快照_{cur.isoformat()}.csv"):
                p = os.path.join(month_dir, name)
                if os.path.isfile(p):
                    return p, cur.isoformat()
    return None, None


def load_snapshot_as_stock(path):
    """快照 → 库存维度表。
    自动识别三种输入:① 增强日更快照(宝贝ID/货号/图/创建时间/库存);
    ② SKU×仓原始镜像;③ 款式级预聚合(i_id/oper/onway)。"""
    head = pd.read_csv(path, encoding="utf-8-sig", nrows=0)
    if {"宝贝ID", "货号", "在仓库存"}.issubset(set(head.columns)):
        validate_required_columns(
            head.columns,
            {"宝贝ID", "货号", "在仓库存", "在途"},
            f"聚水潭增强库存快照 {path}",
        )
        out = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        return _normalize_enriched_stock(out)

    if "oper" in head.columns and "i_id" in head.columns:
        validate_required_columns(
            head.columns,
            {"i_id", "oper", "onway"},
            f"聚水潭款式级库存快照 {path}",
        )
        out = pd.read_csv(path, encoding="utf-8-sig", dtype={"i_id": str})
        out = out.rename(columns={"i_id": "货号", "oper": "在仓库存", "onway": "在途"})
        out["货号"] = out["货号"].astype(str).str.strip()
        out["在仓库存"] = pd.to_numeric(out["在仓库存"], errors="coerce").fillna(0.0)
        out["在途"] = pd.to_numeric(out.get("在途"), errors="coerce").fillna(0.0)
        return out[out["货号"] != ""][["货号", "在仓库存", "在途"]].reset_index(drop=True)

    raw_required = set(_OPER_PLUS) | {"order_lock", "wms_co_id", "sku_id", "i_id"}
    if not raw_required.intersection(head.columns):
        raise DataValidationError(DataValidationIssue(
            f"聚水潭库存快照 {path}",
            "无法识别库存快照结构，未发现增强、款式级或SKU仓级必需字段",
        ))
    validate_required_columns(
        head.columns,
        raw_required,
        f"聚水潭SKU仓级库存快照 {path}",
    )
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


def build_enriched_stock_snapshot(snapshot_path, mapping_path):
    """把款式级库存与 pid↔货号映射合成为日更库存快照。"""
    stock = load_snapshot_as_stock(snapshot_path)
    stock = stock[["货号", "在仓库存", "在途"]].drop_duplicates(subset=["货号"], keep="last")

    mapping = pd.read_csv(mapping_path, encoding="utf-8-sig", dtype=str)
    pid_col = _first_present(mapping.columns, _PID_ALIASES)
    sku_col = _first_present(mapping.columns, _SKU_ALIASES)
    if not pid_col or not sku_col:
        raise ValueError("映射文件必须包含宝贝ID/平台店铺款式编码和货号/线上款式编码")

    mapping = mapping.rename(columns={pid_col: "宝贝ID", sku_col: "货号"}).copy()
    for col in ["款式主图", "创建时间", "成本价（含税）"]:
        if col not in mapping.columns:
            mapping[col] = ""
    mapping["宝贝ID"] = _clean_id_series(mapping["宝贝ID"])
    mapping["货号"] = _clean_id_series(mapping["货号"])
    mapping = mapping[mapping["货号"] != ""]
    mapping = mapping.drop_duplicates(subset=["宝贝ID", "货号"], keep="last")

    mapped = mapping[["宝贝ID", "货号", "款式主图", "创建时间", "成本价（含税）"]].merge(stock, on="货号", how="left")
    out = _normalize_enriched_stock(mapped)
    out["在仓库存"] = _stock_number_for_csv(out["在仓库存"])
    out["在途"] = _stock_number_for_csv(out["在途"])
    out["成本价（含税）"] = _stock_number_for_csv(out["成本价（含税）"])
    return out[ENRICHED_COLS]


def write_daily_enriched_snapshot(snapshot_path, mapping_path, snapshot_root, date_str):
    """写入 库存快照/YYYY-MM/库存快照_YYYY-MM-DD_款式级.csv,月份目录自动创建。"""
    date.fromisoformat(date_str)
    out_dir = Path(snapshot_root) / date_str[:7]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"库存快照_{date_str}_款式级.csv"
    df = build_enriched_stock_snapshot(snapshot_path, mapping_path)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, out_path)
    return out_path
