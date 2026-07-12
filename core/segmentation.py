"""6档分层 + 36宫格矩阵"""
import numpy as np
import pandas as pd
from .field_mapping import standardize_fields, to_num, find_col
from .schema import TIER_LABELS, MATRIX_36, ACTION_RULES


def ensure_segmentation_columns(df):
    """兜底：确保分层所需的数值列都存在，缺失的尝试计算，无法计算则置 0"""
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    # ── 访客平均价值 ──
    if '访客平均价值' not in df.columns:
        gmv_col = find_col(df, ['GMV'])
        uv_col = find_col(df, ['访客数', '商品访客数'])
        if gmv_col and uv_col:
            df['访客平均价值'] = (
                df[gmv_col] / df[uv_col].replace(0, np.nan)
            ).fillna(0)
        else:
            print("[WARNING] 缺少访客平均价值，且无法从 GMV/访客数计算，已默认置为 0")
            df['访客平均价值'] = 0

    # ── 访客数 → 商品访客数（兼容旧列名） ──
    if '商品访客数' not in df.columns and '访客数' in df.columns:
        df['商品访客数'] = df['访客数']

    # ── 兜底列 ──
    for col in ['GMV', '访客数', '商品访客数', '花费', '付费',
                '退款率', '在仓库存', '在途', '推广投产']:
        if col not in df.columns:
            df[col] = 0

    return df


def segment_products(df, traffic_bounds=None, value_bounds=None):
    """6档分层 + 36矩阵定位"""
    # 字段标准化（列名映射 + 数值清洗）
    df = standardize_fields(df)
    # 兜底确保必要列存在
    df = ensure_segmentation_columns(df)
    print("[DEBUG] segment_products df.columns:", list(df.columns))
    if '访客数' in df.columns:
        print("[DEBUG] 访客数字段示例:", df['访客数'].head(3).tolist())

    if len(df) < 3:
        df['流量定位'] = '待改善'
        df['价值定位'] = '待改善'
        return _apply_matrix(df)

    # 安全读取：用 apply(to_num) 处理，不再使用 .values.astype(float)
    visitors = df['商品访客数'].values
    uv_values = df['访客平均价值'].values

    if traffic_bounds is None:
        t_bounds = _default_bounds(visitors[visitors > 0])
    else:
        t_bounds = traffic_bounds

    if value_bounds is None:
        v_bounds = _default_bounds(uv_values[uv_values > 0])
    else:
        v_bounds = value_bounds

    def _tier(val, bounds):
        v = to_num(val)
        for i, b in enumerate(bounds):
            if v <= b: return i
        return len(bounds)

    df['流量定位'] = df['商品访客数'].apply(lambda v: TIER_LABELS[_tier(v, t_bounds)])
    df['价值定位'] = df['访客平均价值'].apply(lambda v: TIER_LABELS[_tier(v, v_bounds)])

    return _apply_matrix(df)


def _default_bounds(values):
    """默认百分位分界点"""
    arr = [v for v in values if v > 0]
    if len(arr) < 6:
        mn, mx = (min(arr) if arr else 0), (max(arr) if arr else 1)
        step = (mx - mn) / 6 or 1
        return [mn + step * i for i in range(1, 6)]
    return [float(np.percentile(arr, p)) for p in [16.67, 33.33, 50, 66.67, 83.33]]


def _apply_matrix(df):
    """应用36宫格矩阵"""
    for col in ['退款率', '在仓库存', '推广投产', '在途', '商品访客数', '付费', '花费', '店铺直达']:
        if col not in df.columns:
            df[col] = 0

    results = []
    for _, row in df.iterrows():
        combo = str(row.get('流量定位','')) + str(row.get('价值定位',''))
        m = MATRIX_36.get(combo, {'grade':'?','pos':'待人工复核','action':'待人工复核'})

        grade = m['grade']; pos = m['pos']; action = m['action']

        refund_rate = to_num(row.get('退款率', 0))
        stock = to_num(row.get('在仓库存', 0))
        roi = to_num(row.get('推广投产', 0))
        spend = to_num(row.get('付费', 0) if row.get('付费') not in (None,'') else row.get('花费', 0))
        risks = []

        # 动量校验:36宫格是静态截面,长周期累计口径会把"爬坡中的新趋势品"误标为衰退预警
        # (高流量×低UV价值)。短期动量向上(日销3>日销7,固定以分析日为终点)时改标"上升验证"。
        if action == '衰退预警' and to_num(row.get('日销3', 0)) > to_num(row.get('日销7', 0)):
            action = '上升验证'
            risks.append('📈动量↑')

        # 阈值按聚水潭30日cohort口径标定(2026-07-10,女装cohort普遍40~60%,
        # 沿用生意参谋口径的0.3会令大半商品被标高退款): >0.5极高 / >0.4高
        if refund_rate > 0.5: risks.append('🚨极高退款')
        elif refund_rate > 0.4:
            risks.append('⚠高退款')
            if action in ('放量','适当放量'): action = '待优化'

        if stock == 0 and to_num(row.get('在途', 0)) == 0: risks.append('🆘缺货')
        elif stock < 10: risks.append('📦库存不足')

        if to_num(row.get('商品访客数', to_num(row.get('访客数', 0)))) < 10 and spend > 0:
            risks.append('👁流量低迷')
        if roi < 1 and spend > 100: risks.append('💸亏损投放')
        if roi > 10: risks.append('⭐高ROI')
        if to_num(row.get('在途', 0)) > 0 and stock < 10: risks.append('🚚在途补货中')

        # P1-P4(低退款线与「⚠高退款」阈值一致,cohort口径 0.4)
        is_low_refund = refund_rate < 0.4
        has_stock = stock >= 10
        spend = to_num(row.get('付费', 0))
        if (grade in ('S','A+')) and action in ('放量','适当放量') and is_low_refund and has_stock:
            priority = 'P1'
        elif grade == 'A' or (grade == 'B' and action in ('成长期','成熟期')):
            priority = 'P2'
        elif ((grade in ('C','D') and action in ('观察期','淘汰'))
              or (not is_low_refund and refund_rate > 0)
              or (not has_stock and stock >= 0)
              or (roi < 1 and spend > 100)):
            priority = 'P4'
        else:
            priority = 'P3'

        act_rule = ACTION_RULES.get(action, {'action': action, 'budget': '', 'focus': ''})

        results.append({
            '流量定位': row.get('流量定位',''), '价值定位': row.get('价值定位',''),
            '组合': combo, '商品定位': pos, '综合等级': grade,
            '商品周期': action, '对应操作': action,
            '推广动作': act_rule['action'], '预算优先级': priority,
            '风险标签': ' '.join(risks),
            '操作': '', '运营备注': '',
        })

    result_df = pd.DataFrame(results)
    for col in result_df.columns:
        df[col] = result_df[col].values

    return df
