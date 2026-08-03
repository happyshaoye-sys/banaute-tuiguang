"""分析函数：KPI、品类、渠道、库存"""
import re

import pandas as pd
import numpy as np
from .schema import safe_div, to_num


AI_TASK_VIEW_COLUMNS = [
    '执行优先级', '任务优先分', '款式主图', '货号', '链接数', '宝贝ID',
    '商品名称', '淘宝类目', '综合等级', '商品定位', '商品周期', 'MetoX算法命中',
    'AI任务类型', '最终推广策略', '主执行动作', '关键词动作', '人群动作',
    '全站动作', '观察周期', '成功标准', '停止条件', '任务触发原因', '库存提醒',
]

_AI_OPPORTUNITY_LABELS = {'明日之星', '强主推', '近3日潜力', '爆发', '加预算'}
_AI_RISK_LABELS = {'亏损风险', '削减'}
_AI_CORRECTION_LABELS = {'饱和观察', '保护持有', '溢出'}
_AI_LABELS = _AI_OPPORTUNITY_LABELS | _AI_RISK_LABELS | _AI_CORRECTION_LABELS
_AI_GROWTH_CYCLES = {
    '测试期', '成长期', '爆发期', '放量', '适当放量', '重点', '加预算', '上升验证',
}
_AI_DECLINE_CYCLES = {'衰退预警', '衰退期', '清退期', '淘汰'}
_AI_HISTORY_FIELDS = {
    '日销3', '日销7', '日销15', '花费3', '花费7', '花费15', '上周期金额', '上周期付费',
}


