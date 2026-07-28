"""分析函数：KPI、品类、渠道、库存"""
import pandas as pd
import numpy as np
from .schema import safe_div, to_num


def _s(df, col):
    """安全求和：列不存在返回 0"""
    return float(df[col].sum()) if col in df.columns else 0.0


def _f(df, col):
    """安全过滤：列不存在返回空 DataFrame，并注入该列为 0"""
    if col not in df.columns:
        return pd.DataFrame(columns=df.columns.tolist() + [col])
    return df[df[col] > 0]


def calc_kpis(df, plan_ids):
    """计算6个核心指标"""
    gmv = _s(df, 'GMV')
    spend = _s(df, '付费')
    return {
        'GMV': round(gmv, 2),
        '本期花费': round(spend, 2),
        '本期在推广计划': len(plan_ids),
        '店铺ROI': round(safe_div(gmv, spend), 2),
        '在推广商品数量': int((df['付费'] > 0).sum()) if '付费' in df.columns else 0,
        '加购件数': int(_s(df, '商品加购件数')),
        '关键词花费': round(_s(df, '关键词'), 2),
        '人群花费': round(_s(df, '人群'), 2),
        '全站花费': round(_s(df, '全站'), 2),
        '店铺直达花费': round(_s(df, '店铺直达'), 2),
    }


KPI_MOM_KEYS = (
    'GMV', '本期花费', '本期在推广计划',
    '店铺ROI', '在推广商品数量', '加购件数',
)


def calc_kpi_mom_percent(current_kpis, previous_kpis):
    """计算看板6个核心指标的环比百分数；上期为0时沿用项目除零口径返回0。"""
    return {
        key: round(
            safe_div(
                float(current_kpis.get(key, 0)) - float(previous_kpis.get(key, 0)),
                float(previous_kpis.get(key, 0)),
            ) * 100,
            2,
        )
        for key in KPI_MOM_KEYS
    }


def grade_distribution(df):
    """等级分布（6张卡片数据）"""
    grades = []
    total = len(df)
    if total == 0: return grades
    total_gmv = _s(df, 'GMV')
    total_vis = _s(df, '商品访客数')
    total_spend = _s(df, '付费')
    for g in ['S','A+','A','B','C','D']:
        if '综合等级' not in df.columns: break
        sub = df[df['综合等级'] == g]
        if len(sub) > 0:
            grades.append({
                'grade': g, 'cnt': len(sub),
                'pct': f'{len(sub)/total*100:.1f}%',
                'gmv_pct': f'{_s(sub,"GMV")/total_gmv*100:.1f}%' if total_gmv else '0%',
                'vis_pct': f'{_s(sub,"商品访客数")/total_vis*100:.1f}%' if total_vis else '0%',
                'spend_pct': f'{_s(sub,"付费")/total_spend*100:.1f}%' if total_spend else '0%',
            })
    return grades


def channel_summary(df):
    """分渠道汇总"""
    return [
        {'name': '关键词', 'val': _s(df, '关键词')},
        {'name': '人群', 'val': _s(df, '人群')},
        {'name': '全站', 'val': _s(df, '全站')},
        {'name': '店铺直达', 'val': _s(df, '店铺直达')},
    ]


def segment_summary(df):
    """商品分层明细（按组合聚合）。
    v9.3信号(2026-07-11 新增):分组表每行是一批商品,该列汇总组内各信号的命中商品数
    (如 `明日之星×12 | 爆发×3`,按命中数降序;一品多标签则分别计数),无信号留空。"""
    if '组合' not in df.columns or '综合等级' not in df.columns:
        return []
    group_keys = ['组合', '综合等级', '商品定位', '对应操作']
    agg = df.groupby(group_keys).agg(
        cnt=('宝贝ID','count'), gmv=('GMV','sum'), spend=('付费','sum')
    ).reset_index().sort_values('cnt', ascending=False)

    sig_map = {}
    builtin_sig_map = {}
    if 'v9.3信号' in df.columns:
        for keys, sub in df.groupby(group_keys):
            label_cnt = {}
            for v in sub['v9.3信号'].dropna():
                for lbl in str(v).split('|'):
                    lbl = lbl.strip()
                    if lbl:
                        label_cnt[lbl] = label_cnt.get(lbl, 0) + 1
            sig_map[keys] = ' | '.join(
                f'{lbl}×{n}' for lbl, n in
                sorted(label_cnt.items(), key=lambda x: (-x[1], x[0])))
    if 'v9.3算法命中' in df.columns:
        for keys, sub in df.groupby(group_keys):
            label_cnt = {}
            for v in sub['v9.3算法命中'].dropna():
                for lbl in str(v).split('|'):
                    lbl = lbl.strip()
                    if lbl:
                        label_cnt[lbl] = label_cnt.get(lbl, 0) + 1
            builtin_sig_map[keys] = ' | '.join(
                f'{lbl}×{n}' for lbl, n in
                sorted(label_cnt.items(), key=lambda x: (-x[1], x[0])))

    from .schema import ACTION_RULES
    result = []
    for _, r in agg.iterrows():
        act = ACTION_RULES.get(r['对应操作'], {'action': r['对应操作'], 'budget': '', 'focus': ''})
        result.append({
            '组合': r['组合'], '综合等级': r['综合等级'], '商品定位': r['商品定位'],
            '对应操作': r['对应操作'], '操作建议': act['action'],
            '预算建议': act['budget'], '关注重点': act['focus'],
            'v9.3信号': sig_map.get(tuple(r[k] for k in group_keys), ''),
            'v9.3算法命中': builtin_sig_map.get(tuple(r[k] for k in group_keys), ''),
            '商品数': int(r['cnt']), 'GMV': float(r['gmv']),
            '付费': round(float(r['spend']), 2),
        })
    return result


