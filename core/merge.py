"""数据合并管线。"""
import pandas as pd

from .schema import (
    SOURCE_PRODUCT_INFO,
    SOURCE_PRODUCT_ROI,
    SOURCE_STOCK,
    SOURCE_WUJIE_SUBJECT,
    classify_channel,
    to_num,
)


LEGACY_SOURCE_PRODUCT_ROI = '生意参谋商品数据'
LEGACY_SOURCE_WUJIE_SUBJECT = '无界商品主体'
LEGACY_SOURCE_STOCK = '商品库存资料'
LEGACY_SOURCE_PRODUCT_INFO = '店铺商品资料'

SUM_COLS = [
    'GMV', '商品访客数', '支付买家数', '支付件数', '成功退款金额',
    '商品加购件数', '搜索引导访客数',
]
V93_PRODUCT_SUM_COLS = ['v9.3推广成交金额', 'v9.3推广加购数', 'v9.3推广访客数']
V93_VISITOR_COLS = ['关键词推广访客数', '人群推广访客数', '场景推广访客数']
V93_CART_PEOPLE_COLS = ['关键词推广加购人数', '人群推广加购人数', '场景推广加购人数']
V93_CART_COUNT_COLS = ['关键词推广加购数', '人群推广加购数', '场景推广加购数']
FIRST_COLS = ['淘宝类目', '统计日期', '商品名称', '创建时间']
WUJIE_SUM_COLS = ['付费', 'v9.3推广花费', '推广加购数', '直购数量', '直购成本', '展现', '点击']
CHANNEL_COLS = ['关键词', '人群', '全站', '店铺直达']
# 渠道归因成交(2026-07-11):渠道分析 ROI=渠道归因成交÷渠道花费,与渠道花费同源同口径
CHANNEL_ATTR_COLS = [f'{ch}归因成交' for ch in CHANNEL_COLS]
DIM_NUM_COLS = ['在途', '成本价（含税）']
STOCK_NUM_COLS = ['在仓库存', '在途', '成本价（含税）', '日销7']
REQUIRED_COLS = [
    'GMV','推广成交金额','付费','关键词','人群','全站','店铺直达',
    'v9.3推广花费','v9.3推广成交金额','v9.3推广加购数','v9.3推广访客数',
    '关键词归因成交','人群归因成交','全站归因成交','店铺直达归因成交',
    '商品访客数','支付买家数','支付件数','商品加购件数','推广加购数',
    '成功退款金额','退款率','商品支付转化率','访客平均价值',
    '搜索引导访客数',
    '推广投产','推广实际投产','件单价','利润率','预估利润','倍率',
    '购物车价值','链接投产','链接实际投产','日均消耗',
    '直购数量','直购成本','展现','点击','点击率','转化率',
    '在仓库存','在途','成本价（含税）','日销7','日销3','日销15','可售天数',
    '流量定位','价值定位','组合','商品定位','综合等级',
    '商品周期','对应操作','推广动作','预算优先级',
    '操作','风险标签','运营备注','计划数','计划明细',
    '销售排名','流量排名','消耗排名',
    '上周期金额','上周期付费','上周期人群','上周期关键词','上周期全站',
    '日销3','日销15','花费3','花费7','花费15',
    '关键词环比','人群环比','全站环比','店铺直达环比',
]
STRING_COLS = ['宝贝ID', '款式主图', '货号', '商品名称', '统计日期', '淘宝类目', '创建时间', '推广明细', 'v9.3信号']


def _frames_for(sources, *source_names):
    frames = []
    for name in source_names:
        frames.extend(sources.get(name, []))
    return frames


def _concat_frames(sources, *source_names):
    frames = [df for _, df in _frames_for(sources, *source_names) if df is not None and len(df) > 0]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _clean_id_series(values):
    return (
        values.astype(str)
        .str.replace(r'\.0$', '', regex=True)
        .str.strip()
        .replace({'nan': '', 'None': ''})
    )


def _numeric_series(values):
    return pd.Series(values).map(to_num).astype(float)


