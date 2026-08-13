"""Excel 导出"""
import io
import pandas as pd
from .analysis import build_ai_task_view, category_analysis
from .data_validation import validate_paid_plan_consistency
from .metric_labels import METRIC_STATUS_RULE_SHEET, metric_status_rule_frame
from .schema import execution_export_frame, safe_div


CATEGORY_INVESTMENT_SHEET = '品类投放占比与ROI'
CATEGORY_INVESTMENT_COLUMNS = [
    '品类', 'GMV', 'GMV占比', '7天GMV', '7日GMV环比',
    '付费', '付费占比', '上周期付费', '关键词', '人群', '全站',
    '上周期关键词', '上周期人群', '上周期全站', 'ROI',
]

EXECUTION_SHEET = '商品推广执行表'
TOP_CATEGORY_LIMIT = 15
TOP_PRODUCT_LIMIT = 50
TOP_PRODUCT_BACKGROUND = '#FFF2CC'
TOP_PRODUCT_TEXT_COLOR = '#1F2937'
TOP_PRODUCT_STYLE = (
    f'background-color: {TOP_PRODUCT_BACKGROUND}; '
    f'color: {TOP_PRODUCT_TEXT_COLOR}'
)


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


def _top_product_style_frame(df, ranking_df=None):
    ranking_source = df if ranking_df is None else ranking_df
    ranking_mask = top_category_product_mask(ranking_source)
    if ranking_source is df:
        row_mask = ranking_mask.to_numpy()
    else:
        highlighted_indices = ranking_source.index[ranking_mask]
        row_mask = df.index.isin(highlighted_indices)

    styles = pd.DataFrame('', index=df.index, columns=df.columns)
    if row_mask.any():
        styles.iloc[row_mask, :] = TOP_PRODUCT_STYLE
    return styles


def style_execution_table(df, ranking_df=None):
    """仅为销售 Top15 品类内、各品类 Top50 单品添加整行底色。"""
    _ensure_styler_capacity(df)
    styler = df.style
    styles = _top_product_style_frame(df, ranking_df=ranking_df)
    if not styles.eq('').all().all():
        styler = styler.apply(lambda _: styles, axis=None)
    return styler


def _write_execution_sheet(writer, df):
    """写入执行表，并将页面 Top 单品整行底色同步到 Excel。"""
    frame = execution_export_frame(df)
    frame.to_excel(writer, sheet_name=EXECUTION_SHEET, index=False)

    workbook = writer.book
    worksheet = writer.sheets[EXECUTION_SHEET]
    highlight_format = workbook.add_format({
        'bg_color': TOP_PRODUCT_BACKGROUND,
        'font_color': TOP_PRODUCT_TEXT_COLOR,
        'pattern': 1,
    })
    for excel_row, highlighted in enumerate(top_category_product_mask(frame), start=1):
        if highlighted:
            worksheet.set_row(excel_row, None, highlight_format)

    return frame


def _write_category_investment_sheet(writer, df, cat_ctx=None):
    """写入与品类分析页面同口径的投放占比与 ROI 表。"""
    required = {'淘宝类目', '宝贝ID', 'GMV', '付费', '商品访客数', '关键词', '人群', '全站'}
    rows = []
    if df is not None and not df.empty and required.issubset(df.columns):
        rows = category_analysis(df, cat_ctx=cat_ctx).get('invest', [])
    pd.DataFrame(rows, columns=CATEGORY_INVESTMENT_COLUMNS).to_excel(
        writer, sheet_name=CATEGORY_INVESTMENT_SHEET, index=False
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


def export_execution_table(df, cat_ctx=None):
    """导出执行表及品类投放占比与 ROI 为 Excel bytes。"""
    validate_paid_plan_consistency(df, '执行表Excel导出')
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        _write_execution_sheet(writer, df)
        _write_metric_status_rule_sheet(writer)
        _write_category_investment_sheet(writer, df, cat_ctx=cat_ctx)
    output.seek(0)
    return output.read()


def export_all_sheets(df, cat_ctx=None):
    """导出全部分析 Sheet"""
    validate_paid_plan_consistency(df, '全部Sheet Excel导出')
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # 1. 执行表
        _write_execution_sheet(writer, df)

        # 2. 五项核心指标状态规则文档
        _write_metric_status_rule_sheet(writer)

        # 3. AI任务视图（基于当前导出范围的最终执行表）
        ai_tasks = build_ai_task_view(df)
        ai_tasks.to_excel(writer, sheet_name='AI任务视图', index=False)

        # 4. 分层
        seg_cols = [c for c in ['组合','综合等级','商品定位','对应操作'] if c in df.columns]
        if seg_cols and '宝贝ID' in df.columns and 'GMV' in df.columns:
            seg = df.groupby(seg_cols).agg(
                商品数=('宝贝ID','count'), GMV=('GMV','sum')
            ).reset_index()
            seg.to_excel(writer, sheet_name='商品分层明细', index=False)

        # 5. 品类
        if '淘宝类目' in df.columns:
            work = df.copy()
            if '退款率' in work.columns:
                work['_退款率加权金额'] = pd.to_numeric(work['退款率'], errors='coerce').fillna(0.0) \
                    * pd.to_numeric(work['GMV'], errors='coerce').fillna(0.0)
            else:
                work['_退款率加权金额'] = 0.0
            agg = {
                '商品数': ('宝贝ID', 'count'),
                'GMV': ('GMV', 'sum'),
                '付费': ('付费', 'sum'),
                '_退款率加权金额': ('_退款率加权金额', 'sum'),
            }
            if '成功退款金额' in work.columns:
                agg['成功退款金额'] = ('成功退款金额', 'sum')
            cat = work.groupby('淘宝类目').agg(**agg).reset_index()
            cat['退款率'] = cat['_退款率加权金额'] / cat['GMV'].replace(0, float('nan'))
            cat['ROI'] = cat['GMV'] / cat['付费'].replace(0, float('nan'))
            cat = cat.drop(columns=['_退款率加权金额'])
            cat.to_excel(writer, sheet_name='品类分析', index=False)

        # 6. 与页面一致的品类投放占比与 ROI
        _write_category_investment_sheet(writer, df, cat_ctx=cat_ctx)

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

    output.seek(0)
    return output.read()