def _task_text(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ''
    text = str(value).strip()
    return '' if text in ('nan', 'None') else text


def _task_raw_or_blank(value):
    """原字段透传：只把真正的空值归一为空，不映射、不推断。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ''
    return value


def _task_labels(value):
    text = _task_text(value)
    return {label for label in _AI_LABELS if label in text}


def _task_benchmarks(df):
    spend = df['付费'].map(to_num) if '付费' in df.columns else pd.Series(0.0, index=df.index)
    carts = (
        df['推广加购数'].map(to_num)
        if '推广加购数' in df.columns else pd.Series(0.0, index=df.index)
    )
    cpa = spend[carts > 0] / carts[carts > 0]
    eligible_cpa = cpa[(spend[carts > 0] >= 50) & np.isfinite(cpa)]
    promotion_gmv = (
        df['推广成交金额'].map(to_num)
        if '推广成交金额' in df.columns else pd.Series(0.0, index=df.index)
    )
    return {
        'cpa_p75': float(eligible_cpa.quantile(0.75)) if len(eligible_cpa) >= 10 else 0.0,
        'promotion_roi': safe_div(float(promotion_gmv.sum()), float(spend.sum())),
    }


def _build_product_context(row, columns, benchmarks):
    metox_text = _task_text(row.get('MetoX算法命中', ''))
    v93_text = _task_text(row.get('v9.3信号', ''))
    metox_labels = _task_labels(metox_text)
    v93_labels = _task_labels(v93_text)
    labels = metox_labels | v93_labels
    opportunity = labels & _AI_OPPORTUNITY_LABELS
    risk = labels & _AI_RISK_LABELS
    corrections = labels & _AI_CORRECTION_LABELS

    grade = _task_text(row.get('综合等级', ''))
    position = _task_text(row.get('商品定位', ''))
    cycle = _task_text(row.get('商品周期', ''))
    spend = to_num(row.get('付费', 0))
    ad_gmv = to_num(row.get('推广成交金额', 0))
    ad_carts = to_num(row.get('推广加购数', 0))
    current_link_roi = to_num(row.get('链接投产', 0))
    if current_link_roi <= 0:
        current_link_roi = safe_div(to_num(row.get('GMV', 0)), spend)
    previous_link_roi = safe_div(
        to_num(row.get('上周期金额', 0)), to_num(row.get('上周期付费', 0))
    )
    cpa = safe_div(spend, ad_carts)

    required_columns = {'宝贝ID', '综合等级', '商品定位', '商品周期', 'MetoX算法命中'}
    missing_columns = sorted(required_columns - set(columns))
    missing_values = [
        name for name in ('宝贝ID', '综合等级', '商品定位', '商品周期')
        if not _task_text(row.get(name, ''))
    ]
    history_missing = bool(labels) and not bool(_AI_HISTORY_FIELDS & set(columns))
    data_issues = []
    if missing_columns:
        data_issues.append('缺少关键列:' + '/'.join(missing_columns))
    if missing_values:
        data_issues.append('关键字段为空:' + '/'.join(missing_values))
    if history_missing:
        data_issues.append('历史指标字段不足')

    risk_text = _task_text(row.get('风险标签', ''))
    non_inventory_risk = any(
        token in risk_text for token in ('高退款', '极高退款', '亏损投放')
    )
    high_spend_no_output = (
        '推广成交金额' in columns and spend >= 200 and ad_gmv <= 0
    )
    roi_decline = (
        previous_link_roi > 0 and current_link_roi >= 0
        and current_link_roi < previous_link_roi * 0.7 and spend >= 100
    )
    cpa_p75 = benchmarks.get('cpa_p75', 0.0)
    cpa_abnormal = (
        spend >= 100
        and (ad_carts <= 0 or (cpa_p75 > 0 and cpa > cpa_p75 * 1.5))
    )
    signal_conflict = bool(opportunity and risk)
    structural_opportunity = (
        grade in ('S', 'A+')
        and cycle in _AI_GROWTH_CYCLES
        and (to_num(row.get('GMV', 0)) > 0 or current_link_roi > 0)
    )

    channels = {}
    for channel in ('关键词', '人群', '全站'):
        available = channel in columns
        channel_spend = to_num(row.get(channel, 0)) if available else 0.0
        attr_col = f'{channel}归因成交'
        attr_available = attr_col in columns
        attributed = to_num(row.get(attr_col, 0)) if attr_available else 0.0
        channels[channel] = {
            'available': available and attr_available,
            'spend': channel_spend,
            'attributed': attributed,
            'roi': safe_div(attributed, channel_spend),
            'mom': to_num(row.get(f'{channel}环比', 0)),
        }

    promotion_risk = bool(
        risk or non_inventory_risk or high_spend_no_output or roi_decline or cpa_abnormal
    )
    return {
        'row': row,
        'columns': set(columns),
        'metox_text': metox_text,
        'v93_text': v93_text,
        'metox_labels': metox_labels,
        'v93_labels': v93_labels,
        'labels': labels,
        'opportunity': opportunity,
        'risk': risk,
        'corrections': corrections,
        'grade': grade,
        'position': position,
        'cycle': cycle,
        'spend': spend,
        'ad_gmv': ad_gmv,
        'ad_carts': ad_carts,
        'promotion_roi': safe_div(ad_gmv, spend),
        'store_promotion_roi': benchmarks.get('promotion_roi', 0.0),
        'previous_link_roi': previous_link_roi,
        'current_link_roi': current_link_roi,
        'cpa': cpa,
        'data_issues': data_issues,
        'signal_conflict': signal_conflict,
        'non_inventory_risk': non_inventory_risk,
        'high_spend_no_output': high_spend_no_output,
        'roi_decline': roi_decline,
        'cpa_abnormal': cpa_abnormal,
        'promotion_risk': promotion_risk,
        'structural_opportunity': structural_opportunity,
        'channels': channels,
    }


def _resolve_task_type(ctx):
    """严格按数据质量/风险/机会/修正信号顺序决策。"""
    if ctx['data_issues'] or ctx['signal_conflict']:
        return '人工复核任务'

    if ctx['promotion_risk']:
        if '亏损风险' in ctx['risk'] or ctx['high_spend_no_output']:
            return '止损任务'
        if '削减' in ctx['risk'] and ctx['promotion_roi'] < 1:
            return '止损任务'
        return '渠道调整任务'

    if ctx['opportunity'] or ctx['structural_opportunity']:
        if '饱和观察' in ctx['corrections']:
            return '稳量任务'
        if ctx['grade'] == 'D' or ctx['position'] in ('滞销款', '清退款', '清退'):
            return '人工复核任务'
        if (
            ctx['grade'] == 'C' or ctx['position'] == '待改善'
            or ctx['cycle'] in ('待优化', '观察期')
        ):
            return '优化任务'
        if ctx['cycle'] == '上升验证':
            return '恢复测试任务'
        if (
            ctx['grade'] == 'B' or ctx['cycle'] == '测试期'
            or bool(ctx['opportunity'] & {'明日之星', '近3日潜力'})
            and not bool(ctx['opportunity'] & {'强主推', '爆发', '加预算'})
        ):
            return '测试任务'
        return '放量任务'

    if ctx['corrections']:
        return '稳量任务'
    return ''


def _resolve_strategy(ctx, task_type):
    if task_type == '人工复核任务':
        return '人工复核'
    if task_type == '止损任务':
        return '暂停推广' if ctx['high_spend_no_output'] else '削减投放'
    if task_type == '渠道调整任务':
        return '暂停低效渠道' if ctx['cpa_abnormal'] else '预算迁移'
    if task_type == '优化任务':
        return '优化后继续测试'
    if task_type in ('测试任务', '恢复测试任务'):
        return '扩量测试'
    if task_type == '稳量任务':
        return '继续观察' if '饱和观察' in ctx['corrections'] else '保持投放'
    if task_type == '放量任务':
        if ctx['grade'] in ('S', 'A+') and ctx['position'] == '明星款':
            return '重点放量'
        return '谨慎放量'
    return '继续观察'


def _main_action(task_type, strategy):
    return {
        '放量任务': '分渠道小步增加10%～20%，每日复盘承接效率',
        '测试任务': '选择有效渠道做小额测试，先验证加购和成交',
        '恢复测试任务': '恢复小额测试，确认短期动量后再逐步加量',
        '稳量任务': '保持当前有效投放，不继续扩量并观察边际回报',
        '渠道调整任务': '保留高效渠道，将预算从低效渠道迁出',
        '止损任务': '削减或暂停低效投放，优先控制无效消耗',
        '优化任务': '先优化商品承接或渠道结构，再继续小额验证',
        '人工复核任务': '保持现状，人工核对数据、冲突信号和渠道表现',
    }.get(task_type, strategy)


def _build_channel_actions(ctx, task_type):
    actions = {}
    baseline = ctx['store_promotion_roi'] or ctx['promotion_roi']
    for channel, metrics in ctx['channels'].items():
        if not metrics['available']:
            actions[channel] = '渠道数据缺失，人工复核'
            continue
        spend = metrics['spend']
        attributed = metrics['attributed']
        roi = metrics['roi']
        if task_type == '人工复核任务':
            actions[channel] = '保持现状，人工复核'
        elif task_type == '止损任务':
            if spend <= 0:
                actions[channel] = '暂无投放'
            elif attributed <= 0 and spend >= 100:
                actions[channel] = '暂停无有效产出的投放'
            else:
                actions[channel] = '削减20%～50%，复核ROI与CPA'
        elif task_type == '渠道调整任务':
            if spend <= 0:
                actions[channel] = '暂无投放，暂不新增'
            elif attributed <= 0:
                actions[channel] = '暂停低效部分'
            elif baseline > 0 and roi < baseline * 0.8:
                actions[channel] = '削减20%～50%，迁出低效预算'
            else:
                actions[channel] = '保持高效部分，承接迁入预算'
        elif task_type in ('放量任务', '测试任务', '恢复测试任务'):
            if spend <= 0:
                actions[channel] = '增加小额测试'
            elif spend < 50:
                actions[channel] = '保持小额测试，积累有效样本'
            elif attributed <= 0:
                actions[channel] = '停止扩量，复核无成交原因'
            elif baseline <= 0 or roi >= baseline * 0.9:
                actions[channel] = (
                    '预算增加10%～20%'
                    if task_type == '放量任务' else '增加小额测试'
                )
            else:
                actions[channel] = '保持并优化，暂不增加预算'
        else:
            if spend <= 0:
                actions[channel] = '暂无投放，继续观察'
            elif attributed <= 0 and spend >= 100:
                actions[channel] = '削减低效投放20%'
            else:
                actions[channel] = '保持，观察效率与饱和度'
    return actions


def _task_guardrails(task_type):
    if task_type == '放量任务':
        return (
            '3天', 'GMV或加购增长，且推广投产不低于当前水平',
            '连续2天ROI下滑或CPA明显恶化时回撤新增预算',
        )
    if task_type in ('测试任务', '恢复测试任务'):
        return (
            '3～7天', '产生有效加购或成交，且ROI达到可接受水平',
            '达到测试观察量仍无有效加购或成交时停止',
        )
    if task_type == '稳量任务':
        return (
            '7天', '成交与ROI保持稳定，未出现明显边际回报下降',
            'ROI持续下滑或新增花费无增量成交时停止扩量',
        )
    if task_type == '渠道调整任务':
        return (
            '3～7天', '低效渠道花费下降，高效渠道成交保持或提升',
            '迁移后整体ROI继续下降时恢复原结构并复核',
        )
    if task_type == '止损任务':
        return (
            '1～3天', '无效花费下降，保留渠道的ROI或CPA改善',
            '继续高花费无有效产出时暂停推广',
        )
    if task_type == '优化任务':
        return (
            '3～7天', '优化后加购率、转化或ROI出现明确改善',
            '优化后仍无改善时暂停测试并重新评估商品',
        )
    return (
        '1～3天', '字段与信号冲突完成核对并形成可执行结论',
        '关键数据未确认前不自动调整预算',
    )


def _build_inventory_alert(row, columns):
    """库存只生成提醒；本函数的结果不进入类型、策略、渠道动作或优先级。"""
    if '在仓库存' not in columns:
        return '库存数据缺失'
    raw_stock = row.get('在仓库存', None)
    if raw_stock is None or pd.isna(raw_stock) or _task_text(raw_stock) == '':
        return '库存数据缺失'
    stock = to_num(raw_stock)
    visitors = to_num(row.get('商品访客数', 0))
    if stock < 10:
        return '库存偏低，请关注'
    if stock > 50 and visitors < 20:
        return '库存较高，请关注动销'
    return '库存正常'


def _calculate_task_priority(ctx, task_type):
    if ctx['signal_conflict']:
        score = 95
    elif ctx['high_spend_no_output'] or '亏损风险' in ctx['risk']:
        score = 90
    elif task_type == '放量任务':
        score = 78 + (8 if ctx['opportunity'] & {'强主推', '爆发', '加预算'} else 0)
    elif task_type == '止损任务':
        score = 82
    elif task_type == '渠道调整任务':
        score = 65
    elif task_type in ('测试任务', '恢复测试任务'):
        score = 60
    elif task_type == '优化任务':
        score = 55
    elif task_type == '稳量任务':
        score = 40
    elif task_type == '人工复核任务' and ctx['data_issues']:
        score = 70
    else:
        score = 25

    if task_type in ('放量任务', '测试任务', '恢复测试任务'):
        score += min(max(int(to_num(ctx['row'].get('MetoX算法分', 0))), 0), 10)
    if ctx['v93_labels']:
        score += 3
    score = max(0, min(int(score), 100))
    if score >= 80:
        priority = 'P1'
    elif score >= 55:
        priority = 'P2'
    elif score >= 35:
        priority = 'P3'
    else:
        priority = 'P4'
    return priority, score


def _task_reason(ctx):
    reasons = []
    if ctx['metox_labels']:
        reasons.append('MetoX:' + '/'.join(sorted(ctx['metox_labels'])))
    if ctx['v93_labels']:
        reasons.append('v9.3:' + '/'.join(sorted(ctx['v93_labels'])))
    if ctx['signal_conflict']:
        reasons.append('机会与风险信号冲突')
    reasons.extend(ctx['data_issues'])
    if ctx['high_spend_no_output']:
        reasons.append(f"花费{ctx['spend']:.0f}但推广成交为0")
    if ctx['roi_decline']:
        reasons.append(
            f"链接ROI较上周期下降({ctx['previous_link_roi']:.1f}→{ctx['current_link_roi']:.1f})"
        )
    if ctx['cpa_abnormal']:
        reasons.append('CPA异常或无有效推广加购')
    if ctx['non_inventory_risk']:
        reasons.append('执行表存在退款或亏损风险')
    if ctx['structural_opportunity'] and not ctx['opportunity']:
        reasons.append('高等级商品处于增长周期且已有成交表现')
    reasons.append(
        f"综合等级{ctx['grade'] or '空'}；商品定位{ctx['position'] or '空'}；"
        f"商品周期{ctx['cycle'] or '空'}"
    )
    return '；'.join(reasons)


def _should_show_task(ctx, task_type):
    if not task_type:
        return False
    return bool(
        ctx['labels'] or ctx['v93_text'] or ctx['promotion_risk']
        or ctx['signal_conflict'] or ctx['data_issues'] or ctx['structural_opportunity']
    )


def build_ai_task_view(df: pd.DataFrame) -> pd.DataFrame:
    """从最终执行表生成只读商品级推广任务，不修改输入 DataFrame。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=AI_TASK_VIEW_COLUMNS)

    source = df.copy(deep=True)
    columns = set(source.columns)
    benchmarks = _task_benchmarks(source)
    tasks = []
    for _, row in source.iterrows():
        ctx = _build_product_context(row, columns, benchmarks)
        task_type = _resolve_task_type(ctx)
        if not _should_show_task(ctx, task_type):
            continue
        strategy = _resolve_strategy(ctx, task_type)
        channel_actions = _build_channel_actions(ctx, task_type)
        observation, success, stop = _task_guardrails(task_type)
        priority, score = _calculate_task_priority(ctx, task_type)
        tasks.append({
            '执行优先级': priority,
            '任务优先分': score,
            '款式主图': _task_raw_or_blank(row.get('款式主图', '')),
            '货号': _task_raw_or_blank(row.get('货号', '')),
            '链接数': _task_raw_or_blank(row.get('链接数', '')),
            '宝贝ID': _task_raw_or_blank(row.get('宝贝ID', '')),
            '商品名称': _task_raw_or_blank(row.get('商品名称', '')),
            '淘宝类目': _task_raw_or_blank(row.get('淘宝类目', '')),
            '综合等级': _task_raw_or_blank(row.get('综合等级', '')),
            '商品定位': _task_raw_or_blank(row.get('商品定位', '')),
            '商品周期': _task_raw_or_blank(row.get('商品周期', '')),
            'MetoX算法命中': _task_raw_or_blank(row.get('MetoX算法命中', '')),
            'AI任务类型': task_type,
            '最终推广策略': strategy,
            '主执行动作': _main_action(task_type, strategy),
            '关键词动作': channel_actions['关键词'],
            '人群动作': channel_actions['人群'],
            '全站动作': channel_actions['全站'],
            '观察周期': observation,
            '成功标准': success,
            '停止条件': stop,
            '任务触发原因': _task_reason(ctx),
            '库存提醒': _build_inventory_alert(row, columns),
        })

    result = pd.DataFrame(tasks, columns=AI_TASK_VIEW_COLUMNS)
    if result.empty:
        return result
    order = result['执行优先级'].map({'P1': 1, 'P2': 2, 'P3': 3, 'P4': 4}).fillna(5)
    return (
        result.assign(_priority_order=order)
        .sort_values(['_priority_order', '任务优先分'], ascending=[True, False], kind='stable')
        .drop(columns=['_priority_order'])
        .reset_index(drop=True)
    )


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


