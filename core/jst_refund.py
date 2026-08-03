"""聚水潭退款率直连(2026-07-10)。

口径与 MetoX profit_audit 一致:
- cohort 30 日: 下单日 ∈ [分析日-29, 分析日]
- 退款率 = SUM(refund_amount) / SUM(amount)
- 销售排除: 非分销/非唯品/非线上发货/非拆分合并

输入 = `<退款率快照根目录>/YYYY-MM/退款率_YYYY-MM-DD.csv`(列: 宝贝ID, 退款率)
快照按 PID 混装多店数据(PID 全局唯一,店铺间无交集):
女装旗舰店 15031784 + 集团旗舰店 18469727(2026-07-10 起)。
输出 = 商品资料源注入, 挂载到执行表 `退款率` 列。
2026-07-10 起为退款率唯一来源(不再回退生意参谋 成功退款金额÷GMV);
快照未覆盖的品(近30日无订单)留空(NaN)。
"""
import os
from datetime import date, timedelta

import pandas as pd

from .data_validation import validate_required_columns

COHORT_DAYS = 30
SHOP_IDS = ("15031784", "18469727")


def find_refund_snapshot(root, date_str, max_back_days=7):
    """取分析日退款率快照;缺失时向前回退最多 max_back_days 天。"""
    if not root or not os.path.isdir(str(root)):
        return None, None
    d = date.fromisoformat(date_str)
    for i in range(max_back_days + 1):
        cur = d - timedelta(days=i)
        month_dir = os.path.join(str(root), cur.strftime("%Y-%m"))
        path = os.path.join(month_dir, f"退款率_{cur.isoformat()}.csv")
        if os.path.isfile(path):
            return path, cur.isoformat()
    return None, None


def load_refund_rates(path):
    """CSV → 宝贝ID/退款率 DataFrame。"""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"宝贝ID": str})
    validate_required_columns(
        df.columns,
        {"宝贝ID", "退款率"},
        f"聚水潭退款率快照 {path}",
    )
    out = df[["宝贝ID", "退款率"]].copy()
    out["宝贝ID"] = out["宝贝ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out["退款率"] = pd.to_numeric(out["退款率"], errors="coerce")
    out = out.dropna(subset=["宝贝ID"]).drop_duplicates(subset=["宝贝ID"], keep="last")
    return out[["宝贝ID", "退款率"]].reset_index(drop=True)
