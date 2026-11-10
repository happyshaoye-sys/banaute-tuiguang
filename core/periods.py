"""日期区间与周期环比计算。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .schema import safe_div, to_num
from .plan_metrics import (
    CURRENT_ACTIVE_PLAN_COUNT_COLUMN,
    FULL_PLAN_DETAIL_COLUMN,
    PLAN_ACTUAL_DAYS_COLUMN,
    PLAN_COMPLETENESS_COLUMN,
    PLAN_COVERAGE_COLUMN,
    PLAN_DISPLAY_COUNT_COLUMN,
    PLAN_METADATA_COLUMNS,
    PLAN_PERIOD_DAYS_COLUMN,
    PLAN_TOTAL_COUNT_COLUMN,
    current_active_counts_from_latest_snapshot,
    summarize_period_product_plans,
)
from .data_validation import validate_paid_plan_consistency


PERIOD_SPECS = [
    (1, "日环比"),
    (3, "3日环比"),
    (7, "7日环比"),
    (15, "15日环比"),
    (30, "30日环比"),
]

#: v2.66：按月 / 自定义 周期的长度上限（含首尾）
MAX_PERIOD_DAYS = 31

MONTH_LABEL = "月环比"
CUSTOM_LABEL = "自定义环比"


def month_bounds(year_month):
    """把 'YYYY-MM' 解析成该月首末日期（date）。"""
    year_text, month_text = str(year_month).split("-")[:2]
    year, month = int(year_text), int(month_text)
    first = datetime(year, month, 1).date()
    if month == 12:
        nxt = datetime(year + 1, 1, 1).date()
    else:
        nxt = datetime(year, month + 1, 1).date()
    return first, nxt - timedelta(days=1)


def resolve_month_range(year_month, base_date):
    """按月口径：历史月取整月；当月取「月初~分析日」（不越界到未来）。"""
    first, last = month_bounds(year_month)
    end = last if last < base_date else base_date
    if end < first:
        end = first
    return first, end


def validate_custom_range(start_date, end_date, max_days=MAX_PERIOD_DAYS):
    """校验自定义区间，返回 (start, end, days, error)。"""
    try:
        start = datetime.strptime(str(start_date), "%Y-%m-%d").date()
        end = datetime.strptime(str(end_date), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None, 0, "日期格式应为 YYYY-MM-DD"
    if end < start:
        return None, None, 0, "结束日期不能早于开始日期"
    days = (end - start).days + 1
    if days > max_days:
        return None, None, days, f"自定义区间最长 {max_days} 天，当前 {days} 天"
    return start, end, days, ""

COMPARISON_FIELDS = []


def build_period_ranges(date_str, month=None, custom=None):
    """基于单个前端日期生成所有当前周期和对比周期。

    v2.66 新增两种口径（原有 日/3/7/15/30 完全保留）：
      * ``month='YYYY-MM'``  → 追加「月环比」：历史月取整月、当月取月初~分析日；
      * ``custom=(start,end)`` → 追加「自定义环比」：跨度 ≤ MAX_PERIOD_DAYS(31) 天，
        超出或非法则不追加（由调用方提示）。
    对比周期统一取"紧邻当前区间之前、等长"的窗口。
    """
    base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    periods = {}

    def _put(label, cur_start, cur_end, days):
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        periods[label] = {
            "current_start": cur_start.strftime("%Y-%m-%d"),
            "current_end": cur_end.strftime("%Y-%m-%d"),
            "prev_start": prev_start.strftime("%Y-%m-%d"),
            "prev_end": prev_end.strftime("%Y-%m-%d"),
            "days": int(days),
        }

    for n, label in PERIOD_SPECS:
        _put(label, base_date - timedelta(days=n - 1), base_date, n)

    if month:
        month_start, month_end = resolve_month_range(month, base_date)
        if month_end >= month_start:
            _put(MONTH_LABEL, month_start, month_end,
                 (month_end - month_start).days + 1)

    if custom:
        start_date, end_date, days, error = validate_custom_range(*custom)
        if not error:
            _put(CUSTOM_LABEL, start_date, end_date, days)

    return periods


def iter_dates(start_date, end_date):
    """逐日返回 YYYY-MM-DD 字符串。"""
    cur = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while cur <= end:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def aggregate_category_metrics(day_map, days, metrics):
    """从已加载日帧直接汇总品类指标，不触发商品级周期重算或额外 IO。"""
    metric_list = list(dict.fromkeys(metrics))
    result = {metric: {} for metric in metric_list}
    if not metric_list:
        return result

    frames = []
    for day in days:
        entry = day_map.get(day)
        if not entry or entry[0] is None or len(entry[0]) == 0:
            continue
        frame = entry[0]
        if "淘宝类目" not in frame.columns:
            continue
        part = pd.DataFrame({
            "淘宝类目": frame["淘宝类目"].fillna("").astype(str).str.strip(),
        })
        for metric in metric_list:
            part[metric] = (
                pd.to_numeric(frame[metric], errors="coerce").fillna(0.0)
                if metric in frame.columns else 0.0
            )
        frames.append(part)

    if not frames:
        return result

    grouped = pd.concat(frames, ignore_index=True).groupby(
        "淘宝类目", dropna=False
    )[metric_list].sum()
    return {
        metric: grouped[metric].to_dict()
        for metric in metric_list
    }


def aggregate_period_frame(
    df,
    period_days=7,
    actual_days=None,
    period_start=None,
    period_end=None,
):
    """按宝贝累计所选周期，并在完整周期计划池上生成 Top3 展示。"""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if "宝贝ID" not in df.columns:
        return df.copy()

    sum_cols = [
        "GMV", "推广成交金额", "付费", "关键词", "人群", "全站", "店铺直达",
        "MetoX推广花费", "MetoX推广成交金额", "MetoX推广加购数", "MetoX推广加购件数", "MetoX推广访客数",
        "关键词归因成交", "人群归因成交", "全站归因成交", "店铺直达归因成交",
        "商品访客数", "支付买家数", "支付件数", "商品加购件数", "推广加购数",
        "成功退款金额", "搜索引导访客数", "直购数量",
        "展现", "点击",
    ]
    has_full_plan_details = FULL_PLAN_DETAIL_COLUMN in df.columns
    first_cols = ["款式主图", "货号", "商品名称", "统计日期", "淘宝类目", "创建时间"]
    if not has_full_plan_details:
        # 兼容历史/手工 DataFrame；新管道由全量商品×PlanID数据重新生成推广明细。
        first_cols.append("推广明细")
    stable_numeric_cols = ["在仓库存", "在途", "成本价（含税）", "日销7", "日销3", "日销15",
                           "花费3", "花费7", "花费15", "退款率"]
    numeric_cols = set(sum_cols + stable_numeric_cols)

    work = df.copy()
    date_values = []
    if "_数据日期" in work.columns:
        date_values = sorted({
            str(value).strip() for value in work["_数据日期"].dropna()
            if str(value).strip() not in ('', 'nan', 'None')
        })
    expected_days = max(int(period_days or 1), 1)
    loaded_days = (
        int(actual_days)
        if actual_days is not None
        else (len(date_values) if date_values else expected_days)
    )
    scope_start = str(period_start or (date_values[0] if date_values else ''))
    scope_end = str(period_end or (date_values[-1] if date_values else ''))
    current_count_date = date_values[-1] if date_values else scope_end
    current_active_counts = current_active_counts_from_latest_snapshot(
        work,
        latest_date=current_count_date,
    )

    plan_summaries = []
    if has_full_plan_details:
        for product_id, product_rows in work.groupby("宝贝ID", sort=False):
            plan_summaries.append({
                '宝贝ID': product_id,
                **summarize_period_product_plans(
                    product_rows,
                    period_days=expected_days,
                    actual_days=loaded_days,
                    period_start=scope_start,
                    period_end=scope_end,
                    top_n=3,
                ),
            })
    for col in numeric_cols:
        if col in work.columns:
            # 退款率需保留 NaN(聚水潭未覆盖=无数据≠0%),to_num 会把 NaN 压成 0
            if col == "退款率":
                work[col] = pd.to_numeric(work[col], errors="coerce")
            else:
                work[col] = _numeric_series(work[col])

    agg = {}
    for col in sum_cols:
        if col in work.columns:
            agg[col] = "sum"
    for col in first_cols:
        if col in work.columns:
            agg[col] = _first_non_empty
    for col in stable_numeric_cols:
        if col in work.columns:
            agg[col] = "max"

    grouped = work.groupby("宝贝ID", as_index=False).agg(agg)
    if plan_summaries:
        grouped = grouped.merge(pd.DataFrame(plan_summaries), on="宝贝ID", how="left")
    elif has_full_plan_details:
        for column in PLAN_METADATA_COLUMNS:
            if column not in grouped.columns:
                grouped[column] = '' if column == FULL_PLAN_DETAIL_COLUMN else 0
    if not current_active_counts.empty:
        if CURRENT_ACTIVE_PLAN_COUNT_COLUMN in grouped.columns:
            grouped = grouped.drop(columns=[CURRENT_ACTIVE_PLAN_COUNT_COLUMN])
        grouped = grouped.merge(current_active_counts, on="宝贝ID", how="left")
    elif CURRENT_ACTIVE_PLAN_COUNT_COLUMN not in grouped.columns:
        grouped[CURRENT_ACTIVE_PLAN_COUNT_COLUMN] = 0.0

    for col in sum_cols + stable_numeric_cols:
        if col not in grouped.columns:
            grouped[col] = float("nan") if col == "退款率" else 0.0
        elif col == "退款率":
            grouped[col] = pd.to_numeric(grouped[col], errors="coerce")
        else:
            grouped[col] = _numeric_series(grouped[col])

    for col in (
        PLAN_TOTAL_COUNT_COLUMN,
        PLAN_DISPLAY_COUNT_COLUMN,
        PLAN_COVERAGE_COLUMN,
        PLAN_COMPLETENESS_COLUMN,
        PLAN_PERIOD_DAYS_COLUMN,
        PLAN_ACTUAL_DAYS_COLUMN,
        CURRENT_ACTIVE_PLAN_COUNT_COLUMN,
    ):
        if col in grouped.columns:
            grouped[col] = _numeric_series(grouped[col])

    gmv = grouped["GMV"]
    spend = grouped["付费"]
    visitors = grouped["商品访客数"]
    buyers = grouped["支付买家数"]
    units = grouped["支付件数"]
    refund = grouped["成功退款金额"]
    cost = grouped["成本价（含税）"]
    add_cart = grouped["商品加购件数"]
    views = grouped["展现"]
    clicks = grouped["点击"]

    # 退款率=聚水潭 30日cohort 唯一口径(与 merge._finalize_result 一致),
    # 上方清洗已保留 NaN,此处无需再转换
    # 双投产口径(与 merge._finalize_result 一致):推广投产=归因成交÷花费,链接投产=GMV÷花费
    grouped["推广投产"] = _ratio_series(grouped["推广成交金额"], spend)
    grouped["推广实际投产"] = grouped["推广投产"] * (1 - grouped["退款率"].fillna(0.0))
    grouped["直购成本"] = _ratio_series(spend, grouped["直购数量"])
    grouped["件单价"] = _ratio_series(gmv, units)
    grouped["利润率"] = _ratio_series(grouped["件单价"] - cost, grouped["件单价"])
    grouped["预估利润"] = (gmv - refund) * grouped["利润率"]
    grouped["倍率"] = _ratio_series(grouped["件单价"], cost)
    grouped["商品支付转化率"] = _ratio_series(buyers, visitors)
    # 周期切换后必须用累计分子÷累计分母重算，不累加或平均每日比率。
    grouped["商品加购率"] = _ratio_series(add_cart, visitors)
    grouped["推广加购率"] = _ratio_series(
        grouped["MetoX推广加购件数"], grouped["MetoX推广访客数"]
    )
    grouped["访客平均价值"] = _ratio_series(gmv, visitors)
    grouped["购物车价值"] = _ratio_series(gmv, add_cart)
    grouped["链接投产"] = _ratio_series(gmv, spend)
    grouped["链接实际投产"] = grouped["链接投产"] * (1 - grouped["退款率"].fillna(0.0))
    days = max(int(period_days or 1), 1)
    grouped["日均消耗"] = spend / days
    grouped["点击率"] = _ratio_series(clicks, views)
    grouped["转化率"] = _ratio_series(buyers, clicks)
    grouped["CPC"] = _ratio_series(spend, clicks)
    grouped["CPM"] = _ratio_series(spend, views) * 1000
    daily_sales = grouped["日销7"]
    grouped["可售天数"] = (grouped["在仓库存"] / daily_sales.where(daily_sales > 0)).round(1)
    if CURRENT_ACTIVE_PLAN_COUNT_COLUMN in grouped.columns and "推广明细" in grouped.columns:
        columns = [column for column in grouped.columns if column != CURRENT_ACTIVE_PLAN_COUNT_COLUMN]
        insert_at = columns.index("推广明细")
        grouped = grouped.loc[:, columns[:insert_at] + [CURRENT_ACTIVE_PLAN_COUNT_COLUMN] + columns[insert_at:]]

    validate_paid_plan_consistency(grouped, '周期累计')
    return grouped


def apply_period_comparisons(df, comparisons):
    """把每个周期的累计环比写回当前执行表。"""
    out = df.copy()
    if "宝贝ID" not in out.columns:
        return out

    for label, payload in comparisons.items():
        cur = payload.get("current")
        prev = payload.get("previous")
        suffix = label
        cur_map = _frame_by_pid(cur)
        prev_map = _frame_by_pid(prev)

        for metric_name, source_col in COMPARISON_FIELDS:
            col_name = f"{metric_name}_{suffix}"
            out[col_name] = out["宝贝ID"].apply(
                lambda pid: _compare_metric(pid, source_col, cur_map, prev_map)
            )

        if label == "7日环比":
            out["上周期金额"] = out["宝贝ID"].apply(lambda pid: _lookup_metric(pid, "GMV", prev_map))
            out["上周期付费"] = out["宝贝ID"].apply(lambda pid: _lookup_metric(pid, "付费", prev_map))
            out["上周期人群"] = out["宝贝ID"].apply(lambda pid: _lookup_metric(pid, "人群", prev_map))
            out["上周期关键词"] = out["宝贝ID"].apply(lambda pid: _lookup_metric(pid, "关键词", prev_map))
            out["上周期全站"] = out["宝贝ID"].apply(lambda pid: _lookup_metric(pid, "全站", prev_map))

    return out


def _ratio_series(num, den):
    n = _numeric_series(num)
    d = _numeric_series(den)
    return n.where(d != 0, 0.0) / d.where(d != 0, 1.0)


def _numeric_series(values):
    series = pd.Series(values)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors='coerce').fillna(0.0).astype(float)
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace(r'[,\s¥￥$%]', '', regex=True)
    )
    return pd.to_numeric(cleaned, errors='coerce').fillna(0.0).astype(float)


def _first_non_empty(values):
    for value in values:
        if pd.notna(value) and str(value).strip() not in ("", "nan", "None"):
            return value
    return ""


def _frame_by_pid(df):
    if df is None or len(df) == 0 or "宝贝ID" not in df.columns:
        return {}
    return {str(row["宝贝ID"]): row for _, row in df.iterrows()}


def _lookup_metric(pid, source_col, row_map):
    row = row_map.get(str(pid))
    if row is None:
        return 0.0
    return to_num(row.get(source_col, 0))


def _compare_metric(pid, source_col, cur_map, prev_map):
    cur_val = _lookup_metric(pid, source_col, cur_map)
    prev_val = _lookup_metric(pid, source_col, prev_map)
    return safe_div(cur_val - prev_val, prev_val)


ROLLING_SALES_SPECS = [
    (3, "日销3", "支付件数"),
    # 日销7 原为聚水潭库存源"近7日日均销量";款式级快照直连后该列无来源恒空,
    # 2026-07-10 改为与日销3/15 同族的滚动口径(生意参谋04 支付件数),有库存源值也统一覆盖
    (7, "日销7", "支付件数"),
    (15, "日销15", "支付件数"),
]
ROLLING_VISITOR_SPECS = [
    (3, "日均访客3", "商品访客数"),
    (7, "日均访客7", "商品访客数"),
    (15, "日均访客15", "商品访客数"),
]
ROLLING_SPEND_SPECS = [
    (3, "花费3", "付费"),
    (7, "花费7", "付费"),
    (15, "花费15", "付费"),
]


def _window_metric_sums(day_map, end_date, window_days, metric):
    """近 N 个完整日(含 end_date)按宝贝ID求和。缺失日跳过(该日贡献 0)。"""
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    start = (end - timedelta(days=window_days - 1)).isoformat()
    frames = []
    for day in iter_dates(start, end_date):
        entry = day_map.get(day)
        if not entry or entry[0] is None or len(entry[0]) == 0:
            continue
        frame = entry[0]
        if "宝贝ID" not in frame.columns or metric not in frame.columns:
            continue
        part = frame[["宝贝ID", metric]].copy()
        part["宝贝ID"] = part["宝贝ID"].astype(str).str.strip()
        part[metric] = pd.to_numeric(part[metric], errors="coerce").fillna(0.0)
        frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["宝贝ID", metric])
    return frames[0] if len(frames) == 1 else (
        pd.concat(frames, ignore_index=True).groupby("宝贝ID", as_index=False)[metric].sum()
    )


def apply_rolling_daily_metrics(df, day_map, date_str):
    """挂载滚动日均字段(固定以分析日为终点,不受主表周期口径影响)。

    - 日销3/日销7/日销15: 生意参谋04 支付件数, 近 N 日累计 ÷ N
    - 日均访客3/日均访客7/日均访客15: 生意参谋04 商品访客数, 近 N 日累计 ÷ N
    - 花费3/花费7/花费15: 无界15天转化行 付费, 近 N 日累计 ÷ N
    """
    if df is None or len(df) == 0 or "宝贝ID" not in df.columns:
        return df
    out = df.copy()
    out["宝贝ID"] = out["宝贝ID"].astype(str).str.strip()

    rolling_specs = ROLLING_SALES_SPECS + ROLLING_VISITOR_SPECS + ROLLING_SPEND_SPECS
    for window_days, out_col, metric in rolling_specs:
        summed = _window_metric_sums(day_map, date_str, window_days, metric)
        if summed.empty:
            out[out_col] = 0.0
            continue
        merged = out[["宝贝ID"]].merge(summed, on="宝贝ID", how="left")
        out[out_col] = (merged[metric].fillna(0.0) / window_days).astype(float)

    # 日销7 改口径后重算可售天数(此前在 aggregate_period_frame 用旧日销7 算,彼时恒空)
    if "在仓库存" in out.columns:
        stock = _numeric_series(out["在仓库存"])
        daily = _numeric_series(out["日销7"])
        out["可售天数"] = (stock / daily.where(daily > 0)).round(1)

    return out