def _safe_div_series(num, den):
    n = _numeric_series(num)
    d = _numeric_series(den)
    return n.where(d != 0, 0.0) / d.where(d != 0, 1.0)


def _first_non_empty(values):
    for value in values:
        if pd.notna(value) and str(value).strip() not in ('', 'nan', 'None'):
            return value
    return ''


def _first_positive(values):
    for value in values:
        num = to_num(value)
        if num > 0:
            return num
    return 0.0


def _is_15_day_period(value):
    text = str(value)
    digits = ''.join(ch for ch in text if ch.isdigit())
    return digits == '15'


def _valid_pid_mask(frame):
    return frame['_pid'].ne('')


def _product_roi_agg(sources):
    df = _concat_frames(sources, SOURCE_PRODUCT_ROI, LEGACY_SOURCE_PRODUCT_ROI)
    if df.empty or '宝贝ID' not in df.columns:
        return pd.DataFrame(columns=['宝贝ID'])

    work = df.copy()
    work['_pid'] = _clean_id_series(work['宝贝ID'])
    work = work[_valid_pid_mask(work)]
    if work.empty:
        return pd.DataFrame(columns=['宝贝ID'])

    if '推广引导总成交金额' in work.columns:
        work['v9.3推广成交金额'] = work['推广引导总成交金额']
    visitor_cols = [col for col in V93_VISITOR_COLS if col in work.columns]
    if visitor_cols:
        work['v9.3推广访客数'] = sum(_numeric_series(work[col]) for col in visitor_cols)
    cart_people_cols = [col for col in V93_CART_PEOPLE_COLS if col in work.columns]
    if cart_people_cols:
        work['v9.3推广加购数'] = sum(_numeric_series(work[col]) for col in cart_people_cols)
    elif '推广总加购数' in work.columns:
        work['v9.3推广加购数'] = work['推广总加购数']
    else:
        cart_count_cols = [col for col in V93_CART_COUNT_COLS if col in work.columns]
        if cart_count_cols:
            work['v9.3推广加购数'] = sum(_numeric_series(work[col]) for col in cart_count_cols)

    agg = {}
    for col in SUM_COLS + V93_PRODUCT_SUM_COLS:
        if col in work.columns:
            work[col] = _numeric_series(work[col])
            agg[col] = 'sum'
    for col in FIRST_COLS:
        if col in work.columns:
            agg[col] = _first_non_empty

    out = work.groupby('_pid', as_index=False).agg(agg)
    return out.rename(columns={'_pid': '宝贝ID'})


def _filtered_wujie(sources):
    df = _concat_frames(sources, SOURCE_WUJIE_SUBJECT, LEGACY_SOURCE_WUJIE_SUBJECT)
    if df.empty or '宝贝ID' not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    work['_pid'] = _clean_id_series(work['宝贝ID'])
    work = work[_valid_pid_mask(work)]
    if '转化周期' in work.columns:
        work = work[work['转化周期'].map(_is_15_day_period)]
    return work