REPORT_CHANNELS = ('关键词', '人群', '全站')
REPORT_PERIOD_LABELS = {
    '当日': '当日 vs 前一日',
    '3天': '近3天 vs 前3天',
    '7天': '近7天 vs 前7天',
    '15天': '近15天 vs 前15天',
}


def _report_number_sum(df, col):
    if df is None or len(df) == 0 or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0.0).sum())


def _first_report_label(values):
    for value in values:
        text = str(value or '').strip()
        if text and text not in ('nan', 'None'):
            return text
    return ''


def _product_report_frame(df, label_col=None):
    columns = ['_pid', 'gmv'] + (['label'] if label_col else [])
    if df is None or len(df) == 0 or '宝贝ID' not in df.columns:
        return pd.DataFrame(columns=columns)

    work = pd.DataFrame({
        '_pid': df['宝贝ID'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True),
        'gmv': pd.to_numeric(df['GMV'], errors='coerce').fillna(0.0)
               if 'GMV' in df.columns else 0.0,
    })
    work = work[work['_pid'].ne('') & work['_pid'].ne('nan')]
    agg = {'gmv': 'sum'}
    if label_col:
        work['label'] = df[label_col].fillna('').astype(str).str.strip() \
            if label_col in df.columns else ''
        agg['label'] = _first_report_label
    return work.groupby('_pid', as_index=False).agg(agg)


