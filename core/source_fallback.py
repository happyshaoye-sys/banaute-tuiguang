"""数据源兜底（v2.61）：某日缺「商品-经营投产比」时，用同日的「商品-整体」+「无界-商品主体」
+ 历史类目映射，构造**列结构完全相同**的替代帧，交给既有合并链路。

原则
----
* **不新增任何业务字段**：输出只使用 `SOURCE_REQUIRED_FIELDS['商品-经营投产比']` 里的 25 个既有列名。
* 只在该日**确缺** `商品-经营投产比` 且至少有一份 `商品-整体` 时启用；有正常源的日子完全不受影响。
* 兜底来源与缺口写进提示（`describe_fallback`），不静默。
* 渠道访客：用无界的**点击量**按场景映射既有的
  `关键词推广访客数 / 人群推广访客数 / 场景推广访客数`；
  实测 11-07 逐宝贝中位比 1.000（点击≈访客），且人群推广在经营投产比里本就恒为 0。
* 渠道加购数：用无界的**总购物车数**按场景映射既有的
  `关键词推广加购数 / 人群推广加购数 / 场景推广加购数`；
  11-05 实证与经营投产比完全相等（关键词 769/769、人群 4,291/4,291，逐宝贝 193/193 相等）。
* 渠道加购人数（关键词/人群/场景）无界无「人数」口径 → 置 0，不臆造并在提示里说明。
* 淘宝类目**不做标题推断**：文件自带「叶子类目名称」优先，其次历史映射，仍补不到即留空。

字段映射
--------
=================  ==========================  ==============================
输出列（既有名）      商品-整体                    无界-商品主体
=================  ==========================  ==============================
统计日期              统计日期/日期                —
宝贝ID               商品ID/宝贝ID               —
商品名称              商品名称                    —
淘宝类目              叶子类目名称 → 历史映射        —
GMV                 支付金额                     —
商品访客数            商品访客数                   —
支付买家数            支付买家数                   —
支付件数              支付件数                     —
商品加购件数          加购件数                     —
商品支付转化率        支付转化率                   —
访客平均价值          UV价值                      —
成功退款金额          成功退款金额                 —
搜索引导访客数        搜索引导访客数                —
推广消耗金额          —                           Σ 付费
推广引导总成交金额     —                           Σ GMV
推广总加购数          —                           Σ 商品加购件数
关键词推广访客数       —                           Σ 点击（场景=关键词推广）
人群推广访客数         —                           Σ 点击（场景=人群推广）
场景推广访客数         —                           Σ 点击（其它场景）
关键词推广加购数       —                           Σ 商品加购件数（关键词推广）
人群推广加购数         —                           Σ 商品加购件数（人群推广）
场景推广加购数         —                           Σ 商品加购件数（其它场景）
渠道加购人数 3 列     —                           0（无界无人数口径）
=================  ==========================  ==============================
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .loader import read_table
from .schema import SOURCE_PRODUCT_OVERALL, SOURCE_PRODUCT_ROI, SOURCE_WUJIE_SUBJECT, to_num

#: 输出必须与「商品-经营投产比」的既有列一致；顺序稳定便于核对
FALLBACK_COLUMNS = (
    '统计日期', '宝贝ID', '商品名称', '淘宝类目', 'GMV', '商品访客数', '支付买家数', '支付件数',
    '商品加购件数', '商品支付转化率', '访客平均价值', '成功退款金额', '搜索引导访客数',
    '推广消耗金额', '推广引导总成交金额', '推广总加购数',
    '关键词推广访客数', '人群推广访客数', '场景推广访客数',
    '关键词推广加购人数', '关键词推广加购数', '人群推广加购人数', '人群推广加购数',
    '场景推广加购人数', '场景推广加购数',
)

_OVERALL_FIELD_MAP = {
    'GMV': ('支付金额', '总成交金额'),
    '商品访客数': ('商品访客数', '访客数'),
    '支付买家数': ('支付买家数',),
    '支付件数': ('支付件数',),
    '商品加购件数': ('加购件数', '商品加购件数'),
    '商品支付转化率': ('支付转化率', '商品支付转化率'),
    '访客平均价值': ('UV价值', '访客平均价值'),
    '成功退款金额': ('成功退款金额',),
    '搜索引导访客数': ('搜索引导访客数',),
}
_DATE_CANDIDATES = ('统计日期', '日期')
_PID_CANDIDATES = ('商品ID', '宝贝ID', '主体ID')
_CAT_CANDIDATES = ('叶子类目名称', '淘宝类目', '类目', '品类')

_SCENE_KEYWORD = '关键词推广'
_SCENE_CROWD = '人群推广'


def _first_column(df, candidates):
    for name in candidates:
        if name in df.columns:
            return name
    return None


def read_overall_raw(path):
    """按原样读「商品-整体」（不套列白名单），只取需要的列。"""
    df = read_table(str(path))
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    date_col = _first_column(df, _DATE_CANDIDATES)
    pid_col = _first_column(df, _PID_CANDIDATES)
    if pid_col is None:
        return pd.DataFrame()
    out['统计日期'] = df[date_col].astype(str).str.strip() if date_col else ''
    out['宝贝ID'] = df[pid_col].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    out['商品名称'] = df['商品名称'].astype(str) if '商品名称' in df.columns else ''
    cat_col = _first_column(df, _CAT_CANDIDATES)
    out['淘宝类目'] = df[cat_col].astype(str).str.strip() if cat_col else ''
    for target, candidates in _OVERALL_FIELD_MAP.items():
        col = _first_column(df, candidates)
        out[target] = df[col] if col else ''
    return out


def build_category_map(product_folders, lookback=7):
    """用最近若干天的 商品 目录建「宝贝ID → 淘宝类目」映射（最新优先）。"""
    mapping = {}
    folders = [p for p in (product_folders or []) if p and Path(p).is_dir()]
    for folder in folders[:lookback]:
        folder = Path(folder)
        for path in sorted(folder.glob('商品-经营投产比-*.xlsx')) + sorted(folder.glob('商品-整体-*.xlsx')):
            try:
                frame = read_table(str(path))
            except Exception:  # noqa: BLE001 - 单文件读失败不影响兜底
                continue
            if frame is None or len(frame) == 0:
                continue
            pid_col = _first_column(frame, _PID_CANDIDATES)
            cat_col = _first_column(frame, _CAT_CANDIDATES)
            if pid_col is None or cat_col is None:
                continue
            for pid, cat in zip(frame[pid_col].astype(str).str.strip(), frame[cat_col]):
                cat = str(cat).strip()
                if pid and pid.lower() != 'nan' and cat and cat.lower() != 'nan':
                    mapping.setdefault(pid, cat)
    return mapping


def _scene_frames(wujie_frames):
    """把无界商品主体帧按场景聚合到宝贝级：点击 / 付费 / GMV / 加购件数。"""
    if not wujie_frames:
        return pd.DataFrame(columns=['宝贝ID'])
    frame = pd.concat([df for _src, df in wujie_frames if df is not None and len(df)], ignore_index=True)
    if len(frame) == 0 or '宝贝ID' not in frame.columns:
        return pd.DataFrame(columns=['宝贝ID'])
    frame['_pid'] = frame['宝贝ID'].astype(str).str.strip()
    for src, dst in (('付费', '_spend'), ('点击', '_click'), ('GMV', '_gmv'), ('商品加购件数', '_cart')):
        frame[dst] = pd.to_numeric(frame[src], errors='coerce').fillna(0.0) if src in frame.columns else 0.0
    scene = frame['场景名字'].astype(str).str.strip() if '场景名字' in frame.columns else pd.Series('', index=frame.index)
    frame['_scene'] = scene

    agg = frame.groupby('_pid', as_index=False).agg(
        _spend=('_spend', 'sum'), _gmv=('_gmv', 'sum'), _cart=('_cart', 'sum'))
    for label, key in (('_kw', _SCENE_KEYWORD), ('_rq', _SCENE_CROWD)):
        sub = frame[frame['_scene'] == key].groupby('_pid')['_click'].sum()
        agg = agg.merge(sub.rename(label), left_on='_pid', right_index=True, how='left')
        # 渠道加购数：无界「总购物车数」= 生意参谋「渠道加购数」
        # （11-05 实证：关键词 769/769、人群 4,291/4,291 完全相等；逐宝贝 193/193 相等）
        sub_cart = frame[frame['_scene'] == key].groupby('_pid')['_cart'].sum()
        agg = agg.merge(sub_cart.rename(label + '_cart'), left_on='_pid', right_index=True, how='left')
    other_mask = ~frame['_scene'].isin([_SCENE_KEYWORD, _SCENE_CROWD])
    agg = agg.merge(frame[other_mask].groupby('_pid')['_click'].sum().rename('_sc'),
                    left_on='_pid', right_index=True, how='left')
    agg = agg.merge(frame[other_mask].groupby('_pid')['_cart'].sum().rename('_sc_cart'),
                    left_on='_pid', right_index=True, how='left')
    for col in ('_kw', '_rq', '_sc', '_kw_cart', '_rq_cart', '_sc_cart'):
        agg[col] = pd.to_numeric(agg[col], errors='coerce').fillna(0.0)
    agg = agg.rename(columns={'_pid': '宝贝ID'})
    return agg


def find_overall_paths(product_folder):
    """在指定商品目录里找「商品-整体」文件（新老命名都认，排除退款分布等衍生表）。

    v2.63 修复：主流程在传入 `shared_dimensions` 时不会扫描商品目录
    （`include_day_product=False`），`sources` 里也就没有 `商品-整体`。
    兜底必须自己按目录再找一遍，否则条件永远不成立、兜底不会触发。
    """
    if not product_folder:
        return []
    folder = Path(product_folder)
    if not folder.is_dir():
        return []
    paths = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ('.xlsx', '.xls', '.csv'):
            continue
        name = path.name
        if '整体' not in name or '退款' in name:
            continue
        if '商品-整体' in name or '商品_整体' in name:
            paths.append(str(path))
    return paths


def apply_fallback(sources, product_folder, period_label=''):
    """就地兜底：sources 缺「商品-经营投产比」时注入替代帧。

    返回 ``(injected: bool, note: str, frame)``；无来源可兜底时 note 说明原因。
    单独抽成函数的原因：主流程在传了 shared_dimensions 时不会扫描商品目录，
    兜底必须自己按目录找「商品-整体」，这段逻辑必须可单测。
    """
    if not isinstance(sources, dict):
        return False, '', None
    from .schema import SOURCE_PRODUCT_ROI  # 延迟导入避免循环
    if SOURCE_PRODUCT_ROI in sources:
        return False, '', None

    overall_paths = [fp for fp, _df in sources.get(SOURCE_PRODUCT_OVERALL, [])]
    if not overall_paths:
        overall_paths = find_overall_paths(product_folder)
    day_label = str(period_label).split(':')[-1] if period_label else ''
    if not overall_paths:
        return False, (f"{day_label} 缺 `商品-经营投产比` 且无 `商品-整体` 可兜底，"
                       f"该日 GMV/花费 将缺失" if day_label else ''), None

    lookback_folders = []
    if product_folder:
        parent = Path(product_folder).parent
        if parent.is_dir():
            lookback_folders = sorted(
                (p for p in parent.iterdir() if p.is_dir() and p.name[:2] == '20'),
                reverse=True)[:7]
    try:
        category_map = build_category_map(lookback_folders)
        frame, stats = build_fallback_frame(
            overall_paths, sources.get(SOURCE_WUJIE_SUBJECT, []), category_map)
    except Exception as exc:  # noqa: BLE001 - 兜底失败不阻断主流程
        return False, f"{day_label} 兜底构造失败：{type(exc).__name__}: {str(exc)[:80]}", None

    if frame is None or len(frame) == 0:
        return False, f"{day_label} 缺 `商品-经营投产比` 且兜底不可用（{stats.get('reason')}）", None

    sources[SOURCE_PRODUCT_ROI] = [(f'<兜底>商品-整体+无界({day_label})', frame)]
    return True, describe_fallback(day_label, stats), frame


def build_fallback_frame(overall_paths, wujie_frames, category_map=None):
    """构造与「商品-经营投产比」同列的兜底帧。返回 (DataFrame, 说明 dict)。"""
    category_map = category_map or {}
    parts = []
    for path in overall_paths or []:
        frame = read_overall_raw(path)
        if len(frame):
            parts.append(frame)
    if not parts:
        return pd.DataFrame(columns=FALLBACK_COLUMNS), {'ok': False, 'reason': '无可用商品-整体文件'}
    overall = pd.concat(parts, ignore_index=True)
    overall['宝贝ID'] = overall['宝贝ID'].astype(str).str.strip()
    overall = overall[overall['宝贝ID'].ne('') & overall['宝贝ID'].str.lower().ne('nan')]
    if len(overall) == 0:
        return pd.DataFrame(columns=FALLBACK_COLUMNS), {'ok': False, 'reason': '商品-整体无有效宝贝ID'}

    # 类目：先用文件自带叶子类目，缺的用历史映射补
    cat = overall['淘宝类目'].astype(str).str.strip()
    need = cat.eq('') | cat.str.lower().isin(['nan', 'none'])
    filled = cat.where(~need, overall['宝贝ID'].map(category_map).fillna(''))
    category_from_history = int((cat[need].eq('') | cat[need].str.lower().isin(['nan', 'none'])).sum()) - int(
        (filled[need].eq('') | filled[need].str.lower().isin(['nan', 'none'])).sum())
    overall['淘宝类目'] = filled

    scene = _scene_frames(wujie_frames)
    merged = overall.merge(scene, on='宝贝ID', how='left') if len(scene) else overall.copy()
    for col in ('_spend', '_gmv', '_cart', '_kw', '_rq', '_sc', '_kw_cart', '_rq_cart', '_sc_cart'):
        if col not in merged.columns:
            merged[col] = 0.0
        merged[col] = pd.to_numeric(merged[col], errors='coerce').fillna(0.0)

    out = pd.DataFrame(index=merged.index)
    out['统计日期'] = merged['统计日期']
    out['宝贝ID'] = merged['宝贝ID']
    out['商品名称'] = merged['商品名称']
    out['淘宝类目'] = merged['淘宝类目']
    for col in ('GMV', '商品访客数', '支付买家数', '支付件数', '商品加购件数',
                '商品支付转化率', '访客平均价值', '成功退款金额', '搜索引导访客数'):
        out[col] = merged[col]
    out['推广消耗金额'] = merged['_spend']
    out['推广引导总成交金额'] = merged['_gmv']
    out['推广总加购数'] = merged['_cart']
    out['关键词推广访客数'] = merged['_kw']
    out['人群推广访客数'] = merged['_rq']
    out['场景推广访客数'] = merged['_sc']
    # 渠道加购数：无界「总购物车数」即生意参谋「渠道加购数」（11-05 实证完全相等）
    out['关键词推广加购数'] = merged['_kw_cart']
    out['人群推广加购数'] = merged['_rq_cart']
    out['场景推广加购数'] = merged['_sc_cart']
    # 渠道加购人数：无界无「人数」口径，按 0 计（不臆造）
    for col in ('关键词推广加购人数', '人群推广加购人数', '场景推广加购人数'):
        out[col] = 0
    for col in FALLBACK_COLUMNS:
        if col not in out.columns:
            out[col] = ''
    out = out.loc[:, list(FALLBACK_COLUMNS)]

    total = len(out)
    with_cat = int(out['淘宝类目'].astype(str).str.strip().ne('').sum())
    gmv = pd.to_numeric(out['GMV'], errors='coerce').fillna(0.0)
    cat_ok = out['淘宝类目'].astype(str).str.strip().ne('')
    stats = {
        'ok': True,
        'rows': total,
        'category_covered': with_cat,
        'category_from_history': max(category_from_history, 0),
        'category_gmv_ratio': float(gmv[cat_ok].sum() / gmv.sum()) if gmv.sum() else 0.0,
        'spend': float(pd.to_numeric(out['推广消耗金额'], errors='coerce').fillna(0).sum()),
        'spend_missing': bool(pd.to_numeric(out['推广消耗金额'], errors='coerce').fillna(0).sum() == 0),
    }
    return out, stats


def describe_fallback(day, stats):
    """给界面用的一句人话提示。"""
    if not stats or not stats.get('ok'):
        return f"{day}：未启用兜底（{stats.get('reason') if stats else '无信息'}）"
    return (
        f"{day} 缺 `商品-经营投产比`，已用 `商品-整体` + 无界兜底："
        f"{stats['rows']:,} 个宝贝；类目覆盖 {stats['category_covered']:,} 个"
        f"（GMV 占比 {stats['category_gmv_ratio']*100:.1f}%，其中 {stats['category_from_history']:,} 个取自历史映射，"
        f"其余留空、不做标题推断）；渠道访客取无界点击量、渠道加购数取无界购物车数；"
        f"渠道加购人数无来源、按 0 计。"
    )
