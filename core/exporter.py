"""Excel 导出"""
import io
import pandas as pd
from .analysis import (
    CATEGORY_BASIC_COLUMNS,
    CATEGORY_INVESTMENT_COLUMNS,
    category_analysis,
)
from .data_validation import validate_paid_plan_consistency
from .metric_labels import METRIC_STATUS_RULE_SHEET, metric_status_rule_frame
from .schema import execution_export_frame
from .budget_decision import is_removed_budget_column


CATEGORY_INVESTMENT_SHEET = '品类投放占比与ROI'
CATEGORY_BASIC_SHEET = '品类分析'

EXECUTION_SHEET = '商品推广执行表'
NEW_PRODUCT_LIFECYCLE_SHEET = '新品判定'
NEW_PRODUCT_MATERIAL_SHEET = '素材表现'
NEW_PRODUCT_BACKTEST_SHEET = '回测验证'
NEW_PRODUCT_METHODOLOGY_SHEET = '新品口径说明'
NEW_PRODUCT_LIFECYCLE_COLUMNS = [
    '款式主图', '图片', '货号', '关联宝贝ID', '淘宝类目', 'ERP建档时间',
    '首次在线发现日', '新品年龄',
    'D1_D30_商品访客数', 'D1_D30_商品加购件数', 'D1_D30_GMV', 'D1_D30_商品加购率',
    '日均访客', '日均GMV',
    '判定轮次', 'D15结果', 'D30结果', '当前结果',
    '保护状态', '流量验证', '本轮成交', '判定原因', '样本充分性',
    '保护因素', '风险因素', '预测日期',
]
NEW_PRODUCT_MATERIAL_COLUMNS = [
    '宝贝ID', '货号', '淘宝类目', '主图整体点击率', '主图CTR是否有效',
    '计划ID', '计划名称', '主体ID', '主体名称', '主体类型',
    '创意ID', '创意名称', '创意首次发现日', '创意年龄', '创意展现量', '创意点击量',
    '无界创意CTR', '上一7天CTR', 'CTR环比', '创意展现占比', '是否主投创意',
]
NEW_PRODUCT_BACKTEST_COLUMNS = [
    '判定节点', '规则版本', '样本数', '下架建议数', '下架命中率', '误杀率',
    '召回率', '下架覆盖率', '机会损失GMV', '机会损失预估利润',
    '不可验证样本数', 'CTR增益', 'MetoX保护有效率', '分组',
]
BUDGET_DECISION_PRODUCT_SHEET = '预算决策看板'
BUDGET_DECISION_TIER_SHEET = '排名档对齐'
BUDGET_DECISION_PLAN_SHEET = '推广计划管理'
BUDGET_RANK_BAND_COLUMN = '排名档'
BUDGET_RANK_BAND_LEGACY_COLUMNS = ('销售梯队', '梯队', '层级')
BUDGET_RANK_BAND_VALUE_MAP = {
    'S1': 'Top1-5',
    'S2': 'Top6-10',
    'S3': 'Top11-20',
    'S4': 'Top21-50',
    'S5': 'Top51-100',
    'S6': 'Top101-尾部',
    'Top101以后': 'Top101-尾部',
    'Top101后': 'Top101-尾部',
    'Top101以上': 'Top101-尾部',
}
BUDGET_DECISION_REQUIRED_PRODUCT_COLUMNS = [
    BUDGET_RANK_BAND_COLUMN,
    '预算风险',
    '近7日日均GMV',
    '上一7日日均GMV',
    'GMV趋势率',
    'TH_MAIN',
    'TH_MID',
    '阈值版本',
]
BUDGET_DECISION_PRODUCT_ALIASES = {
    BUDGET_RANK_BAND_COLUMN: BUDGET_RANK_BAND_LEGACY_COLUMNS,
    '预算风险': ('风险',),
}
BUDGET_DECISION_PRODUCT_DROP_COLUMNS = {
    '风险',
    *BUDGET_RANK_BAND_LEGACY_COLUMNS,
}
BUDGET_DECISION_TIER_COLUMNS = [
    BUDGET_RANK_BAND_COLUMN, '商品数', 'GMV', '销售占比', '付费', '花费占比',
    '占比差(pp)', '预算错配金额', 'ROI', '未来7日建议调整金额',
    '待回收金额', '建议增加金额', '零GMV消耗', '显著错配',
]
BUDGET_DECISION_TIER_ALIASES = {
    BUDGET_RANK_BAND_COLUMN: BUDGET_RANK_BAND_LEGACY_COLUMNS,
    '预算错配金额': ('错配金额',),
}
BUDGET_DECISION_TIER_DROP_COLUMNS = {'错配金额', *BUDGET_RANK_BAND_LEGACY_COLUMNS}
BUDGET_DECISION_PLAN_COLUMNS = [
    '序号', '计划ID', '计划名称', '所属渠道', '商品数', '计划类型',
    '计划经营结构', '预算', '出价方式',
    '周期花费', '归因成交', 'ROI', '日均花费', '方向证据', '计划管理建议',
]
BUDGET_DECISION_PLAN_ALIASES = {
    '计划类型': ('计划商品类型',),
}
TOP_CATEGORY_LIMIT = 15
TOP_PRODUCT_LIMIT = 50
STATUS_HIGH_BACKGROUND = '#DDEBF7'
STATUS_LOW_BACKGROUND = '#FCE4EC'
STATUS_TEXT_COLOR = '#1F2937'
STATUS_BACKGROUND_BY_COLUMN = {
    '链接投产状态': {
        '高': STATUS_HIGH_BACKGROUND,
        '低': STATUS_LOW_BACKGROUND,
    },
    '推广加购率状态': {
        '高': STATUS_HIGH_BACKGROUND,
        '低': STATUS_LOW_BACKGROUND,
    },
    '商品加购率状态': {
        '高': STATUS_HIGH_BACKGROUND,
        '低': STATUS_LOW_BACKGROUND,
    },
    '商品支付转化率状态': {
        '高': STATUS_HIGH_BACKGROUND,
        '低': STATUS_LOW_BACKGROUND,
    },
    '可售天数状态': {
        '紧急': STATUS_LOW_BACKGROUND,
        '需关注': STATUS_HIGH_BACKGROUND,
    },
}