def _rank_report_deltas(rows):
    positive = sorted((row.copy() for row in rows if row['delta'] > 0),
                      key=lambda row: row['delta'], reverse=True)
    negative = sorted((row.copy() for row in rows if row['delta'] < 0),
                      key=lambda row: row['delta'])
    positive_total = sum(row['delta'] for row in positive)
    negative_total = sum(abs(row['delta']) for row in negative)
    for row in positive:
        row['direction_share'] = safe_div(row['delta'], positive_total)
    for row in negative:
        row['direction_share'] = safe_div(abs(row['delta']), negative_total)
    return {
        'all': rows,
        'growth': positive[0] if positive else None,
        'decline': negative[0] if negative else None,
        'positive_total': positive_total,
        'negative_total': negative_total,
    }


def _grade_report_deltas(current_df, previous_df):
    current = _product_report_frame(current_df, '综合等级').rename(
        columns={'gmv': 'current_gmv'}
    )
    previous = _product_report_frame(previous_df).rename(
        columns={'gmv': 'previous_gmv'}
    )
    merged = current.merge(previous, on='_pid', how='outer', indicator=True)
    if merged.empty:
        return _rank_report_deltas([])
    merged['current_gmv'] = merged['current_gmv'].fillna(0.0)
    merged['previous_gmv'] = merged['previous_gmv'].fillna(0.0)
    if 'label' not in merged.columns:
        merged['label'] = ''
    merged['label'] = merged['label'].fillna('').astype(str).str.strip()
    merged.loc[merged['_merge'].eq('right_only'), 'label'] = '流失商品'
    merged.loc[merged['label'].eq(''), 'label'] = '未分级'
    merged['delta'] = merged['current_gmv'] - merged['previous_gmv']
    grouped = merged.groupby('label', as_index=False).agg(
        current=('current_gmv', 'sum'),
        previous=('previous_gmv', 'sum'),
        delta=('delta', 'sum'),
    )
    return _rank_report_deltas(grouped.to_dict('records'))


