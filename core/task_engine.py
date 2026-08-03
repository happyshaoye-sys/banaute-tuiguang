"""只读 AI 任务策略引擎。

输入只能是最终执行表 DataFrame。模块不读取文件、不调用推广平台，也不修改
MetoX、商品分层、KPI 或输入 DataFrame。
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter

import numpy as np
import pandas as pd

from .schema import safe_div, to_num


AI_TASK_VIEW_COLUMNS = [
    '执行优先级', '任务优先分', '款式主图', '货号', '链接数', '宝贝ID',
    'GMV', '商品访客数', '链接投产', '淘宝类目', '综合等级',
    '商品定位', '商品周期', 'MetoX算法命中',
    'AI任务类型', '商品级总策略', '动作对象', '主执行动作',
    '关键词状态', '关键词动作', '人群状态', '人群动作', '全站状态', '全站动作',
    '推广计划数', '计划总数', '计划展示数', '计划覆盖率', '计划数据完整度',
    '计划数据说明', 'AI可信度', 'AI可信度分', 'AI可信度说明',
    '商品策略数据源', '渠道动作数据源', '计划动作数据源',
    '计划执行摘要', '计划执行明细', '非投放动作',
    '观察周期', '成功标准', '停止条件', '任务触发原因', '库存提醒',
]

# 页面主表只投影运营直接需要的字段。其余字段保留在后台DataFrame中，
# 供详情、导出和可信度解释使用。
AI_TASK_MAIN_COLUMNS = [
    '执行优先级', '任务优先分', '款式主图', '货号', '链接数', '宝贝ID',
    'GMV', '商品访客数', '链接投产', '淘宝类目', '综合等级', '商品定位',
    '商品周期', 'MetoX算法命中', 'AI任务类型', '商品级总策略', '主执行动作',
    '动作对象', '关键词动作', '人群动作', '全站动作', '计划执行摘要',
    '观察周期', '成功标准', '停止条件', '任务触发原因', 'AI可信度', '库存提醒',
]

PLAN_DETAIL_COLUMNS = [
    '动作对象', '执行对象', '计划ID', '计划名称', '所属渠道',
    '当前花费', '归因成交', 'ROI', 'CPA', '点击',
    '当前状态', '计划效率状态', '建议动作', '建议调整比例', '动作依据',
    '观察周期', '成功标准', '停止条件', '数据来源',
]

_OPPORTUNITY_LABELS = {'明日之星', '强主推', '近3日潜力', '爆发', '加预算'}
_RISK_LABELS = {'亏损风险', '削减'}
_CORRECTION_LABELS = {'饱和观察', '保护持有', '溢出'}
_KNOWN_LABELS = _OPPORTUNITY_LABELS | _RISK_LABELS | _CORRECTION_LABELS
_GROWTH_CYCLES = {'测试期', '成长期', '爆发期', '重点', '加预算', '上升验证', '放量', '适当放量'}
_DECLINE_CYCLES = {'衰退预警', '衰退期', '清退期', '淘汰'}
_HISTORY_FIELDS = {
    '日销3', '日销7', '日销15', '花费3', '花费7', '花费15', '上周期金额', '上周期付费',
}
_ACTIVE_ACTIONS = {'增加', '保持', '削减', '暂停', '小额测试', '恢复测试'}
_NUMBER = r'[-+]?\d[\d,]*(?:\.\d+)?'


def _text(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ''
    result = str(value).strip()
    return '' if result in ('nan', 'None', '<NA>') else result


def _raw_or_blank(value):
    """透传原字段，只归一真正空值。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ''
    return value


def _field_available(row, columns, field: str, allow_blank: bool = False) -> bool:
    """判断执行表字段是否可用；0是有效值，不把业务上的0当成缺失。"""
    if field not in columns:
        return False
    value = row.get(field, None)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    return allow_blank or bool(_text(value))


def _labels(value) -> set[str]:
    text = _text(value)
    return {label for label in _KNOWN_LABELS if label in text}


def _series_num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index, dtype=float)
    return df[column].map(to_num).astype(float)


def _task_benchmarks(df: pd.DataFrame) -> dict:
    spend = _series_num(df, '付费')
    gmv = _series_num(df, 'GMV')
    promotion_gmv = _series_num(df, '推广成交金额')
    carts = _series_num(df, '推广加购数')
    cpa = spend[carts > 0] / carts[carts > 0]
    eligible_cpa = cpa[(spend[carts > 0] >= 50) & np.isfinite(cpa)]
    return {
        'cpa_p75': float(eligible_cpa.quantile(0.75)) if len(eligible_cpa) >= 10 else 0.0,
        'promotion_roi': safe_div(float(promotion_gmv.sum()), float(spend.sum())),
        'spend_p75': float(spend[spend > 0].quantile(0.75)) if (spend > 0).any() else 0.0,
        'gmv_p75': float(gmv[gmv > 0].quantile(0.75)) if (gmv > 0).any() else 0.0,
    }


def _extract_text_field(line: str, names: tuple[str, ...]) -> str:
    labels = '|'.join(re.escape(name) for name in names)
    next_labels = (
        '计划ID|计划编号|计划名称|渠道|所属渠道|场景|场景名字|花费|当前花费|'
        '归因成交|成交|投产|ROI|加购|CPA|点击|状态|当前状态'
    )
    match = re.search(
        rf'(?:{labels})\s*[:：]\s*(.*?)(?=\s+(?:{next_labels})\s*[:：]|$)',
        line,
        flags=re.IGNORECASE,
    )
    return _text(match.group(1)) if match else ''


def _extract_number_field(line: str, names: tuple[str, ...]):
    labels = '|'.join(re.escape(name) for name in names)
    match = re.search(rf'(?:{labels})\s*[:：]\s*({_NUMBER})', line, flags=re.IGNORECASE)
    return to_num(match.group(1)) if match else None


def _dict_value(item: dict, aliases: tuple[str, ...], default=''):
    lowered = {str(key).strip().lower(): value for key, value in item.items()}
    for alias in aliases:
        if alias in item:
            return item[alias]
        value = lowered.get(alias.lower())
        if value is not None:
            return value
    return default