def _wujie_agg(work):
    if work.empty:
        return pd.DataFrame(columns=['宝贝ID'])

    agg = {}
    if '商品加购件数' in work.columns and '推广加购数' not in work.columns:
        work['推广加购数'] = work['商品加购件数']
    if '付费' in work.columns:
        work['v9.3推广花费'] = work['付费']
    for col in WUJIE_SUM_COLS:
        if col in work.columns:
            work[col] = _numeric_series(work[col])
            agg[col] = 'sum'
    # 推广归因成交(供 推广投产=归因成交÷花费,与04全链接GMV分列):
    # 无界"总成交金额"(读取层改名GMV)缺数严重(07-09样本仅259/8143行非零),
    # 而"投入产出比"(读取层改名推广投产)有值——两列同存的行完全自洽(成交=花费×ROI),
    # 故行级重构: 归因成交 = 总成交金额,缺失时回退 花费×投入产出比
    if '付费' in work.columns:
        gmv_v = _numeric_series(work['GMV']) if 'GMV' in work.columns else pd.Series(0.0, index=work.index)
        roi_v = _numeric_series(work['推广投产']) if '推广投产' in work.columns else pd.Series(0.0, index=work.index)
        work['推广成交金额'] = gmv_v.where(gmv_v > 0, work['付费'] * roi_v)
        agg['推广成交金额'] = 'sum'

    grouped = work.groupby('_pid', as_index=False).agg(agg) if agg else work[['_pid']].drop_duplicates()

    if '付费' in work.columns:
        spend = work.copy()
        spend['付费'] = _numeric_series(spend['付费'])
        spend = spend[spend['付费'] > 0]
        if not spend.empty:
            scene = spend['场景名字'] if '场景名字' in spend.columns else pd.Series('', index=spend.index)
            spend['_channel'] = scene.map(classify_channel)
            channels = (
                spend.pivot_table(index='_pid', columns='_channel', values='付费', aggfunc='sum', fill_value=0)
                .reset_index()
            )
            grouped = grouped.merge(channels, on='_pid', how='left')

        # 渠道归因成交:同一套 classify_channel,把行级归因成交也按渠道分列,
        # 供渠道分析 ROI=渠道归因成交÷渠道花费(此前误配全渠道推广投产)
        attr = work[_numeric_series(work['推广成交金额']) > 0].copy()
        if not attr.empty:
            scene = attr['场景名字'] if '场景名字' in attr.columns else pd.Series('', index=attr.index)
            attr['_channel'] = scene.map(classify_channel) + '归因成交'
            attr_piv = (
                attr.pivot_table(index='_pid', columns='_channel', values='推广成交金额', aggfunc='sum', fill_value=0)
                .reset_index()
            )
            grouped = grouped.merge(attr_piv, on='_pid', how='left')

    return grouped.rename(columns={'_pid': '宝贝ID'})


def _collect_plan_ids(work):
    if work.empty or '计划ID' not in work.columns:
        return set()
    spend = _numeric_series(work['付费']) if '付费' in work.columns else pd.Series(0, index=work.index)
    plan_ids = _clean_id_series(work['计划ID'])
    return set(plan_ids[(plan_ids != '') & (spend > 0)])


def _format_amount(value):
    return f"{to_num(value):.2f}"


def _format_count(value):
    num = to_num(value)
    if float(num).is_integer():
        return str(int(num))
    return f"{num:.2f}".rstrip('0').rstrip('.')


def _promotion_detail_agg(work):
    if work.empty or '计划ID' not in work.columns:
        return pd.DataFrame(columns=['宝贝ID', '推广明细'])

    detail = work.copy()
    detail['plan_id'] = _clean_id_series(detail['计划ID'])
    detail['spend'] = _numeric_series(detail['付费']) if '付费' in detail.columns else 0.0
    roi_col = '推广投产' if '推广投产' in detail.columns else '投入产出比'
    detail['roi'] = _numeric_series(detail[roi_col]) if roi_col in detail.columns else 0.0
    detail['add_cart'] = _numeric_series(detail['商品加购件数']) if '商品加购件数' in detail.columns else 0.0
    detail = detail[(detail['_pid'].ne('')) & (detail['plan_id'].ne('')) & (detail['spend'] > 0)]
    if detail.empty:
        return pd.DataFrame(columns=['宝贝ID', '推广明细'])

    detail['weighted_roi'] = detail['roi'] * detail['spend']
    grouped = (
        detail.groupby(['_pid', 'plan_id'], as_index=False)
        .agg(spend=('spend', 'sum'), weighted_roi=('weighted_roi', 'sum'), add_cart=('add_cart', 'sum'))
    )
    grouped['roi'] = grouped['weighted_roi'] / grouped['spend'].where(grouped['spend'] != 0, 1)
    grouped = grouped.sort_values(['_pid', 'spend', 'plan_id'], ascending=[True, False, True])

    rows = []
    for pid, part in grouped.groupby('_pid', sort=False):
        lines = []
        for idx, row in enumerate(part.head(3).to_dict('records'), start=1):
            lines.append(
                f"{idx}. 计划ID:{row['plan_id']} "
                f"花费:{_format_amount(row['spend'])} "
                f"投产:{_format_amount(row['roi'])} "
                f"加购:{_format_count(row['add_cart'])}"
            )
        rows.append({'宝贝ID': pid, '推广明细': '\n'.join(lines)})
    return pd.DataFrame(rows)