def _ensure_styler_capacity(df):
    """Ensure Pandas Styler can render every cell in the visible table."""
    required_elements = int(df.size)
    current_limit = int(pd.get_option('styler.render.max_elements'))
    if required_elements > current_limit:
        pd.set_option('styler.render.max_elements', required_elements)


def top_category_product_mask(
        df,
        category_limit=TOP_CATEGORY_LIMIT,
        product_limit=TOP_PRODUCT_LIMIT):
    """返回品类 GMV Top N 内、每品类单品 GMV Top N 的行掩码。"""
    mask = pd.Series(False, index=df.index, dtype=bool)
    if (
            df is None
            or df.empty
            or category_limit <= 0
            or product_limit <= 0
            or not {'淘宝类目', 'GMV'}.issubset(df.columns)):
        return mask

    work = pd.DataFrame({
        '_category': df['淘宝类目'].astype('string').fillna('').str.strip(),
        '_gmv': pd.to_numeric(df['GMV'], errors='coerce').fillna(0.0),
        '_position': range(len(df)),
    }, index=df.index)
    eligible = work['_category'].ne('') & work['_gmv'].gt(0)
    if not eligible.any():
        return mask

    category_totals = (
        work.loc[eligible]
        .groupby('_category', sort=False)['_gmv']
        .sum()
        .sort_values(ascending=False, kind='mergesort')
    )
    top_categories = category_totals.head(category_limit).index
    selected_positions = (
        work.loc[eligible & work['_category'].isin(top_categories)]
        .sort_values(
            ['_category', '_gmv', '_position'],
            ascending=[True, False, True],
            kind='mergesort',
        )
        .groupby('_category', sort=False)
        .head(product_limit)['_position']
        .to_numpy()
    )
    mask.iloc[selected_positions] = True
    return mask


def _style_for_background(background):
    return (
        f'background-color: {background}; '
        f'color: {STATUS_TEXT_COLOR}'
    )


def _status_value(value):
    if pd.isna(value):
        return ''
    return str(value).strip()


