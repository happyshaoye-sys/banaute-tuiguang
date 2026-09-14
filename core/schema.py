"""半自动推广工具 — 数据字典与常量配置"""
import hashlib
import json
import re
import numpy as np

# ── 文件分类规则 ──────────────────────────────────────────
SOURCE_PRODUCT_ROI = '商品-经营投产比'
SOURCE_WUJIE_SUBJECT = '无界-商品主体'
SOURCE_PRODUCT_OVERALL = '商品-整体'
SOURCE_WUJIE_CREATIVE = '无界-创意'
SOURCE_STOCK = '库存'
SOURCE_PRODUCT_INFO = '商品资料'

FILE_CLASSIFIERS = [
    (['商品-经营投产比', '经营投产比'], SOURCE_PRODUCT_ROI),
    (['无界-商品主体', '无界商品主体'], SOURCE_WUJIE_SUBJECT),
    (['库存明细', '商品库存资料'], SOURCE_STOCK),
    (['店铺商品资料', '商品资料'], SOURCE_PRODUCT_INFO),
]

# 新品源必须是按日导出的原始表。不能用“包含关键字”匹配，否则
# `商品-整体退款分布-YYYY-MM-DD.xlsx` 会被误读为主图整体数据源并触发无效校验。
NEW_PRODUCT_DAILY_FILE_CLASSIFIERS = [
    (re.compile(r'(?:商品[-_]?整体|商品整体)[-_]?\d{4}-\d{2}-\d{2}$', re.IGNORECASE), SOURCE_PRODUCT_OVERALL),
    (re.compile(r'(?:无界[-_]?创意|无界创意)[-_]?\d{4}-\d{2}-\d{2}$', re.IGNORECASE), SOURCE_WUJIE_CREATIVE),
]

ALLOWED_SOURCE_TYPES = {
    SOURCE_PRODUCT_ROI,
    SOURCE_WUJIE_SUBJECT,
    SOURCE_PRODUCT_OVERALL,
    SOURCE_WUJIE_CREATIVE,
    SOURCE_STOCK,
    SOURCE_PRODUCT_INFO,
}