def _product_info_agg(sources):
    df = _concat_frames(sources, SOURCE_PRODUCT_INFO, LEGACY_SOURCE_PRODUCT_INFO)
    if df.empty:
        return pd.DataFrame(columns=['宝贝ID']), pd.DataFrame(columns=['货号'])

    work = df.copy()
    work['_pid'] = _clean_id_series(work['宝贝ID']) if '宝贝ID' in work.columns else ''
    work['_sku'] = _clean_id_series(work['货号']) if '货号' in work.columns else ''

    pid_work = work[work['_pid'].ne('')]
    pid_agg = pd.DataFrame(columns=['宝贝ID'])
    if not pid_work.empty:
        agg = {}
        if '_sku' in pid_work.columns:
            agg['货号'] = ('_sku', _first_non_empty)
        if '款式主图' in pid_work.columns:
            agg['款式主图'] = ('款式主图', _first_non_empty)
        if '创建时间' in pid_work.columns:
            agg['创建时间'] = ('创建时间', _first_non_empty)
        if '退款率' in pid_work.columns:
            agg['退款率'] = ('退款率', 'max')
        for col in DIM_NUM_COLS:
            if col in pid_work.columns:
                agg[col] = (col, _first_positive)
        pid_agg = pid_work.groupby('_pid', as_index=False).agg(**agg).rename(columns={'_pid': '宝贝ID'})

    sku_work = work[work['_sku'].ne('')]
    sku_agg = pd.DataFrame(columns=['货号'])
    if not sku_work.empty:
        agg = {}
        for col in DIM_NUM_COLS:
            if col in sku_work.columns:
                agg[col] = (col, _first_positive)
        if agg:
            sku_agg = sku_work.groupby('_sku', as_index=False).agg(**agg).rename(columns={'_sku': '货号'})
        else:
            sku_agg = sku_work[['_sku']].drop_duplicates().rename(columns={'_sku': '货号'})

    return pid_agg, sku_agg


def _stock_agg(sources):
    df = _concat_frames(sources, SOURCE_STOCK, LEGACY_SOURCE_STOCK)
    if df.empty:
        return pd.DataFrame(columns=['宝贝ID']), pd.DataFrame(columns=['货号'])

    work = df.copy()
    work['_pid'] = _clean_id_series(work['宝贝ID']) if '宝贝ID' in work.columns else ''
    work['_sku'] = _clean_id_series(work['货号']) if '货号' in work.columns else ''
    for col in STOCK_NUM_COLS:
        if col in work.columns:
            work[col] = _numeric_series(work[col])

    pid_work = work[work['_pid'].ne('')]
    pid_agg = pd.DataFrame(columns=['宝贝ID'])
    if not pid_work.empty:
        agg = {}
        if '_sku' in pid_work.columns:
            agg['货号'] = ('_sku', _first_non_empty)
        # 在仓库存仅款式级(货号)挂载,不做 pid 级求和——多链接共享同一库存池
        if '日销7' in pid_work.columns:
            agg['日销7'] = ('日销7', 'sum')
        for col in DIM_NUM_COLS:
            if col in pid_work.columns:
                agg[col] = (col, _first_positive)
        pid_agg = pid_work.groupby('_pid', as_index=False).agg(**agg).rename(columns={'_pid': '宝贝ID'})

    sku_work = work[work['_sku'].ne('')]
    sku_agg = pd.DataFrame(columns=['货号'])
    if not sku_work.empty:
        agg = {}
        if '在仓库存' in sku_work.columns:
            agg['在仓库存'] = ('在仓库存', 'sum')
        if '日销7' in sku_work.columns:
            agg['日销7'] = ('日销7', 'sum')
        for col in DIM_NUM_COLS:
            if col in sku_work.columns:
                agg[col] = (col, _first_positive)
        sku_agg = sku_work.groupby('_sku', as_index=False).agg(**agg).rename(columns={'_sku': '货号'})

    return pid_agg, sku_agg


