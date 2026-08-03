"""推广计划周期指标的序列化、累计与 Top3 展示。"""
from __future__ import annotations

import json

import pandas as pd

from .schema import safe_div, to_num


FULL_PLAN_DETAIL_COLUMN = '推广计划全量明细'
PLAN_TOTAL_COUNT_COLUMN = '推广计划总数'
PLAN_DISPLAY_COUNT_COLUMN = '推广计划展示数'
PLAN_COVERAGE_COLUMN = '计划覆盖率'
PLAN_COMPLETENESS_COLUMN = '计划数据完整度'
PLAN_PERIOD_DAYS_COLUMN = '计划统计天数'
PLAN_ACTUAL_DAYS_COLUMN = '计划实际数据天数'
PLAN_PERIOD_LABEL_COLUMN = '计划数据口径'
PLAN_PERIOD_RANGE_COLUMN = '计划数据范围'

PLAN_METADATA_COLUMNS = [
    FULL_PLAN_DETAIL_COLUMN,
    PLAN_TOTAL_COUNT_COLUMN,
    PLAN_DISPLAY_COUNT_COLUMN,
    PLAN_COVERAGE_COLUMN,
    PLAN_COMPLETENESS_COLUMN,
    PLAN_PERIOD_DAYS_COLUMN,
    PLAN_ACTUAL_DAYS_COLUMN,
    PLAN_PERIOD_LABEL_COLUMN,
    PLAN_PERIOD_RANGE_COLUMN,
]


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    text = str(value).strip()
    return '' if text in ('', 'nan', 'None', '<NA>') else text


def _first_text(*values) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ''


def _normalize_record(item: dict) -> dict | None:
    plan_id = _first_text(item.get('计划ID'), item.get('计划编号'), item.get('plan_id'))
    if not plan_id:
        return None
    spend = to_num(item.get('当前花费', item.get('花费', item.get('spend', 0))))
    attributed_raw = item.get('归因成交', item.get('推广成交金额', item.get('attributed_gmv')))
    roi_raw = item.get('ROI', item.get('投产', item.get('roi')))
    attributed = (
        to_num(attributed_raw)
        if attributed_raw not in (None, '')
        else spend * to_num(roi_raw)
    )
    carts = to_num(item.get('加购', item.get('推广加购数', item.get('add_cart', 0))))
    clicks = to_num(item.get('点击', item.get('clicks', 0)))
    return {
        '计划ID': plan_id,
        '计划名称': _first_text(item.get('计划名称'), item.get('name')),
        '所属渠道': _first_text(item.get('所属渠道'), item.get('渠道'), item.get('channel')),
        '当前花费': spend,
        '归因成交': attributed,
        '加购': carts,
        '点击': clicks,
        '当前状态': _first_text(item.get('当前状态'), item.get('状态'), item.get('status')),
    }


def parse_full_plan_details(value) -> list[dict]:
    """解析管道内部全量计划 JSON；无效记录安全忽略。"""
    if isinstance(value, list):
        payload = value
    elif isinstance(value, dict):
        payload = value.get('plans', [value])
    else:
        text = _text(value)
        if not text:
            return []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(payload, dict):
        payload = payload.get('plans', [payload])
    if not isinstance(payload, list):
        return []
    records = []
    for item in payload:
        if isinstance(item, dict):
            normalized = _normalize_record(item)
            if normalized is not None:
                records.append(normalized)
    return records


def serialize_full_plan_details(records: list[dict]) -> str:
    return json.dumps(records, ensure_ascii=False, separators=(',', ':'))


def _period_label(period_days: int) -> str:
    return '当日累计' if period_days == 1 else f'近{period_days}天累计'


def _format_number(value) -> str:
    number = to_num(value)
    if float(number).is_integer():
        return str(int(number))
    return f'{number:.2f}'.rstrip('0').rstrip('.')