# ── 列名别名映射 ──────────────────────────────────────────
FIELD_ALIASES = {
    '宝贝ID': ['宝贝ID','商品ID','主体ID','item_id','Item ID','商品id','宝贝id','主体id','平台款式编码','平台店铺款式编码'],
    '商品名称': ['商品名称','商品标题','标题','产品名称'],
    '店铺': ['店铺','店铺名称','店铺名'],
    '款式主图': ['款式主图'],
    'GMV': ['GMV','销售额','成交金额','支付金额','总成交金额'],
    '付费': ['付费','广告费','消耗','花费'],
    '计划ID': ['计划ID','计划编号','计划id','campaign_id','Campaign ID'],
    '计划名字': ['计划名字','计划名称','计划名','campaign_name','Campaign Name'],
    '商品访客数': ['商品访客数','访客数','流量','UV','引导访问量'],
    '统计日期': ['统计日期','日期','周期'],
    '淘宝类目': ['淘宝类目','类目','品类','商品类目','叶子类目','叶子类目名称'],
    '货号': ['货号','商家编码','款号','款式编码','线上款式编码'],
    '创建时间': ['创建时间','上架时间'],
    '在仓库存': ['在仓库存','库存','可售库存','经营库存数量','库存数量','经营库存（陈明共享）','公有可用数'],
    '在途': ['在途','在途库存','采购在途数','采购在途','在途数'],
    '日销7': ['日销7','近7日日均销量','7日日均销量','日均销量'],
    '成功退款金额': ['成功退款金额','退款金额','售后退款金额'],
    '退款率': ['退款率','售后退款率'],
    '访客平均价值': ['访客平均价值','UV价值','访客价值'],
    '成本价（含税）': ['成本价（含税）','成本价','初始买断价','含税成本'],
    '支付买家数': ['支付买家数','支付人数','买家数','成交用户数','成交人数'],
    '支付件数': ['支付件数','成交件数','销量'],
    '商品加购件数': ['商品加购件数','加购件数','总购物车数','总收藏加购数'],
    '商品状态': ['商品状态','宝贝状态','当前状态'],
    '主图整体点击率': ['主图整体点击率','主图点击率','商品主图点击率'],
    '推广加购数': ['推广加购数','推广加购件数','广告加购数','总购物车数'],
    '推广消耗金额': ['推广消耗金额'],
    '推广引导总成交金额': ['推广引导总成交金额'],
    '推广总加购数': ['推广总加购数'],
    '关键词推广访客数': ['关键词推广访客数'],
    '人群推广访客数': ['人群推广访客数'],
    '场景推广访客数': ['场景推广访客数'],
    '关键词推广加购人数': ['关键词推广加购人数'],
    '人群推广加购人数': ['人群推广加购人数'],
    '场景推广加购人数': ['场景推广加购人数'],
    '关键词推广加购数': ['关键词推广加购数'],
    '人群推广加购数': ['人群推广加购数'],
    '场景推广加购数': ['场景推广加购数'],
    '商品支付转化率': ['商品支付转化率','支付转化率','点击转化率'],
    '搜索引导访客数': ['搜索引导访客数','搜索访客数'],
    '投入产出比': ['投入产出比','推广投产','投产比','ROI'],
    '直接购物车数': ['直接购物车数','直购数量','直接购物车数量'],
    '场景名字': ['场景名字','营销场景','场景','渠道','投放场景'],
    '转化周期': ['转化周期','周期类型','归因周期','转化窗口'],
    '创意ID': ['创意ID','创意id','creative_id','Creative ID'],
    '创意名字': ['创意名字','创意名称','创意名','创意标题'],
    '主体名称': ['主体名称','主体名字','主体名'],
    '展现': ['展现','展现量','曝光','展示'],
    '点击': ['点击','点击量','点击数'],
    '点击率': ['点击率','CTR'],
    '转化率': ['转化率','CVR'],
    '关键词': ['关键词','关键词名称','词'],
    '人群名称': ['人群名称','人群','人群名'],
}

# 无界-商品主体 特殊列重映射
WUJIE_REMAP = {
    '主体ID':'宝贝ID','商品ID':'宝贝ID',
    '支付金额':'GMV','总成交金额':'GMV',
    '投入产出比':'推广投产','直接购物车数':'直购数量',
    '总购物车数':'商品加购件数',
    '点击转化率':'商品支付转化率',
}

# ── 6档分层标签 ──────────────────────────────────────────
TIER_LABELS = ['滞销款','待改善','潜力款','冲刺款','金牛款','明星款']


# ── 取数 schema 指纹（v2.56）──────────────────────────────
#: 影响「源文件 → 标准化列集合」的解析层规则。改动任何一项都必须让两层缓存失效：
#: 解析层缓存（loader._parsed）与日事实缓存（daily_cache）都只按
#: 「版本 + 文件路径 + 大小 + mtime」命中，源文件不变就一直命中，
#: 于是"改了白名单/别名却没换缓存版本"会表现为改完没生效（v2.55 踩过一次）。
#: 现在把这张指纹编进缓存键与缓存载荷校验里，改规则即自动失效，不再依赖人工记得升版本。
_PARSE_SCHEMA_TABLES = (
    'FILE_CLASSIFIERS',
    'NEW_PRODUCT_DAILY_FILE_CLASSIFIERS',
    'ALLOWED_SOURCE_TYPES',
    'FIELD_ALIASES',
    'WUJIE_REMAP',
)