def _merge_dimension(base, dim, on, suffix):
    if dim.empty or on not in dim.columns:
        return base
    return base.merge(dim, on=on, how='left', suffixes=('', suffix))


def _coalesce_columns(df, target, *sources):
    if target not in df.columns:
        df[target] = ''
    result = df[target]
    for src in sources:
        if src in df.columns:
            result = result.where(result.astype(str).str.strip().ne(''), df[src])
    df[target] = result


def _coalesce_numeric_columns(df, target, *sources):
    if target not in df.columns:
        df[target] = 0.0
    result = _numeric_series(df[target])
    for src in sources:
        if src in df.columns:
            values = _numeric_series(df[src])
            result = result.where(result > 0, values)
    df[target] = result


def _finalize_result(base):
    result_df = pd.DataFrame()
    result_df['宝贝ID'] = base['宝贝ID'].astype(str) if '宝贝ID' in base.columns else pd.Series(dtype=str)

    for col in ['款式主图', '货号', '商品名称', '统计日期', '淘宝类目', '创建时间', '推广明细']:
        result_df[col] = base[col].fillna('').astype(str) if col in base.columns else ''

    for col in [
        'GMV', '推广成交金额', '付费', '关键词', '人群', '全站', '店铺直达',
        'v9.3推广花费', 'v9.3推广成交金额', 'v9.3推广加购数', 'v9.3推广访客数',
        '关键词归因成交', '人群归因成交', '全站归因成交', '店铺直达归因成交',
        '商品访客数', '支付买家数', '支付件数', '商品加购件数', '推广加购数',
        '成功退款金额', '搜索引导访客数',
        '直购数量', '直购成本', '展现', '点击',
        '在仓库存', '在途', '成本价（含税）', '日销7',
    ]:
        result_df[col] = _numeric_series(base[col]) if col in base.columns else 0.0

    gmv = result_df['GMV']
    spend = result_df['付费']
    visitors = result_df['商品访客数']
    buyers = result_df['支付买家数']
    units = result_df['支付件数']
    refund_amt = result_df['成功退款金额']
    cost = result_df['成本价（含税）']
    add_cart = result_df['商品加购件数']
    views = result_df['展现']
    clicks = result_df['点击']

    # 2026-07-10 统一聚水潭 30日cohort 口径:不再回退生意参谋 成功退款金额/GMV。
    # 快照未覆盖的品(近30日无订单)留 NaN(展示为空),不按 0 误导——
    # 故用 to_numeric(coerce) 而非 to_num(后者把 NaN 压成 0)
    if '退款率' in base.columns:
        result_df['退款率'] = pd.to_numeric(base['退款率'], errors='coerce')
    else:
        result_df['退款率'] = float('nan')
    result_df['商品支付转化率'] = _safe_div_series(buyers, visitors)
    result_df['访客平均价值'] = _safe_div_series(gmv, visitors)
    # 双投产口径(2026-07-10 拆分,此前链接*为推广*的赋值复制、恒等):
    # 推广投产=无界归因成交÷花费(纯广告效率);链接投产=全链接GMV÷花费(含自然流量);
    # 差值≈自然流量贡献。归因成交为0而花费>0时推广投产记0(真实亏损投放,非缺数)
    result_df['推广投产'] = _safe_div_series(result_df['推广成交金额'], spend)
    result_df['推广实际投产'] = result_df['推广投产'] * (1 - result_df['退款率'].fillna(0.0))
    result_df['件单价'] = _safe_div_series(gmv, units)
    result_df['利润率'] = _safe_div_series(result_df['件单价'] - cost, result_df['件单价'])
    result_df['预估利润'] = (gmv - refund_amt) * result_df['利润率']
    result_df['倍率'] = _safe_div_series(result_df['件单价'], cost)
    result_df['购物车价值'] = _safe_div_series(gmv, add_cart)
    result_df['链接投产'] = _safe_div_series(gmv, spend)
    result_df['链接实际投产'] = result_df['链接投产'] * (1 - result_df['退款率'].fillna(0.0))
    result_df['日均消耗'] = spend / 7
    result_df['点击率'] = _safe_div_series(clicks, views)
    result_df['转化率'] = _safe_div_series(buyers, clicks)
    # 可售天数 = 在仓库存/日销7;日均为0 时留空(NaN,区别于"0天=缺货")
    daily_sales = result_df['日销7']
    result_df['可售天数'] = (result_df['在仓库存'] / daily_sales.where(daily_sales > 0)).round(1)

    for col in REQUIRED_COLS:
        if col not in result_df.columns:
            result_df[col] = 0
    for col in STRING_COLS:
        if col not in result_df.columns:
            result_df[col] = ''

    if len(result_df) > 0:
        result_df['销售排名'] = result_df['GMV'].rank(ascending=False, method='min').astype(int)
        result_df['流量排名'] = result_df['商品访客数'].rank(ascending=False, method='min').astype(int)
        result_df['消耗排名'] = result_df['付费'].rank(ascending=False, method='min').astype(int)

    return result_df