def _status_background(column, value):
    return STATUS_BACKGROUND_BY_COLUMN.get(column, {}).get(_status_value(value))


def _top_product_row_mask(df, ranking_df=None):
    ranking_source = df if ranking_df is None else ranking_df
    ranking_mask = top_category_product_mask(ranking_source)
    if ranking_source is df:
        return ranking_mask.astype(bool)
    else:
        highlighted_indices = ranking_source.index[ranking_mask]
        return pd.Series(df.index.isin(highlighted_indices), index=df.index, dtype=bool)


def _top_product_style_frame(df, ranking_df=None):
    row_mask = _top_product_row_mask(df, ranking_df=ranking_df)
    styles = pd.DataFrame('', index=df.index, columns=df.columns)
    if row_mask.any():
        highlighted_index = row_mask[row_mask].index
        for column, color_map in STATUS_BACKGROUND_BY_COLUMN.items():
            if column not in df.columns:
                continue
            values = df.loc[highlighted_index, column].map(_status_value)
            for status, background in color_map.items():
                matching_index = values[values.eq(status)].index
                if not matching_index.empty:
                    styles.loc[matching_index, column] = _style_for_background(background)
    return styles


def style_execution_table(df, ranking_df=None):
    """仅为销售 Top15 品类内、各品类 Top50 单品的目标状态格着色。"""
    _ensure_styler_capacity(df)
    styler = df.style
    styles = _top_product_style_frame(df, ranking_df=ranking_df)
    if not styles.eq('').all().all():
        styler = styler.apply(lambda _: styles, axis=None)
    return styler


def _write_execution_sheet(writer, df, ranking_df=None):
    """写入执行表，并将页面 Top 单品状态格底色同步到 Excel。"""
    frame = execution_export_frame(df)
    frame.to_excel(writer, sheet_name=EXECUTION_SHEET, index=False)

    workbook = writer.book
    worksheet = writer.sheets[EXECUTION_SHEET]
    status_formats = {
        background: workbook.add_format({
            'bg_color': background,
            'font_color': STATUS_TEXT_COLOR,
            'pattern': 1,
        })
        for background in {STATUS_HIGH_BACKGROUND, STATUS_LOW_BACKGROUND}
    }
    ranking_frame = execution_export_frame(ranking_df) if ranking_df is not None else frame
    top_product_mask = _top_product_row_mask(frame, ranking_df=ranking_frame)
    column_positions = {column: position for position, column in enumerate(frame.columns)}
    for row_position, highlighted in enumerate(top_product_mask):
        excel_row = row_position + 1
        if highlighted:
            for column in STATUS_BACKGROUND_BY_COLUMN:
                if column not in column_positions:
                    continue
                value = frame.iloc[row_position][column]
                background = _status_background(column, value)
                if background:
                    worksheet.write(
                        excel_row,
                        column_positions[column],
                        value,
                        status_formats[background],
                    )

    return frame


def _write_category_investment_sheet(writer, df, cat_ctx=None):
    """写入与品类分析页面同口径的投放占比与 ROI 表。"""
    required = {'淘宝类目', '宝贝ID', 'GMV', '付费'}
    rows = []
    if df is not None and not df.empty and required.issubset(df.columns):
        rows = category_analysis(df, cat_ctx=cat_ctx).get('invest', [])
    pd.DataFrame(rows, columns=CATEGORY_INVESTMENT_COLUMNS).to_excel(
        writer, sheet_name=CATEGORY_INVESTMENT_SHEET, index=False
    )


def _write_category_basic_sheet(writer, df, cat_ctx=None):
    """写入与品类分析页面完全同字段、同口径的基础分析表。"""
    required = {'淘宝类目', '宝贝ID', 'GMV', '付费'}
    rows = []
    if df is not None and not df.empty and required.issubset(df.columns):
        rows = category_analysis(df, cat_ctx=cat_ctx).get('basic', [])
    pd.DataFrame(rows, columns=CATEGORY_BASIC_COLUMNS).to_excel(
        writer, sheet_name=CATEGORY_BASIC_SHEET, index=False
    )