def category_analysis(df, cat_ctx=None):
    """品类分析（两张表）。
    cat_ctx(可选,2026-07-10 新增): 品类级环比上下文,由 app 按所选时间周期构建:
      {'sel_prev_spend': {类目: 上一周期付费}, 'h7_cur_gmv': {类目: 7日窗GMV},
       'h7_prev_gmv': {类目: 上一7日窗GMV},
       'h7_prev_spend|keyword|crowd|omni': {类目: 上一7日窗渠道金额}}
    - 基础表新增 付费占比(=品类付费/品类GMV,用户定义口径)与 付费环比((当期-上期)/上期,基准0记0);
    - 投放表新增 7日GMV环比(7日窗口品类GMV环比);
    - '7天GMV'优先使用上一7日窗口的完整品类GMV,避免只汇总本期仍存在商品的上周期金额。"""
    if '淘宝类目' not in df.columns or len(df) == 0:
        return {'basic':[], 'invest':[], 'total':{}}
    ctx = cat_ctx or {}
    sel_prev_spend = ctx.get('sel_prev_spend') or {}
    h7_cur_gmv = ctx.get('h7_cur_gmv') or {}
    h7_prev_gmv = ctx.get('h7_prev_gmv') or {}
    has_complete_h7_prev = ctx.get('h7_prev_gmv') is not None
    h7_prev_spend = ctx.get('h7_prev_spend') or {}
    h7_prev_keyword = ctx.get('h7_prev_keyword') or {}
    h7_prev_crowd = ctx.get('h7_prev_crowd') or {}
    h7_prev_omni = ctx.get('h7_prev_omni') or {}
    has_complete_h7_channels = all(
        ctx.get(key) is not None for key in (
            'h7_prev_spend', 'h7_prev_keyword', 'h7_prev_crowd', 'h7_prev_omni',
        )
    )

    def _mom(cur, prev):
        return f'{safe_div(cur - prev, prev)*100:.1f}%' if prev else '0.0%'

    # 品类退款率=GMV加权聚水潭cohort退款率(2026-07-10 统一口径,不再用成功退款金额/GMV);
    # 未覆盖品(退款率NaN,近30日无订单)权重按0计,其当期GMV≈0影响可忽略
    work = df.copy()
    if '退款率' in work.columns:
        work['_refund_w'] = pd.to_numeric(work['退款率'], errors='coerce').fillna(0.0) \
            * pd.to_numeric(work['GMV'], errors='coerce').fillna(0.0)
    else:
        work['_refund_w'] = 0.0
    for col in ('上周期金额', '上周期付费', '上周期关键词', '上周期人群', '上周期全站'):
        if col not in work.columns:
            work[col] = 0.0

    cat_agg = work.groupby('淘宝类目').agg(
        cnt=('宝贝ID','count'), gmv=('GMV','sum'), spend=('付费','sum'),
        refund_w=('_refund_w','sum'), visitors=('商品访客数','sum'),
        kw=('关键词','sum'), crowd=('人群','sum'), omni=('全站','sum'),
        d7gmv=('上周期金额','sum'), d7spend=('上周期付费','sum'),
        d7kw=('上周期关键词','sum'), d7crowd=('上周期人群','sum'), d7omni=('上周期全站','sum'),
    ).reset_index().sort_values('gmv', ascending=False)

    total_gmv = _s(df, 'GMV')
    total_spend = _s(df, '付费')
    total = len(df)

    basic, invest = [], []
    for _, r in cat_agg.iterrows():
        cat_key = r['淘宝类目']
        basic.append({
            '品类': cat_key or '未分类', '商品数': int(r['cnt']),
            'GMV': float(r['gmv']), 'GMV占比': f'{safe_div(r["gmv"],total_gmv)*100:.1f}%',
            '付费占比': f'{safe_div(r["spend"],r["gmv"])*100:.1f}%',
            '付费': float(r['spend']),
            '付费环比': _mom(float(r['spend']), float(sel_prev_spend.get(cat_key, 0.0))),
            '商品访客数': int(r['visitors']),
            '退款率': f'{safe_div(r["refund_w"],r["gmv"])*100:.1f}%',
            'SPU价值': float(safe_div(r['gmv'],r['cnt'])),
            'ROI': f'{safe_div(r["gmv"],r["spend"]):.2f}',
        })
        invest.append({
            '品类': cat_key or '未分类',
            'GMV': float(r['gmv']), 'GMV占比': f'{safe_div(r["gmv"],total_gmv)*100:.1f}%',
            '7天GMV': float(h7_prev_gmv.get(cat_key, 0.0))
                    if has_complete_h7_prev else float(r['d7gmv']),
            '7日GMV环比': _mom(float(h7_cur_gmv.get(cat_key, 0.0)),
                            float(h7_prev_gmv.get(cat_key, 0.0))),
            '付费': float(r['spend']),
            '付费占比': f'{safe_div(r["spend"],total_spend)*100:.1f}%' if total_spend else '0%',
            '上周期付费': float(h7_prev_spend.get(cat_key, 0.0))
                    if has_complete_h7_channels else float(r['d7spend']),
            '关键词': float(r['kw']), '人群': float(r['crowd']), '全站': float(r['omni']),
            '上周期关键词': float(h7_prev_keyword.get(cat_key, 0.0))
                    if has_complete_h7_channels else float(r['d7kw']),
            '上周期人群': float(h7_prev_crowd.get(cat_key, 0.0))
                    if has_complete_h7_channels else float(r['d7crowd']),
            '上周期全站': float(h7_prev_omni.get(cat_key, 0.0))
                    if has_complete_h7_channels else float(r['d7omni']),
            'ROI': f'{safe_div(r["gmv"],r["spend"]):.2f}',
        })

    total_row = {
        'cnt': total, 'gmv': float(total_gmv), 'spend': float(total_spend),
        'visitors': int(_s(df, '商品访客数')),
        'refund_rate': f'{safe_div(_s(work,"_refund_w"),total_gmv)*100:.1f}%',
        'spu_value': float(safe_div(total_gmv, total)) if total > 0 else 0,
        'roi': f'{safe_div(total_gmv,total_spend):.2f}',
    }

    return {'basic': basic, 'invest': invest, 'total': total_row}