def _category_report_rows(current_df, previous_df):
    def _totals(frame, prefix):
        if frame is None or len(frame) == 0:
            return pd.DataFrame(columns=['label', f'{prefix}_gmv', f'{prefix}_spend'])
        label = frame['淘宝类目'].fillna('').astype(str).str.strip() \
            if '淘宝类目' in frame.columns else pd.Series('', index=frame.index)
        work = pd.DataFrame({
            'label': label.replace({'': '未分类', 'nan': '未分类', 'None': '未分类'}),
            f'{prefix}_gmv': pd.to_numeric(frame['GMV'], errors='coerce').fillna(0.0)
                            if 'GMV' in frame.columns else 0.0,
            f'{prefix}_spend': pd.to_numeric(frame['付费'], errors='coerce').fillna(0.0)
                              if '付费' in frame.columns else 0.0,
        })
        return work.groupby('label', as_index=False).sum(numeric_only=True)

    current = _totals(current_df, 'current')
    previous = _totals(previous_df, 'previous')
    merged = current.merge(previous, on='label', how='outer').fillna(0.0)
    rows = []
    for _, row in merged.iterrows():
        current_gmv = float(row['current_gmv'])
        previous_gmv = float(row['previous_gmv'])
        current_spend = float(row['current_spend'])
        previous_spend = float(row['previous_spend'])
        current_roi = safe_div(current_gmv, current_spend)
        previous_roi = safe_div(previous_gmv, previous_spend)
        rows.append({
            'label': row['label'],
            'current': current_gmv,
            'previous': previous_gmv,
            'delta': current_gmv - previous_gmv,
            'current_spend': current_spend,
            'previous_spend': previous_spend,
            'current_roi': current_roi,
            'previous_roi': previous_roi,
            'roi_delta': current_roi - previous_roi,
        })
    return rows