def _write_metric_status_rule_sheet(writer):
    """写入与页面五项状态算法同源的运营规则文档。"""
    rules = metric_status_rule_frame()
    rules.to_excel(writer, sheet_name=METRIC_STATUS_RULE_SHEET, index=False)

    workbook = writer.book
    worksheet = writer.sheets[METRIC_STATUS_RULE_SHEET]
    header_format = workbook.add_format({
        'bold': True,
        'font_color': '#FFFFFF',
        'bg_color': '#1F4E78',
        'border': 1,
        'align': 'center',
        'valign': 'vcenter',
        'text_wrap': True,
    })
    body_format = workbook.add_format({
        'border': 1,
        'valign': 'top',
        'text_wrap': True,
    })
    for column_index, column_name in enumerate(rules.columns):
        worksheet.write(0, column_index, column_name, header_format)
    worksheet.set_column('A:A', 22, body_format)
    worksheet.set_column('B:B', 34, body_format)
    worksheet.set_column('C:C', 68, body_format)
    worksheet.set_column('D:D', 66, body_format)
    worksheet.set_column('E:E', 58, body_format)
    worksheet.set_column('F:F', 52, body_format)
    worksheet.set_row(0, 32)
    for row_index in range(1, len(rules) + 1):
        worksheet.set_row(row_index, 76)
    worksheet.freeze_panes(1, 1)
    worksheet.autofilter(0, 0, len(rules), len(rules.columns) - 1)
    worksheet.hide_gridlines(2)
    worksheet.set_zoom(85)
    worksheet.set_landscape()
    worksheet.fit_to_pages(1, 1)
    worksheet.repeat_rows(0)


def _new_product_methodology_frame(status_note=''):
    rows = [
        {
            '主题': '生命周期起点',
            '口径': '首次在线发现日',
            '说明': '按款式/货号聚合，同一货号下所有宝贝ID首次在商品-整体日报中当前在线的日期作为D1。',
        },
        {
            '主题': '判定周期',
            '口径': 'D15至D45每日更新',
            '说明': 'D15起每天更新当前结果，最长到D45；流量验证和成交保护使用D1至分析日累计事实，加购表现仍使用D1-D15或D16-D30固定窗口。',
        },
        {
            '主题': '判定结果',
            '口径': '下架/继续观察',
            '说明': 'D45内支付买家、支付件数或GMV任一大于0即动销保护；累计访客满300后进入加购判断，D45仍无成交且累计访客不足300时直接下架。加购件数为0是强弱势证据；同层同期加购率低于80%为主判定，D30额外要求当前周期未高于第一周期。支付转化率和UV价值不参与新品下架判定。',
        },
        {
            '主题': '比率公式',
            '口径': '累计分子÷累计分母',
            '说明': '商品加购率=Σ商品加购件数÷Σ商品访客数；商品支付转化率=Σ支付买家数÷Σ商品访客数；无界创意CTR=Σ点击量÷Σ展现量。',
        },
        {
            '主题': '主图整体点击率',
            '口径': '平台原值',
            '说明': '当前源无主图点击量/曝光量分子分母，MVP只展示，不参与A-D判定。',
        },
        {
            '主题': 'MetoX',
            '口径': '影子验证',
            '说明': '正向标签可作为保护候选；负向标签只作旁证，不能单独触发下架。',
        },
    ]
    if status_note:
        rows.insert(0, {'主题': '导出状态', '口径': '新品上下文', '说明': status_note})
    return pd.DataFrame(rows, columns=['主题', '口径', '说明'])


def _frame_from_context(context, keys, columns):
    if context is None:
        return pd.DataFrame(columns=columns)
    if isinstance(context, pd.DataFrame):
        frame = context.copy()
    elif isinstance(context, dict):
        frame = None
        for key in keys:
            candidate = context.get(key)
            if candidate is not None:
                frame = candidate.copy() if isinstance(candidate, pd.DataFrame) else pd.DataFrame(candidate)
                break
        if frame is None:
            frame = pd.DataFrame(columns=columns)
    else:
        frame = pd.DataFrame(context)
    if frame.empty and not list(frame.columns):
        return pd.DataFrame(columns=columns)
    preferred = [column for column in columns if column in frame.columns]
    remaining = [column for column in frame.columns if column not in preferred]
    return frame[preferred + remaining]