def _normalize_for_hash(value):
    """把正则/集合/映射统一成可排序、可比对的纯文本结构。"""
    if isinstance(value, re.Pattern):
        return {'pattern': value.pattern, 'flags': value.flags}
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize_for_hash(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _normalize_for_hash(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(item) for item in value]
    return value


def schema_fingerprint() -> str:
    """解析层 schema 指纹（内容哈希，不含时间戳，跨进程稳定）。"""
    module = globals()
    payload = {
        name: _normalize_for_hash(module.get(name))
        for name in _PARSE_SCHEMA_TABLES
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]


# ── 36宫格矩阵 ────────────────────────────────────────────
MATRIX_36 = {}
_matrix_raw = [
    ['金牛款','冲刺款','A','冲刺款','重点'],
    ['金牛款','金牛款','A+','金牛款','放量'],
    ['金牛款','潜力款','A','冲刺款','成长期'],
    ['金牛款','待改善','B','潜力款','衰退预警'],
    ['金牛款','滞销款','C','待改善','待优化'],
    ['金牛款','明星款','A+','金牛款','放量'],
    ['冲刺款','冲刺款','A','冲刺款','重点'],
    ['冲刺款','金牛款','A','冲刺款','重点'],
    ['冲刺款','潜力款','A','冲刺款','成长期'],
    ['冲刺款','待改善','C','待改善','待优化'],
    ['冲刺款','滞销款','C','待改善','待优化'],
    ['冲刺款','明星款','A','冲刺款','重点'],
    ['潜力款','冲刺款','B','潜力款','成长期'],
    ['潜力款','金牛款','B','潜力款','成长期'],
    ['潜力款','潜力款','B','潜力款','待优化'],
    ['潜力款','待改善','C','待改善','待优化'],
    ['潜力款','滞销款','C','待改善','待优化'],
    ['潜力款','明星款','B','潜力款','加预算'],
    ['待改善','冲刺款','C','待改善','加预算'],
    ['待改善','金牛款','B','潜力款','加预算'],
    ['待改善','潜力款','C','待改善','待优化'],
    ['待改善','待改善','C','待改善','待优化'],
    ['待改善','滞销款','D','滞销款','淘汰'],
    ['待改善','明星款','C','待改善','加预算'],
    ['滞销款','冲刺款','B','潜力款','加预算'],
    ['滞销款','金牛款','B','潜力款','加预算'],
    ['滞销款','潜力款','C','待改善','观察期'],
    ['滞销款','待改善','D','滞销款','观察期'],
    ['滞销款','滞销款','D','滞销款','观察期'],
    ['滞销款','明星款','B','潜力款','加预算'],
    ['明星款','冲刺款','A+','金牛款','适当放量'],
    ['明星款','金牛款','S','明星款','放量'],
    ['明星款','潜力款','A','冲刺款','待优化'],
    ['明星款','待改善','B','潜力款','衰退预警'],
    ['明星款','滞销款','C','待改善','衰退期'],
    ['明星款','明星款','S','明星款','放量'],
]
for r in _matrix_raw:
    MATRIX_36[r[0]+r[1]] = {'grade':r[2],'pos':r[3],'action':r[4]}

# ── 推广动作规则 ──────────────────────────────────────────
ACTION_RULES = {
    '放量':     {'action':'加大投入','budget':'提高预算，重点保量','focus':'控库存、控节奏、防衰退'},
    '适当放量': {'action':'适当加大投入','budget':'逐步提预算','focus':'保持 ROI 和库存安全'},
    '重点':     {'action':'重点测试放量','budget':'小步加预算，观察承接','focus':'增加曝光和成交'},
    '成长期':   {'action':'优化后逐步加量','budget':'低到中预算测试','focus':'验证流量承载和转化稳定性'},
    '加预算':   {'action':'增加预算验证','budget':'小步增加预算','focus':'验证新增流量承接能力'},
    '成熟期':   {'action':'维持或控节奏放量','budget':'保持稳定预算，ROI好时加量','focus':'稳定成交和利润，防止衰退'},
    '衰退预警': {'action':'排查衰退并控节奏','budget':'谨慎放量或控预算','focus':'排查流量、转化、价格和库存变化'},
    '上升验证': {'action':'小步加预算验证动量','budget':'小幅提预算，观察承接','focus':'短期动量向上（日销3>日销7），确认转化和退款稳定后转放量，动量消失则控节奏'},
    '衰退期':   {'action':'控投控库存','budget':'控制预算','focus':'清库存、防止无效消耗'},
    '待优化':   {'action':'优化后再投放','budget':'降低预算或小额验证','focus':'优化主图、价格、详情、评价'},
    '观察期':   {'action':'控投或清仓观察','budget':'原则上不新增预算','focus':'根据库存做清仓、下架或保留观察'},
    '淘汰':     {'action':'停投清仓淘汰','budget':'停止新增预算','focus':'清仓、下架或移出推广池'},
}

# ── 执行表展示/导出列顺序 ────────────────────────────────
# 2026-08-10 按运营指定清单重排；原清单中的重复“推广动作”只保留首次位置，
# 避免页面与 Excel 出现两个同名表头。这里只定义顺序，不参与任何字段计算。
EXPORT_COLS = [
    '统计日期','宝贝ID','款式主图','淘宝类目','货号','链接数','创建时间',
    '销售排名','流量排名','消耗排名','GMV','链接投产',
    '推广动作','商品动作','运营备注',
    '链接投产状态','可售天数状态','推广加购率状态','商品加购率状态','商品支付转化率状态',
    '流量定位','价值定位','组合','商品定位','综合等级','商品周期','v9.3信号',
    'MetoX算法命中','MetoX算法分','MetoX算法原因',
    '预算优先级','风险标签','当前有效投放计划数','推广明细',
    '日销3','日销7','日销15','花费3','花费7','花费15','可售天数','在仓库存','在途','成本价（含税）',
    '预估利润','付费','推广投产','推广加购率','点击率','日均消耗','利润率',
    '人群','人群环比','关键词','关键词环比','全站','全站环比',
    '上周期人群','上周期关键词','上周期全站','上周期付费','直购数量','直购成本',
    '推广实际投产','链接实际投产','购物车价值','件单价','倍率',
    '商品访客数','商品加购件数','商品加购率','支付买家数','支付件数','上周期金额',
    '商品支付转化率','访客平均价值','成功退款金额','退款率',
]


def ensure_export_columns(frame):
    """补齐执行表缺失字段；不改已有字段值、计算结果或额外内部列。"""
    out = frame.copy()
    for col in EXPORT_COLS:
        if col not in out.columns:
            out[col] = ''
    return out


def execution_export_frame(frame):
    """返回严格按 EXPORT_COLS 排列的执行表副本。"""
    return ensure_export_columns(frame).loc[:, EXPORT_COLS]

# ── 工具函数 ─────────────────────────────────────────────
def safe_div(a, b):
    return a / b if b else 0.0

def to_num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 0.0
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r'[,\s¥￥$%]', '', str(v).strip())
    try: return float(s)
    except: return 0.0