def refund_risk(df):
    """退款风险（聚水潭cohort退款率>40%,与「⚠高退款」阈值对齐）"""
    if '退款率' not in df.columns: return []
    sub = df[df['退款率'] > 0.4].sort_values('退款率', ascending=False).head(500)
    return [{'宝贝ID': r['宝贝ID'], '商品名称': r.get('商品名称',''), '淘宝类目': r.get('淘宝类目',''),
             'GMV': float(r.get('GMV',0)), '成功退款金额': float(r.get('成功退款金额',0)),
             '退款率': f'{float(r.get("退款率",0))*100:.1f}%', '付费': float(r.get('付费',0)),
             '综合等级': r.get('综合等级',''), '对应操作': r.get('对应操作',''), '风险标签': r.get('风险标签','')}
            for _, r in sub.iterrows()]


def channel_top(df, col, n=30):
    """渠道 TOP N。
    渠道投产(2026-07-11 修正口径):该渠道归因成交÷该渠道花费。此前 ROI 列直接挂
    全渠道推广投产(全渠道归因成交÷全渠道花费),与旁边的单渠道花费口径错配。
    GMV 仍为全链接口径,仅作规模参考,不参与该行 ROI。"""
    sub = _f(df, col).sort_values(col, ascending=False).head(n)
    attr_col = f'{col}归因成交'
    rows = []
    for _, r in sub.iterrows():
        ch_spend = to_num(r.get(col, 0))
        ch_attr = to_num(r.get(attr_col, 0))
        rows.append({
            '宝贝ID': r['宝贝ID'], '商品名称': r.get('商品名称', ''),
            '渠道花费': ch_spend, '渠道成交': round(ch_attr, 2),
            '渠道投产': f'{safe_div(ch_attr, ch_spend):.2f}',
            'GMV': float(r.get('GMV', 0)),
            '综合等级': r.get('综合等级', ''), '对应操作': r.get('对应操作', ''),
        })
    return rows


def inventory_actions(df):
    """库存动作"""
    has_stock = '在仓库存' in df.columns
    has_vis = '商品访客数' in df.columns
    has_transit = '在途' in df.columns
    has_spend = '付费' in df.columns

    hi = df[(df['在仓库存'] > 50) & (df['商品访客数'] < 20)].sort_values('在仓库存', ascending=False).head(200) if (has_stock and has_vis) else pd.DataFrame()
    no = df[(df['在仓库存'] == 0) & (df['在途'] == 0) & (df['付费'] > 0)].head(200) if (has_stock and has_transit and has_spend) else pd.DataFrame()
    def _fmt(sub):
        return [{'宝贝ID': r['宝贝ID'], '商品名称': r.get('商品名称',''), '在仓库存': int(r.get('在仓库存',0)),
                 '在途': int(r.get('在途',0)), '商品访客数': int(r.get('商品访客数',0)),
                 'GMV': float(r.get('GMV',0)), '付费': float(r.get('付费',0)), '综合等级': r.get('综合等级','')}
                for _, r in sub.iterrows()]
    return {'hi_stock': _fmt(hi), 'no_stock': _fmt(no)}