def _report_completeness(period_log):
    if not period_log:
        return 'unknown', '未取得周期完整性记录'
    completeness = str(period_log.get('完整性', '') or '').strip()
    pairs = re.findall(r'(\d+)\s*/\s*(\d+)', completeness)
    status = 'complete' if len(pairs) >= 2 and all(
        int(actual) == int(expected) for actual, expected in pairs[:2]
    ) else 'incomplete'
    missing = str(period_log.get('缺失日期', '') or '').strip()
    message = completeness
    if status != 'complete' and missing:
        message = f'{message}；{missing}' if message else missing
    return status, message or '周期完整性未知'


def _report_money(value, signed=False):
    number = float(value or 0.0)
    sign = '+' if signed and number > 0 else ('-' if number < 0 else '')
    amount = abs(number)
    if amount >= 10000:
        return f'{sign}¥{amount / 10000:.2f}万'
    return f'{sign}¥{amount:,.0f}'


def _report_rate(current, previous):
    current = float(current or 0.0)
    previous = float(previous or 0.0)
    if previous == 0:
        return '新增' if current > 0 else '持平'
    return f'{safe_div(current - previous, previous):+.1%}'


def _report_movement_line(ranked):
    parts = []
    growth = ranked.get('growth')
    decline = ranked.get('decline')
    if growth:
        parts.append(
            f"增长集中在{growth['label']} {_report_money(growth['delta'], signed=True)}"
            f"（占正向变化{growth['direction_share']:.0%}）"
        )
    if decline:
        parts.append(
            f"主要拖累{decline['label']} {_report_money(decline['delta'], signed=True)}"
            f"（占负向变化{decline['direction_share']:.0%}）"
        )
    return '；'.join(parts) if parts else '无明显增长或下滑项'