def merge_sources(sources):
    """
    sources: {source_type: [(filepath, df), ...]}
    返回: 合并后的执行表 DataFrame
    """
    product = _product_roi_agg(sources)
    wujie_rows = _filtered_wujie(sources)
    wujie = _wujie_agg(wujie_rows)
    promotion_detail = _promotion_detail_agg(wujie_rows)
    plan_ids = _collect_plan_ids(wujie_rows)

    base_ids = pd.concat(
        [frame[['宝贝ID']] for frame in [product, wujie] if '宝贝ID' in frame.columns],
        ignore_index=True,
    ).dropna().drop_duplicates()

    if base_ids.empty:
        return _finalize_result(pd.DataFrame(columns=['宝贝ID'])), plan_ids

    base = base_ids.merge(product, on='宝贝ID', how='left')
    base = base.merge(wujie, on='宝贝ID', how='left', suffixes=('', '_wujie'))
    base = base.merge(promotion_detail, on='宝贝ID', how='left')

    product_info_by_pid, product_info_by_sku = _product_info_agg(sources)
    stock_by_pid, stock_by_sku = _stock_agg(sources)

    base = _merge_dimension(base, product_info_by_pid, '宝贝ID', '_info_pid')
    base = _merge_dimension(base, stock_by_pid, '宝贝ID', '_stock_pid')
    _coalesce_columns(base, '货号', '货号_info_pid', '货号_stock_pid')

    base = _merge_dimension(base, product_info_by_sku, '货号', '_info_sku')
    base = _merge_dimension(base, stock_by_sku, '货号', '_stock_sku')

    _coalesce_columns(base, '创建时间', '创建时间_info_pid')
    # 退款率:聚水潭 30日cohort 唯一来源(2026-07-10 起不再回退生意参谋)。
    # 不走 _coalesce_numeric_columns——它会把未覆盖品的 NaN 压成 0,而 0%(无退款)
    # 与 空(近30日无订单、无数据) 语义不同,必须保留 NaN
    for _refund_cand in ('退款率_info_pid', '退款率'):
        if _refund_cand in base.columns:
            base['退款率'] = pd.to_numeric(base[_refund_cand], errors='coerce')
            break
    _coalesce_numeric_columns(base, '在仓库存', '在仓库存_stock_sku', '在仓库存_stock_pid')
    _coalesce_numeric_columns(base, '日销7', '日销7_stock_pid', '日销7_stock_sku')
    _coalesce_numeric_columns(base, '在途', '在途_info_pid', '在途_stock_pid', '在途_info_sku', '在途_stock_sku')
    _coalesce_numeric_columns(
        base,
        '成本价（含税）',
        '成本价（含税）_info_pid',
        '成本价（含税）_stock_pid',
        '成本价（含税）_info_sku',
        '成本价（含税）_stock_sku',
    )

    for col in CHANNEL_COLS + SUM_COLS + V93_PRODUCT_SUM_COLS + WUJIE_SUM_COLS:
        if col in base.columns:
            base[col] = _numeric_series(base[col])

    return _finalize_result(base), plan_ids