def classify_file(fn):
    name = fn.lower()
    stem = re.sub(r'\.(?:xlsx|xls|csv)$', '', name, flags=re.IGNORECASE)
    for pattern, src in NEW_PRODUCT_DAILY_FILE_CLASSIFIERS:
        if pattern.fullmatch(stem):
            return src
    for keywords, src in FILE_CLASSIFIERS:
        if any(kw.lower() in name for kw in keywords):
            return src
    return '其他数据'

def extract_date(fn):
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', fn)
    if m: return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(\d{4})(\d{2})(\d{2})', fn)
    if m: return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None

def classify_channel(scene):
    s = str(scene).lower()
    if '关键词' in s or '标准计划' in s: return '关键词'
    if '人群' in s or '定向' in s: return '人群'
    if '全站' in s or '智选' in s: return '全站'
    if any(x in s for x in ['店铺','品牌','直达']): return '店铺直达'
    return '关键词'

def compute_bounds(values, n=6):
    """等距分组参考分界点"""
    arr = [v for v in values if v > 0]
    if len(arr) < n:
        mn, mx = min(arr) if arr else 0, max(arr) if arr else 1
        step = (mx - mn) / n or 1
        return [mn + step * i for i in range(1, n)]
    return [float(np.percentile(arr, p)) for p in [16.67, 33.33, 50, 66.67, 83.33]]