def _normalize_plan(item: dict) -> dict:
    plan_id = _text(_dict_value(item, ('计划ID', '计划编号', '计划id', 'campaign_id', 'id')))
    name = _text(_dict_value(item, ('计划名称', '名称', 'campaign_name', 'name')))
    channel = _text(_dict_value(item, ('所属渠道', '渠道', '场景名字', '场景', 'channel')))
    spend_raw = _dict_value(item, ('当前花费', '花费', '付费', 'spend'), None)
    attr_raw = _dict_value(item, ('归因成交', '成交', '推广成交金额', 'attributed_gmv'), None)
    roi_raw = _dict_value(item, ('ROI', 'roi', '投产', '推广投产'), None)
    cpa_raw = _dict_value(item, ('CPA', 'cpa'), None)
    carts_raw = _dict_value(item, ('加购', '推广加购数', 'add_cart'), None)
    clicks_raw = _dict_value(item, ('点击', '点击量', 'clicks'), None)
    status = _text(_dict_value(item, ('当前状态', '状态', 'status')))
    return {
        '计划ID': plan_id,
        '计划名称': name or '执行表未提供',
        '所属渠道': channel or '执行表未提供',
        '当前花费': to_num(spend_raw) if spend_raw is not None else 0.0,
        '归因成交': to_num(attr_raw) if attr_raw is not None else '',
        'ROI': to_num(roi_raw) if roi_raw is not None else None,
        'CPA': to_num(cpa_raw) if cpa_raw is not None else '',
        '加购': to_num(carts_raw) if carts_raw is not None else '',
        '点击': to_num(clicks_raw) if clicks_raw is not None else '',
        '当前状态': status or '执行表未提供',
    }


def _parse_plan_line(line: str) -> dict | None:
    plan_id = _extract_text_field(line, ('计划ID', '计划编号', 'campaign_id'))
    plan_name = _extract_text_field(line, ('计划名称',))
    channel = _extract_text_field(line, ('所属渠道', '渠道', '场景名字', '场景'))
    spend = _extract_number_field(line, ('当前花费', '花费', '付费'))
    attributed = _extract_number_field(line, ('归因成交', '成交'))
    roi = _extract_number_field(line, ('ROI', '投产', '推广投产'))
    cpa = _extract_number_field(line, ('CPA',))
    carts = _extract_number_field(line, ('加购', '推广加购数'))
    clicks = _extract_number_field(line, ('点击', '点击量'))
    status = _extract_text_field(line, ('当前状态', '状态'))
    if not any((plan_id, plan_name, channel, spend is not None, roi is not None)):
        return None
    return _normalize_plan({
        '计划ID': plan_id,
        '计划名称': plan_name,
        '所属渠道': channel,
        '当前花费': spend,
        '归因成交': attributed,
        'ROI': roi,
        'CPA': cpa,
        '加购': carts,
        '点击': clicks,
        '当前状态': status,
    })