def _dedupe_columns(columns):
    result = []
    seen = set()
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        result.append(column)
    return result


def _to_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def _clean_export_text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return '' if text in ('', 'nan', 'None', '<NA>', 'NaT') else text


def _clean_budget_plan_advice_text(value):
    text = _clean_export_text(value)
    replacements = (
        ('先按预算角色拆分复核', '先拆分复核'),
        ('按预算角色拆分复核', '拆分复核'),
        ('商品预算动作', '商品调整方向'),
        ('预算动作', '调整方向'),
        ('预算角色', '经营结构'),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _rank_band_value(value, gmv=None):
    text = _clean_export_text(value)
    if any(token in text for token in ('零GMV', '零成交', '零销售')):
        return '零GMV回收池'
    if text == 'S6' and _to_float(gmv, default=0.0) <= 0:
        return '零GMV回收池'
    return BUDGET_RANK_BAND_VALUE_MAP.get(text, text)


def _normalize_rank_band_frame(frame):
    if frame is None:
        return pd.DataFrame()
    work = frame.copy()
    legacy_source_column = next(
        (column for column in BUDGET_RANK_BAND_LEGACY_COLUMNS if column in work.columns),
        None,
    )
    if BUDGET_RANK_BAND_COLUMN not in work.columns:
        if legacy_source_column is not None:
            work.insert(
                work.columns.get_loc(legacy_source_column),
                BUDGET_RANK_BAND_COLUMN,
                work[legacy_source_column],
            )
        else:
            return work
    elif legacy_source_column is not None:
        work[BUDGET_RANK_BAND_COLUMN] = work[BUDGET_RANK_BAND_COLUMN].mask(
            _blank_series_mask(work[BUDGET_RANK_BAND_COLUMN]),
            work[legacy_source_column],
        )
    if BUDGET_RANK_BAND_COLUMN in work.columns:
        gmv_values = work['GMV'] if 'GMV' in work.columns else pd.Series([None] * len(work), index=work.index)
        work[BUDGET_RANK_BAND_COLUMN] = [
            _rank_band_value(value, gmv)
            for value, gmv in zip(work[BUDGET_RANK_BAND_COLUMN], gmv_values)
        ]
    return work.drop(columns=list(BUDGET_RANK_BAND_LEGACY_COLUMNS), errors='ignore')


def _with_budget_decision_identity_columns_adjacent(columns):
    group = [BUDGET_RANK_BAND_COLUMN]
    present = [column for column in group if column in columns]
    if not present:
        return columns
    remaining = [column for column in columns if column not in group]
    anchor = next((column for column in ('淘宝类目', '货号', '宝贝ID') if column in remaining), None)
    insert_at = remaining.index(anchor) + 1 if anchor is not None else 0
    return remaining[:insert_at] + present + remaining[insert_at:]


def _budget_product_columns_from_canonical(product_columns):
    columns = []
    for column in product_columns:
        if column in BUDGET_RANK_BAND_LEGACY_COLUMNS:
            column = BUDGET_RANK_BAND_COLUMN
        if is_removed_budget_column(column) or column in BUDGET_DECISION_PRODUCT_DROP_COLUMNS:
            continue
        columns.append(column)
    return _with_budget_decision_identity_columns_adjacent(_dedupe_columns(columns))


def budget_decision_product_columns():
    from .budget_decision import PRODUCT_COLUMNS

    return _budget_product_columns_from_canonical(PRODUCT_COLUMNS)


def _drop_inactive_new_product_columns(frame):
    inactive_prefixes = (
        'D14',
        'D21',
        'D28',
        'D1_D14',
        'D15_D21',
        'D15_D28',
        'D1_D21',
        'D1_D28',
    )
    inactive_exact = {
        'left_censored',
        'D15商品状态',
        'D15在仓库存',
    }
    inactive = [
        column for column in frame.columns
        if column in inactive_exact or str(column).startswith(inactive_prefixes)
    ]
    return frame.drop(columns=inactive, errors='ignore')


def _write_new_product_sheets(writer, new_product_ctx=None):
    lifecycle = _frame_from_context(
        new_product_ctx,
        ('lifecycle', 'decision', 'decisions', '新品判定'),
        NEW_PRODUCT_LIFECYCLE_COLUMNS,
    )
    lifecycle = _drop_inactive_new_product_columns(
        lifecycle.drop(columns=['商品名称'], errors='ignore')
    )
    materials = _frame_from_context(
        new_product_ctx,
        ('materials', 'material', 'creative', '素材表现'),
        NEW_PRODUCT_MATERIAL_COLUMNS,
    )
    materials = materials.drop(columns=['商品名称'], errors='ignore')
    backtest = _frame_from_context(
        new_product_ctx,
        ('backtest', 'validation', '回测验证'),
        NEW_PRODUCT_BACKTEST_COLUMNS,
    )
    if isinstance(new_product_ctx, dict) and new_product_ctx.get('methodology') is not None:
        methodology = _frame_from_context(
            new_product_ctx,
            ('methodology', '口径说明'),
            ['主题', '口径', '说明'],
        )
    else:
        status_note = ''
        if isinstance(new_product_ctx, dict):
            status_note = str(new_product_ctx.get('message') or '').strip()
        methodology = _new_product_methodology_frame(status_note=status_note)

    lifecycle.to_excel(writer, sheet_name=NEW_PRODUCT_LIFECYCLE_SHEET, index=False)
    materials.to_excel(writer, sheet_name=NEW_PRODUCT_MATERIAL_SHEET, index=False)
    backtest.to_excel(writer, sheet_name=NEW_PRODUCT_BACKTEST_SHEET, index=False)
    methodology.to_excel(writer, sheet_name=NEW_PRODUCT_METHODOLOGY_SHEET, index=False)


def _blank_series_mask(series):
    text = series.astype('string').fillna('').str.strip()
    return text.eq('') | text.isin(['nan', 'None', '<NA>', 'NaT'])


def _budget_frame_from_context(context, keys, columns, aliases=None, drop_columns=None):
    frame = _frame_from_context(context, keys, columns)
    for target, sources in (aliases or {}).items():
        if target not in frame.columns:
            frame[target] = ''
        for source in sources:
            if source in frame.columns:
                frame[target] = frame[target].mask(_blank_series_mask(frame[target]), frame[source])
                break
    for column in columns:
        if column not in frame.columns:
            frame[column] = ''
    frame = _normalize_rank_band_frame(frame)
    frame = frame.drop(columns=list(drop_columns or set()), errors='ignore')
    frame = frame.loc[:, [column for column in frame.columns if not is_removed_budget_column(column)]]
    preferred = [column for column in columns if column in frame.columns]
    remaining = [column for column in frame.columns if column not in preferred]
    return frame.loc[:, preferred + remaining]


def _write_budget_decision_sheets(writer, df, budget_decision_context=None):
    if budget_decision_context is None:
        from .budget_decision import build_budget_decision_context

        context = build_budget_decision_context(df)
    else:
        context = budget_decision_context
    products = _budget_frame_from_context(
        context,
        ('products', BUDGET_DECISION_PRODUCT_SHEET),
        budget_decision_product_columns(),
        aliases=BUDGET_DECISION_PRODUCT_ALIASES,
        drop_columns=BUDGET_DECISION_PRODUCT_DROP_COLUMNS,
    )
    tiers = _budget_frame_from_context(
        context,
        ('tiers', BUDGET_DECISION_TIER_SHEET),
        BUDGET_DECISION_TIER_COLUMNS,
        aliases=BUDGET_DECISION_TIER_ALIASES,
        drop_columns=BUDGET_DECISION_TIER_DROP_COLUMNS,
    )
    plans = _budget_frame_from_context(
        context,
        ('plans', BUDGET_DECISION_PLAN_SHEET),
        BUDGET_DECISION_PLAN_COLUMNS,
        aliases=BUDGET_DECISION_PLAN_ALIASES,
    )
    if '计划管理建议' in plans.columns:
        plans['计划管理建议'] = plans['计划管理建议'].map(_clean_budget_plan_advice_text)

    products.to_excel(writer, sheet_name=BUDGET_DECISION_PRODUCT_SHEET, index=False)
    tiers.to_excel(writer, sheet_name=BUDGET_DECISION_TIER_SHEET, index=False)
    plans.to_excel(writer, sheet_name=BUDGET_DECISION_PLAN_SHEET, index=False)


BUDGET_DECISION_PRODUCT_COLUMNS = budget_decision_product_columns()


def export_execution_table(df, cat_ctx=None, ranking_df=None, category_df=None):
    """导出执行表及品类投放占比与 ROI 为 Excel bytes。"""
    validate_paid_plan_consistency(df, '执行表Excel导出')
    output = io.BytesIO()
    category_source = df if category_df is None else category_df
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        _write_execution_sheet(writer, df, ranking_df=ranking_df)
        _write_metric_status_rule_sheet(writer)
        _write_category_investment_sheet(writer, category_source, cat_ctx=cat_ctx)
    output.seek(0)
    return output.read()


def export_all_sheets(
        df, cat_ctx=None, ranking_df=None, new_product_ctx=None,
        budget_decision_context=None, category_df=None):
    """导出全部分析 Sheet"""
    validate_paid_plan_consistency(df, '全部Sheet Excel导出')
    output = io.BytesIO()
    category_source = df if category_df is None else category_df
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # 1. 执行表
        _write_execution_sheet(writer, df, ranking_df=ranking_df)

        # 2. 五项核心指标状态规则文档
        _write_metric_status_rule_sheet(writer)

        # 3. 预算决策
        _write_budget_decision_sheets(
            writer, df, budget_decision_context=budget_decision_context
        )

        # 4. 分层
        seg_cols = [c for c in ['组合','综合等级','商品定位','对应操作'] if c in df.columns]
        if seg_cols and '宝贝ID' in df.columns and 'GMV' in df.columns:
            seg = df.groupby(seg_cols).agg(
                商品数=('宝贝ID','count'), GMV=('GMV','sum')
            ).reset_index()
            seg.to_excel(writer, sheet_name='商品分层明细', index=False)

        # 5. 与页面一致的品类基础分析
        _write_category_basic_sheet(writer, category_source, cat_ctx=cat_ctx)

        # 6. 与页面一致的品类投放占比与 ROI
        _write_category_investment_sheet(writer, category_source, cat_ctx=cat_ctx)

        # 7. 退款风险（聚水潭cohort退款率>70%，与页面阈值一致）
        if '退款率' in df.columns:
            ref_cols = [c for c in ['宝贝ID','商品名称','淘宝类目','GMV','成功退款金额','退款率','付费','综合等级','对应操作','风险标签'] if c in df.columns]
            if ref_cols:
                ref = df[df['退款率'] > 0.7].sort_values('退款率', ascending=False)[ref_cols]
                ref.to_excel(writer, sheet_name='退款风险', index=False)

        # 8-10. 渠道
        for col, name in [('关键词','关键词建议'),('人群','人群建议'),('全站','全站建议')]:
            if col in df.columns:
                attr_col = f'{col}归因成交'
                ch_cols = [c for c in ['宝贝ID','商品名称',col,attr_col,'GMV','综合等级','对应操作'] if c in df.columns]
                if ch_cols:
                    ch = df[df[col] > 0].sort_values(col, ascending=False).head(500)[ch_cols].copy()
                    if attr_col in ch.columns:
                        ch['渠道投产'] = ch[attr_col] / ch[col].replace(0, float('nan'))
                    ch.to_excel(writer, sheet_name=name, index=False)

        # 11. 库存
        if '在仓库存' in df.columns:
            inv_cols = [c for c in ['宝贝ID','货号','商品名称','在仓库存','在途','商品访客数','GMV','付费','综合等级','风险标签'] if c in df.columns]
            if inv_cols:
                inv = df[(df['在仓库存'] > 50) | (df['在仓库存'] == 0)].sort_values('在仓库存', ascending=False)[inv_cols]
                inv.to_excel(writer, sheet_name='库存动作', index=False)

        # 12-15. 新品视图（只读MVP）：缺少新品上下文时仍导出口径和空模板，旧分析不受影响。
        _write_new_product_sheets(writer, new_product_ctx=new_product_ctx)

    output.seek(0)
    return output.read()
