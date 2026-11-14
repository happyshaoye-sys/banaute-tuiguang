"""Read-only budget decision context built from the final execution table."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from .budget_role import (
    ROLE_DAYS_KEY,
    ROLE_LAYER_COLUMNS,
    ROLE_LAYER_ENABLED,
    gate_budget_decisions,
)
from .plan_metrics import (
    CURRENT_ACTIVE_PLAN_COUNT_COLUMN,
    FULL_PLAN_DETAIL_COLUMN,
    PLAN_ACTUAL_DAYS_COLUMN,
    parse_full_plan_details,
)
from .schema import safe_div, to_num


# 仅在预算看板/导出边界过滤旧上下文字段，避免缓存带回已退役功能。
BUDGET_DECISION_REMOVED_COLUMNS = frozenset({
    '预算角色', '预算动作', '角色候选', '角色状态', '角色原因', '角色风险',
    '推广商品定位', '候选', '状态', '判定依据',
    '人工业务标记', '人工标记', '本月主推', '应季状态', '季节状态',
    '人工清仓', 'NOCAP', '生效月份', '标记备注',
    'manual_marks', 'manual_state', 'effective_month',
})


def is_removed_budget_column(column) -> bool:
    """旧角色及人工标记列不再进入预算页面或任何预算工作表。"""
    text = str(column).strip()
    return text in BUDGET_DECISION_REMOVED_COLUMNS or any(
        token in text.lower() for token in (
            '预算角色', '预算动作', '角色候选', '角色状态', '角色原因', '角色风险',
            '人工业务标记', '人工标记', 'budget_role', 'manual_marks',
        )
    )


PASSTHROUGH_PRODUCT_COLUMNS = [
    '预算风险',
    '近7日日均GMV',
    '上一7日日均GMV',
    'GMV趋势率',
    'TH_MAIN',
    'TH_MID',
    '阈值版本',
]

#: 商品预算决策表的**规范列顺序**（看板与导出共用这一份，运营指定顺序）。
#: 角色层列（决策角色/角色闸门/决策角色依据/角色闸门依据/风险命中/角色闸门命中）
#: 与阈值列（TH_MAIN/TH_MID/阈值版本）都在这份顺序里占固定位置；
#: 角色层关闭时这些列仍存在，只是取值为空（表的形状不随开关变化）。
PRODUCT_COLUMNS = [
    '宝贝ID', '款式主图', '货号', '淘宝类目', '排名档', '销售排名',
    '预算风险', '决策角色', '角色闸门', '当前有效投放计划数',
    '未来7日建议调整金额', '预算错配金额', '库存提醒',
    '近7日日均GMV', '上一7日日均GMV', 'GMV趋势率',
    '链接投产', '推广投产',
    'GMV', '付费', '销售占比', '花费占比', '占比差(pp)', '基准应得花费',
    '未来7日花费基线', '未来7日建议花费', '预算匹配状态', '动作依据',
    '综合等级', '商品周期', '商品定位', '推广动作', 'MetoX算法命中',
    '决策角色依据', '角色闸门依据', '风险命中', '角色闸门命中',
    'TH_MAIN', 'TH_MID', '阈值版本',
]

BUDGET_DECISION_MAIN_COLUMNS = list(PRODUCT_COLUMNS)

#: 角色层列（可选）：仅在 roles 打开时进入商品决策行与导出尾列。
#: 刻意不复用已退役的「预算角色 / 角色状态 / 角色原因 / 角色风险」列名。
BUDGET_DECISION_ROLE_COLUMNS = list(ROLE_LAYER_COLUMNS) + ['角色闸门命中']

ZERO_GMV_RANK_BAND = '零GMV回收池'
RANK_BAND_ORDER = [
    'Top1-5',
    'Top6-10',
    'Top11-20',
    'Top21-50',
    'Top51-100',
    'Top101-尾部',
    ZERO_GMV_RANK_BAND,
]
SALES_TIER_ORDER = RANK_BAND_ORDER

_TEXT_FIELDS = (
    '款式主图', '货号', '淘宝类目', '创建时间', '统计日期', '综合等级', '商品周期', '商品定位',
    '推广动作', 'MetoX算法命中', '对应操作', '件单价', '成本价（含税）', '盈亏平衡线',
    '预算风险', '退款风险', '风险标签', '风控标签',
)
_NUMERIC_SUM_FIELDS = (
    'GMV', '付费', '推广成交金额', '商品访客数', '商品加购件数',
    '点击', '成功退款金额',
)
_REFUND_RATE_FIELDS = ('当前退款率', '退款率')
_CLOSEOUT_TOKENS = ('衰退预警', '衰退期', '观察期', '清退期', '淘汰')
_TEST_TOKENS = ('测试期',)


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value) -> str:
    if _is_missing(value):
        return ''
    text = str(value).strip()
    return '' if text in ('', 'nan', 'None', '<NA>', 'NaT') else text


def _first_nonblank(values: Iterable) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ''


def _clean_product_id(value) -> str:
    text = _text(value)
    if text.endswith('.0'):
        return text[:-2]
    return text


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    return frame[column].map(to_num).astype(float)


def _max_positive(values, default: int = 1) -> int:
    numbers = [int(to_num(value)) for value in values if to_num(value) > 0]
    return max(numbers) if numbers else default


def _round_money(value: float) -> float:
    return round(float(value or 0.0), 2)


def _round_rate(value: float) -> float:
    return round(float(value or 0.0), 6)


def _round_pp(value: float) -> float:
    return round(float(value or 0.0), 2)


def _rank_band(rank, gmv: float) -> str:
    if gmv <= 0:
        return ZERO_GMV_RANK_BAND
    if rank <= 5:
        return 'Top1-5'
    if rank <= 10:
        return 'Top6-10'
    if rank <= 20:
        return 'Top11-20'
    if rank <= 50:
        return 'Top21-50'
    if rank <= 100:
        return 'Top51-100'
    return 'Top101-尾部'


def _inventory_alert(row: dict, source_columns: set[str]) -> str:
    if '在仓库存' not in source_columns or _text(row.get('在仓库存', '')) == '':
        return '缺失'
    stock = to_num(row.get('在仓库存', 0))
    if stock < 10:
        return '库存偏低'
    if stock > 50:
        return '较高'
    return '正常'


def _actual_days_from_source(source: pd.DataFrame) -> int:
    if source is None or len(source) == 0 or PLAN_ACTUAL_DAYS_COLUMN not in source.columns:
        return 1
    return _max_positive(source[PLAN_ACTUAL_DAYS_COLUMN], default=1)


def _aggregate_product_rows(source: pd.DataFrame) -> list[dict]:
    if source is None or len(source) == 0 or '宝贝ID' not in source.columns:
        return []

    work = source.copy(deep=True)
    work['_budget_product_id'] = work['宝贝ID'].map(_clean_product_id)
    work = work[work['_budget_product_id'].ne('')]
    if work.empty:
        return []

    if '推广成交金额' in work.columns:
        work['_budget_has_promotion_gmv'] = work['推广成交金额'].map(lambda value: _text(value) != '')
    else:
        work['_budget_has_promotion_gmv'] = False

    rows: list[dict] = []
    source_columns = set(source.columns)
    has_roi = '推广投产' in source_columns

    for product_id, group in work.groupby('_budget_product_id', sort=False):
        row: dict = {'宝贝ID': product_id}
        for field in _TEXT_FIELDS:
            row[field] = _first_nonblank(group[field]) if field in group.columns else ''
        for field in _NUMERIC_SUM_FIELDS:
            row[field] = float(_numeric_series(group, field).sum())
        if '在仓库存' in group.columns:
            row['在仓库存'] = _first_nonblank(group['在仓库存'])
        if CURRENT_ACTIVE_PLAN_COUNT_COLUMN in group.columns:
            row[CURRENT_ACTIVE_PLAN_COUNT_COLUMN] = int(
                max(_numeric_series(group, CURRENT_ACTIVE_PLAN_COUNT_COLUMN).max(), 0)
            )
        else:
            row[CURRENT_ACTIVE_PLAN_COUNT_COLUMN] = 0
        if PLAN_ACTUAL_DAYS_COLUMN in group.columns:
            row[PLAN_ACTUAL_DAYS_COLUMN] = _max_positive(group[PLAN_ACTUAL_DAYS_COLUMN])
        else:
            row[PLAN_ACTUAL_DAYS_COLUMN] = 1
        for field in PASSTHROUGH_PRODUCT_COLUMNS:
            row[field] = _first_nonblank(group[field]) if field in group.columns else ''
        # 链接投产：直接从执行表取（口径 = 全链接GMV ÷ 付费，含自然流量；见 core/merge.py）
        # 同一宝贝ID多行时取第一个非空值，不做加总（比率不能相加）
        if '链接投产' in group.columns:
            row['链接投产'] = next(
                (value for value in (to_num(item) for item in group['链接投产']) if value),
                0.0,
            )
        else:
            row['链接投产'] = 0.0

        spend = row['付费']
        has_promotion_gmv = bool(group['_budget_has_promotion_gmv'].any())
        row['_has_explicit_promotion_gmv'] = has_promotion_gmv
        if has_promotion_gmv:
            row['推广投产'] = safe_div(row['推广成交金额'], spend)
        elif has_roi and spend > 0:
            roi_values = group['推广投产']
            weights = _numeric_series(group, '付费')
            valid = roi_values.map(_text).ne('') & weights.gt(0)
            weighted_roi = (_numeric_series(group.loc[valid], '推广投产') * weights.loc[valid]).sum()
            row['推广投产'] = safe_div(float(weighted_roi), float(weights.loc[valid].sum())) if valid.any() else safe_div(row['GMV'], spend)
        else:
            row['推广投产'] = safe_div(row['GMV'], spend)
        if '商品加购率' in source_columns and row['商品访客数'] <= 0:
            rates = [to_num(value) for value in group['商品加购率'] if _text(value) != '']
            row['商品加购率'] = sum(rates) / len(rates) if rates else 0.0
        else:
            row['商品加购率'] = safe_div(row['商品加购件数'], row['商品访客数'])
        refund_rate_fields = [field for field in _REFUND_RATE_FIELDS if field in source_columns]
        if refund_rate_fields:
            rates = [
                to_num(value)
                for field in refund_rate_fields
                for value in group[field]
                if _text(value) != ''
            ]
            row['退款率'] = max(rates) if rates else safe_div(row['成功退款金额'], row['GMV'])
        else:
            row['退款率'] = safe_div(row['成功退款金额'], row['GMV'])

        row['_plan_detail_values'] = (
            list(group[FULL_PLAN_DETAIL_COLUMN]) if FULL_PLAN_DETAIL_COLUMN in group.columns else []
        )
        row['_source_columns'] = source_columns
        rows.append(row)

    return rows


def _store_summary(products: list[dict], source_columns: set[str], actual_days: int) -> dict:
    total_gmv = sum(item['GMV'] for item in products)
    total_spend = sum(item['付费'] for item in products)
    total_promotion_gmv = sum(item.get('推广成交金额', 0.0) for item in products)
    has_promotion_gmv = any(bool(item.get('_has_explicit_promotion_gmv')) for item in products)
    if has_promotion_gmv:
        store_roi = safe_div(total_promotion_gmv, total_spend)
        roi_note = '全店推广ROI=推广成交金额总和/付费总和'
    else:
        store_roi = safe_div(total_gmv, total_spend)
        roi_note = '执行表缺少推广成交金额，使用商品GMV/付费回退全店推广ROI'
    return {
        '商品数': len(products),
        '全店GMV': _round_money(total_gmv),
        '全店付费': _round_money(total_spend),
        '全店推广成交金额': _round_money(total_promotion_gmv),
        '全店推广ROI': _round_rate(store_roi),
        '全店推广ROI口径说明': roi_note,
        '实际数据天数': int(actual_days or 1),
        '未来7日花费基线': _round_money(safe_div(total_spend, actual_days or 1) * 7),
    }


def _budget_action(row: dict, store_roi: float, scale_to_7_days: float) -> tuple[str, str, str, float, str]:
    gmv = row['GMV']
    spend = row['付费']
    mismatch = row['预算错配金额']
    future_baseline = row['未来7日花费基线']
    roi = row.get('推广投产', 0.0)

    if gmv <= 0 and spend > 0:
        return (
            '无GMV有花费',
            '暂停',
            'GMV=0且付费>0，未来7日按当前花费基线全部回收',
            -future_baseline,
            '回收',
        )
    if spend <= 0 and gmv > 0:
        return (
            '自然成交无花费',
            '测试',
            '付费=0且GMV>0，可建立小额测试验证付费效率；当前无付费样本，不直接生成增加金额',
            0.0,
            '保持',
        )
    if row['基准应得花费'] <= 0 and spend <= 0:
        return '无有效预算基准', '保持', '全店或商品缺少可分配花费基准', 0.0, '保持'

    if abs(row['占比差(pp)']) <= 5:
        return '基本匹配', '保持', '销售占比与花费占比差异未超过5个百分点', 0.0, '保持'

    if mismatch > 0:
        if roi >= store_roi and store_roi > 0:
            return (
                '超配但高投产',
                '保持',
                f'花费超配但ROI {roi:.2f} >= 全店推广ROI {store_roi:.2f}',
                0.0,
                '保持',
            )
        factor = 0.5 if (roi <= 0 or (store_roi > 0 and roi < store_roi * 0.7)) else 0.3
        adjustment = -mismatch * factor * scale_to_7_days
        action = '降投'
        reason = f'花费占比高于销售占比{row["占比差(pp)"]:.2f}pp，按错配额{factor:.0%}回收'
        direction = '回收'
    elif mismatch < 0:
        if spend < 100 or roi < store_roi:
            return (
                '少投但未达加投条件',
                '保持',
                f'少投但样本付费{spend:.0f}<100或ROI {roi:.2f}低于全店推广ROI {store_roi:.2f}',
                0.0,
                '保持',
            )
        factor = 0.5 if (store_roi > 0 and roi >= store_roi * 1.2) else 0.3
        adjustment = abs(mismatch) * factor * scale_to_7_days
        action = '加投'
        reason = f'花费占比低于销售占比{abs(row["占比差(pp)"]):.2f}pp，按错配额{factor:.0%}增加'
        direction = '增加'
    else:
        return '基本匹配', '保持', '销售占比与花费占比一致', 0.0, '保持'

    if abs(adjustment) < 100:
        return '基本匹配', '保持', '测算未来7日建议调整不足100元，保持/观察', 0.0, '保持'
    return ('花费超配' if mismatch > 0 else '花费少投', action, reason, adjustment, direction)


def _with_rank_and_band(products: list[dict]) -> list[dict]:
    ranked = sorted(
        [item for item in products if item['GMV'] > 0],
        key=lambda item: (-item['GMV'], item['宝贝ID']),
    )
    for rank, item in enumerate(ranked, start=1):
        item['销售排名'] = rank
        item['排名档'] = _rank_band(rank, item['GMV'])
    zero_gmv = sorted([item for item in products if item['GMV'] <= 0], key=lambda item: item['宝贝ID'])
    for item in zero_gmv:
        item['销售排名'] = None  # 未上榜留空；用 None 不用 ''，避免「整数+空串」混排致渲染失败
        item['排名档'] = ZERO_GMV_RANK_BAND
    return ranked + zero_gmv


def _roles_enabled(roles) -> bool:
    """角色层开关：None 走全局常量，显式 bool 优先（便于灰度与测试）。"""
    return ROLE_LAYER_ENABLED if roles is None else bool(roles)


def _reference_date(source: pd.DataFrame) -> date | None:
    """判定「上架天数」用的基准日：优先取执行表数据日期，缺失才用本机当天。"""
    if source is not None and '统计日期' in source.columns:
        stamps = pd.to_datetime(source['统计日期'], errors='coerce').dropna()
        if len(stamps):
            return stamps.max().date()
    return date.today()


def _build_product_budget_decisions(
    source: pd.DataFrame,
    include_internal: bool,
    roles=None,
) -> list[dict]:
    products = _with_rank_and_band(_aggregate_product_rows(source))
    source_columns = set(source.columns) if source is not None else set()
    actual_days = _actual_days_from_source(source)
    summary = _store_summary(products, source_columns, actual_days)
    total_gmv = summary['全店GMV']
    total_spend = summary['全店付费']
    store_roi = summary['全店推广ROI']

    enriched: list[dict] = []
    for row in products:
        product_days = max(int(row.get(PLAN_ACTUAL_DAYS_COLUMN, actual_days) or actual_days or 1), 1)
        scale_to_7_days = safe_div(7, product_days)
        sales_share = safe_div(row['GMV'], total_gmv)
        spend_share = safe_div(row['付费'], total_spend)
        expected_spend = sales_share * total_spend
        mismatch = row['付费'] - expected_spend
        future_baseline = safe_div(row['付费'], product_days) * 7
        row.update({
            '销售占比': sales_share,
            '花费占比': spend_share,
            '占比差(pp)': (spend_share - sales_share) * 100,
            '基准应得花费': expected_spend,
            '预算错配金额': mismatch,
            '未来7日花费基线': future_baseline,
            ROLE_DAYS_KEY: product_days,
        })
        state, _action, reason, adjustment, direction = _budget_action(row, store_roi, scale_to_7_days)
        suggested_spend = max(0.0, future_baseline + adjustment)
        row.update({
            '预算匹配状态': state,
            '动作依据': reason,
            '未来7日建议调整金额': adjustment,
            '未来7日建议花费': suggested_spend,
            '库存提醒': _inventory_alert(row, source_columns),
            '_budget_direction': direction,
        })
        enriched.append(row)

    if _roles_enabled(roles):
        # 角色层只做减法：拦截 / 封顶加投，回收与硬风险仍由上面的既有规则负责。
        enriched = gate_budget_decisions(
            enriched,
            store_roi=store_roi,
            days=actual_days or 7,
            today=_reference_date(source),
        )

    decisions: list[dict] = []
    for row in enriched:
        decision = {
            '宝贝ID': row['宝贝ID'],
            '款式主图': row.get('款式主图', ''),
            '货号': row.get('货号', ''),
            '淘宝类目': row.get('淘宝类目', ''),
            '销售排名': row['销售排名'],
            '排名档': row['排名档'],
            **{field: row.get(field, '') for field in PASSTHROUGH_PRODUCT_COLUMNS},
            'GMV': _round_money(row['GMV']),
            '销售占比': _round_rate(row['销售占比']),
            '付费': _round_money(row['付费']),
            '花费占比': _round_rate(row['花费占比']),
            '占比差(pp)': _round_pp(row['占比差(pp)']),
            '基准应得花费': _round_money(row['基准应得花费']),
            '预算错配金额': _round_money(row['预算错配金额']),
            '未来7日花费基线': _round_money(row['未来7日花费基线']),
            '未来7日建议调整金额': _round_money(row['未来7日建议调整金额']),
            '未来7日建议花费': _round_money(row['未来7日建议花费']),
            '预算匹配状态': row['预算匹配状态'],
            '动作依据': row['动作依据'],
            # 链接投产：直接从执行表提取（全链接GMV÷付费），排在推广投产之前
            '链接投产': _round_rate(row.get('链接投产', 0.0)),
            '推广投产': _round_rate(row['推广投产']),
            '综合等级': row.get('综合等级', ''),
            '商品周期': row.get('商品周期', ''),
            '商品定位': row.get('商品定位', ''),
            '推广动作': row.get('推广动作', '') or row.get('对应操作', ''),
            'MetoX算法命中': row.get('MetoX算法命中', ''),
            '当前有效投放计划数': row.get(CURRENT_ACTIVE_PLAN_COUNT_COLUMN, 0),
            '库存提醒': row['库存提醒'],
        }
        # 角色层列：无论开关是否打开都建键（关闭时为空值），保证列顺序稳定
        decision.update({field: row.get(field, '') for field in BUDGET_DECISION_ROLE_COLUMNS})
        internal: dict = {}
        if include_internal:
            internal['_budget_direction'] = row['_budget_direction']
            internal['_plan_detail_values'] = row.get('_plan_detail_values', [])
        # 按 PRODUCT_COLUMNS 的规范顺序输出
        decisions.append({
            field: decision.get(field, '') for field in PRODUCT_COLUMNS
        } | internal)
    return decisions


def build_product_budget_decisions(df: pd.DataFrame, roles=None) -> list[dict]:
    """Build one read-only budget decision row per product ID.

    ``roles`` 打开后会追加角色层列，并用角色闸门约束加投金额；
    默认 None = 跟随 :data:`core.budget_role.ROLE_LAYER_ENABLED`（当前关闭）。
    """
    if df is None or len(df) == 0:
        return []
    return _build_product_budget_decisions(df.copy(deep=True), include_internal=False, roles=roles)


def _coerce_products(products_or_df) -> list[dict]:
    if isinstance(products_or_df, pd.DataFrame):
        return build_product_budget_decisions(products_or_df)
    return [dict(item) for item in (products_or_df or [])]


def build_rank_band_summary(products_or_df) -> list[dict]:
    """Summarize budget mismatch by ranking band, with zero-GMV spend isolated."""
    products = _coerce_products(products_or_df)
    rows = []
    for band in RANK_BAND_ORDER:
        band_rows = [item for item in products if item.get('排名档') == band]
        gmv = sum(to_num(item.get('GMV', 0)) for item in band_rows)
        spend = sum(to_num(item.get('付费', 0)) for item in band_rows)
        sales_share = sum(to_num(item.get('销售占比', 0)) for item in band_rows)
        spend_share = sum(to_num(item.get('花费占比', 0)) for item in band_rows)
        mismatch = sum(to_num(item.get('预算错配金额', 0)) for item in band_rows)
        adjustments = [to_num(item.get('未来7日建议调整金额', 0)) for item in band_rows]
        adjustment = sum(adjustments)
        recover = abs(sum(value for value in adjustments if value < 0))
        increase = sum(value for value in adjustments if value > 0)
        zero_gmv_spend = sum(to_num(item.get('付费', 0)) for item in band_rows if to_num(item.get('GMV', 0)) <= 0)
        diff_pp = (spend_share - sales_share) * 100
        rows.append({
            '排名档': band,
            '商品数': len(band_rows),
            'GMV': _round_money(gmv),
            '销售占比': _round_rate(sales_share),
            '付费': _round_money(spend),
            '花费占比': _round_rate(spend_share),
            '占比差(pp)': _round_pp(diff_pp),
            '预算错配金额': _round_money(mismatch),
            'ROI': _round_rate(safe_div(gmv, spend)),
            '未来7日建议调整金额': _round_money(adjustment),
            '待回收金额': _round_money(recover),
            '建议增加金额': _round_money(increase),
            '零GMV消耗': _round_money(zero_gmv_spend),
            '显著错配': abs(diff_pp) > 5 and abs(mismatch) >= 500,
        })
    return rows


def build_sales_tier_summary(products_or_df) -> list[dict]:
    """Backward-compatible wrapper for ranking-band summaries."""
    return build_rank_band_summary(products_or_df)


def _structure_for_product(product: dict) -> str:
    cycle = _text(product.get('商品周期', ''))
    position = _text(product.get('商品定位', ''))
    text = ' '.join(value for value in (cycle, position) if value)
    if any(token in text for token in (*_CLOSEOUT_TOKENS, '清退', '清仓')):
        return '清理型'
    if any(token in text for token in (*_TEST_TOKENS, '测试')):
        return '测试型'
    if any(token in position for token in ('主推', '冲刺', '重点', '核心')):
        return '主推型'
    if any(token in position for token in ('承接', '成熟', '稳定', '日常')):
        return '承接型'
    if any(token in position for token in ('投产', '梯队', '利润', 'ROI')):
        return '梯队型'
    direction = _budget_direction_from_product(product)
    if direction == '增加':
        return '增长型'
    if direction == '回收':
        return '回收型'
    return '中性'


def _budget_direction_from_product(product: dict) -> str:
    direction = _text(product.get('_budget_direction', ''))
    if direction in ('增加', '回收', '保持'):
        return direction
    adjustment = to_num(product.get('未来7日建议调整金额', 0))
    if adjustment > 0:
        return '增加'
    if adjustment < 0:
        return '回收'
    return '保持'


def _plan_management_advice(product_count: int, structures: set[str], directions: set[str]) -> str:
    conflict = product_count > 1 and '增加' in directions and '回收' in directions
    if conflict:
        return '合集内商品调整方向冲突，先拆分/清退低效商品，勿直接整体调整'
    if product_count > 1 and len(structures) > 1:
        return '合集推广计划包含多种经营结构，先拆分复核，再小步调整'
    structure = next(iter(structures)) if structures else '混合型'
    if structure == '清理型':
        return '清理型计划优先清退低效商品，不补充计划预算或出价'
    if structure == '测试型':
        return '测试型计划按商品测试结论保留或回收，不补充计划预算或出价'
    return '结构方向一致，可按商品调整方向复核；不补充计划预算或出价'


def build_plan_management(
    df: pd.DataFrame,
    products: list[dict] | None = None,
    plan_config: dict | None = None,
) -> list[dict]:
    """Aggregate full plan details by PlanID without inventing names, budget, or bids.

    2026-11-14：新增 ``预算`` / ``出价方式`` 两列，来源是无界目录下最新的
    `计划列表_*.xlsx`（按计划ID左连接，见 core.plan_config）。这两列是**后台当前配置**，
    只做展示，不参与任何指标或预算决策计算；匹配不到就留空。
    """
    if df is None or len(df) == 0 or FULL_PLAN_DETAIL_COLUMN not in df.columns:
        return []
    source = df.copy(deep=True)
    internal_products = (
        _build_product_budget_decisions(source, include_internal=True)
        if products is None
        else [dict(item) for item in products]
    )
    plans: dict[str, dict] = {}
    for product in internal_products:
        product_id = product.get('宝贝ID', '')
        structure = _structure_for_product(product)
        direction = _budget_direction_from_product(product)
        for value in product.get('_plan_detail_values', []):
            for detail in parse_full_plan_details(value):
                plan_id = _text(detail.get('计划ID'))
                if not plan_id:
                    continue
                target = plans.setdefault(plan_id, {
                    '计划ID': plan_id,
                    '计划名称': '',
                    '所选周期花费': 0.0,
                    '归因成交': 0.0,
                    '_products': set(),
                    '_structures': set(),
                    '_directions': set(),
                    '_channels': set(),
                })
                target['计划名称'] = target['计划名称'] or _text(detail.get('计划名称'))
                target['所选周期花费'] += to_num(detail.get('当前花费', 0))
                target['归因成交'] += to_num(detail.get('归因成交', 0))
                target['_products'].add(product_id)
                target['_structures'].add(structure)
                target['_directions'].add(direction)
                channel = _text(detail.get('所属渠道'))
                if channel:
                    target['_channels'].add(channel)

    rows = []
    config_by_id = (plan_config or {}).get('by_id') or {}
    for plan in plans.values():
        product_count = len(plan['_products'])
        structures = plan['_structures']
        directions = plan['_directions']
        structure = next(iter(structures)) if len(structures) == 1 else '混合型'
        period_spend = _round_money(plan['所选周期花费'])
        plan_type = '单商品计划' if product_count == 1 else '合集推广计划'
        direction_evidence = '、'.join(
            direction for direction in ('增加', '回收', '保持') if direction in directions
        ) or '保持'
        config = config_by_id.get(plan['计划ID']) or {}
        rows.append({
            '计划ID': plan['计划ID'],
            '计划名称': plan['计划名称'],
            '所属渠道': '、'.join(sorted(plan['_channels'])),
            '所选周期花费': period_spend,
            '周期花费': period_spend,
            '归因成交': _round_money(plan['归因成交']),
            'ROI': _round_rate(safe_div(plan['归因成交'], plan['所选周期花费'])),
            '商品数': product_count,
            '计划商品类型': plan_type,
            '计划类型': plan_type,
            '计划经营结构': structure,
            '预算': config.get('预算', ''),
            '出价方式': config.get('出价方式', ''),
            '方向证据': direction_evidence,
            '计划管理建议': _plan_management_advice(product_count, structures, directions),
        })
    sorted_rows = sorted(rows, key=lambda item: (-item['所选周期花费'], item['计划ID']))
    for index, row in enumerate(sorted_rows, start=1):
        row['序号'] = index
    return sorted_rows


def build_budget_decision_context(df: pd.DataFrame, roles=None, plan_config=None) -> dict:
    """Build the read-only budget decision context from a final execution table DataFrame."""
    source = pd.DataFrame() if df is None else df.copy(deep=True)
    source_columns = set(source.columns)
    actual_days = _actual_days_from_source(source)
    internal_products = _build_product_budget_decisions(source, include_internal=True, roles=roles)
    # 列顺序固定按 PRODUCT_COLUMNS（含角色列与阈值列），不随角色层开关变化
    products = [
        {field: item.get(field, '') for field in PRODUCT_COLUMNS}
        for item in internal_products
    ]
    summary = _store_summary(_aggregate_product_rows(source), source_columns, actual_days)
    tiers = build_rank_band_summary(products)
    plans = build_plan_management(source, products=internal_products, plan_config=plan_config)
    adjustments = [to_num(row.get('未来7日建议调整金额', 0)) for row in products]
    summary['周期GMV'] = summary['全店GMV']
    summary['周期花费'] = summary['全店付费']
    summary['待回收金额'] = _round_money(abs(sum(value for value in adjustments if value < 0)))
    summary['建议增加金额'] = _round_money(sum(value for value in adjustments if value > 0))
    summary['零GMV消耗'] = _round_money(
        sum(to_num(row.get('付费', 0)) for row in products if to_num(row.get('GMV', 0)) <= 0)
    )
    summary['显著错配排名档数'] = sum(1 for row in tiers if row['显著错配'])
    return {
        'products': products,
        'tiers': tiers,
        'plans': plans,
        'summary': summary,
        'actual_days': actual_days,
    }


__all__ = [
    'PRODUCT_COLUMNS',
    'BUDGET_DECISION_MAIN_COLUMNS',
    'BUDGET_DECISION_ROLE_COLUMNS',
    'RANK_BAND_ORDER',
    'SALES_TIER_ORDER',
    'build_budget_decision_context',
    'build_product_budget_decisions',
    'build_rank_band_summary',
    'build_sales_tier_summary',
    'build_plan_management',
]