def _parse_promotion_plans(value) -> tuple[list[dict], str]:
    """安全解析最终执行表的推广明细；失败只降级计划层，不中断商品任务。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return [], ''

    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if not text or text in ('nan', 'None'):
            return [], ''
        if text[:1] in ('[', '{'):
            parsed = None
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                try:
                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    parsed = None
            if parsed is not None:
                payload = parsed
            else:
                payload = text

    if isinstance(payload, dict):
        for key in ('plans', '计划', '计划明细', '推广计划'):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]

    plans = []
    if isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, dict):
                plan = _normalize_plan(item)
            elif isinstance(item, str):
                plan = _parse_plan_line(item)
            else:
                plan = None
            if plan and (plan['计划ID'] or plan['计划名称'] != '执行表未提供'):
                plans.append(plan)
    elif isinstance(payload, str):
        for line in re.split(r'[\r\n]+', payload):
            line = re.sub(r'^\s*\d+[.、)]\s*', '', line.strip())
            if not line:
                continue
            plan = _parse_plan_line(line)
            if plan:
                plans.append(plan)
    else:
        return [], '推广明细格式不受支持'

    if plans:
        return plans, ''
    return [], '推广明细解析失败'


def _efficiency_state(spend: float, attributed, roi, reference_roi: float) -> tuple[str, str]:
    if spend <= 0:
        return '暂无投放', '花费为0'
    if roi is None and attributed in ('', None):
        return '数据不足', '有花费但缺少归因成交和ROI'
    actual_roi = to_num(roi) if roi is not None else safe_div(to_num(attributed), spend)
    if spend < 20 and actual_roi <= 0:
        return '数据不足', '花费样本不足且暂无归因产出'
    if actual_roi <= 0:
        return '低效渠道', '已有有效花费但暂无归因产出'
    if reference_roi > 0 and actual_roi >= reference_roi * 1.2:
        return '高效渠道', f'ROI {actual_roi:.2f} ≥ 商品基准的120%'
    if reference_roi > 0 and actual_roi < reference_roi * 0.7:
        return '低效渠道', f'ROI {actual_roi:.2f} < 商品基准的70%'
    if reference_roi <= 0 and actual_roi >= 1:
        return '正常渠道', f'ROI {actual_roi:.2f}，缺少可比商品基准'
    return '正常渠道', f'ROI {actual_roi:.2f}处于商品基准70%～120%区间'


def _evaluate_channel_efficiency(row, columns: set[str], reference_roi: float) -> dict[str, dict]:
    channels = {}
    for channel in ('关键词', '人群', '全站'):
        spend_col = channel
        attr_col = f'{channel}归因成交'
        if spend_col not in columns:
            channels[channel] = {
                'spend': 0.0, 'attributed': '', 'roi': 0.0, 'mom': 0.0,
                'state': '数据不足', 'reason': f'执行表缺少{spend_col}字段',
            }
            continue
        spend = to_num(row.get(spend_col, 0))
        if spend <= 0:
            channels[channel] = {
                'spend': 0.0, 'attributed': to_num(row.get(attr_col, 0)) if attr_col in columns else '',
                'roi': 0.0, 'mom': to_num(row.get(f'{channel}环比', 0)),
                'state': '暂无投放', 'reason': '当前渠道花费为0',
            }
            continue
        if attr_col not in columns:
            channels[channel] = {
                'spend': spend, 'attributed': '', 'roi': 0.0,
                'mom': to_num(row.get(f'{channel}环比', 0)),
                'state': '数据不足', 'reason': f'有花费但执行表缺少{attr_col}',
            }
            continue
        attributed = to_num(row.get(attr_col, 0))
        roi = safe_div(attributed, spend)
        state, reason = _efficiency_state(spend, attributed, roi, reference_roi)
        channels[channel] = {
            'spend': spend, 'attributed': attributed, 'roi': roi,
            'mom': to_num(row.get(f'{channel}环比', 0)), 'state': state, 'reason': reason,
        }
    return channels


def _evaluate_plan_efficiency(plans: list[dict], reference_roi: float) -> list[dict]:
    evaluated = []
    for plan in plans:
        current = dict(plan)
        state, reason = _efficiency_state(
            to_num(plan.get('当前花费', 0)), plan.get('归因成交', ''), plan.get('ROI'), reference_roi
        )
        current['计划效率状态'] = state.replace('渠道', '计划')
        current['_效率依据'] = reason
        evaluated.append(current)
    return evaluated


def _source_window(period_label: str) -> str:
    label = _text(period_label)
    if not label:
        return '当前执行表'
    return label[:-2] if label.endswith('累计') else label


def _build_product_context(row, columns, benchmarks):
    metox_text = _text(row.get('MetoX算法命中', ''))
    v93_text = _text(row.get('v9.3信号', ''))
    metox_labels = _labels(metox_text)
    v93_labels = _labels(v93_text)
    labels = metox_labels | v93_labels
    opportunity = labels & _OPPORTUNITY_LABELS
    risk = labels & _RISK_LABELS
    corrections = labels & _CORRECTION_LABELS
    grade = _text(row.get('综合等级', ''))
    position = _text(row.get('商品定位', ''))
    cycle = _text(row.get('商品周期', ''))
    spend = to_num(row.get('付费', 0))
    gmv = to_num(row.get('GMV', 0))
    ad_gmv = to_num(row.get('推广成交金额', 0))
    ad_carts = to_num(row.get('推广加购数', 0))
    promotion_roi = safe_div(ad_gmv, spend)
    current_link_roi = to_num(row.get('链接投产', 0)) or safe_div(gmv, spend)
    previous_link_roi = safe_div(to_num(row.get('上周期金额', 0)), to_num(row.get('上周期付费', 0)))
    cpa = safe_div(spend, ad_carts)

    required_columns = {'宝贝ID', '综合等级', '商品定位', '商品周期', 'MetoX算法命中'}
    missing_columns = sorted(required_columns - set(columns))
    missing_values = [
        name for name in ('宝贝ID', '综合等级', '商品定位', '商品周期')
        if not _text(row.get(name, ''))
    ]
    data_issues = []
    if missing_columns:
        data_issues.append('缺少关键列:' + '/'.join(missing_columns))
    if missing_values:
        data_issues.append('关键字段为空:' + '/'.join(missing_values))
    if labels and not bool(_HISTORY_FIELDS & set(columns)):
        data_issues.append('历史指标字段不足')

    risk_text = _text(row.get('风险标签', ''))
    non_inventory_risk = any(token in risk_text for token in ('高退款', '极高退款', '亏损投放'))
    high_spend_no_output = '推广成交金额' in columns and spend >= 200 and ad_gmv <= 0
    roi_decline = (
        previous_link_roi > 0 and current_link_roi >= 0
        and current_link_roi < previous_link_roi * 0.7 and spend >= 100
    )
    cpa_p75 = benchmarks.get('cpa_p75', 0.0)
    cpa_abnormal = spend >= 100 and (
        ad_carts <= 0 or (cpa_p75 > 0 and cpa > cpa_p75 * 1.5)
    )

    reference_roi = promotion_roi or benchmarks.get('promotion_roi', 0.0)
    channels = _evaluate_channel_efficiency(row, set(columns), reference_roi)
    plans, plan_parse_error = _parse_promotion_plans(row.get('推广明细', ''))
    plans = _evaluate_plan_efficiency(plans, reference_roi)
    plan_total = int(to_num(row.get('推广计划总数', len(plans))))
    plan_display = int(to_num(row.get('推广计划展示数', len(plans))))
    if plan_total <= 0 and plans:
        plan_total = len(plans)
    if plan_display <= 0 and plans:
        plan_display = len(plans)
    has_plan_data = plan_total > 0
    plan_coverage = (
        to_num(row.get('计划覆盖率', 1.0 if plan_total <= 3 else 0.0))
        if has_plan_data else None
    )
    plan_completeness = to_num(row.get('计划数据完整度', 1.0 if plans or spend <= 0 else 0.0))
    period_label = _text(row.get('计划数据口径', ''))
    source_window = _source_window(period_label)
    plan_period_days = int(to_num(row.get('计划统计天数', 0)))
    plan_actual_days = int(to_num(row.get('计划实际数据天数', 0)))
    if period_label == '当日累计':
        label_days = 1
    else:
        period_match = re.fullmatch(r'近(\d+)天累计', period_label)
        label_days = int(period_match.group(1)) if period_match else 0
    plan_applicable = has_plan_data or spend > 0
    period_consistent = (
        True if not plan_applicable
        else bool(plan_period_days > 0 and label_days == plan_period_days)
    )
    active_channels = [m for m in channels.values() if m['spend'] > 0]
    high_channels = [name for name, m in channels.items() if m['state'] == '高效渠道']
    normal_channels = [name for name, m in channels.items() if m['state'] == '正常渠道']
    low_channels = [name for name, m in channels.items() if m['state'] == '低效渠道']
    plan_states = Counter(plan['计划效率状态'] for plan in plans)
    mixed_channels = bool(low_channels and (high_channels or normal_channels))
    all_active_low = bool(active_channels) and all(m['state'] == '低效渠道' for m in active_channels)
    structural_opportunity = (
        grade in ('S', 'A+') and cycle in _GROWTH_CYCLES
        and (gmv > 0 or current_link_roi > 0)
    )

    return {
        'row': row, 'columns': set(columns), 'benchmarks': benchmarks,
        'metox_text': metox_text, 'v93_text': v93_text,
        'metox_labels': metox_labels, 'v93_labels': v93_labels, 'labels': labels,
        'opportunity': opportunity, 'risk': risk, 'corrections': corrections,
        'grade': grade, 'position': position, 'cycle': cycle,
        'spend': spend, 'gmv': gmv, 'ad_gmv': ad_gmv, 'ad_carts': ad_carts,
        'promotion_roi': promotion_roi, 'store_promotion_roi': benchmarks.get('promotion_roi', 0.0),
        'previous_link_roi': previous_link_roi, 'current_link_roi': current_link_roi, 'cpa': cpa,
        'data_issues': data_issues, 'non_inventory_risk': non_inventory_risk,
        'high_spend_no_output': high_spend_no_output, 'roi_decline': roi_decline,
        'cpa_abnormal': cpa_abnormal, 'structural_opportunity': structural_opportunity,
        'channels': channels, 'high_channels': high_channels, 'normal_channels': normal_channels,
        'low_channels': low_channels, 'mixed_channels': mixed_channels,
        'all_active_low': all_active_low, 'plans': plans, 'plan_states': plan_states,
        'plan_parse_error': plan_parse_error,
        'has_plan_data': has_plan_data,
        'plan_total': plan_total, 'plan_display': plan_display,
        'plan_coverage': (
            max(0.0, min(plan_coverage, 1.0)) if has_plan_data else None
        ),
        'plan_completeness': max(0.0, min(plan_completeness, 1.0)),
        'plan_period_days': plan_period_days, 'plan_actual_days': plan_actual_days,
        'period_consistent': period_consistent,
        'period_label': period_label, 'source_window': source_window,
    }


def _diagnose_product_issue(ctx) -> dict:
    severe_loss = '亏损风险' in ctx['risk'] or ctx['high_spend_no_output']
    promotion_anomaly = bool(
        ctx['roi_decline'] or ctx['cpa_abnormal']
        or (ctx['non_inventory_risk'] and ctx['spend'] > 0)
    )
    opportunity_risk_conflict = bool(ctx['opportunity'] and ctx['risk'])
    channel_difference = ctx['mixed_channels']
    return {
        'severe_loss': severe_loss,
        'reduce': '削减' in ctx['risk'],
        'saturation': '饱和观察' in ctx['corrections'],
        'spillover': '溢出' in ctx['corrections'],
        'protect': '保护持有' in ctx['corrections'],
        'promotion_anomaly': promotion_anomaly,
        'opportunity_risk_conflict': opportunity_risk_conflict,
        'channel_difference': channel_difference,
    }


def _resolve_product_strategy(ctx, diagnosis) -> tuple[str, str]:
    if ctx['data_issues']:
        return '人工复核任务', '人工复核'

    paid_channels = [item for item in ctx['channels'].values() if item['spend'] > 0]
    if paid_channels and all(item['state'] == '数据不足' for item in paid_channels) and not ctx['plans']:
        return '人工复核任务', '人工复核'

    has_receiver = bool(ctx['high_channels'] or ctx['normal_channels'])
    decline = ctx['cycle'] in _DECLINE_CYCLES
    growth = ctx['cycle'] in _GROWTH_CYCLES
    weak_product = ctx['position'] in ('待改善', '滞销款', '清退款', '清退') or ctx['grade'] in ('C', 'D')

    if diagnosis['severe_loss']:
        if ctx['mixed_channels'] or (ctx['low_channels'] and has_receiver):
            return '渠道调整任务', '预算迁移'
        if ctx['all_active_low'] or ctx['high_spend_no_output']:
            return '止损任务', '暂停推广'
        return '止损任务', '削减投放'

    if diagnosis['reduce']:
        if ctx['all_active_low']:
            return '止损任务', '暂停推广' if decline or weak_product else '削减投放'
        if ctx['mixed_channels']:
            if ctx['position'] in ('金牛款', '潜力款'):
                return '渠道调整任务', '结构优化'
            return '渠道调整任务', '预算迁移'
        if has_receiver:
            if ctx['position'] in ('冲刺款', '金牛款', '潜力款') or diagnosis['spillover']:
                return '渠道调整任务', '结构优化'
            return '止损任务', '削减投放'
        if ctx['spend'] > 0:
            return '止损任务', '暂停低效计划'
        return '人工复核任务', '人工复核'

    if diagnosis['promotion_anomaly']:
        if ctx['mixed_channels']:
            return '渠道调整任务', '预算迁移'
        if ctx['all_active_low']:
            return '止损任务', '削减投放'
        return '渠道调整任务', '结构优化'

    if ctx['opportunity'] or ctx['structural_opportunity']:
        if diagnosis['saturation']:
            return '稳量任务', '继续观察'
        if weak_product or ctx['cycle'] in ('待优化', '观察期'):
            return '优化任务', '优化后复测'
        if decline:
            return '优化任务', '优化后复测'
        if ctx['position'] == '潜力款' or ctx['cycle'] in ('测试期', '上升验证') or ctx['grade'] == 'B':
            return '测试任务', '扩量测试'
        if ctx['position'] == '金牛款' or ctx['cycle'] == '成熟期':
            return '稳量任务', '保持投放'
        if ctx['position'] in ('冲刺款', '明星款') and growth:
            strong = bool(ctx['opportunity'] & {'强主推', '爆发', '加预算'})
            return '放量任务', '重点放量' if strong and ctx['grade'] in ('S', 'A+') else '谨慎放量'
        return '放量任务', '谨慎放量'

    if diagnosis['spillover'] and ctx['mixed_channels']:
        return '渠道调整任务', '结构优化'
    if diagnosis['protect']:
        return '稳量任务', '保持投放'
    if diagnosis['saturation']:
        return '稳量任务', '继续观察'
    if diagnosis['spillover']:
        return '稳量任务', '保持投放'
    return '', ''


def _execution_profile(ctx) -> dict:
    position = ctx['position']
    cycle = ctx['cycle']
    profile = {'increase': '增加10%～20%', 'reduce': '削减20%～30%', 'observation': '3天'}
    if position == '金牛款':
        profile.update(increase='保持，不激进增加', reduce='削减30%～50%', observation='3～7天')
    elif position == '潜力款':
        profile.update(increase='小额测试，不给精确金额', reduce='暂停继续追加', observation='3天')
    elif position in ('待改善', '滞销款', '清退款', '清退'):
        profile.update(increase='暂不增加', reduce='削减30%～50%', observation='3～7天')
    elif position in ('冲刺款', '明星款'):
        profile.update(increase='增加10%～20%', reduce='削减20%～30%', observation='1～3天')

    if cycle == '测试期':
        profile.update(increase='小额测试，不给精确金额', reduce='暂停继续追加', observation='2～3天')
    elif cycle == '成长期':
        profile.update(increase='增加10%～30%', observation='1～3天')
    elif cycle in ('重点', '爆发期', '放量', '适当放量'):
        profile.update(increase='分步增加10%～20%', observation='1～2天')
    elif cycle == '成熟期':
        profile.update(increase='保持，不激进增加', reduce='削减30%～50%', observation='3～7天')
    elif cycle in ('待优化', '观察期'):
        profile.update(increase='暂不增加', reduce='削减30%～50%', observation='3～7天')
    elif cycle == '衰退预警':
        profile.update(increase='暂停新增', reduce='削减20%～40%', observation='2～5天')
    elif cycle in ('衰退期', '清退期', '淘汰'):
        profile.update(increase='停止新增', reduce='削减50%或暂停', observation='1～3天')
    return profile


def _profile_allows_increase(profile: dict) -> bool:
    """Only numeric scale-up profiles may produce an explicit increase action."""
    increase = str(profile.get('increase', '')).strip()
    return increase.startswith('增加') or increase.startswith('分步增加')


def _channel_action_for(ctx, strategy: str, channel: str, metrics: dict, profile: dict) -> dict:
    state = metrics['state']
    if state == '暂无投放':
        if strategy in ('重点放量', '谨慎放量', '扩量测试'):
            return {'action': '小额测试', 'text': '暂无投放；如需新增仅做小额测试，不做预算迁移'}
        return {'action': '无动作', 'text': '暂无投放，不生成预算迁移动作'}
    if state == '数据不足':
        return {'action': '人工复核', 'text': '保持现状；归因样本不足，人工复核后再调整'}
    if strategy == '人工复核':
        return {'action': '人工复核', 'text': '保持现状，等待商品/计划策略冲突复核'}
    if strategy == '暂停推广':
        return {'action': '暂停', 'text': '暂停该渠道有效投放并在1～3天内复核'}
    if state == '低效渠道':
        if strategy in ('暂停低效计划', '削减投放') and metrics['roi'] <= 0 and metrics['spend'] >= 100:
            return {'action': '暂停', 'text': '暂停无归因产出的低效投放'}
        return {'action': '削减', 'text': f"{profile['reduce']}；{metrics['reason']}"}
    if state == '高效渠道':
        if strategy in ('重点放量', '谨慎放量', '预算迁移') and _profile_allows_increase(profile):
            return {'action': '增加', 'text': f"{profile['increase']}；承接有效增量"}
        if strategy == '预算迁移':
            return {'action': '保持', 'text': '保持高效投放，作为迁移承接方；不新增总预算'}
        if strategy in ('扩量测试', '优化后复测'):
            return {'action': '小额测试', 'text': f"{profile['increase']}；先验证新增流量"}
        return {'action': '保持', 'text': '保持高效投放，不激进增加'}
    if strategy in ('重点放量', '谨慎放量') and ctx['position'] not in ('金牛款', '待改善', '滞销款'):
        return {'action': '增加', 'text': f"{profile['increase']}；逐步验证边际效率"}
    if strategy in ('扩量测试', '优化后复测'):
        return {'action': '小额测试', 'text': f"{profile['increase']}；达到成功线后再扩量"}
    if strategy in ('削减投放', '暂停低效计划'):
        return {'action': '削减', 'text': f"{profile['reduce']}；保留必要观察量"}
    return {'action': '保持', 'text': '保持当前投放，观察ROI和成交稳定性'}


def _build_channel_strategy(ctx, strategy: str, profile: dict) -> dict[str, dict]:
    return {
        channel: {
            **metrics,
            **_channel_action_for(ctx, strategy, channel, metrics, profile),
        }
        for channel, metrics in ctx['channels'].items()
    }


def _plan_action_for(ctx, strategy: str, plan: dict, profile: dict) -> tuple[str, str, str]:
    state = plan['计划效率状态']
    spend = to_num(plan.get('当前花费', 0))
    roi = plan.get('ROI')
    if strategy == '人工复核' or state == '数据不足':
        return '人工复核', '待人工确认', '计划归因数据不足或商品级策略待复核'
    if state == '暂无投放':
        if strategy in ('扩量测试', '优化后复测'):
            return '恢复测试', '小额测试，不给精确金额', '商品需要验证且计划当前无花费'
        return '人工复核', '待人工确认', '计划当前无花费，不凭空生成预算动作'
    if strategy == '暂停推广':
        return '暂停', '-100%', '商品整体止损且当前计划有花费'
    if state == '低效计划':
        if (roi is not None and to_num(roi) <= 0 and spend >= 100) or strategy == '暂停低效计划':
            return '暂停', '-100%', '计划有有效花费但无归因产出'
        return '削减', profile['reduce'].replace('削减', '-'), plan['_效率依据']
    if state == '高效计划':
        if strategy in ('重点放量', '谨慎放量', '预算迁移') and _profile_allows_increase(profile):
            return '增加', profile['increase'].replace('增加', '+').replace('分步', ''), plan['_效率依据']
        if strategy == '预算迁移':
            return '保持', '0%', f"{plan['_效率依据']}；作为预算迁移承接方但不新增总预算"
        if strategy in ('扩量测试', '优化后复测'):
            return '小额测试', profile['increase'], plan['_效率依据']
        return '保持', '0%', plan['_效率依据']
    if strategy in ('重点放量', '谨慎放量') and ctx['position'] not in ('金牛款', '待改善', '滞销款'):
        return '增加', profile['increase'].replace('增加', '+').replace('分步', ''), '计划效率正常，按商品策略渐进验证'
    if strategy in ('扩量测试', '优化后复测'):
        return '小额测试', profile['increase'], '计划效率正常，保留小额验证'
    if strategy in ('削减投放', '暂停低效计划'):
        return '削减', profile['reduce'].replace('削减', '-'), '商品处于止损策略，计划未达到高效标准'
    return '保持', '0%', '计划效率正常，按商品策略保持'


def _plan_validation(action: str, observation: str) -> tuple[str, str]:
    if action in ('增加', '小额测试', '恢复测试'):
        return (
            '新增花费产生增量成交，且计划ROI不低于调整前的80%',
            'ROI较调整前下降超过20%，或连续2天新增花费无增量成交时回撤',
        )
    if action in ('削减', '暂停'):
        return (
            '无效花费下降，保留计划或商品整体ROI改善',
            '削减后整体成交明显受损且高效渠道无法承接时恢复并复核',
        )
    if action == '保持':
        return '计划ROI和成交保持稳定', '连续2天ROI明显下滑时重新判断'
    return '补齐计划数据并形成可执行结论', '计划数据未确认前不调整预算'


def _build_plan_actions(ctx, strategy: str, profile: dict) -> list[dict]:
    actions = []
    for plan in ctx['plans']:
        action, ratio, basis = _plan_action_for(ctx, strategy, plan, profile)
        success, stop = _plan_validation(action, profile['observation'])
        plan_id = plan['计划ID'] or '执行表未提供'
        product_id = _text(ctx['row'].get('宝贝ID', '')) or '执行表未提供'
        actions.append({
            '动作对象': '商品-计划级',
            '执行对象': f'宝贝ID {product_id} × PlanID {plan_id}',
            '计划ID': plan_id,
            '计划名称': plan['计划名称'],
            '所属渠道': plan['所属渠道'],
            '当前花费': round(to_num(plan.get('当前花费', 0)), 2),
            '归因成交': plan.get('归因成交', ''),
            'ROI': '' if plan.get('ROI') is None else round(to_num(plan.get('ROI')), 2),
            'CPA': plan.get('CPA', ''),
            '点击': plan.get('点击', ''),
            '当前状态': plan['当前状态'],
            '计划效率状态': plan['计划效率状态'],
            '建议动作': action,
            '建议调整比例': ratio,
            '动作依据': basis,
            '观察周期': profile['observation'],
            '成功标准': success,
            '停止条件': stop,
            '数据来源': f"{ctx['source_window']}商品×PlanID数据",
        })
    return actions


def _plan_summary(plan_actions: list[dict], parse_error: str, spend: float) -> str:
    if not plan_actions:
        if parse_error or spend > 0:
            return '计划明细不足，请人工复核'
        return '无在投计划明细'
    counts = Counter(item['建议动作'] for item in plan_actions)
    order = ('增加', '保持', '削减', '暂停', '小额测试', '恢复测试', '人工复核')
    parts = [f'{action}{counts[action]}个' for action in order if counts[action]]
    return f"{len(plan_actions)}个计划：" + '｜'.join(parts)


def _build_execution_validation(ctx, task_type: str, strategy: str, profile: dict) -> tuple[str, str, str]:
    observation = profile['observation']
    if strategy in ('重点放量', '谨慎放量', '扩量测试', '优化后复测'):
        return (
            observation,
            '新增花费带来增量成交，商品整体ROI不低于调整前的80%',
            'ROI下降超过20%，或连续2天新增花费无增量成交时停止并回撤',
        )
    if strategy in ('预算迁移', '结构优化', '暂停低效计划'):
        return (
            observation,
            '低效渠道花费下降，高效或正常渠道成交保持，商品整体ROI改善',
            '迁移或削减后整体ROI继续下降、成交明显受损时恢复原结构',
        )
    if strategy in ('削减投放', '暂停推广'):
        return (
            observation,
            '无效花费下降，保留部分的ROI或CPA改善',
            '仍持续高花费无有效产出时进一步暂停；误伤成交时人工复核',
        )
    if strategy == '保持投放':
        return observation, '成交与ROI保持稳定', 'ROI连续2天下降超过20%时重新判断'
    if strategy == '继续观察':
        return observation, '边际ROI和成交保持稳定，未出现进一步饱和', 'ROI持续下滑或新增花费无增量时停止扩量'
    return '1～3天', '数据与动作冲突完成核对', '关键数据未确认前不调整预算'


def _build_non_promotion_action(ctx, strategy: str) -> str:
    actions = []
    if ctx['position'] in ('待改善', '滞销款') or ctx['cycle'] in ('待优化', '观察期', '衰退预警', '衰退期'):
        actions.append('复核主图、详情、价格与商品承接')
    if ctx['non_inventory_risk']:
        actions.append('复核退款或亏损原因')
    return '；'.join(actions) if actions else '无'


def _build_inventory_alert(row, columns) -> str:
    """库存只生成提醒，不参与任何策略、优先级和动作。"""
    if '在仓库存' not in columns:
        return '库存数据缺失'
    raw_stock = row.get('在仓库存', None)
    if raw_stock is None or pd.isna(raw_stock) or _text(raw_stock) == '':
        return '库存数据缺失'
    stock = to_num(raw_stock)
    visitors = to_num(row.get('商品访客数', 0))
    if stock < 10:
        return '库存偏低，请关注'
    if stock > 50 and visitors < 20:
        return '库存较高，请关注动销'
    return '库存正常'


def _data_source_labels(ctx) -> tuple[str, str, str]:
    window = ctx['source_window']
    return (
        f'{window}商品汇总数据',
        f'{window}商品×渠道数据',
        f'{window}商品×PlanID数据（Top3展示）',
    )


def _plan_data_explanation(ctx) -> str:
    period = ctx['period_label'] or '当前执行表口径'
    if not ctx['has_plan_data']:
        return f'{period}｜无计划数据'
    return (
        f'{period}｜Top3关键计划（按周期花费排序）｜'
        f'覆盖{ctx["plan_display"]}/{ctx["plan_total"]}个计划｜'
        f'花费覆盖{ctx["plan_coverage"]:.1%}'
    )


def _ai_confidence(ctx) -> tuple[str, int, str]:
    """生成唯一综合可信度和运营检查清单，不回流任何业务判断。"""
    product_fields = (
        '宝贝ID', '综合等级', '商品定位', '商品周期',
        'GMV', '商品访客数', '链接投产',
    )
    product_complete = sum(
        _field_available(ctx['row'], ctx['columns'], field)
        for field in product_fields
    ) / len(product_fields)
    channel_complete = sum(
        metrics['state'] != '数据不足' for metrics in ctx['channels'].values()
    ) / max(len(ctx['channels']), 1)
    if not ctx['has_plan_data']:
        score = min(round(50 * product_complete + 50 * channel_complete), 89)
        star_count = 4 if score >= 75 else 3 if score >= 60 else 2 if score >= 40 else 1
        display = f'{"★" * star_count}{"☆" * (5 - star_count)}（{score}%）'
        reasons = [
            '✓ 商品数据完整' if product_complete == 1 else '△ 商品关键数据缺失',
            '✓ 渠道数据完整' if channel_complete == 1 else '△ 部分渠道数据不足',
            '△ 当前无计划数据，计划可信度未参与计算',
            '— Top3计划覆盖率不适用',
            '— 计划周期一致性不适用',
        ]
        return display, score, '\n'.join(reasons)

    plan_applicable = True
    plan_complete = ctx['plan_completeness'] if plan_applicable else 1.0
    if plan_applicable and (ctx['plan_parse_error'] or (ctx['plan_total'] > 0 and not ctx['plans'])):
        plan_complete = 0.0
    plan_coverage = ctx['plan_coverage'] if plan_applicable else 1.0
    period_consistent = 1.0 if ctx['period_consistent'] else 0.0
    missing_data_complete = 0.0 if (ctx['data_issues'] or ctx['plan_parse_error']) else 1.0
    metox_complete = float(all(
        _field_available(ctx['row'], ctx['columns'], field, allow_blank=True)
        for field in ('MetoX算法命中', 'MetoX算法分', 'MetoX算法原因')
    ))
    score = round(
        20 * product_complete
        + 20 * channel_complete
        + 20 * plan_complete
        + 15 * plan_coverage
        + 15 * period_consistent
        + 5 * missing_data_complete
        + 5 * metox_complete
    )
    score = max(0, min(int(score), 100))
    star_count = 5 if score >= 90 else 4 if score >= 75 else 3 if score >= 60 else 2 if score >= 40 else 1
    display = f'{"★" * star_count}{"☆" * (5 - star_count)}（{score}%）'
    reasons = [
        '✓ 商品数据完整' if product_complete == 1 else '△ 商品关键数据缺失',
        '✓ 渠道数据完整' if channel_complete == 1 else '△ 部分渠道数据不足',
        '✓ 商品-计划数据完整' if plan_complete == 1 else '△ 部分计划数据缺失',
    ]
    if plan_applicable and ctx['plan_total'] > ctx['plan_display']:
        reasons.append(f'△ 当前仅展示Top3关键计划（花费覆盖{plan_coverage:.0%}）')
    else:
        reasons.append('✓ 关键计划展示完整')
    reasons.extend([
        '✓ 周期统计一致' if period_consistent == 1 else '△ 周期统计口径待核对',
        '✓ 未发现关键数据缺失' if missing_data_complete == 1 else '△ 存在关键数据缺失',
        '✓ MetoX数据完整' if metox_complete == 1 else '△ MetoX数据不完整',
    ])
    return display, score, '\n'.join(reasons)


def _consistency_conflicts(ctx, strategy: str, channels: dict, plans: list[dict]) -> list[str]:
    conflicts = []
    channel_actions = [item['action'] for item in channels.values() if item['spend'] > 0]
    plan_actions = [item['建议动作'] for item in plans]
    has_receiver = bool(ctx['high_channels'] or ctx['normal_channels'])
    if strategy == '预算迁移':
        if not (ctx['low_channels'] and has_receiver):
            conflicts.append('预算迁移缺少真实低效渠道或可承接渠道')
        if channel_actions and all(action in ('削减', '暂停') for action in channel_actions):
            conflicts.append('所有在投渠道均削减/暂停，不能称为预算迁移')
    if strategy in ('重点放量', '谨慎放量'):
        actionable = channel_actions + plan_actions
        if actionable and not any(action in ('增加', '小额测试') for action in actionable):
            conflicts.append('放量策略没有任何渠道或计划执行增加/测试')
        if plan_actions and all(action in ('保持', '削减', '暂停') for action in plan_actions):
            conflicts.append('放量策略与全部计划动作不一致')
    if strategy == '暂停推广':
        if any(action in ('增加', '保持', '小额测试', '恢复测试') for action in plan_actions):
            conflicts.append('暂停推广但仍有计划建议增加/保持/测试')
    return conflicts


def _manualize_actions(channels: dict, plans: list[dict], reason: str) -> tuple[dict, list[dict]]:
    for metrics in channels.values():
        if metrics['spend'] > 0:
            metrics['action'] = '人工复核'
            metrics['text'] = f'保持现状，人工复核：{reason}'
    for plan in plans:
        plan['建议动作'] = '人工复核'
        plan['建议调整比例'] = '待人工确认'
        plan['动作依据'] = reason
        plan['成功标准'] = '动作冲突完成核对并形成唯一策略'
        plan['停止条件'] = '冲突未解决前不调整预算'
    return channels, plans


def _calculate_task_priority(ctx, task_type: str, strategy: str, conflicts: list[str]) -> tuple[str, int]:
    diagnosis = _diagnose_product_issue(ctx)
    if conflicts or diagnosis['opportunity_risk_conflict']:
        score = 90
    elif task_type == '止损任务':
        score = 84 if diagnosis['severe_loss'] else 76
    elif task_type == '放量任务':
        score = 78 if ctx['opportunity'] & {'强主推', '爆发', '加预算'} else 70
    elif task_type == '渠道调整任务':
        score = 66
    elif task_type == '测试任务':
        score = 60
    elif task_type == '优化任务':
        score = 52
    elif task_type == '稳量任务':
        score = 42
    elif task_type == '人工复核任务':
        score = 30
    else:
        score = 20

    spend_p75 = ctx['benchmarks'].get('spend_p75', 0.0)
    gmv_p75 = ctx['benchmarks'].get('gmv_p75', 0.0)
    if spend_p75 > 0 and ctx['spend'] >= spend_p75:
        score += 5
    if gmv_p75 > 0 and ctx['gmv'] >= gmv_p75:
        score += 4
    if strategy in ('重点放量', '暂停推广'):
        score += 4
    if ctx['plan_parse_error'] and ctx['spend'] > 0:
        score -= 4
    if ctx['data_issues']:
        score = min(score, 35)
    score = max(0, min(int(score), 100))
    if score >= 85:
        priority = 'P1'
    elif score >= 60:
        priority = 'P2'
    elif score >= 40:
        priority = 'P3'
    else:
        priority = 'P4'
    return priority, score


def _build_task_reason(ctx, diagnosis, conflicts) -> str:
    reasons = []
    if ctx['metox_labels']:
        reasons.append('MetoX:' + '/'.join(sorted(ctx['metox_labels'])))
    if ctx['v93_labels']:
        reasons.append('v9.3:' + '/'.join(sorted(ctx['v93_labels'])))
    metox_reason = _text(ctx['row'].get('MetoX算法原因', ''))
    if metox_reason:
        reasons.append('MetoX原因:' + metox_reason[:160])
    reasons.extend(ctx['data_issues'])
    if diagnosis['opportunity_risk_conflict']:
        reasons.append('机会与风险信号并存，风险优先')
    if ctx['high_spend_no_output']:
        reasons.append(f"花费{ctx['spend']:.0f}但推广成交为0")
    if ctx['roi_decline']:
        reasons.append(f"链接ROI较上周期下降({ctx['previous_link_roi']:.2f}→{ctx['current_link_roi']:.2f})")
    if ctx['cpa_abnormal']:
        reasons.append('CPA异常或无有效推广加购')
    if ctx['non_inventory_risk']:
        reasons.append('执行表存在退款或亏损风险')
    state_text = '、'.join(f"{name}{metrics['state']}" for name, metrics in ctx['channels'].items())
    reasons.append('渠道:' + state_text)
    if ctx['plan_parse_error'] and ctx['spend'] > 0:
        reasons.append(ctx['plan_parse_error'])
    else:
        reasons.append(
            f"已解析周期Top3计划{len(ctx['plans'])}个（完整周期共{ctx['plan_total']}个）"
        )
    reasons.append(
        f"综合等级{ctx['grade'] or '空'}；商品定位{ctx['position'] or '空'}；商品周期{ctx['cycle'] or '空'}"
    )
    if conflicts:
        reasons.append('动作冲突:' + '/'.join(conflicts))
    return '；'.join(reasons)


def _should_show_task(ctx, task_type: str, diagnosis) -> bool:
    if not task_type:
        return False
    if ctx['labels'] or ctx['v93_labels']:
        return True
    if ctx['spend'] > 0 and (
        diagnosis['promotion_anomaly'] or ctx['low_channels'] or ctx['mixed_channels']
    ):
        return True
    if any(plan['计划效率状态'] == '低效计划' for plan in ctx['plans']):
        return True
    return False


def _main_action(channels: dict, plan_summary: str, strategy: str) -> str:
    channel_parts = []
    for channel in ('关键词', '人群', '全站'):
        item = channels[channel]
        channel_parts.append(f"{channel}:{item['text']}")
    return f"商品策略:{strategy}；" + '；'.join(channel_parts) + f"；计划:{plan_summary}"


def parse_plan_execution_details(value) -> pd.DataFrame:
    """把 AI 任务表中的计划执行明细转回用于页面展示的 DataFrame。"""
    if isinstance(value, list):
        records = value
    else:
        text = _text(value)
        if not text:
            return pd.DataFrame(columns=PLAN_DETAIL_COLUMNS)
        try:
            records = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return pd.DataFrame(columns=PLAN_DETAIL_COLUMNS)
    if not isinstance(records, list):
        return pd.DataFrame(columns=PLAN_DETAIL_COLUMNS)
    return pd.DataFrame(records).reindex(columns=PLAN_DETAIL_COLUMNS)


def build_ai_task_view(df: pd.DataFrame) -> pd.DataFrame:
    """从最终执行表生成一行一商品的只读商品/渠道/计划任务。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=AI_TASK_VIEW_COLUMNS)

    source = df.copy(deep=True)
    columns = set(source.columns)
    benchmarks = _task_benchmarks(source)
    tasks = []
    for _, row in source.iterrows():
        ctx = _build_product_context(row, columns, benchmarks)
        diagnosis = _diagnose_product_issue(ctx)
        task_type, strategy = _resolve_product_strategy(ctx, diagnosis)
        if not _should_show_task(ctx, task_type, diagnosis):
            continue
        profile = _execution_profile(ctx)
        channel_actions = _build_channel_strategy(ctx, strategy, profile)
        plan_actions = _build_plan_actions(ctx, strategy, profile)
        conflicts = _consistency_conflicts(ctx, strategy, channel_actions, plan_actions)
        if conflicts:
            task_type, strategy = '人工复核任务', '人工复核'
            channel_actions, plan_actions = _manualize_actions(
                channel_actions, plan_actions, '/'.join(conflicts)
            )
        observation, success, stop = _build_execution_validation(
            ctx, task_type, strategy, profile
        )
        summary = _plan_summary(plan_actions, ctx['plan_parse_error'], ctx['spend'])
        priority, score = _calculate_task_priority(ctx, task_type, strategy, conflicts)
        product_source, channel_source, plan_source = _data_source_labels(ctx)
        confidence, confidence_score, confidence_reason = _ai_confidence(ctx)
        tasks.append({
            '执行优先级': priority,
            '任务优先分': score,
            '款式主图': _raw_or_blank(row.get('款式主图', '')),
            '货号': _raw_or_blank(row.get('货号', '')),
            '链接数': _raw_or_blank(row.get('链接数', '')),
            '宝贝ID': _raw_or_blank(row.get('宝贝ID', '')),
            'GMV': _raw_or_blank(row.get('GMV', '')),
            '商品访客数': _raw_or_blank(row.get('商品访客数', '')),
            '链接投产': _raw_or_blank(row.get('链接投产', '')),
            '淘宝类目': _raw_or_blank(row.get('淘宝类目', '')),
            '综合等级': _raw_or_blank(row.get('综合等级', '')),
            '商品定位': _raw_or_blank(row.get('商品定位', '')),
            '商品周期': _raw_or_blank(row.get('商品周期', '')),
            'MetoX算法命中': _raw_or_blank(row.get('MetoX算法命中', '')),
            'AI任务类型': task_type,
            '商品级总策略': strategy,
            '动作对象': '商品-计划级',
            '主执行动作': _main_action(channel_actions, summary, strategy),
            '关键词状态': channel_actions['关键词']['state'],
            '关键词动作': channel_actions['关键词']['text'],
            '人群状态': channel_actions['人群']['state'],
            '人群动作': channel_actions['人群']['text'],
            '全站状态': channel_actions['全站']['state'],
            '全站动作': channel_actions['全站']['text'],
            '推广计划数': len(plan_actions),
            '计划总数': ctx['plan_total'],
            '计划展示数': ctx['plan_display'],
            '计划覆盖率': (
                f"{ctx['plan_coverage']:.1%}" if ctx['has_plan_data'] else '无计划'
            ),
            '计划数据完整度': (
                '无计划'
                if ctx['spend'] <= 0 and not ctx['has_plan_data']
                else f"{ctx['plan_completeness']:.1%}"
            ),
            '计划数据说明': _plan_data_explanation(ctx),
            'AI可信度': confidence,
            'AI可信度分': confidence_score,
            'AI可信度说明': confidence_reason,
            '商品策略数据源': product_source,
            '渠道动作数据源': channel_source,
            '计划动作数据源': plan_source,
            '计划执行摘要': summary,
            '计划执行明细': json.dumps(plan_actions, ensure_ascii=False),
            '非投放动作': _build_non_promotion_action(ctx, strategy),
            '观察周期': observation,
            '成功标准': success,
            '停止条件': stop,
            '任务触发原因': _build_task_reason(ctx, diagnosis, conflicts),
            '库存提醒': _build_inventory_alert(row, columns),
        })

    result = pd.DataFrame(tasks, columns=AI_TASK_VIEW_COLUMNS)
    if result.empty:
        return result
    priority_order = result['执行优先级'].map({'P1': 1, 'P2': 2, 'P3': 3, 'P4': 4}).fillna(5)
    return (
        result.assign(_priority_order=priority_order)
        .sort_values(['_priority_order', '任务优先分'], ascending=[True, False], kind='stable')
        .drop(columns=['_priority_order'])
        .reset_index(drop=True)
    )