def build_operating_daily_report(current_df, previous_df, period_choice='当日', period_log=None,
                                 risk_spend_floor=100.0):
    """生成可对账的本地经营日报，不读文件、不调用外部 AI 服务。"""
    current_gmv = _report_number_sum(current_df, 'GMV')
    previous_gmv = _report_number_sum(previous_df, 'GMV')
    current_spend = _report_number_sum(current_df, '付费')
    previous_spend = _report_number_sum(previous_df, '付费')
    current_roi = safe_div(current_gmv, current_spend)
    previous_roi = safe_div(previous_gmv, previous_spend)

    grades = _grade_report_deltas(current_df, previous_df)
    category_rows = _category_report_rows(current_df, previous_df)
    categories = _rank_report_deltas(category_rows)

    channels = []
    for channel in REPORT_CHANNELS:
        current_attr = _report_number_sum(current_df, f'{channel}归因成交')
        previous_attr = _report_number_sum(previous_df, f'{channel}归因成交')
        current_channel_spend = _report_number_sum(current_df, channel)
        previous_channel_spend = _report_number_sum(previous_df, channel)
        channels.append({
            'name': channel,
            'current_attributed_sales': current_attr,
            'previous_attributed_sales': previous_attr,
            'attributed_sales_delta': current_attr - previous_attr,
            'current_spend': current_channel_spend,
            'previous_spend': previous_channel_spend,
            'spend_delta': current_channel_spend - previous_channel_spend,
        })

    risk_candidates = [
        row for row in category_rows
        if row['current_spend'] >= float(risk_spend_floor)
        and row['previous_spend'] > 0
        and row['roi_delta'] < 0
    ]
    roi_risk = min(risk_candidates, key=lambda row: row['roi_delta']) \
        if risk_candidates else None

    status, completeness = _report_completeness(period_log)
    overview = {
        'current_gmv': current_gmv,
        'previous_gmv': previous_gmv,
        'gmv_delta': current_gmv - previous_gmv,
        'gmv_change_label': _report_rate(current_gmv, previous_gmv),
        'current_spend': current_spend,
        'previous_spend': previous_spend,
        'spend_delta': current_spend - previous_spend,
        'spend_change_label': _report_rate(current_spend, previous_spend),
        'current_roi': current_roi,
        'previous_roi': previous_roi,
        'roi_delta': current_roi - previous_roi,
    }

    channel_line = '；'.join(
        f"{row['name']}归因{_report_money(row['attributed_sales_delta'], signed=True)}"
        f"/花费{_report_money(row['spend_delta'], signed=True)}"
        for row in channels
    )
    risk_line = (
        f"{roi_risk['label']} ROI {roi_risk['previous_roi']:.2f}→{roi_risk['current_roi']:.2f}"
        f"（{roi_risk['roi_delta']:+.2f}），本期付费{_report_money(roi_risk['current_spend'])}"
        if roi_risk else f'暂无本期付费≥{risk_spend_floor:.0f}元且ROI下滑的品类'
    )
    lines = {
        '总览': (
            f"GMV {_report_money(overview['gmv_delta'], signed=True)}"
            f"（{overview['gmv_change_label']}），花费{_report_money(overview['spend_delta'], signed=True)}"
            f"（{overview['spend_change_label']}），ROI {previous_roi:.2f}→{current_roi:.2f}"
            f"（{overview['roi_delta']:+.2f}）"
        ),
        '等级': _report_movement_line(grades),
        '品类': _report_movement_line(categories),
        '渠道': channel_line,
        '风险': risk_line,
    }
    return {
        'status': status,
        'completeness': completeness,
        'period_scope': REPORT_PERIOD_LABELS.get(period_choice, str(period_choice)),
        'overview': overview,
        'grades': grades,
        'categories': categories,
        'channels': channels,
        'roi_risk': roi_risk,
        'lines': lines,
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
    if 'MetoX算法命中' in df.columns:
        for keys, sub in df.groupby(group_keys):
            label_cnt = {}
            for v in sub['MetoX算法命中'].dropna():
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
            'MetoX算法命中': builtin_sig_map.get(tuple(r[k] for k in group_keys), ''),
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
    """退款风险（聚水潭cohort退款率>70%,与「⚠高退款」和 Excel 退款风险阈值对齐）"""
    if '退款率' not in df.columns: return []
    sub = df[df['退款率'] > 0.7].sort_values('退款率', ascending=False).head(500)
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
    channel_spend_total = _s(df, col)
    channel_attr_total = _s(df, attr_col)
    channel_base_roi = safe_div(channel_attr_total, channel_spend_total)
    rows = []
    for _, r in sub.iterrows():
        ch_spend = to_num(r.get(col, 0))
        ch_attr = to_num(r.get(attr_col, 0))
        ch_roi = safe_div(ch_attr, ch_spend)
        decision = _channel_decision(r, ch_spend, ch_attr, ch_roi, channel_base_roi)
        rows.append({
            '宝贝ID': r['宝贝ID'], '商品名称': r.get('商品名称', ''),
            '渠道花费': ch_spend, '渠道成交': round(ch_attr, 2),
            '渠道投产': f'{ch_roi:.2f}',
            '渠道基准投产': f'{channel_base_roi:.2f}',
            '渠道效率倍数': f'{safe_div(ch_roi, channel_base_roi):.2f}' if channel_base_roi else '',
            '渠道判断': decision['judge'],
            '渠道动作': decision['action'],
            '渠道预算建议': decision['budget'],
            '渠道判断原因': decision['reason'],
            'GMV': float(r.get('GMV', 0)),
            '综合等级': r.get('综合等级', ''), '对应操作': r.get('对应操作', ''),
        })
    return rows


def _channel_decision(row, spend, attr, roi, base_roi):
    ratio = safe_div(roi, base_roi)
    labels = str(row.get('MetoX算法命中', '') or '')
    grade = str(row.get('综合等级', '') or '')
    op = str(row.get('对应操作', '') or '')
    risks = str(row.get('风险标签', '') or '')
    strong_signal = any(x in labels for x in ['爆发', '溢出', '加预算', '明日之星', '近3日潜力', '强主推'])
    risk_signal = (
        grade in ('C', 'D')
        or op in ('观察期', '淘汰', '衰退期', '待优化')
        or any(x in risks for x in ['缺货', '高退款', '亏损投放', '库存不足'])
    )

    def _reason(prefix):
        sig = '强信号' if strong_signal else '无强信号'
        risk = '有风险' if risk_signal else '无明显风险'
        return f"{prefix}; 渠道投产{roi:.2f},基准{base_roi:.2f},效率{ratio:.2f}倍,{sig},{risk}"

    if spend < 50:
        return {
            'judge': '花费<50',
            'action': '观察',
            'budget': '样本不足，不做强判断',
            'reason': f"渠道花费{spend:.0f}<50，不做强判断",
        }
    if attr <= 0 and spend >= 100:
        return {
            'judge': '暂停观察',
            'action': '暂停新增预算',
            'budget': '停止新增预算，复查定向/素材/出价',
            'reason': _reason('花费≥100且渠道成交=0'),
        }
    if spend < 100:
        return {
            'judge': '小额测试',
            'action': '观察',
            'budget': '维持小预算，继续观察1-3天',
            'reason': _reason('50≤渠道花费<100，样本偏小'),
        }
    if strong_signal and not risk_signal and ratio >= 1.2:
        return {
            'judge': '高效放量',
            'action': '加预算',
            'budget': '增加20%-30%',
            'reason': _reason('商品正向强信号且渠道效率≥1.2倍基准'),
        }
    if strong_signal and ratio < 0.9:
        return {
            'judge': '低效优化',
            'action': '优化渠道',
            'budget': '不加预算，先优化关键词/人群/出价/素材',
            'reason': _reason('商品有强信号但该渠道效率低于0.9倍基准'),
        }
    if risk_signal and ratio >= 1.2:
        return {
            'judge': '小额保留',
            'action': '谨慎维持',
            'budget': '不放大，先处理商品风险',
            'reason': _reason('渠道高效但商品分层或风险标签不支持放量'),
        }
    if ratio >= 1.2:
        return {
            'judge': '有效维持',
            'action': '维持或小幅加预算',
            'budget': '维持或增加5%-10%',
            'reason': _reason('渠道效率≥1.2倍基准但缺少商品强信号'),
        }
    if ratio >= 0.9:
        return {
            'judge': '有效维持',
            'action': '维持',
            'budget': '维持预算，观察稳定性',
            'reason': _reason('渠道效率在0.9-1.2倍基准之间'),
        }
    if ratio >= 0.3:
        return {
            'judge': '低效优化',
            'action': '优化渠道',
            'budget': '不加预算，先优化渠道结构',
            'reason': _reason('渠道效率在0.3-0.9倍基准之间'),
        }
    return {
        'judge': '控投止损',
        'action': '控投或暂停',
        'budget': '降低20%-50%',
        'reason': _reason('渠道效率<0.3倍基准'),
    }


def inventory_actions(df):
    """库存动作"""
    has_stock = '在仓库存' in df.columns
    has_vis = '商品访客数' in df.columns
    has_transit = '在途' in df.columns
    has_spend = '付费' in df.columns

    hi = df[(df['在仓库存'] > 50) & (df['商品访客数'] < 20)].sort_values('在仓库存', ascending=False).head(200) if (has_stock and has_vis) else pd.DataFrame()
    no = df[(df['在仓库存'] == 0) & (df['在途'] == 0) & (df['付费'] > 0)].head(200) if (has_stock and has_transit and has_spend) else pd.DataFrame()
    def _fmt(sub):
        return [{'宝贝ID': r['宝贝ID'], '货号': r.get('货号',''), '商品名称': r.get('商品名称',''), '在仓库存': int(r.get('在仓库存',0)),
                 '在途': int(r.get('在途',0)), '商品访客数': int(r.get('商品访客数',0)),
                 'GMV': float(r.get('GMV',0)), '付费': float(r.get('付费',0)), '综合等级': r.get('综合等级','')}
                for _, r in sub.iterrows()]
    return {'hi_stock': _fmt(hi), 'no_stock': _fmt(no)}