def format_top_plan_details(records: list[dict]) -> str:
    """把周期累计后的展示计划格式化为执行表可读文本。"""
    lines = []
    for index, record in enumerate(records, start=1):
        channel = _text(record.get('所属渠道')) or '执行表未提供'
        lines.append(
            f"{index}. 计划ID:{record['计划ID']} 所属渠道:{channel} "
            f"花费:{to_num(record.get('当前花费')):.2f} "
            f"归因成交:{to_num(record.get('归因成交')):.2f} "
            f"投产:{to_num(record.get('ROI')):.2f} "
            f"CPA:{to_num(record.get('CPA')):.2f} "
            f"加购:{_format_number(record.get('加购'))} "
            f"点击:{_format_number(record.get('点击'))}"
        )
    return '\n'.join(lines)


def summarize_plan_records(
    records: list[dict],
    period_days: int,
    actual_days: int,
    period_start: str = '',
    period_end: str = '',
    top_n: int = 3,
    full_source_available: bool = True,
) -> dict:
    """按 PlanID 累计完整周期，再生成 Top3 展示及覆盖指标。"""
    accumulated: dict[str, dict] = {}
    for raw in records:
        record = _normalize_record(raw)
        if record is None:
            continue
        plan_id = record['计划ID']
        target = accumulated.setdefault(plan_id, {
            '计划ID': plan_id,
            '计划名称': '',
            '所属渠道': '',
            '当前花费': 0.0,
            '归因成交': 0.0,
            '加购': 0.0,
            '点击': 0.0,
            '当前状态': '',
        })
        target['计划名称'] = _first_text(target['计划名称'], record.get('计划名称'))
        target['所属渠道'] = _first_text(target['所属渠道'], record.get('所属渠道'))
        target['当前状态'] = _first_text(record.get('当前状态'), target['当前状态'])
        for field in ('当前花费', '归因成交', '加购', '点击'):
            target[field] += to_num(record.get(field, 0))

    plans = []
    for target in accumulated.values():
        spend = target['当前花费']
        if spend <= 0:
            continue
        target['ROI'] = safe_div(target['归因成交'], spend)
        target['CPA'] = safe_div(spend, target['加购'])
        plans.append(target)
    plans.sort(key=lambda item: (-item['当前花费'], item['计划ID']))

    displayed = plans[:top_n]
    total_spend = sum(item['当前花费'] for item in plans)
    displayed_spend = sum(item['当前花费'] for item in displayed)
    expected_days = max(int(period_days or 1), 1)
    loaded_days = max(min(int(actual_days or 0), expected_days), 0)
    completeness = loaded_days / expected_days if full_source_available else 0.0
    coverage = safe_div(displayed_spend, total_spend) if total_spend > 0 else 1.0
    period_range = ''
    if period_start and period_end:
        period_range = period_start if period_start == period_end else f'{period_start}~{period_end}'

    return {
        '推广明细': format_top_plan_details(displayed),
        FULL_PLAN_DETAIL_COLUMN: serialize_full_plan_details(plans),
        PLAN_TOTAL_COUNT_COLUMN: len(plans),
        PLAN_DISPLAY_COUNT_COLUMN: len(displayed),
        PLAN_COVERAGE_COLUMN: coverage,
        PLAN_COMPLETENESS_COLUMN: completeness,
        PLAN_PERIOD_DAYS_COLUMN: expected_days,
        PLAN_ACTUAL_DAYS_COLUMN: loaded_days,
        PLAN_PERIOD_LABEL_COLUMN: _period_label(expected_days),
        PLAN_PERIOD_RANGE_COLUMN: period_range,
    }


def summarize_period_product_plans(
    product_rows: pd.DataFrame,
    period_days: int,
    actual_days: int,
    period_start: str = '',
    period_end: str = '',
    top_n: int = 3,
) -> dict:
    records = []
    full_source_available = FULL_PLAN_DETAIL_COLUMN in product_rows.columns
    if full_source_available:
        for value in product_rows[FULL_PLAN_DETAIL_COLUMN]:
            records.extend(parse_full_plan_details(value))
    return summarize_plan_records(
        records,
        period_days=period_days,
        actual_days=actual_days,
        period_start=period_start,
        period_end=period_end,
        top_n=top_n,
        full_source_available=full_source_available,
    )
