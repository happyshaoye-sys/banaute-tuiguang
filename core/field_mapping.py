"""字段标准化：列名映射 + 数值安全转换"""
import re
import numpy as np
import pandas as pd

# 标准字段 → 备选列名
STANDARD_FIELDS = {
    '宝贝ID':       ['宝贝ID','商品ID','主体ID','item_id','Item ID','商品id','宝贝id'],
    '访客数':       ['访客数','商品访客数','UV','访客','商品访客','访客人数','引导访问量'],
    'GMV':          ['GMV','成交金额','支付金额','总成交金额','销售额','成交GMV'],
    '花费':         ['花费','消耗','广告费','付费','推广花费'],
    '展现':         ['展现','展现量','曝光','展示','曝光量'],
    '点击':         ['点击','点击量','点击数'],
    '访客平均价值': ['访客平均价值','UV价值','访客价值','uv价值'],
    '退款率':       ['退款率','售后退款率'],
    '件单价':       ['件单价','单价','成交单价'],
    '点击率':       ['点击率','CTR','ctr'],
    '转化率':       ['转化率','CVR','cvr','支付转化率','商品支付转化率'],
    '加购数':       ['加购数','加购件数','商品加购件数','总购物车数','购物车数'],
    '支付件数':     ['支付件数','成交件数','销量','销售件数'],
    '支付买家数':   ['支付买家数','买家数','成交用户数','成交人数'],
    '在仓库存':     ['在仓库存','库存','可售库存','经营库存数量','库存数量'],
    '在途':         ['在途','在途库存','采购在途数','采购在途','在途数'],
    '成功退款金额': ['成功退款金额','退款金额','售后退款金额'],
    '成本价':       ['成本价（含税）','成本价','初始买断价','含税成本'],
    '统计日期':     ['统计日期','日期','周期'],
    '淘宝类目':     ['淘宝类目','类目','品类','商品类目','叶子类目','叶子类目名称'],
    '货号':         ['货号','款号','款式编码'],
    '创建时间':     ['创建时间'],
    '场景名字':     ['场景名字','营销场景','渠道','投放场景'],
    '转化周期':     ['转化周期','周期类型','归因周期'],
    '推广投产':     ['推广投产','投入产出比','投产比','ROI'],
    '直购数量':     ['直购数量','直接购物车数','直接购物车数量'],
    '直购成本':     ['直购成本'],
    '搜索引导访客数':     ['搜索引导访客数','搜索访客数'],
    '计划ID':       ['计划ID','计划id','campaign_id','Campaign ID'],
    '商品名称':     ['商品名称','商品标题','标题','产品名称'],
    '关键词':       ['关键词推广'],
    '人群推广':     ['精准人群','精准人群推广'],
}


def to_num(v):
    """安全数值转换：处理空值、逗号、货币符号、百分号"""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and np.isnan(v):
            return 0.0
        return float(v)
    s = str(v).strip()
    if s in ('', '--', '-', 'NA', 'N/A', 'nan', 'None'):
        return 0.0
    # 去掉千分位逗号、货币符号、百分号
    s = re.sub(r'[,\s￥¥$%]', '', s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def find_col(df, candidates):
    """返回第一个存在于 DataFrame 中的列名"""
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _coalesce_duplicate_columns(df):
    """合并重命名后产生的重复列，逐行保留第一个非空值。"""
    if not df.columns.has_duplicates:
        return df

    out = pd.DataFrame(index=df.index)
    seen = set()
    for col in df.columns:
        if col in seen:
            continue
        seen.add(col)
        same_name = df.loc[:, df.columns == col]
        if same_name.shape[1] == 1:
            out[col] = same_name.iloc[:, 0]
            continue

        non_empty = same_name.replace(r'^\s*$', np.nan, regex=True)
        non_empty = non_empty.replace(['nan', 'None', 'NA', 'N/A', '--', '-'], np.nan)
        out[col] = non_empty.bfill(axis=1).iloc[:, 0].fillna('')
    return out


def standardize_fields(df):
    """
    字段标准化：
    1. 清理列名空白
    2. 按 STANDARD_FIELDS 映射为标准列名
    3. 挂载 for_analysis.py/segmentation.py 所需关键列
    4. 对所有数值候选列做 to_num 清洗
    """
    df = df.copy()
    # 清理列名
    df.columns = df.columns.astype(str).str.strip()

    # 列名映射
    rename = {}
    for std_name, aliases in STANDARD_FIELDS.items():
        lowered_aliases = [a.lower() for a in aliases]
        for col in df.columns:
            if col in aliases or col.lower() in lowered_aliases:
                if col != std_name:
                    rename[col] = std_name
    df.rename(columns=rename, inplace=True)
    df = _coalesce_duplicate_columns(df)

    # 数值列清洗：对所有可能含数值的列执行 to_num
    numeric_candidates = [
        '访客数', 'GMV', '花费', '展现', '点击', '访客平均价值',
        '件单价', '点击率', '转化率', '加购数', '支付件数',
        '支付买家数', '在仓库存', '在途', '成功退款金额', '成本价',
        '推广投产', '直购数量', '直购成本', '搜索引导访客数',
        '关键词', '人群名称',
    ]
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = df[col].apply(to_num)
    # 退款率单独处理:聚水潭cohort口径下 NaN=无数据(近30日无订单)≠0%,
    # to_num 会把 NaN 压成 0 造成误导,故用 coerce 保留 NaN
    if '退款率' in df.columns:
        df['退款率'] = pd.to_numeric(df['退款率'], errors='coerce')

    # 兼容旧代码：挂载别名列
    if '访客数' in df.columns and '商品访客数' not in df.columns:
        df['商品访客数'] = df['访客数']
    if '花费' in df.columns and '付费' not in df.columns:
        df['付费'] = df['花费']
    if '加购数' in df.columns and '商品加购件数' not in df.columns:
        df['商品加购件数'] = df['加购数']
    if '成本价' in df.columns and '成本价（含税）' not in df.columns:
        df['成本价（含税）'] = df['成本价']

    return df
