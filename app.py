"""MetoX 半自动推广工具 — Streamlit 版。"""
from __future__ import annotations

import os, re, time, io, json, shutil, tempfile, hashlib, importlib, inspect
from pathlib import Path
from datetime import datetime, timedelta
from functools import partial

import pandas as pd
import streamlit as st

from core.loader import dedupe_source_files, read_prepared_source
from core.source_fallback import apply_fallback
from core.data_validation import (
    DataValidationError,
    DataValidationIssue,
    validate_paid_plan_consistency,
)
from core.daily_cache import (
    dataset_cache_key,
    load_daily_fact_cache,
    load_shared_dimension_cache,
    save_daily_fact_cache,
    save_shared_dimension_cache,
    source_fingerprint,
)
from core.merge import (
    build_daily_facts,
    build_shared_dimensions,
    merge_daily_facts,
    merge_sources,
)
from core.plan_config import format_budget_cell, load_plan_config, plan_config_caption
from core.periods import (
    CUSTOM_LABEL,
    MAX_PERIOD_DAYS,
    MONTH_LABEL,
    aggregate_category_metrics,
    aggregate_period_frame,
    apply_period_comparisons,
    apply_rolling_daily_metrics,
    build_period_ranges,
    iter_dates,
    month_bounds,
    resolve_month_range,
    validate_custom_range,
)
from core.segmentation import segment_products
from core.analysis import (
    CATEGORY_BASIC_COLUMNS, CATEGORY_INVESTMENT_COLUMNS,
    build_daily_kpi_trends, calc_kpis, calc_kpi_mom_percent, grade_distribution, channel_summary,
    segment_summary, category_analysis, refund_risk, channel_top, inventory_actions,
    build_operating_daily_report,
)
from core.acceptance import build_source_status
from core.budget_decision import (
    BUDGET_DECISION_MAIN_COLUMNS, build_budget_decision_context, is_removed_budget_column,
)
from core.v93_signal import parse_v93_report
from core.metox_algorithm import apply_metox_algorithm
from core.metric_labels import apply_metric_labels
from core.jst_stock import find_snapshot, load_snapshot_as_stock
from core.jst_refund import find_refund_snapshot, load_refund_rates
from core.exporter import export_execution_table, export_all_sheets, style_execution_table
from core.dataframe_clipboard import build_dataframe_copy_button_html
from core.version_control import find_repository, get_status, next_version_name, save_version
from core.version import APP_VERSION, DISPLAY_VERSION, PRODUCT_NAME
from core.schema import (
    ALLOWED_SOURCE_TYPES, SOURCE_PRODUCT_INFO, SOURCE_PRODUCT_ROI, SOURCE_STOCK,
    SOURCE_WUJIE_SUBJECT, SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE,
    classify_file, EXPORT_COLS, ACTION_RULES, compute_bounds,
    ensure_export_columns, resolve_display_columns,
)

st.set_page_config(page_title=f"{DISPLAY_VERSION} 半自动推广工具", layout="wide")
st.title(f"{DISPLAY_VERSION} 半自动推广工具")
GIT_PROJECT_NAME = PRODUCT_NAME
if not hasattr(st.column_config, "PercentColumn"):
    st.column_config.PercentColumn = partial(
        st.column_config.NumberColumn,
        format="percent",
    )

# ── 初始化 Session State ─────────────────────────────────
for key in ['df','prev_df','kpis','prev_kpis','kpi_mom','plan_ids','prev_plan_ids','cur_date','prev_date',
            'sources_info','traffic_bounds','value_bounds','traffic_ref','value_ref',
            'loaded','processing_msg','timings','total_time','period_logs','cat_ctx','kpi_trends',
            'new_product_ctx','new_product_load_state','new_product_history','new_product_progress',
            'budget_decision_ctx_cache','plan_config']:
    if key not in st.session_state:
        if key == 'loaded':
            st.session_state[key] = False
        else:
            st.session_state[key] = None


def _render_kpi_metric_with_trend(column, label, value, delta, *, trend_key,
                                  trend_label, scale=1.0):
    """渲染原生指标卡与已加载的日趋势，不触发文件读取或业务重算。"""
    with column:
        st.metric(label, value, delta)
        with st.popover(
            "日趋势",
            type="tertiary",
            icon=":material/show_chart:",
            width="content",
            key=f"kpi_trend_{trend_key}",
        ):
            trend_data = st.session_state.get('kpi_trends')
            if trend_data is None or trend_data.empty or trend_key not in trend_data.columns:
                st.info("暂无日趋势数据")
                return

            chart_data = trend_data[['日期', trend_key]].copy()
            chart_data['日期'] = pd.to_datetime(chart_data['日期'], errors='coerce')
            chart_data[trend_label] = pd.to_numeric(
                chart_data.pop(trend_key), errors='coerce'
            ) / scale
            chart_data = chart_data.dropna(subset=['日期'])
            if chart_data.empty or chart_data[trend_label].notna().sum() == 0:
                st.info("暂无日趋势数据")
                return

            start = chart_data['日期'].min().date().isoformat()
            end = chart_data['日期'].max().date().isoformat()
            missing_days = int(chart_data[trend_label].isna().sum())
            missing_note = f" · 缺失 {missing_days} 天（折线断开）" if missing_days else ""
            st.caption(f"{start} 至 {end} · 日维度{missing_note}")
            # X 轴按运营口径显示年月日（避免图表自动格式化成 “Sep 11” 这类）
            chart_data['日期'] = chart_data['日期'].dt.strftime('%Y-%m-%d')
            st.line_chart(
                chart_data,
                x='日期',
                y=trend_label,
                x_label='日期',
                y_label=trend_label,
                width='stretch',
                height=240,
            )


def _parse_bounds(s):
    if not s:
        return None
    parts = [float(x.strip()) for x in re.split(r'[,，\s]+', s.strip()) if x.strip()]
    if len(parts) == 5 and all(parts[i] < parts[i + 1] for i in range(4)):
        return parts
    return None


def _as_dataframe(value):
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if value is None:
        return pd.DataFrame()
    return pd.DataFrame(value)


def _option_values(frame, column):
    if column not in frame.columns:
        return []
    return sorted({
        str(value).strip() for value in frame[column].dropna()
        if str(value).strip() not in ('', 'nan', 'None')
    })


def _as_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def _format_money(value):
    return f"¥{_as_float(value) / 1e4:.2f}万"


# ── 加载数据 ──────────────────────────────────────────────
def _log_time(timings, label, start, extra=""):
    """记录并打印单步耗时"""
    elapsed = time.perf_counter() - start
    msg = f"[耗时] {label}：{elapsed:.2f} 秒"
    if extra: msg += f" | {extra}"
    print(msg)
    timings.append((label, f"{elapsed:.2f} 秒", extra))
    return elapsed

#: v2.60 同源类型多份文件（zip 与 xlsx 并存）被择一后丢弃的条目，供界面提示
_dropped_multi_source_files = []

#: v2.61 数据源覆盖/兜底提示（每次加载按日期重建）
_SOURCE_COMPAT_NOTES = {'day': '', 'notes': []}


def _source_compat_note(text: str, day: str = '') -> None:
    """记录一条覆盖/兜底提示（同文去重，按加载日期重建）。"""
    if day and _SOURCE_COMPAT_NOTES.get('day') != day:
        _SOURCE_COMPAT_NOTES['day'] = day
        _SOURCE_COMPAT_NOTES['notes'] = []
    notes = _SOURCE_COMPAT_NOTES.setdefault('notes', [])
    if text and text not in notes:
        notes.append(text)


def _render_source_compat_banner() -> None:
    """数据源覆盖提示条：缺日 / 兜底 / 同源择一。"""
    notes = list(_SOURCE_COMPAT_NOTES.get('notes') or [])
    dropped = list(_dropped_multi_source_files or [])
    if not notes and not dropped:
        return
    lines = list(notes)
    if dropped:
        pairs = ', '.join(f"{item.get('name')}" for item in dropped[:4])
        lines.append(f"同日同源文件重复，已只取 zip：{pairs}"
                     + ("…" if len(dropped) > 4 else ""))
    st.warning("⚠️ 数据源提示\n\n" + "\n\n".join(f"- {line}" for line in lines))


def _scan_target_files(path, source_types=None):
    files = []
    allowed = PRIMARY_LOAD_SOURCE_TYPES if source_types is None else set(source_types)
    if not path or not os.path.isdir(str(path)):
        return files
    for fn in sorted(os.listdir(str(path))):
        fp = os.path.join(str(path), fn)
        if not os.path.isfile(fp) or fn.startswith('~$'):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ('.xlsx', '.xls', '.csv', '.zip'):   # v2.60：无界报表 zip
            continue
        src_type = classify_file(fn)
        if src_type in allowed:
            files.append((fp, fn, src_type))
    # v2.60：同日同源类型可能 zip 与 xlsx 并存（同一批数据），必须先择一再读，否则花费翻倍
    entries = [{'path': fp, 'name': fn, 'source': src} for fp, fn, src in files]
    kept, dropped = dedupe_source_files(entries)
    if dropped:
        _dropped_multi_source_files[:] = dropped
    return [(e['path'], e['name'], e['source']) for e in kept]


def _period_cache_key(base_root, year, shop, start_date, end_date, label):
    raw = f"{base_root}|{year}|{shop}|{start_date}|{end_date}|{label}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _copy_file_to_cache(fp, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = hashlib.md5(str(fp).encode()).hexdigest()[:12] + '_' + os.path.basename(fp)
    cache_path = cache_dir / cache_name
    try:
        if cache_path.exists() and cache_path.stat().st_mtime >= os.path.getmtime(fp):
            return str(cache_path), False
        shutil.copy2(fp, cache_path)
        return str(cache_path), True
    except Exception:
        return fp, False


PRIMARY_LOAD_SOURCE_TYPES = {
    SOURCE_PRODUCT_ROI,
    SOURCE_WUJIE_SUBJECT,
    SOURCE_STOCK,
    SOURCE_PRODUCT_INFO,
}
NEW_PRODUCT_SOURCE_TYPES = [SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE]
NEW_PRODUCT_HISTORY_DAYS = 90
NEW_PRODUCT_CREATIVE_HISTORY_DAYS = 45
NEW_PRODUCT_BUSINESS_HISTORY_DAYS = 45
NEW_PRODUCT_LIFECYCLE_COLUMNS = [
    '款式主图', '图片', '货号', '关联宝贝ID', '淘宝类目', 'ERP建档时间',
    '首次在线发现日', '新品年龄',
    'D1_D30_商品访客数', 'D1_D30_商品加购件数', 'D1_D30_GMV', 'D1_D30_商品加购率',
    '日均访客', '日均GMV',
    '判定轮次', 'D15结果', 'D30结果', '当前结果',
    '动作可执行', '库存提示', '保护状态', '保护证据', '流量验证', '本轮成交',
    '判定原因', '下架阻断原因', '样本充分性',
    '保护因素', '风险因素', '预测日期',
]
NEW_PRODUCT_OFFICIAL_RESULTS = {'下架', '继续观察'}
NEW_PRODUCT_LIFECYCLE_HIDDEN_COLUMNS = {
    '商品名称', '判定结果', 'left_censored',
    'D14商品状态', 'D14在仓库存',
    'D15商品状态', 'D15在仓库存',
    'D21商品状态', 'D21在仓库存',
    'D14原因', 'D21原因', 'D28原因',
    'D15原因', 'D30原因',
    'D14样本充分性', 'D21样本充分性', 'D28样本充分性',
    'D15样本充分性', 'D30样本充分性',
    'D14保护状态', 'D21保护状态', 'D28保护状态',
    'D15保护状态', 'D30保护状态',
    'D14流量验证', 'D21流量验证', 'D28流量验证',
    'D15流量验证', 'D30流量验证',
    'D14本轮成交', 'D21本轮成交', 'D28本轮成交',
    'D15本轮成交', 'D30本轮成交',
    'D14保护因素', 'D21保护因素', 'D28保护因素',
    'D15保护因素', 'D30保护因素',
    'D14风险因素', 'D21风险因素', 'D28风险因素',
    'D15风险因素', 'D30风险因素',
    'D14动作可执行', 'D21动作可执行', 'D28动作可执行',
    'D15动作可执行', 'D30动作可执行',
    'D14库存提示', 'D21库存提示', 'D28库存提示',
    'D15库存提示', 'D30库存提示',
    'D14保护证据', 'D21保护证据', 'D28保护证据',
    'D15保护证据', 'D30保护证据',
    'D14下架阻断原因', 'D21下架阻断原因', 'D28下架阻断原因',
    'D15下架阻断原因', 'D30下架阻断原因',
    'D14结果', 'D21结果', 'D28结果',
}
NEW_PRODUCT_MATERIAL_COLUMNS = [
    '宝贝ID', '货号', '淘宝类目', '主图整体点击率', '主图CTR是否有效',
    '计划ID', '计划名称', '主体ID', '主体名称', '主体类型',
    '创意ID', '创意名称', '创意首次发现日', '创意年龄', '创意展现量', '创意点击量',
    '无界创意CTR', '上一7天CTR', 'CTR环比', '创意展现占比', '是否主投创意',
]
NEW_PRODUCT_MATERIAL_HIDDEN_COLUMNS = {'商品名称'}
NEW_PRODUCT_BACKTEST_COLUMNS = [
    '判定节点', '规则版本', '样本数', '下架建议数', '下架命中率', '误杀率',
    '召回率', '下架覆盖率', '机会损失GMV', '机会损失预估利润',
    '不可验证样本数', 'CTR增益', 'MetoX保护有效率', '分组',
]


def _new_product_methodology_frame(status_note=''):
    rows = [
        ('生命周期起点', '首次在线发现日', '同一货号下所有宝贝ID首次在商品-整体日报中当前在线的日期作为D1。'),
        ('判定周期', 'D15至D45每日更新', 'D15起每天重算当前结果，最长观察到D45；官方判定结果只输出下架或继续观察。'),
        ('动销保护', 'D1至分析日累计成交', 'D45内支付买家数、支付件数、GMV任一大于0即继续观察并标记动销保护。'),
        ('链接/库存现状', '仅作运营信息', '链接状态展示为动作可执行，库存展示为库存提示；两者不参与下架判定，库存问题由运营另行处理。'),
        ('流量验证', 'D1至分析日累计访客数', '达到D15后每日更新：累计商品访客数>=300为已验证，否则为待流量验证；访客数正好300属于已验证。'),
        ('D45兜底', '无成交且累计访客数<300', 'D44及以前继续等待流量；到D45仍未成交且累计访客数<300时直接判定下架。'),
        ('无成交评估', '已验证后看加购承接', 'D45前累计无成交且流量已验证时，继续按既有有效数据天数、同层样本和加购表现判断。'),
        ('比率公式', '累计分子÷累计分母', '商品加购率、无界创意CTR等展示指标按窗口累计分子除以累计分母重算。'),
        ('主图整体点击率', '平台原值', '当前源没有主图点击量/曝光量，MVP只展示，不聚合、不参与A-D判定；待获得点击量/曝光量后再纳入验证。'),
        ('MetoX', '影子验证', '正向标签作为保护候选；负向标签只作旁证，不能单独触发下架。'),
    ]
    if status_note:
        rows.insert(0, ('状态', '新品上下文', status_note))
    return pd.DataFrame(rows, columns=['主题', '口径', '说明'])


def _normalize_new_product_result(value):
    text = '' if value is None or pd.isna(value) else str(value).strip()
    return text if text in NEW_PRODUCT_OFFICIAL_RESULTS else '继续观察'


def _normalize_new_product_result_series(series, index):
    if series is None:
        series = pd.Series('', index=index, dtype=object)
    return series.reindex(index).map(_normalize_new_product_result)


def _normalize_new_product_protection_status(value):
    text = '' if value is None or pd.isna(value) else str(value).strip()
    return '动销保护' if text == '动销保护' else '无保护'


def _new_product_lifecycle_display_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    obsolete_period_cols = [
        col for col in frame.columns
        if str(col).startswith((
            'D1_D14_', 'D15_D21_', 'D15_D28_', 'D1_D21_', 'D1_D28_'
        ))
    ]
    out = frame.drop(
        columns=list(NEW_PRODUCT_LIFECYCLE_HIDDEN_COLUMNS) + obsolete_period_cols,
        errors='ignore',
    ).copy()
    if '当前结果' not in out.columns and '判定结果' in frame.columns:
        out['当前结果'] = frame['判定结果']
    for column in ('D15结果', 'D30结果', '当前结果'):
        if column in out.columns:
            out[column] = out[column].map(_normalize_new_product_result)
    if '保护状态' not in out.columns:
        factors = out.get('保护因素', pd.Series('', index=out.index, dtype=object))
        out['保护状态'] = factors.fillna('').astype(str).str.strip().map(
            lambda text: '动销保护' if text else '无保护'
        )
    else:
        out['保护状态'] = out['保护状态'].map(_normalize_new_product_protection_status)
    preferred = [col for col in NEW_PRODUCT_LIFECYCLE_COLUMNS if col in out.columns]
    remaining = [
        col for col in out.columns
        if col not in preferred
        and col not in NEW_PRODUCT_LIFECYCLE_HIDDEN_COLUMNS
        and not str(col).startswith('_')
    ]
    return out[preferred + remaining]


def _new_product_material_display_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    out = frame.drop(columns=list(NEW_PRODUCT_MATERIAL_HIDDEN_COLUMNS), errors='ignore').copy()
    preferred = [col for col in NEW_PRODUCT_MATERIAL_COLUMNS if col in out.columns]
    remaining = [
        col for col in out.columns
        if col not in preferred
        and col not in NEW_PRODUCT_MATERIAL_HIDDEN_COLUMNS
        and not str(col).startswith('_')
    ]
    return out[preferred + remaining]


def _empty_new_product_context(message='新品生命周期模块未生成结果', status='unavailable'):
    return {
        'status': status,
        'message': message,
        'lifecycle': pd.DataFrame(columns=NEW_PRODUCT_LIFECYCLE_COLUMNS),
        'materials': pd.DataFrame(columns=NEW_PRODUCT_MATERIAL_COLUMNS),
        'backtest': pd.DataFrame(columns=NEW_PRODUCT_BACKTEST_COLUMNS),
        'methodology': _new_product_methodology_frame(message),
        'source_status': pd.DataFrame(columns=['数据源', '状态', '文件数']),
        'coverage': pd.DataFrame(columns=['数据源', '状态', '文件数']),
    }


NEW_PRODUCT_UNLOADED_MESSAGE = '新品数据尚未加载，不影响推广执行表和其他分析。'


def _new_product_unloaded_context():
    return _empty_new_product_context(NEW_PRODUCT_UNLOADED_MESSAGE, status='unloaded')


def _new_product_progress(progress_callback, value, text):
    if not callable(progress_callback):
        return
    try:
        progress_value = max(0.0, min(1.0, float(value)))
    except Exception:
        progress_value = 0.0
    progress_callback(progress_value, text)


def _build_new_product_load_state(
    *,
    base_root,
    year,
    shop,
    date_str,
    cache_root,
    product_folder,
    files_info,
    cat_ctx,
    sources,
    day_map,
    shared_dimensions,
):
    return {
        'base_root': str(base_root or ''),
        'year': str(year or ''),
        'shop': str(shop or ''),
        'date_str': str(date_str or ''),
        'cache_root': str(cache_root or ''),
        'product_folder': str(product_folder) if product_folder is not None else None,
        'files_info': list(files_info or []),
        'cat_ctx': dict(cat_ctx or {}),
        'sources': dict(sources or {}),
        'day_map': dict(day_map or {}),
        'shared_dimensions': shared_dimensions,
    }


def _load_new_product_context_from_state(df, load_state, progress_callback=None):
    if not isinstance(load_state, dict):
        return None, _empty_new_product_context('请先加载推广数据，再加载新品历史。', status='error')

    base_root = load_state.get('base_root') or ''
    year = load_state.get('year') or ''
    shop = load_state.get('shop') or ''
    date_str = load_state.get('date_str') or ''
    cache_root = load_state.get('cache_root') or ''
    if not (base_root and year and shop and date_str and cache_root):
        return None, _empty_new_product_context('新品历史加载参数不完整，请重新加载推广数据。', status='error')

    _new_product_progress(progress_callback, 0.02, '准备加载新品历史...')
    day_map = load_state.get('day_map') or {}
    history = _load_new_product_history(
        base_root,
        year,
        shop,
        date_str,
        Path(cache_root),
        history_days=NEW_PRODUCT_HISTORY_DAYS,
        product_folder=load_state.get('product_folder'),
        shared_dimensions=load_state.get('shared_dimensions'),
        existing_day_map=day_map,
        progress_callback=progress_callback,
    )
    _new_product_progress(progress_callback, 0.94, '正在生成新品生命周期上下文...')
    context = _build_new_product_context(
        df,
        day_map,
        date_str,
        files_info=load_state.get('files_info') or [],
        base_root=base_root,
        year=year,
        shop=shop,
        product_folder=load_state.get('product_folder'),
        cache_root=Path(cache_root),
        cat_ctx=load_state.get('cat_ctx') or {},
        sources=load_state.get('sources') or {},
        new_product_history=history,
    )
    _new_product_progress(progress_callback, 1.0, '新品历史加载完成')
    return history, context


def _call_with_supported_kwargs(func, **kwargs):
    signature = inspect.signature(func)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return func(**kwargs)
    supported = {name: value for name, value in kwargs.items() if name in signature.parameters}
    return func(**supported)


def _call_export_execution_table(df, **kwargs):
    return _call_with_supported_kwargs(export_execution_table, df=df, **kwargs)


def _call_export_all_sheets(df, **kwargs):
    return _call_with_supported_kwargs(export_all_sheets, df=df, **kwargs)


def _call_export_all_sheets_with_budget_context(df, **kwargs):
    """Build the filtered budget context only when the download is requested.

    与看板保持一致：导出同样打开决策角色层（roles=True），
    否则导出的商品预算表会缺角色/闸门列。
    """
    plan_config = kwargs.pop('plan_config', None) or {}
    context = build_budget_decision_context(df, roles=True, plan_config=plan_config) or {}
    return _call_export_all_sheets(
        df,
        budget_decision_context=context,
        **kwargs,
    )


def _coerce_new_product_context(result, default_message='新品生命周期模块未返回结果'):
    if result is None:
        return _empty_new_product_context(default_message)
    if isinstance(result, pd.DataFrame):
        ctx = _empty_new_product_context('', status='partial')
        ctx['lifecycle'] = _new_product_lifecycle_display_frame(result)
        return ctx
    if isinstance(result, (list, tuple)):
        ctx = _empty_new_product_context('', status='partial')
        if len(result) > 0: ctx['lifecycle'] = _new_product_lifecycle_display_frame(result[0])
        if len(result) > 1: ctx['materials'] = _new_product_material_display_frame(result[1])
        if len(result) > 2: ctx['backtest'] = result[2]
        return ctx
    if isinstance(result, dict):
        ctx = dict(result)
        ctx.setdefault('status', 'complete')
        ctx.setdefault('message', '')
        ctx.setdefault('lifecycle', pd.DataFrame(columns=NEW_PRODUCT_LIFECYCLE_COLUMNS))
        ctx.setdefault('materials', pd.DataFrame(columns=NEW_PRODUCT_MATERIAL_COLUMNS))
        ctx.setdefault('backtest', pd.DataFrame(columns=NEW_PRODUCT_BACKTEST_COLUMNS))
        ctx.setdefault('methodology', _new_product_methodology_frame(ctx.get('message', '')))
        ctx['lifecycle'] = _new_product_lifecycle_display_frame(ctx.get('lifecycle'))
        ctx['materials'] = _new_product_material_display_frame(ctx.get('materials'))
        return ctx
    return _empty_new_product_context(default_message)


def _build_new_product_context(df, day_map, date_str, *, files_info=None, base_root=None,
                               year=None, shop=None, product_folder=None, cache_root=None,
                               cat_ctx=None, sources=None, new_product_history=None):
    builders = [
        ('core.new_product_lifecycle', [
            'build_new_product_view_context',
            'build_new_product_context',
            'build_new_product_view',
            'build_new_product_mvp',
        ]),
    ]
    kwargs = {
        'df': df,
        'current_df': df,
        'day_map': day_map,
        'date_str': date_str,
        'analysis_date': date_str,
        'files_info': files_info or [],
        'base_root': base_root,
        'year': year,
        'shop': shop,
        'product_folder': product_folder,
        'cache_root': cache_root,
        'cat_ctx': cat_ctx or {},
        'sources': sources or {},
        'current_sources': sources or {},
        'new_product_history': new_product_history or {},
        'product_overall_daily': (new_product_history or {}).get('product_overall_daily', {}),
        'creative_daily': (new_product_history or {}).get('creative_daily', {}),
        'business_daily_facts': day_map,
    }
    for module_name, function_names in builders:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for function_name in function_names:
            builder = getattr(module, function_name, None)
            if builder is None:
                continue
            try:
                return _coerce_new_product_context(
                    _call_with_supported_kwargs(builder, **kwargs)
                )
            except Exception as exc:
                return _empty_new_product_context(
                    f'新品视图生成失败：{str(exc)[:160]}',
                    status='error',
                )
    if new_product_history is not None:
        try:
            return _build_new_product_context_from_sources(
                df, day_map, date_str, new_product_history
            )
        except Exception as exc:
            return _empty_new_product_context(
                f'新品视图本地适配失败：{str(exc)[:160]}',
                status='error',
            )
    return _empty_new_product_context('新品生命周期模块未接入；当前仅显示口径说明和空导出模板。')


def _new_product_context_frame(context, keys, columns):
    if not isinstance(context, dict):
        return pd.DataFrame(columns=columns)
    for key in keys:
        value = context.get(key)
        if value is None:
            continue
        frame = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
        if columns == NEW_PRODUCT_LIFECYCLE_COLUMNS:
            frame = _new_product_lifecycle_display_frame(frame)
        elif columns == NEW_PRODUCT_MATERIAL_COLUMNS:
            frame = _new_product_material_display_frame(frame)
        preferred = [column for column in columns if column in frame.columns]
        hidden = set(NEW_PRODUCT_LIFECYCLE_HIDDEN_COLUMNS)
        if columns == NEW_PRODUCT_MATERIAL_COLUMNS:
            hidden |= NEW_PRODUCT_MATERIAL_HIDDEN_COLUMNS
        remaining = [
            column for column in frame.columns
            if column not in preferred and column not in hidden
        ]
        return frame[preferred + remaining]
    return pd.DataFrame(columns=columns)


def _frame_to_csv_bytes(frame):
    return frame.to_csv(index=False).encode('utf-8-sig')


def _filter_new_product_context(context, execution_frame):
    if not isinstance(context, dict) or execution_frame is None or execution_frame.empty:
        return context
    sku_set = set()
    pid_set = set()
    splitter = re.compile(r'[,，、;；|\s/]+')

    def _clean_sku(value):
        text = str(value).strip() if value is not None and not pd.isna(value) else ''
        return '' if text in ('', 'nan', 'None') else text

    def _split_pid_list(value):
        text = str(value or '').strip()
        if not text:
            return set()
        return {
            _id_key(part) for part in splitter.split(text)
            if _id_key(part)
        }

    if '货号' in execution_frame.columns:
        sku_set = {
            _clean_sku(value) for value in execution_frame['货号'].dropna()
            if _clean_sku(value)
        }
    if '宝贝ID' in execution_frame.columns:
        pid_set = {
            _id_key(value) for value in execution_frame['宝贝ID'].dropna()
            if _id_key(value)
        }

    def _filtered(frame):
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return frame
        mask = pd.Series(False, index=frame.index)
        has_filter = False
        if sku_set and '货号' in frame.columns:
            mask |= frame['货号'].map(_clean_sku).isin(sku_set)
            has_filter = True
        if pid_set and '宝贝ID' in frame.columns:
            mask |= frame['宝贝ID'].map(_id_key).isin(pid_set)
            has_filter = True
        if pid_set and '关联宝贝ID' in frame.columns:
            mask |= frame['关联宝贝ID'].apply(
                lambda value: bool(_split_pid_list(value) & pid_set)
            )
            has_filter = True
        return frame[mask].copy() if has_filter else frame

    filtered = dict(context)
    for key in ('lifecycle', 'decision', 'decisions', 'materials', 'material', 'creative'):
        if isinstance(filtered.get(key), pd.DataFrame):
            filtered[key] = _filtered(filtered[key])
            if key in ('lifecycle', 'decision', 'decisions'):
                filtered[key] = _new_product_lifecycle_display_frame(filtered[key])
            elif key in ('materials', 'material', 'creative'):
                filtered[key] = _new_product_material_display_frame(filtered[key])
    return filtered


# ── 日级文件读取缓存（重复读取同一文件时直接复用）──
@st.cache_data(show_spinner=False)
def _load_day_raw_cached(fp, src_type, parsed_cache_dir=None, _mtime=None):
    """缓存单文件读取+标准化结果。_mtime 参与缓存键:源文件更新后旧缓存自动失效
    (此前仅按路径缓存,同名文件改内容后本进程内会拿到旧数据)。"""
    return read_prepared_source(fp, src_type, parsed_cache_dir=parsed_cache_dir)


def _read_sources_for_date(
    biz_folder, wujie_folder, product_folder, cache_dir, period_label,
    include_product=True, file_list=None,
):
    """读取一天的源文件（使用文件级缓存，不重复解析）"""
    sources = {}
    files_info = []
    copied = 0
    if file_list is None:
        file_list = _scan_target_files(biz_folder) + _scan_target_files(wujie_folder)
        if include_product:
            file_list += _scan_target_files(product_folder)
    else:
        file_list = list(file_list)

    for fp, fn, src_type in file_list:
        local_fp, did_copy = _copy_file_to_cache(fp, cache_dir)
        copied += 1 if did_copy else 0
        try:
            df = _load_day_raw_cached(local_fp, src_type, str(Path(cache_dir) / '_parsed'),
                                      _mtime=os.path.getmtime(local_fp))
            sources.setdefault(src_type, []).append((fp, df))
            files_info.append({
                'name': fn, 'source': src_type, 'rows': len(df),
                'cols': len(df.columns), 'period': period_label,
            })
        except DataValidationError:
            raise
        except Exception as e:
            raise DataValidationError(DataValidationIssue(
                f'{src_type}文件 {fp}',
                f'读取失败，已停止处理：{str(e)[:160]}',
            )) from e

    # ── v2.61 数据源兜底：某日缺「商品-经营投产比」时，用同日「商品-整体」+ 无界 + 历史类目
    #    构造列结构完全相同的替代帧（不新增任何业务字段），并把来源与缺口写进提示。
    #    v2.64：抽到 core.source_fallback.apply_fallback，且自己按商品目录找「商品-整体」
    #    （主流程传 shared_dimensions 时 include_day_product=False，商品目录不扫描）。
    # 注意：商品-整体 / 经营投产比 放在 `商品/<日期>` 目录（= biz_folder），不是侧边栏『商品目录』根
    _fb_injected, _fb_note, _fb_frame = apply_fallback(sources, biz_folder, period_label)
    if _fb_injected and _fb_frame is not None:
        files_info.append({
            'name': '（兜底）商品-整体 + 无界', 'source': SOURCE_PRODUCT_ROI,
            'rows': len(_fb_frame), 'cols': len(_fb_frame.columns), 'period': period_label,
        })
    if _fb_note:
        _source_compat_note(_fb_note, str(period_label).split(':')[-1])

    return sources, files_info, copied, len(file_list)


def _new_product_history_dates(date_str, history_days=NEW_PRODUCT_HISTORY_DAYS):
    end = pd.to_datetime(date_str, errors='coerce')
    if pd.isna(end):
        return []
    start = end - pd.Timedelta(days=max(int(history_days), 1) - 1)
    return [
        day.date().isoformat()
        for day in pd.date_range(start.normalize(), end.normalize(), freq='D')
    ]


def _scan_new_product_source_files(biz_folder, wujie_folder, include_creative=True):
    files = _scan_target_files(biz_folder, source_types={SOURCE_PRODUCT_OVERALL})
    if include_creative:
        files += _scan_target_files(wujie_folder, source_types={SOURCE_WUJIE_CREATIVE})
    return files


def _append_daily_frame(target, day, frame):
    frame = frame.copy()
    frame['_数据日期'] = day
    if '统计日期' not in frame.columns:
        frame['统计日期'] = day
    if day in target:
        target[day] = pd.concat([target[day], frame], ignore_index=True)
    else:
        target[day] = frame


def _build_new_product_source_status(product_daily, creative_daily, files_info, errors, dates):
    expected_days = max(len(dates), 1)
    rows = []
    error_by_source = {}
    for error in errors:
        error_by_source.setdefault(error.get('source', ''), []).append(error.get('message', ''))
    for source, daily in (
        (SOURCE_PRODUCT_OVERALL, product_daily),
        (SOURCE_WUJIE_CREATIVE, creative_daily),
    ):
        file_count = sum(1 for item in files_info if item.get('source') == source)
        covered_days = len(daily)
        source_errors = error_by_source.get(source, [])
        if file_count <= 0:
            status = '未提供'
        elif source_errors:
            status = '部分可用'
        else:
            status = '已提供'
        rows.append({
            '数据源': source,
            '状态': status,
            '文件数': file_count,
            '覆盖天数': covered_days,
            '期望天数': expected_days,
            '覆盖率': f'{covered_days}/{expected_days}',
            '备注': '；'.join(source_errors[:3]),
        })
    return pd.DataFrame(rows)


def _load_new_product_business_history(
    base_root, year, shop, product_folder, date_str, cache_root,
    history_days=NEW_PRODUCT_BUSINESS_HISTORY_DAYS, shared_dimensions=None,
    existing_day_map=None, progress_callback=None,
):
    if product_folder is None:
        return {}, [], []
    dates = _new_product_history_dates(date_str, history_days=history_days)
    day_map = {}
    files_info = []
    errors = []
    cache_stats = {'hits': 0, 'misses': 0}
    total_days = max(len(dates), 1)
    for idx, day in enumerate(dates, start=1):
        reused = False
        try:
            entry = (existing_day_map or {}).get(day)
            if entry is None:
                entry = _load_single_day(
                    base_root, day, year, shop, product_folder, cache_root,
                    False, '新品经营历史',
                    shared_dimensions=shared_dimensions,
                    cache_stats=cache_stats,
                )
            else:
                reused = True
        except Exception as exc:
            errors.append({'date': day, 'source': '经营日事实', 'message': str(exc)[:160]})
            _new_product_progress(
                progress_callback,
                idx / total_days,
                f"经营日事实读取失败 {idx}/{len(dates)} 天：{day}",
            )
            continue
        action = '复用已加载经营日事实' if reused else '补读经营日事实'
        _new_product_progress(
            progress_callback,
            idx / total_days,
            f"正在{action} {idx}/{len(dates)} 天：{day}",
        )
        if entry is None or len(entry) == 0 or entry[0] is None:
            continue
        day_map[day] = entry
        files_info.extend(entry[1] if len(entry) > 1 and entry[1] else [])
    return day_map, files_info, errors


def _load_new_product_history(base_root, year, shop, date_str, cache_root,
                              history_days=NEW_PRODUCT_HISTORY_DAYS,
                              product_folder=None, shared_dimensions=None,
                              existing_day_map=None, progress_callback=None):
    dates = _new_product_history_dates(date_str, history_days=history_days)
    creative_dates = set(_new_product_history_dates(
        date_str, history_days=NEW_PRODUCT_CREATIVE_HISTORY_DAYS
    ))
    product_daily = {}
    creative_daily = {}
    files_info = []
    errors = []
    cache_dir = Path(cache_root) / '_new_product_sources'
    parsed_cache_dir = cache_dir / '_parsed'

    total_days = max(len(dates), 1)
    for idx, day in enumerate(dates, start=1):
        if day in creative_dates:
            progress_text = f"正在读取首次发现与创意历史 {idx}/{len(dates)} 天：{day}"
        else:
            progress_text = f"正在读取首次发现历史 {idx}/{len(dates)} 天：{day}"
        _new_product_progress(
            progress_callback,
            0.05 + 0.65 * ((idx - 1) / total_days),
            progress_text,
        )
        biz_folder, wujie_folder = _build_paths(base_root, day, year, shop)
        for fp, fn, src_type in _scan_new_product_source_files(
            biz_folder, wujie_folder, include_creative=day in creative_dates
        ):
            try:
                local_fp, _did_copy = _copy_file_to_cache(fp, cache_dir / day)
                frame = _load_day_raw_cached(
                    local_fp,
                    src_type,
                    str(parsed_cache_dir),
                    _mtime=os.path.getmtime(local_fp),
                )
                if src_type == SOURCE_PRODUCT_OVERALL:
                    _append_daily_frame(product_daily, day, frame)
                elif src_type == SOURCE_WUJIE_CREATIVE:
                    _append_daily_frame(creative_daily, day, frame)
                files_info.append({
                    'name': fn,
                    'source': src_type,
                    'rows': len(frame),
                    'cols': len(frame.columns),
                    'period': f'新品历史:{day}',
                })
            except Exception as exc:
                errors.append({
                    'date': day,
                    'source': src_type,
                    'file': fn,
                    'message': str(exc)[:160],
                })

    _new_product_progress(progress_callback, 0.72, '正在整理新品源覆盖...')

    def business_progress(value, text):
        _new_product_progress(progress_callback, 0.75 + 0.15 * value, text)

    business_day_map, business_files_info, business_errors = _load_new_product_business_history(
        base_root, year, shop, product_folder, date_str, cache_root,
        history_days=NEW_PRODUCT_BUSINESS_HISTORY_DAYS,
        shared_dimensions=shared_dimensions,
        existing_day_map=existing_day_map,
        progress_callback=business_progress,
    )
    _new_product_progress(progress_callback, 0.91, '正在生成新品源覆盖摘要...')
    source_status = _build_new_product_source_status(
        product_daily, creative_daily, files_info, errors, dates
    )
    missing_sources = source_status[source_status['文件数'].eq(0)]['数据源'].tolist()
    status = 'complete'
    if missing_sources:
        status = 'missing'
    elif errors:
        status = 'partial'
    message = ''
    if missing_sources:
        message = f"缺少新品源：{', '.join(missing_sources)}。新品Tab仅显示可用部分，旧9个Tab不受影响。"
    elif errors:
        message = f"新品源有 {len(errors)} 个文件读取失败，已隔离到新品Tab。"
    if not business_day_map:
        note = '新品经营日历史未覆盖，生命周期判定和回放可能不完整。'
        message = (message + '；' if message else '') + note
    elif business_errors:
        note = f"新品经营日历史有 {len(business_errors)} 天读取失败。"
        message = (message + '；' if message else '') + note
    actual_history_start = min(product_daily.keys()) if product_daily else ''
    requested_history_start = dates[0] if dates else ''
    return {
        'status': status,
        'message': message,
        'history_start_date': actual_history_start,
        'requested_history_start_date': requested_history_start,
        'history_end_date': dates[-1] if dates else date_str,
        'first_seen_index_path': str(_first_seen_index_path(cache_root, year, shop)),
        'new_product_snapshot_path': str(_new_product_snapshot_path(cache_root, year, shop)),
        'product_overall_daily': product_daily,
        'creative_daily': creative_daily,
        'business_day_map': business_day_map,
        'business_files_info': business_files_info,
        'business_errors': business_errors,
        'files_info': files_info,
        'errors': errors,
        'source_status': source_status,
        'coverage': source_status,
    }


def _safe_cache_token(value):
    text = str(value or '').strip()
    text = re.sub(r'[^\w\u4e00-\u9fff.-]+', '_', text)
    return text or 'default'


def _first_seen_index_path(cache_root, year, shop):
    cache_base = Path(cache_root)
    return cache_base / f"new_product_first_seen_{_safe_cache_token(year)}_{_safe_cache_token(shop)}.pkl"


def _new_product_snapshot_path(cache_root, year, shop):
    """新品变化区快照（v2.54）：与首次发现索引同目录，按 年|店 分键。"""
    from core.new_product_change import snapshot_path

    return snapshot_path(Path(cache_root), year, shop)


def _load_first_seen_index(path):
    if not path:
        return None
    try:
        path = Path(path)
        if not path.exists():
            return None
        return pd.read_pickle(path)
    except Exception:
        return None


def _save_first_seen_index(frame, path):
    if frame is None or not path:
        return ''
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        frame.to_pickle(tmp)
        os.replace(tmp, path)
        return ''
    except Exception as exc:
        return f'首次发现索引持久化失败：{str(exc)[:120]}'


def _id_key(value):
    text = '' if value is None or pd.isna(value) else str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return '' if text.lower() in ('nan', 'none', 'null') else text


def _build_product_dimension_lookup(df, day_map):
    frames = []
    dimension_cols = [
        '宝贝ID', '货号', '商品名称', '淘宝类目', '店铺', '款式主图', '创建时间',
        '在仓库存', '件单价', '成本价（含税）', '价格段',
    ]
    for source in [df] + [
        entry[0] for entry in (day_map or {}).values()
        if entry is not None and len(entry) > 0 and isinstance(entry[0], pd.DataFrame)
    ]:
        if source is None or source.empty or '宝贝ID' not in source.columns:
            continue
        cols = [col for col in dimension_cols if col in source.columns]
        part = source[cols].copy()
        part['_pid_key'] = part['宝贝ID'].map(_id_key)
        part = part[part['_pid_key'] != '']
        if not part.empty:
            frames.append(part)
    if not frames:
        return pd.DataFrame(columns=['_pid_key'])
    lookup = pd.concat(frames, ignore_index=True)
    return lookup.drop_duplicates('_pid_key', keep='last')


def _merge_pid_dimensions(frame, dimensions):
    if frame is None or frame.empty or '宝贝ID' not in frame.columns or dimensions is None or dimensions.empty:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    work = frame.copy()
    work['_pid_key'] = work['宝贝ID'].map(_id_key)
    dim_cols = [col for col in dimensions.columns if col not in work.columns or col == '_pid_key']
    merged = work.merge(dimensions[dim_cols], on='_pid_key', how='left')
    return merged.drop(columns=['_pid_key'], errors='ignore')


def _enrich_product_overall_daily(product_daily, dimensions):
    enriched = {}
    for day, frame in (product_daily or {}).items():
        enriched[day] = _merge_pid_dimensions(frame, dimensions)
        if '_数据日期' not in enriched[day].columns:
            enriched[day]['_数据日期'] = day
    return enriched


def _daily_business_facts_with_new_product_status(day_map, product_daily):
    frames = []
    status_cols = ['宝贝ID', '商品状态', '主图整体点击率']
    for day, entry in (day_map or {}).items():
        if entry is None or len(entry) == 0 or not isinstance(entry[0], pd.DataFrame):
            continue
        frame = entry[0]
        if frame is None or frame.empty:
            continue
        work = frame.copy()
        work['_数据日期'] = day
        status = product_daily.get(day)
        if status is not None and not status.empty and '宝贝ID' in status.columns:
            keep = [col for col in status_cols if col in status.columns]
            status_frame = status[keep].copy()
            status_frame['_pid_key'] = status_frame['宝贝ID'].map(_id_key)
            status_frame = status_frame.drop_duplicates('_pid_key', keep='last')
            work['_pid_key'] = work['宝贝ID'].map(_id_key) if '宝贝ID' in work.columns else ''
            work = work.merge(
                status_frame.drop(columns=['宝贝ID'], errors='ignore'),
                on='_pid_key',
                how='left',
                suffixes=('', '_overall'),
            ).drop(columns=['_pid_key'], errors='ignore')
        frames.append(work)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _replace_day_map_frame(entry, frame):
    if isinstance(entry, tuple):
        return (frame,) + tuple(entry[1:])
    if isinstance(entry, list):
        return [frame] + list(entry[1:])
    return (frame, [], 0, 0, set(), {})


def _with_checkpoint_visible_metox_labels(day_map):
    if not day_map:
        return day_map
    labeled_map = {}
    for day in sorted(day_map):
        entry = day_map.get(day)
        if entry is None or len(entry) == 0 or not isinstance(entry[0], pd.DataFrame):
            labeled_map[day] = entry
            continue
        frame = entry[0]
        if frame is None or frame.empty or 'MetoX算法命中' in frame.columns:
            labeled_map[day] = entry
            continue
        visible_map = dict(labeled_map)
        visible_map[day] = entry
        try:
            labeled = apply_metox_algorithm(frame.copy(), visible_map, day)
        except Exception:
            labeled = frame.copy()
            labeled['MetoX算法命中'] = ''
        labeled_map[day] = _replace_day_map_frame(entry, labeled)
    return labeled_map


def _numeric_metric_series(values, index=None):
    """Convert metric values to floats while preserving thousands separators."""
    if values is None:
        return pd.Series(0.0, index=index, dtype=float)
    series = values.copy() if isinstance(values, pd.Series) else pd.Series(values, index=index)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors='coerce').fillna(0.0).astype(float)
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace(r'[,\s¥￥$%]', '', regex=True)
    )
    return pd.to_numeric(cleaned, errors='coerce').fillna(0.0).astype(float)


def _creative_metrics_by_style_age(creative_daily, first_seen, product_style_map=None):
    from core.new_product_backtest import BacktestColumns as C
    from core.new_product_first_seen import (
        FIRST_SEEN_DATE,
        FIRST_SEEN_PRODUCT_ID,
        STYLE_KEY,
        build_style_key_series,
        normalize_identifier,
    )

    if not creative_daily or first_seen is None or first_seen.empty:
        return pd.DataFrame(columns=[C.style_key, C.age_day, C.creative_impressions, C.creative_clicks])

    first_seen_keep = [STYLE_KEY, FIRST_SEEN_DATE]
    first_seen_dates = first_seen[first_seen_keep].drop_duplicates(STYLE_KEY).copy()
    pid_map = {}
    if FIRST_SEEN_PRODUCT_ID in first_seen.columns:
        pid_map = first_seen.set_index(FIRST_SEEN_PRODUCT_ID)[STYLE_KEY].to_dict()
    for product_id, style_key in (product_style_map or {}).items():
        normalized_product_id = normalize_identifier(product_id)
        normalized_style_key = normalize_identifier(style_key)
        if normalized_product_id and normalized_style_key:
            pid_map[normalized_product_id] = normalized_style_key
    frames = []
    for day, frame in (creative_daily or {}).items():
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        if '_数据日期' not in part.columns:
            part['_数据日期'] = day
        keys = build_style_key_series(part)
        part[STYLE_KEY] = keys[STYLE_KEY]
        if '宝贝ID' in part.columns and pid_map:
            mapped = part['宝贝ID'].map(normalize_identifier).map(pid_map).fillna('')
            part[STYLE_KEY] = part[STYLE_KEY].where(mapped == '', mapped)
        part = part.merge(first_seen_dates, on=STYLE_KEY, how='inner')
        if part.empty:
            continue
        data_date = pd.to_datetime(part['_数据日期'], errors='coerce')
        first_seen_date = pd.to_datetime(part[FIRST_SEEN_DATE], errors='coerce')
        part[C.age_day] = (data_date - first_seen_date).dt.days + 1
        part = part[part[C.age_day].between(1, 45, inclusive='both')].copy()
        if part.empty:
            continue
        impressions_col = '展现' if '展现' in part.columns else '展现量'
        clicks_col = '点击' if '点击' in part.columns else '点击量'
        part[C.creative_impressions] = _numeric_metric_series(
            part.get(impressions_col), index=part.index
        )
        part[C.creative_clicks] = _numeric_metric_series(
            part.get(clicks_col), index=part.index
        )
        frames.append(part[[STYLE_KEY, C.age_day, C.creative_impressions, C.creative_clicks]])
    if not frames:
        return pd.DataFrame(columns=[C.style_key, C.age_day, C.creative_impressions, C.creative_clicks])
    work = pd.concat(frames, ignore_index=True)
    work = work.rename(columns={STYLE_KEY: C.style_key})
    return work.groupby([C.style_key, C.age_day], as_index=False, sort=False).agg({
        C.creative_impressions: 'sum',
        C.creative_clicks: 'sum',
    })


def _merge_backtest_label_values(values):
    labels = []
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        for part in re.split(r'[\s,，;；、/|]+', str(value)):
            label = part.strip()
            if label and label not in labels:
                labels.append(label)
    return ' '.join(labels)


def _last_backtest_value(values, default=''):
    for value in reversed(list(values)):
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        if str(value).strip():
            return value
    return default


def _collapse_backtest_rows_by_style_age(rows):
    if rows is None or rows.empty:
        return rows
    from core.new_product_backtest import BacktestColumns as C

    collapsed = []
    numeric_cols = [C.visitors, C.carts, C.buyers, C.units, C.gmv, C.profit]
    for (style_key, age_day), part in rows.groupby([C.style_key, C.age_day], sort=False, dropna=False):
        row = {C.style_key: style_key, C.age_day: age_day}
        row[C.store] = _last_backtest_value(part.get(C.store, []), '')
        row[C.category] = _last_backtest_value(part.get(C.category, []), '')
        row[C.price_band] = _last_backtest_value(part.get(C.price_band, []), '')
        for column in numeric_cols:
            row[column] = pd.to_numeric(part.get(column, pd.Series(dtype=float)), errors='coerce').fillna(0.0).sum()
        row[C.valid_data_day] = bool(part.get(C.valid_data_day, pd.Series(False, index=part.index)).fillna(False).astype(bool).any())
        row[C.is_online] = bool(part.get(C.is_online, pd.Series(True, index=part.index)).fillna(False).astype(bool).any())
        row[C.stock_ok] = bool(part.get(C.stock_ok, pd.Series(True, index=part.index)).fillna(False).astype(bool).any())
        row[C.manual_removed] = not row[C.is_online]
        row[C.data_missing] = bool(part.get(C.data_missing, pd.Series(False, index=part.index)).fillna(False).astype(bool).any())
        row[C.historical_truncated] = bool(part.get(C.historical_truncated, pd.Series(False, index=part.index)).fillna(False).astype(bool).any())
        row[C.main_ctr] = _last_backtest_value(part.get(C.main_ctr, []), float('nan'))
        row[C.creative_impressions] = pd.to_numeric(
            part.get(C.creative_impressions, pd.Series(dtype=float)),
            errors='coerce',
        ).fillna(0.0).sum()
        row[C.creative_clicks] = pd.to_numeric(
            part.get(C.creative_clicks, pd.Series(dtype=float)),
            errors='coerce',
        ).fillna(0.0).sum()
        row[C.metox_labels] = _merge_backtest_label_values(part.get(C.metox_labels, []))
        collapsed.append(row)
    return pd.DataFrame(collapsed)


def _format_new_product_lifecycle_view(
    lifecycle,
    first_seen,
    d15=None,
    d30=None,
    date_str='',
    current=None,
):
    if lifecycle is None or lifecycle.empty:
        return pd.DataFrame(columns=NEW_PRODUCT_LIFECYCLE_COLUMNS)
    from core.new_product_first_seen import FIRST_SEEN_DATE, STYLE_KEY

    view = lifecycle.copy()
    age_filter = pd.to_numeric(view.get('新品年龄'), errors='coerce')
    view = view[age_filter.between(1, 45, inclusive='both')].copy()
    if view.empty:
        return pd.DataFrame(columns=NEW_PRODUCT_LIFECYCLE_COLUMNS)
    if first_seen is not None and not first_seen.empty:
        keep = [col for col in first_seen.columns if col not in view.columns or col == STYLE_KEY]
        view = view.merge(first_seen[keep], on=STYLE_KEY, how='left')

    decision_cols = [
        '判定结果', '判定原因', '样本充分性',
        '动作可执行', '库存提示', '保护状态', '保护证据', '流量验证', '本轮成交',
        '下架阻断原因', '保护因素', '风险因素',
    ]
    decision_info_cols = [
        '动作可执行', '库存提示', '保护状态', '保护证据', '流量验证', '本轮成交',
        '下架阻断原因', '保护因素', '风险因素',
    ]
    for source, prefix in ((d15, 'D15'), (d30, 'D30')):
        if source is None or source.empty or STYLE_KEY not in source.columns:
            view[f'{prefix}结果'] = '继续观察'
            view[f'{prefix}原因'] = ''
            view[f'{prefix}样本充分性'] = ''
            for column in decision_info_cols:
                view[f'{prefix}{column}'] = ''
            continue
        cols = [STYLE_KEY] + decision_cols
        part = source[[col for col in cols if col in source.columns]].copy()
        rename = {
            '判定结果': f'{prefix}结果',
            '判定原因': f'{prefix}原因',
            '样本充分性': f'{prefix}样本充分性',
            '动作可执行': f'{prefix}动作可执行',
            '库存提示': f'{prefix}库存提示',
            '保护状态': f'{prefix}保护状态',
            '保护证据': f'{prefix}保护证据',
            '流量验证': f'{prefix}流量验证',
            '本轮成交': f'{prefix}本轮成交',
            '下架阻断原因': f'{prefix}下架阻断原因',
            '保护因素': f'{prefix}保护因素',
            '风险因素': f'{prefix}风险因素',
        }
        view = view.merge(part.rename(columns=rename), on=STYLE_KEY, how='left')
    current_prefix = '__当前'
    if current is not None and not current.empty and STYLE_KEY in current.columns:
        current_cols = [STYLE_KEY, '判定轮次'] + decision_cols
        current_part = current[[col for col in current_cols if col in current.columns]].copy()
        current_part = current_part.drop_duplicates(subset=[STYLE_KEY], keep='last')
        current_part = current_part.rename(
            columns={col: f'{current_prefix}{col}' for col in current_part.columns if col != STYLE_KEY}
        )
        view = view.merge(current_part, on=STYLE_KEY, how='left')
    view['D15结果'] = _normalize_new_product_result_series(view.get('D15结果'), view.index)
    view['D30结果'] = _normalize_new_product_result_series(view.get('D30结果'), view.index)
    age = pd.to_numeric(view.get('新品年龄'), errors='coerce').fillna(0).astype(int)
    view['判定轮次'] = '未到D15'
    d15_mask = (age >= 15) & (age < 30)
    d30_mask = age >= 30
    view.loc[d15_mask, '判定轮次'] = 'D15'
    view.loc[d30_mask, '判定轮次'] = 'D30'
    view['当前结果'] = '继续观察'
    view.loc[d15_mask, '当前结果'] = view.loc[d15_mask, 'D15结果']
    view.loc[d30_mask, '当前结果'] = view.loc[d30_mask, 'D30结果']
    current_result_col = f'{current_prefix}判定结果'
    current_mask = age.ge(15) & view.get(
        current_result_col,
        pd.Series(index=view.index, dtype=object),
    ).notna()
    if current_result_col in view.columns:
        view.loc[current_mask, '当前结果'] = view.loc[current_mask, current_result_col]
    view['当前结果'] = _normalize_new_product_result_series(view['当前结果'], view.index)
    view['判定原因'] = '未到第一轮判定日'
    view.loc[d15_mask, '判定原因'] = view.loc[d15_mask, 'D15原因'].fillna('')
    view.loc[d30_mask, '判定原因'] = view.loc[d30_mask, 'D30原因'].fillna('')
    current_reason_col = f'{current_prefix}判定原因'
    if current_reason_col in view.columns:
        view.loc[current_mask, '判定原因'] = view.loc[current_mask, current_reason_col].fillna('')
    current_round_col = f'{current_prefix}判定轮次'
    if current_round_col in view.columns:
        view.loc[current_mask, '判定轮次'] = view.loc[current_mask, current_round_col].fillna('')

    for target in (
        '动作可执行', '库存提示', '样本充分性', '保护证据', '流量验证',
        '本轮成交', '下架阻断原因', '保护因素', '风险因素',
    ):
        default = view.get(target, pd.Series('', index=view.index, dtype=object))
        view[target] = default.fillna('').astype(str)
        for mask, prefix in ((d15_mask, 'D15'), (d30_mask, 'D30')):
            source_col = f'{prefix}{target}'
            if source_col in view.columns:
                view.loc[mask, target] = view.loc[mask, source_col].fillna('').astype(str)
        current_col = f'{current_prefix}{target}'
        if current_col in view.columns:
            view.loc[current_mask, target] = view.loc[current_mask, current_col].fillna('').astype(str)

    default_protection = view.get('保护状态', pd.Series('无保护', index=view.index, dtype=object))
    view['保护状态'] = default_protection.fillna('无保护').map(_normalize_new_product_protection_status)
    for mask, prefix in ((d15_mask, 'D15'), (d30_mask, 'D30')):
        source_col = f'{prefix}保护状态'
        factor_col = f'{prefix}保护因素'
        if source_col in view.columns:
            view.loc[mask, '保护状态'] = view.loc[mask, source_col].map(_normalize_new_product_protection_status)
        elif factor_col in view.columns:
            view.loc[mask, '保护状态'] = view.loc[mask, factor_col].fillna('').astype(str).str.strip().map(
                lambda text: '动销保护' if text else '无保护'
            )
    current_protection_col = f'{current_prefix}保护状态'
    if current_protection_col in view.columns:
        view.loc[current_mask, '保护状态'] = view.loc[
            current_mask, current_protection_col
        ].map(_normalize_new_product_protection_status)
    view.loc[view['保护状态'].astype(str).str.strip().eq(''), '保护状态'] = '无保护'
    blank_protection_evidence = (
        view['保护证据'].astype(str).str.strip().eq('')
        & view['保护状态'].astype(str).str.strip().eq('动销保护')
    )
    view.loc[blank_protection_evidence, '保护证据'] = view.loc[blank_protection_evidence, '本轮成交']
    blank_block_reason = (
        view['下架阻断原因'].astype(str).str.strip().eq('')
        & view['当前结果'].astype(str).str.strip().ne('下架')
    )
    view.loc[blank_block_reason, '下架阻断原因'] = view.loc[blank_block_reason, '判定原因']
    view.loc[view['风险因素'].astype(str).str.strip().eq(''), '风险因素'] = view['判定原因']
    view['预测日期'] = date_str
    if FIRST_SEEN_DATE in view.columns and '首次在线发现日' not in view.columns:
        view['首次在线发现日'] = view[FIRST_SEEN_DATE]
    return _new_product_lifecycle_display_frame(view)


def _safe_ratio_scalar(numerator, denominator):
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return float('nan')
    return numerator / denominator if denominator > 0 else float('nan')


def _latest_product_overall_by_pid(product_daily, date_str):
    frames = []
    as_of = pd.to_datetime(date_str, errors='coerce')
    for day, frame in (product_daily or {}).items():
        if frame is None or frame.empty or '宝贝ID' not in frame.columns:
            continue
        part = frame.copy()
        part['_date'] = pd.to_datetime(part.get('_数据日期', day), errors='coerce')
        if pd.notna(as_of):
            part = part[part['_date'] <= as_of]
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    work = pd.concat(frames, ignore_index=True).sort_values('_date')
    work['_pid_key'] = work['宝贝ID'].map(_id_key)
    return work.drop_duplicates('_pid_key', keep='last')


def _eligible_new_product_overall(product_daily, first_seen, date_str):
    from core.new_product_first_seen import (
        FIRST_SEEN_DATE,
        IS_HISTORY_TRUNCATED,
        STYLE_KEY,
        build_style_key_series,
    )

    if first_seen is None or first_seen.empty:
        return pd.DataFrame()
    as_of = pd.to_datetime(date_str, errors='coerce')
    eligible = first_seen.copy()
    first_seen_dates = pd.to_datetime(eligible.get(FIRST_SEEN_DATE), errors='coerce')
    age = (as_of.normalize() - first_seen_dates).dt.days + 1 if pd.notna(as_of) else pd.Series([], dtype=float)
    truncated = eligible.get(IS_HISTORY_TRUNCATED, pd.Series(False, index=eligible.index)).fillna(False).astype(bool)
    eligible_styles = set(eligible.loc[age.between(1, 45, inclusive='both') & ~truncated, STYLE_KEY].astype(str))
    latest = _latest_product_overall_by_pid(product_daily, date_str)
    if latest.empty or not eligible_styles:
        return pd.DataFrame()
    keys = build_style_key_series(latest)
    latest[STYLE_KEY] = keys[STYLE_KEY]
    latest = latest[latest[STYLE_KEY].astype(str).isin(eligible_styles)].copy()
    latest['_pid_key'] = latest['宝贝ID'].map(_id_key) if '宝贝ID' in latest.columns else ''
    return latest[latest['_pid_key'] != '']


def _main_ctr_only_material_rows(latest_overall):
    if latest_overall is None or latest_overall.empty:
        return pd.DataFrame(columns=NEW_PRODUCT_MATERIAL_COLUMNS)
    out = pd.DataFrame()
    for column in ['宝贝ID', '货号', '淘宝类目', '主图整体点击率']:
        out[column] = latest_overall[column] if column in latest_overall.columns else ''
    out['主图CTR是否有效'] = out['主图整体点击率'].astype(str).str.strip().ne('')
    out['计划ID'] = ''
    out['计划名称'] = ''
    out['主体ID'] = ''
    out['主体名称'] = ''
    out['主体类型'] = ''
    out['创意ID'] = ''
    out['创意名称'] = ''
    out['创意首次发现日'] = ''
    out['创意年龄'] = pd.NA
    out['创意展现量'] = 0
    out['创意点击量'] = 0
    out['无界创意CTR'] = pd.NA
    out['上一7天CTR'] = pd.NA
    out['CTR环比'] = pd.NA
    out['创意展现占比'] = 0.0
    out['是否主投创意'] = False
    return _new_product_material_display_frame(out)


def _build_material_view(creative_daily, product_daily, dimensions, first_seen, date_str):
    latest_overall = _eligible_new_product_overall(product_daily, first_seen, date_str)
    eligible_pids = set(latest_overall['_pid_key']) if not latest_overall.empty and '_pid_key' in latest_overall.columns else set()
    frames = []
    for day, frame in (creative_daily or {}).items():
        if frame is None or frame.empty:
            continue
        part = _merge_pid_dimensions(frame, dimensions)
        part['_数据日期'] = day
        frames.append(part)
    if not frames:
        return _main_ctr_only_material_rows(latest_overall)
    creative = pd.concat(frames, ignore_index=True)
    if '宝贝ID' not in creative.columns or '创意ID' not in creative.columns:
        return _main_ctr_only_material_rows(latest_overall)
    creative['_date'] = pd.to_datetime(creative['_数据日期'], errors='coerce')
    creative['_pid_key'] = creative['宝贝ID'].map(_id_key)
    for column in ['计划ID', '计划名字', '主体ID', '主体名称', '主体类型']:
        if column not in creative.columns:
            creative[column] = ''
    creative['_plan_key'] = creative['计划ID'].map(_id_key)
    creative['_creative_key'] = creative['创意ID'].map(_id_key)
    creative = creative[(creative['_pid_key'] != '') & (creative['_creative_key'] != '')]
    if eligible_pids:
        creative = creative[creative['_pid_key'].isin(eligible_pids)].copy()
    as_of = pd.to_datetime(date_str, errors='coerce')
    if pd.isna(as_of):
        as_of = creative['_date'].max()
    cur_start = as_of - pd.Timedelta(days=6)
    prev_start = as_of - pd.Timedelta(days=13)
    prev_end = as_of - pd.Timedelta(days=7)
    current = creative[(creative['_date'] >= cur_start) & (creative['_date'] <= as_of)].copy()
    previous = creative[(creative['_date'] >= prev_start) & (creative['_date'] <= prev_end)].copy()
    if current.empty:
        return _main_ctr_only_material_rows(latest_overall)

    for col in ['展现', '点击']:
        current[col] = _numeric_metric_series(current.get(col), index=current.index)
        previous[col] = (
            _numeric_metric_series(previous.get(col), index=previous.index)
            if col in previous.columns else 0.0
        )
    group_cols = ['_pid_key', '_plan_key', '_creative_key']
    current_agg = current.groupby(group_cols, sort=False).agg(
        宝贝ID=('宝贝ID', 'last'),
        货号=('货号', 'last') if '货号' in current.columns else ('宝贝ID', 'last'),
        淘宝类目=('淘宝类目', 'last') if '淘宝类目' in current.columns else ('宝贝ID', 'last'),
        计划ID=('计划ID', 'last'),
        计划名称=('计划名字', 'last'),
        主体ID=('主体ID', 'last'),
        主体名称=('主体名称', 'last'),
        主体类型=('主体类型', 'last'),
        创意ID=('创意ID', 'last'),
        创意名称=('创意名字', 'last') if '创意名字' in current.columns else ('创意ID', 'last'),
        创意展现量=('展现', 'sum'),
        创意点击量=('点击', 'sum'),
    ).reset_index()
    previous_agg = previous.groupby(group_cols, sort=False).agg(
        上一7天展现=('展现', 'sum'),
        上一7天点击=('点击', 'sum'),
    ).reset_index() if not previous.empty else pd.DataFrame(columns=group_cols + ['上一7天展现', '上一7天点击'])
    out = current_agg.merge(previous_agg, on=group_cols, how='left')
    out['无界创意CTR'] = out.apply(lambda row: _safe_ratio_scalar(row['创意点击量'], row['创意展现量']), axis=1)
    out['上一7天CTR'] = out.apply(lambda row: _safe_ratio_scalar(row.get('上一7天点击', 0), row.get('上一7天展现', 0)), axis=1)
    out['CTR环比'] = (out['无界创意CTR'] - out['上一7天CTR']) / out['上一7天CTR'].where(out['上一7天CTR'] > 0)
    first_dates = creative.groupby(group_cols)['_date'].min().reset_index().rename(columns={'_date': '创意首次发现日'})
    out = out.merge(first_dates, on=group_cols, how='left')
    out['创意年龄'] = (as_of.normalize() - out['创意首次发现日']).dt.days + 1
    out['创意首次发现日'] = out['创意首次发现日'].dt.date.astype(str)
    product_totals = out.groupby('_pid_key')['创意展现量'].transform('sum')
    out['创意展现占比'] = out['创意展现量'] / product_totals.where(product_totals > 0)
    max_impression = out.groupby('_pid_key')['创意展现量'].transform('max')
    out['是否主投创意'] = out['创意展现量'].eq(max_impression)
    if not latest_overall.empty and '主图整体点击率' in latest_overall.columns:
        out = out.merge(
            latest_overall[['_pid_key', '主图整体点击率']],
            on='_pid_key',
            how='left',
        )
    else:
        out['主图整体点击率'] = ''
    out['主图CTR是否有效'] = out['主图整体点击率'].astype(str).str.strip().ne('')
    if not latest_overall.empty:
        missing_main = latest_overall[~latest_overall['_pid_key'].isin(set(out['_pid_key']))].copy()
        if not missing_main.empty:
            out = pd.concat([out, _main_ctr_only_material_rows(missing_main)], ignore_index=True, sort=False)
    return _new_product_material_display_frame(out)


def _build_backtest_status_frame(lifecycle_view, history_sources):
    note = history_sources.get('message') or '当前MVP未生成完整D15/D30验证窗口；回测样本暂列为不可验证。'
    sample_count = int(len(lifecycle_view)) if lifecycle_view is not None else 0
    return pd.DataFrame([{
        '判定节点': 'D15/D30',
        '规则版本': 'A-D',
        '样本数': sample_count,
        '下架建议数': int((lifecycle_view.get('当前结果') == '下架').sum()) if sample_count else 0,
        '下架命中率': pd.NA,
        '误杀率': pd.NA,
        '召回率': pd.NA,
        '下架覆盖率': pd.NA,
        '机会损失GMV': pd.NA,
        '机会损失预估利润': pd.NA,
        '不可验证样本数': sample_count,
        'CTR增益': pd.NA,
        'MetoX保护有效率': pd.NA,
        '分组': note,
    }], columns=NEW_PRODUCT_BACKTEST_COLUMNS)


def _build_backtest_rows_from_facts(facts, first_seen, creative_daily=None):
    if facts is None or facts.empty or first_seen is None or first_seen.empty:
        return pd.DataFrame()
    from core.new_product_backtest import BacktestColumns as C
    from core.new_product_first_seen import (
        FIRST_SEEN_DATE,
        FIRST_SEEN_PRODUCT_ID,
        IS_HISTORY_TRUNCATED,
        STYLE_KEY,
        build_style_key_series,
        normalize_identifier,
    )

    work = facts.copy()
    keys = build_style_key_series(work)
    work[STYLE_KEY] = keys[STYLE_KEY]
    if '宝贝ID' in work.columns and FIRST_SEEN_PRODUCT_ID in first_seen.columns:
        pid_map = first_seen.set_index(FIRST_SEEN_PRODUCT_ID)[STYLE_KEY].to_dict()
        mapped = work['宝贝ID'].map(normalize_identifier).map(pid_map).fillna('')
        work[STYLE_KEY] = work[STYLE_KEY].where(mapped == '', mapped)
    keep = [STYLE_KEY, FIRST_SEEN_DATE, IS_HISTORY_TRUNCATED]
    work = work.merge(first_seen[[col for col in keep if col in first_seen.columns]], on=STYLE_KEY, how='inner')
    if work.empty:
        return pd.DataFrame()
    data_date = pd.to_datetime(work.get('_数据日期'), errors='coerce')
    first_seen_date = pd.to_datetime(work.get(FIRST_SEEN_DATE), errors='coerce')
    work[C.age_day] = (data_date - first_seen_date).dt.days + 1
    work = work[work[C.age_day].between(1, 45, inclusive='both')].copy()
    if work.empty:
        return pd.DataFrame()

    rows = pd.DataFrame()
    rows[C.style_key] = work[STYLE_KEY]
    rows[C.age_day] = work[C.age_day]
    rows[C.store] = work['店铺'] if '店铺' in work.columns else ''
    rows[C.category] = work['淘宝类目'] if '淘宝类目' in work.columns else ''
    rows[C.price_band] = work['价格段'] if '价格段' in work.columns else ''
    rows[C.visitors] = pd.to_numeric(work.get('商品访客数'), errors='coerce').fillna(0.0)
    rows[C.carts] = pd.to_numeric(work.get('商品加购件数'), errors='coerce').fillna(0.0)
    rows[C.buyers] = pd.to_numeric(work.get('支付买家数'), errors='coerce').fillna(0.0)
    rows[C.units] = pd.to_numeric(work.get('支付件数'), errors='coerce').fillna(0.0) if '支付件数' in work.columns else 0.0
    rows[C.gmv] = pd.to_numeric(work.get('GMV'), errors='coerce').fillna(0.0)
    rows[C.profit] = pd.to_numeric(work.get('预估利润'), errors='coerce').fillna(0.0) if '预估利润' in work.columns else 0.0
    rows[C.valid_data_day] = True
    rows[C.is_online] = work.get('商品状态', '当前在线').astype(str).eq('当前在线') if '商品状态' in work.columns else True
    rows[C.stock_ok] = pd.to_numeric(work.get('在仓库存'), errors='coerce').fillna(1.0).gt(0) if '在仓库存' in work.columns else True
    rows[C.manual_removed] = rows[C.is_online].eq(False)
    rows[C.data_missing] = False
    rows[C.historical_truncated] = work.get(IS_HISTORY_TRUNCATED, False).fillna(False).astype(bool) if IS_HISTORY_TRUNCATED in work.columns else False
    rows[C.main_ctr] = float('nan')
    rows[C.creative_impressions] = 0.0
    rows[C.creative_clicks] = 0.0
    metox_label_col = 'MetoX' + '\u7b97\u6cd5\u547d\u4e2d'
    rows[C.metox_labels] = work.get(metox_label_col, '') if metox_label_col in work.columns else ''
    rows = _collapse_backtest_rows_by_style_age(rows)

    product_style_map = {}
    if '宝贝ID' in work.columns:
        mapping = work[['宝贝ID', STYLE_KEY]].copy()
        mapping['宝贝ID'] = mapping['宝贝ID'].map(normalize_identifier)
        mapping[STYLE_KEY] = mapping[STYLE_KEY].astype(str).str.strip()
        mapping = mapping[(mapping['宝贝ID'] != '') & (mapping[STYLE_KEY] != '')]
        product_style_map = (
            mapping.drop_duplicates('宝贝ID', keep='last')
            .set_index('宝贝ID')[STYLE_KEY]
            .to_dict()
        )
    creative_metrics = _creative_metrics_by_style_age(
        creative_daily,
        first_seen,
        product_style_map=product_style_map,
    )
    if not creative_metrics.empty:
        rows = rows.merge(
            creative_metrics,
            on=[C.style_key, C.age_day],
            how='left',
            suffixes=('', '_creative'),
        )
        for column in (C.creative_impressions, C.creative_clicks):
            merged_col = f'{column}_creative'
            if merged_col in rows.columns:
                rows[column] = pd.to_numeric(rows[merged_col], errors='coerce').fillna(rows[column])
                rows = rows.drop(columns=[merged_col])
    baseline_cols = [
        C.baseline_cart_rate,
        C.baseline_pay_cvr,
        C.baseline_uv_value,
        C.baseline_cumulative_cart_rate,
        C.baseline_cumulative_pay_cvr,
        C.baseline_cumulative_uv_value,
        C.baseline_main_ctr,
        C.baseline_creative_ctr,
    ]
    for column in baseline_cols:
        rows[column] = float('nan')
    rows[C.ctr_positive_signal] = False
    return rows


def _format_backtest_summary(summary):
    if summary is None or summary.empty:
        return pd.DataFrame(columns=NEW_PRODUCT_BACKTEST_COLUMNS)
    out = pd.DataFrame()
    out['判定节点'] = summary['checkpoint'].map(lambda value: f"D{int(value)}") if 'checkpoint' in summary.columns else 'D15/D30'
    out['规则版本'] = summary['rule'].map(lambda value: f"{value}-影子规则") if 'rule' in summary.columns else ''
    out['样本数'] = summary.get('total', 0)
    out['下架建议数'] = summary.get('predicted_down', 0)
    out['下架命中率'] = summary.get('down_hit_rate', pd.NA)
    out['误杀率'] = summary.get('false_kill_rate', pd.NA)
    out['召回率'] = summary.get('recall', pd.NA)
    out['下架覆盖率'] = summary.get('down_coverage_rate', pd.NA)
    out['机会损失GMV'] = summary.get('opportunity_loss_gmv', pd.NA)
    out['机会损失预估利润'] = summary.get('opportunity_loss_profit', pd.NA)
    out['不可验证样本数'] = summary.get('unverifiable', pd.NA)
    out['CTR增益'] = summary.get('ctr_gain_false_kill_rate', pd.NA)
    out['MetoX保护有效率'] = summary.get('metox_protection_effectiveness', pd.NA)
    out['分组'] = '当前可用经营日事实'
    return out[NEW_PRODUCT_BACKTEST_COLUMNS]


def _build_backtest_view(facts, first_seen, lifecycle_view, history_sources, creative_daily=None):
    rows = _build_backtest_rows_from_facts(facts, first_seen, creative_daily=creative_daily)
    if rows.empty:
        return _build_backtest_status_frame(lifecycle_view, history_sources)
    try:
        from core.new_product_backtest import BacktestColumns as C, run_backtest
        result = run_backtest(rows, group_cols=[C.checkpoint])
        summary = _format_backtest_summary(result.summary)
        return summary if not summary.empty else _build_backtest_status_frame(lifecycle_view, history_sources)
    except Exception as exc:
        fallback = _build_backtest_status_frame(lifecycle_view, history_sources)
        fallback.loc[0, '分组'] = f"回测生成失败：{str(exc)[:120]}"
        return fallback


def _build_new_product_context_from_sources(df, day_map, date_str, history_sources):
    from core.new_product_first_seen import build_first_seen_index
    from core import new_product_lifecycle as lifecycle_core

    product_daily_raw = history_sources.get('product_overall_daily') or {}
    creative_daily = history_sources.get('creative_daily') or {}
    if not product_daily_raw:
        ctx = _empty_new_product_context(
            history_sources.get('message') or '缺少商品-整体历史源，无法确认首次在线发现日。',
            status=history_sources.get('status', 'missing'),
        )
        ctx['source_status'] = history_sources.get('source_status', ctx['source_status'])
        ctx['coverage'] = history_sources.get('coverage', ctx['coverage'])
        return ctx

    business_day_map = history_sources.get('business_day_map') or day_map
    labeled_business_day_map = _with_checkpoint_visible_metox_labels(business_day_map)
    dimensions = _build_product_dimension_lookup(df, labeled_business_day_map)
    product_daily = _enrich_product_overall_daily(product_daily_raw, dimensions)
    index_path = history_sources.get('first_seen_index_path')
    existing_index = _load_first_seen_index(index_path)
    first_seen = build_first_seen_index(
        product_daily,
        existing_index=existing_index,
        history_start_date=history_sources.get('history_start_date') or '',
    )
    persist_warning = _save_first_seen_index(first_seen, index_path)
    facts = _daily_business_facts_with_new_product_status(labeled_business_day_map, product_daily)
    lifecycle = lifecycle_core.aggregate_lifecycle_windows(facts, first_seen, as_of_date=date_str)
    d15_checkpoint = getattr(lifecycle_core, 'CHECKPOINT_D15', None)
    d15 = (
        lifecycle_core.apply_lifecycle_decisions(lifecycle, d15_checkpoint, peer_frame=lifecycle)
        if d15_checkpoint else pd.DataFrame()
    )
    d30_checkpoint = getattr(lifecycle_core, 'CHECKPOINT_D30', None)
    d30 = (
        lifecycle_core.apply_lifecycle_decisions(lifecycle, d30_checkpoint, peer_frame=lifecycle)
        if d30_checkpoint else pd.DataFrame()
    )
    current = lifecycle_core.apply_current_lifecycle_decisions(lifecycle, peer_frame=lifecycle)
    lifecycle_view = _format_new_product_lifecycle_view(
        lifecycle, first_seen, d15, d30, date_str=date_str, current=current
    )
    materials = _build_material_view(creative_daily, product_daily, dimensions, first_seen, date_str)
    backtest = _build_backtest_view(
        facts, first_seen, lifecycle_view, history_sources, creative_daily=creative_daily
    )
    message = history_sources.get('message') or ''
    if persist_warning:
        message = (message + '；' if message else '') + persist_warning
    if len(facts) == 0:
        message = (message + '；' if message else '') + '当前仅加载到新品源，缺少可聚合的经营日事实。'
    change_meta = _build_new_product_change(
        lifecycle_view, first_seen, product_daily, date_str,
        history_sources.get('new_product_snapshot_path'),
    )
    return {
        'status': history_sources.get('status', 'complete'),
        'message': message,
        'lifecycle': lifecycle_view,
        'materials': materials,
        'backtest': backtest,
        'methodology': _new_product_methodology_frame(message),
        'source_status': history_sources.get('source_status', pd.DataFrame()),
        'coverage': history_sources.get('coverage', pd.DataFrame()),
        'first_seen_index': first_seen,
        'change_summary': change_meta.get('汇总', {}),
        'change_meta': change_meta,
    }


def _build_new_product_change(lifecycle_view, first_seen, product_daily, date_str, snapshot_path):
    """生成/更新新品变化快照，并算好默认（对比上次运行）的变化汇总（v2.54）。

    只写快照与汇总；具体变化表在页面上按所选基线现算，方便切换"上次运行 / N 天前 / 上周同期"。
    """
    from core import new_product_change as change_core

    meta = {
        '状态': '未生成',
        '说明': '',
        '上次快照日': '',
        '本次快照日': str(date_str)[:10],
        '快照路径': str(snapshot_path or ''),
        '快照写入': {},
        '快照数': 0,
        '源可用': False,
        '汇总': {},
    }
    if not snapshot_path:
        meta['说明'] = '缺少快照路径，本次不做变化判定。'
        return meta
    try:
        online = change_core.build_online_status(product_daily, date_str)
        snapshot = change_core.build_snapshot(lifecycle_view, first_seen, online, date_str)
        meta['源可用'] = bool(snapshot.get('源可用'))
        written = change_core.append_snapshot(snapshot_path, snapshot)
        meta['快照写入'] = written
        snapshots = change_core.load_snapshots(snapshot_path)
        meta['快照数'] = len(snapshots)
        baseline = change_core.select_baseline(snapshots, date_str)
        result = change_core.diff_snapshot(baseline, snapshot)
        meta.update({
            '状态': result['状态'],
            '说明': result['说明'],
            '上次快照日': result['上次快照日'],
            '汇总': result['summary'],
            '变化行数': len(result['rows']),
            '当前快照': snapshot,
        })
        if written.get('历史回算'):
            meta['说明'] = f"{result['说明']}｜本次为历史回算：{written.get('原因', '')}"
        elif not written.get('写入'):
            meta['说明'] = f"{result['说明']}｜{written.get('原因', '')}"
    except Exception as exc:  # pragma: no cover - 兜底，不影响新品判定
        meta['状态'] = '生成失败'
        meta['说明'] = f'新品变化区生成失败：{str(exc)[:160]}'
        print(f"[新品变化] 生成失败：{exc}")
    return meta


def _load_shared_dimensions(product_folder, cache_root, jst_entry=None,
                            jst_refund_entry=None, extra_fingerprint_entries=None):
    """一次加载并预聚合周期内每天共用的商品资料、库存和退款率维度。"""
    product_file_list = _scan_target_files(
        product_folder, source_types={SOURCE_PRODUCT_INFO, SOURCE_STOCK}
    )
    extra_fingerprint_entries = list(extra_fingerprint_entries or [])
    fingerprint_entries = [
        (fp, src_type) for fp, _fn, src_type in product_file_list
    ] + extra_fingerprint_entries
    extra_types = {source_type for _path, source_type in extra_fingerprint_entries}
    cacheable = bool(fingerprint_entries)
    if jst_entry is not None and 'jst_stock_snapshot' not in extra_types:
        cacheable = False
    if jst_refund_entry is not None and 'jst_refund_snapshot' not in extra_types:
        cacheable = False

    dimension_fingerprint = None
    if cacheable:
        try:
            dimension_fingerprint = source_fingerprint(fingerprint_entries)
            cached = load_shared_dimension_cache(
                cache_root, fingerprint_entries,
                expected_fingerprint=dimension_fingerprint,
            )
        except Exception:
            cached = None
            cacheable = False
        if cached is not None:
            info = [dict(item) for item in cached['files_info']]
            return cached['dimensions'], (info, 0, cached['total_files'])

    sources, info, copied, total = _read_sources_for_date(
        None, None, product_folder, Path(cache_root) / '_shared_dimensions',
        '共享维度', include_product=True,
        file_list=product_file_list,
    )
    sources = {
        src: entries for src, entries in sources.items()
        if src in (SOURCE_PRODUCT_INFO, SOURCE_STOCK)
    }

    if jst_entry is not None:
        jst_name, jst_df = jst_entry
        legacy_stock = sources.pop(SOURCE_STOCK, None) or []
        kept_legacy = []
        for leg_name, leg_df in legacy_stock:
            leg = leg_df.copy()
            for drop_col in ('在仓库存', '在途'):
                if drop_col in leg.columns:
                    leg = leg.drop(columns=[drop_col])
            if len(leg.columns) > 0:
                kept_legacy.append((leg_name, leg))
        sources[SOURCE_STOCK] = [(jst_name, jst_df)] + kept_legacy
        info.append({'name': jst_name, 'source': SOURCE_STOCK,
                     'rows': len(jst_df), 'cols': len(jst_df.columns),
                     'period': '共享维度'})
        total += 1

    if jst_refund_entry is not None:
        jr_name, jr_df = jst_refund_entry
        sources.setdefault(SOURCE_PRODUCT_INFO, []).append((jr_name, jr_df))
        info.append({'name': jr_name, 'source': SOURCE_PRODUCT_INFO,
                     'rows': len(jr_df), 'cols': len(jr_df.columns),
                     'period': '共享维度'})
        total += 1

    dimensions = build_shared_dimensions(sources)
    if cacheable:
        try:
            fingerprint_after_load = source_fingerprint(fingerprint_entries)
            if fingerprint_after_load == dimension_fingerprint:
                save_shared_dimension_cache(
                    cache_root, fingerprint_entries, dimensions, info, total,
                    verified_fingerprint=dimension_fingerprint,
                )
            else:
                print("[缓存] 共享维度源文件在读取期间发生变化，本次跳过缓存写入")
        except Exception as exc:
            print(f"[缓存] 共享维度缓存写入失败，继续使用本次计算结果：{exc}")
    return dimensions, (info, copied, total)


def _load_single_day(base_root, day, year, shop, product_folder, cache_root,
                     include_product, label_prefix, jst_entry=None, jst_refund_entry=None,
                     shared_dimensions=None, shared_dimension_meta=None,
                     cache_stats=None):
    """加载单日数据：扫描→读文件→合并→返回 day_df"""
    biz_folder, wujie_folder = _build_paths(base_root, day, year, shop)
    if not os.path.isdir(str(biz_folder)) and not os.path.isdir(str(wujie_folder)):
        return None, [], 0, 0, set(), {}

    day_cache = cache_root / day
    period_label = f"{label_prefix}:{day}"
    include_day_product = include_product and shared_dimensions is None
    legacy_file_list = _scan_target_files(biz_folder) + _scan_target_files(wujie_folder)
    if include_day_product:
        legacy_file_list += _scan_target_files(product_folder)
    file_list = [
        item for item in legacy_file_list
        if item[2] not in set(NEW_PRODUCT_SOURCE_TYPES)
    ]
    fingerprint_entries = [(fp, src_type) for fp, _fn, src_type in file_list]
    cacheable_facts = shared_dimensions is not None and bool(file_list)
    fact_fingerprint = None
    if cacheable_facts:
        try:
            fact_fingerprint = source_fingerprint(fingerprint_entries)
            cached_facts = load_daily_fact_cache(
                cache_root, day, fingerprint_entries,
                expected_fingerprint=fact_fingerprint,
            )
        except Exception:
            cached_facts = None
            cacheable_facts = False
    else:
        cached_facts = None

    if cached_facts is not None:
        base_df = cached_facts['base_df']
        day_plan_ids = set(cached_facts['plan_ids'])
        info = [dict(item, period=period_label) for item in cached_facts['files_info']]
        copied = 0
        total = cached_facts['total_files']
        sources = None
        if cache_stats is not None:
            cache_stats['hits'] = cache_stats.get('hits', 0) + 1
    else:
        if cacheable_facts and cache_stats is not None:
            cache_stats['misses'] = cache_stats.get('misses', 0) + 1
        sources, info, copied, total = _read_sources_for_date(
            biz_folder, wujie_folder, product_folder, day_cache,
            period_label,
            include_product=include_day_product,
            file_list=file_list,
        )

        # 聚水潭快照直连:在仓库存=款式级经营库存(多链接共享同一池)
        if shared_dimensions is None and include_product and jst_entry is not None:
            jst_name, jst_df = jst_entry
            legacy_stock = sources.pop(SOURCE_STOCK, None) or []
            kept_legacy = []
            for leg_name, leg_df in legacy_stock:
                leg = leg_df.copy()
                # 快照已提供款式级在仓/在途,剥离 legacy 中同名列避免 pid 级加总覆盖
                for drop_col in ('在仓库存', '在途'):
                    if drop_col in leg.columns:
                        leg = leg.drop(columns=[drop_col])
                if len(leg.columns) > 0:
                    kept_legacy.append((leg_name, leg))
            sources[SOURCE_STOCK] = [(jst_name, jst_df)] + kept_legacy
            info.append({'name': jst_name, 'source': SOURCE_STOCK,
                         'rows': len(jst_df), 'cols': len(jst_df.columns),
                         'period': period_label})

        # 聚水潭退款率直连:30日 cohort 退款率,注入商品资料源
        if shared_dimensions is None and include_product and jst_refund_entry is not None:
            jr_name, jr_df = jst_refund_entry
            sources.setdefault(SOURCE_PRODUCT_INFO, []).append((jr_name, jr_df))
            info.append({'name': jr_name, 'source': SOURCE_PRODUCT_INFO,
                         'rows': len(jr_df), 'cols': len(jr_df.columns),
                         'period': period_label})

        if not sources:
            return None, info, copied, total, set(), sources or {}

        if cacheable_facts:
            base_df, day_plan_ids = build_daily_facts(sources)
            try:
                fingerprint_after_load = source_fingerprint(fingerprint_entries)
                if fingerprint_after_load == fact_fingerprint:
                    save_daily_fact_cache(
                        cache_root, day, fingerprint_entries,
                        base_df, day_plan_ids, info, total,
                        verified_fingerprint=fact_fingerprint,
                    )
                else:
                    print(f"[缓存] 日源文件在读取期间发生变化，本次跳过缓存写入：{day}")
            except Exception as exc:
                print(f"[缓存] 日事实缓存写入失败，继续使用本次计算结果：{day} | {exc}")
        else:
            base_df = None
            day_plan_ids = set()

    if include_product and shared_dimension_meta is not None:
        shared_info, shared_copied, shared_total = shared_dimension_meta
        info.extend(shared_info)
        copied += shared_copied
        total += shared_total

    if shared_dimensions is not None:
        day_df, day_plan_ids = merge_daily_facts(
            base_df, day_plan_ids, shared_dimensions,
        )
    else:
        day_df, day_plan_ids = merge_sources(sources, dimensions=shared_dimensions)
    if day_df is None or len(day_df) == 0:
        return None, info, copied, total, set(day_plan_ids), sources or {}

    day_df = day_df.copy()
    day_df['_数据日期'] = day
    return day_df, info, copied, total, day_plan_ids, sources or {}


def load_period_data(days, day_map):
    """纯拼接函数：从 day_map 取预加载的数据，concat + aggregate。禁止 IO。"""
    started = time.perf_counter()
    loaded_dates, missing_dates, daily_frames = [], [], []
    plan_ids, files_info, copied_files, total_files = set(), [], 0, 0

    for day in days:
        entry = day_map.get(day)
        if entry is None:
            missing_dates.append(day)
            continue
        day_df, info, copied, total, pids = entry[:5]
        files_info.extend(info)
        copied_files += copied
        total_files += total
        if day_df is None:
            missing_dates.append(day)
            continue
        plan_ids.update(pids)
        daily_frames.append(day_df)
        loaded_dates.append(day)

    combined = (
        aggregate_period_frame(
            pd.concat(daily_frames, ignore_index=True),
            len(days),
            actual_days=len(loaded_dates),
            period_start=days[0] if days else '',
            period_end=days[-1] if days else '',
        )
        if daily_frames else pd.DataFrame()
    )

    return {
        'loaded_dates': loaded_dates,
        'missing_dates': missing_dates,
        'expected_days': len(days),
        'actual_days': len(loaded_dates),
        'df': combined,
        'plan_ids': plan_ids,
        'files_info': files_info,
        'copied_files': copied_files,
        'total_files': total_files,
        'elapsed': time.perf_counter() - started,
    }


@st.cache_data(show_spinner=False)
def _load_jst_refund_cached(path, mtime):
    return load_refund_rates(path)


@st.cache_data(show_spinner=False)
def _load_jst_snapshot_cached(path, mtime):
    """快照聚合结果缓存(mtime 参与 key,文件更新自动失效)。"""
    return load_snapshot_as_stock(path)


PERIOD_LABEL_MAP = {
    "当日": "日环比", "3天": "3日环比", "7天": "7日环比",
    "15天": "15日环比", "30天": "30日环比",
    # v2.66：按月 / 自定义（跨度 ≤ 31 天）
    "按月": MONTH_LABEL, "自定义": CUSTOM_LABEL,
}


def _cat_sum(frame, col):
    """品类级求和 → {类目原始值: 合计}。frame 为空/缺列时返回空 dict。"""
    if frame is None or len(frame) == 0 or "淘宝类目" not in frame.columns or col not in frame.columns:
        return {}
    w = frame[["淘宝类目", col]].copy()
    w[col] = pd.to_numeric(w[col], errors="coerce").fillna(0.0)
    return w.groupby("淘宝类目")[col].sum().to_dict()


def _load_folder_cached(base_root, year, shop, date_str, product_folder, cache_dir=None,
                        jst_snap_dir=None, jst_refund_dir=None, period_choice="当日",
                        period_month=None, period_custom=None):
    """读取当前日，并按日/3日/7日/15日/30日生成周期累计环比。一次扫描全部日期，不重复读文件。
    period_choice(2026-07-10 新增): 主表展示口径——当日=单日;3天/7天/15天/30天=对应周期
    current 窗口累计(与文档§五同一窗口定义),KPI/分层/品类分析随主表口径。"""
    timings = []
    period_logs = []
    t_total = time.perf_counter()
    cache_root = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / 'tgtool_cache'
    cache_root.mkdir(parents=True, exist_ok=True)

    # ── 0. 聚水潭快照直连(可选;取分析日快照,缺失回退最近≤7天) ──
    jst_entry = None
    jst_note = None
    snap_path = None
    if jst_snap_dir:
        t0 = time.perf_counter()
        snap_path, snap_date = find_snapshot(jst_snap_dir, date_str)
        if snap_path:
            jst_df = _load_jst_snapshot_cached(snap_path, os.path.getmtime(snap_path))
            jst_entry = (f"聚水潭快照_{snap_date}(款式级)", jst_df)
            jst_note = None if snap_date == date_str else \
                f"⚠️ 聚水潭快照缺 {date_str},已回退使用 {snap_date}"
            _log_time(timings, "聚水潭快照聚合", t0,
                      f"{snap_date} {len(jst_df)} 款式")
        else:
            jst_note = f"⚠️ 聚水潭快照目录中未找到 {date_str} 及其前7天的快照,本次不带库存"

    jst_refund_entry = None
    jst_refund_note = None
    refund_path = None
    if jst_refund_dir:
        t0 = time.perf_counter()
        refund_path, refund_date = find_refund_snapshot(jst_refund_dir, date_str)
        if refund_path:
            jr_df = _load_jst_refund_cached(refund_path, os.path.getmtime(refund_path))
            jst_refund_entry = (f"聚水潭退款率_{refund_date}(30日cohort)", jr_df)
            jst_refund_note = None if refund_date == date_str else \
                f"⚠️ 聚水潭退款率缺 {date_str},已回退使用 {refund_date}"

            _log_time(timings, "聚水潭退款率加载", t0,
                      f"{refund_date} {len(jr_df)} 链接")
        else:
            jst_refund_note = f"⚠️ 聚水潭退款率目录中未找到 {date_str} 及其前7天快照,本次退款率列为空(已统一聚水潭口径,不回退生意参谋)"

    dimension_fingerprint_entries = []
    if snap_path:
        dimension_fingerprint_entries.append((snap_path, 'jst_stock_snapshot'))
    if refund_path:
        dimension_fingerprint_entries.append((refund_path, 'jst_refund_snapshot'))
    shared_dimensions, shared_dimension_meta = _load_shared_dimensions(
        product_folder, cache_root, jst_entry=jst_entry,
        jst_refund_entry=jst_refund_entry,
        extra_fingerprint_entries=dimension_fingerprint_entries,
    )

    periods = build_period_ranges(
        date_str,
        month=period_month if period_choice == "按月" else None,
        custom=period_custom if period_choice == "自定义" else None,
    )
    sel_label = PERIOD_LABEL_MAP.get(period_choice, "日环比")
    comparison_labels = [sel_label] if sel_label in periods else ["日环比"]

    # ── 1. 收集所有需要的日期（去重）──
    t0 = time.perf_counter()
    all_needed_dates = {date_str}  # 本期当天
    # 日销/花费滚动字段固定需要近15天；核心指标状态的同层基准固定读取过去28天。
    baseline_start = (pd.Timestamp(date_str) - pd.Timedelta(days=27)).date().isoformat()
    for d in iter_dates(baseline_start, date_str):
        all_needed_dates.add(d)
    # 预算决策角色层：固定近7日基准 + 覆盖法阈值需要 8 个完整 7 天窗口（=56 天）。
    # v2.54：阈值已由运营冻结时，本轮不需要现场算引擎建议值——引擎建议改由
    # 「🔒 阈值冻结 → 🔍 主推线复核」按钮读轻量 GMV 缓存现算（毫秒级），
    # 所以这里不再补 56 天，只保留同层基准需要的 28 天（判定基准 14 天已在其内）。
    from core.budget_role import THRESHOLD_WINDOW_DAYS, THRESHOLD_WINDOWS

    role_history_days = int(THRESHOLD_WINDOW_DAYS) * int(THRESHOLD_WINDOWS)
    frozen_thresholds = None
    if year is not None and shop:
        try:
            frozen_thresholds = _threshold_freeze_store().get(year, shop)
        except Exception as exc:  # pragma: no cover - 登记读失败按未冻结处理
            print(f"[角色层] 阈值冻结登记读取失败，按未冻结加载 56 天：{exc}")
    role_history_note = f"阈值历史 {role_history_days} 天（未冻结，需现场算引擎建议）"
    if frozen_thresholds:
        role_history_note = (
            f"阈值已冻结 → 跳过 {role_history_days} 天历史（复核走轻量 GMV 缓存）"
        )
    else:
        role_history_start = (
            pd.Timestamp(date_str) - pd.Timedelta(days=role_history_days - 1)
        ).date().isoformat()
        for d in iter_dates(role_history_start, date_str):
            all_needed_dates.add(d)
    rolling_start = periods.get("15日环比", {}).get("current_start", date_str)
    for d in iter_dates(rolling_start, date_str):
        all_needed_dates.add(d)
    for label in comparison_labels:
        p = periods[label]
        for d in iter_dates(p['current_start'], p['current_end']):
            all_needed_dates.add(d)
        for d in iter_dates(p['prev_start'], p['prev_end']):
            all_needed_dates.add(d)
    _log_time(timings, "收集所有日期", t0,
              f"{len(all_needed_dates)} 个不同日期 | {role_history_note}")

    # ── 2. 一次性加载每个日期的数据（仅 include_product=True 的首日）──
    t0 = time.perf_counter()
    day_map = {}   # {date: (day_df, info, copied, total, plan_ids)}
    first_day_sources = None  # 本期当天的 sources（用于 includes_product）
    sorted_needed_dates = sorted(all_needed_dates)
    day_cache_stats = {'hits': 0, 'misses': 0}
    load_progress = st.progress(0, text=f"准备读取 {len(sorted_needed_dates)} 天数据...")
    for idx, day in enumerate(sorted_needed_dates, start=1):
        include_product = (day == date_str)  # 只有本期首日加载商品资料
        entry = _load_single_day(
            base_root, day, year, shop, product_folder, cache_root,
            include_product, '周期', jst_entry=jst_entry, jst_refund_entry=jst_refund_entry,
            shared_dimensions=shared_dimensions,
            shared_dimension_meta=shared_dimension_meta,
            cache_stats=day_cache_stats,
        )
        day_map[day] = entry
        if include_product and entry[0] is not None:
            first_day_sources = entry
        load_progress.progress(
            idx / max(len(sorted_needed_dates), 1),
            text=f"读取文件中 {idx}/{len(sorted_needed_dates)}：{day}"
        )
    load_progress.empty()
    _log_time(
        timings, "批量加载所有日期", t0,
        f"{len(day_map)} 天 | 日事实缓存命中 {day_cache_stats['hits']} / 未命中 {day_cache_stats['misses']}",
    )

    # ── 2b. 沉淀角色层轻量历史缓存（阈值引擎与看板趋势用；失败不阻断）──
    t0 = time.perf_counter()
    if cache_root is not None and year is not None and shop:
        try:
            from core.role_history_cache import update_gmv_daily

            gmv_cache_stats = update_gmv_daily(cache_root, year, shop, day_map)
            _log_time(
                timings, "角色层轻量GMV缓存", t0,
                f"本次更新 {gmv_cache_stats['本次新增/更新天数']} 天 | "
                f"缓存共 {gmv_cache_stats['缓存总天数']} 天 "
                f"({gmv_cache_stats['缓存最早日']}~{gmv_cache_stats['缓存最新日']})",
            )
        except Exception as exc:  # pragma: no cover - 缓存失败不影响主流程
            print(f"[角色层] 轻量 GMV 缓存写入失败（不影响本次结果）：{exc}")

    # ── 3. 本期当天 ──
    current_days = list(iter_dates(date_str, date_str))
    cur_result = load_period_data(current_days, day_map)
    df = cur_result['df']
    if df is None or len(df) == 0:
        return (
            None, None, [], set(), set(), timings, period_logs, {}, pd.DataFrame(),
            _empty_new_product_context('未找到本期商品数据，无法生成新品视图。'), {},
        )

    files_info = first_day_sources[1] if first_day_sources else []
    plan_ids = cur_result['plan_ids']

    # ── 4. 各周期环比（纯拼接，不读文件）──
    sel_prev_frame = None
    sel_prev_plan_ids = set()
    comparisons = {}
    for label in comparison_labels:
        p = periods[label]
        t_loop = time.perf_counter()
        cur_days = list(iter_dates(p['current_start'], p['current_end']))
        prev_days = list(iter_dates(p['prev_start'], p['prev_end']))
        cur_result_p = load_period_data(cur_days, day_map)
        prev_result_p = load_period_data(prev_days, day_map)
        comparisons[label] = {
            'current': cur_result_p['df'],
            'previous': prev_result_p['df'],
        }
        if label == sel_label:
            sel_prev_frame = prev_result_p['df']
            sel_prev_plan_ids = prev_result_p['plan_ids']
            if period_choice != "当日" and cur_result_p['df'] is not None \
                    and len(cur_result_p['df']) > 0:
                # 主表切换为所选周期的累计口径(单日帧仅作回退)
                df = cur_result_p['df']
                plan_ids = cur_result_p['plan_ids']
        period_logs.append({
            '周期': label,
            '范围': f"当前 {p['current_start']} ~ {p['current_end']}；对比 {p['prev_start']} ~ {p['prev_end']}",
            '完整性': f"当前 {cur_result_p['actual_days']}/{cur_result_p['expected_days']} 天；"
                    f"对比 {prev_result_p['actual_days']}/{prev_result_p['expected_days']} 天",
            '已读取日期': f"当前: {', '.join(cur_result_p['loaded_dates']) or '无'} | "
                      f"对比: {', '.join(prev_result_p['loaded_dates']) or '无'}",
            '缺失日期': f"当前: {', '.join(cur_result_p['missing_dates']) or '无'} | "
                    f"对比: {', '.join(prev_result_p['missing_dates']) or '无'}",
            '耗时': f"{(time.perf_counter() - t_loop):.2f} 秒",
        })
        for missing_day in cur_result_p['missing_dates'] + prev_result_p['missing_dates']:
            print(f"[WARNING] 未找到 {missing_day} 数据，已跳过该日期。")

    t0 = time.perf_counter()
    t_step = time.perf_counter()
    df = apply_period_comparisons(df, comparisons)
    _log_time(timings, "环比字段写回", t_step, f"{len(comparisons)} 个周期")
    t_step = time.perf_counter()
    df = apply_rolling_daily_metrics(df, day_map, date_str)
    _log_time(timings, "滚动日销花费计算", t_step, "3/7/15 天")
    t_step = time.perf_counter()
    df = apply_metric_labels(df, day_map, date_str)
    _log_time(timings, "核心指标状态打标", t_step, "28日同层基准")
    t_step = time.perf_counter()
    df = apply_metox_algorithm(df, day_map, date_str)
    _log_time(timings, "MetoX算法打标", t_step, f"{len(df)} 行")
    # 预算决策角色层：固定近7日判定基准 + 覆盖法阈值（失败自动跳过，不影响既有结果）
    t_step = time.perf_counter()
    df = _attach_budget_role_basis(
        df, day_map, date_str, year=year, shop=shop, cache_root=cache_root,
    )
    _log_time(timings, "角色层基准+阈值", t_step, "固定近7日 + 覆盖法(7天×8窗)")
    # 链接数:当前表内同一货号(款式)的链接个数——多链接共享同一款式库存池,
    # 该列标出分流规模;货号为空的行留空(无法判定款式归属)
    t_step = time.perf_counter()
    if '货号' in df.columns and '宝贝ID' in df.columns:
        _sku = df['货号'].astype(str).str.strip().replace({'nan': ''})
        _cnt = (df.assign(_sku=_sku).loc[_sku.ne('')]
                .groupby('_sku')['宝贝ID'].transform('nunique'))
        df['链接数'] = _cnt.astype('Int64')
    if period_choice != "当日":
        p_sel = periods[sel_label]
        df['统计日期'] = f"{p_sel['current_start']}~{p_sel['current_end']}"
    _log_time(timings, "链接数/日期整理", t_step, f"{len(df)} 行")
    _log_time(timings, "周期环比+滚动+MetoX合计", t0, f"{len(comparisons)} 个周期")
    _log_time(timings, "总计", t_total, f"{len(day_map)} 天数据")

    if jst_note:
        period_logs.append({'周期': '聚水潭', '范围': jst_note, '完整性': '',
                            '已读取日期': '', '缺失日期': '', '耗时': ''})
    if jst_refund_note:
        period_logs.append({'周期': '聚水潭退款率', '范围': jst_refund_note, '完整性': '',
                            '已读取日期': '', '缺失日期': '', '耗时': ''})

    # 品类环比上下文:所选周期付费环比 + 固定7日品类投放指标。
    # 直接从已加载日帧按品类汇总，避免为非7日选择额外执行两次商品级周期聚合。
    h7_period = periods["7日环比"]
    h7_current = aggregate_category_metrics(
        day_map,
        iter_dates(h7_period["current_start"], h7_period["current_end"]),
        ["GMV", "商品访客数", "商品加购件数", "支付买家数"],
    )
    h7_previous = aggregate_category_metrics(
        day_map,
        iter_dates(h7_period["prev_start"], h7_period["prev_end"]),
        ["GMV", "付费", "关键词", "人群", "全站", "商品访客数", "商品加购件数", "支付买家数"],
    )
    cat_ctx = {
        'period_choice': period_choice,
        'sel_label': sel_label,
        'sel_prev_spend': _cat_sum(sel_prev_frame, '付费'),
        'h7_cur_gmv': h7_current['GMV'],
        'h7_prev_gmv': h7_previous['GMV'],
        'h7_cur_visitors': h7_current['商品访客数'],
        'h7_prev_visitors': h7_previous['商品访客数'],
        'h7_cur_carts': h7_current['商品加购件数'],
        'h7_prev_carts': h7_previous['商品加购件数'],
        'h7_cur_buyers': h7_current['支付买家数'],
        'h7_prev_buyers': h7_previous['支付买家数'],
        'h7_prev_spend': h7_previous['付费'],
        'h7_prev_keyword': h7_previous['关键词'],
        'h7_prev_crowd': h7_previous['人群'],
        'h7_prev_omni': h7_previous['全站'],
    }
    kpi_trends = build_daily_kpi_trends(day_map, end_date=date_str)
    # v2.54：冻结后主流程只加载 28 天，看板日趋势图靠逐日 KPI 缓存补回历史 56 天。
    if cache_root is not None and year is not None and shop and not kpi_trends.empty:
        try:
            from core.role_history_cache import merge_kpi_trend, update_kpi_daily

            update_kpi_daily(cache_root, year, shop, kpi_trends)
            kpi_trends = merge_kpi_trend(kpi_trends, cache_root, year, shop)
        except Exception as exc:  # pragma: no cover - 趋势缓存失败不影响主流程
            print(f"[看板] 日趋势缓存读写失败（不影响本次结果）：{exc}")
    current_sources = first_day_sources[5] if first_day_sources and len(first_day_sources) > 5 else {}
    new_product_load_state = _build_new_product_load_state(
        base_root=base_root,
        year=year,
        shop=shop,
        date_str=date_str,
        cache_root=cache_root,
        product_folder=product_folder,
        files_info=files_info,
        cat_ctx=cat_ctx,
        sources=current_sources,
        day_map=day_map,
        shared_dimensions=shared_dimensions,
    )

    return (
        df, sel_prev_frame, files_info, plan_ids, sel_prev_plan_ids,
        timings, period_logs, cat_ctx, kpi_trends,
        _new_product_unloaded_context(), new_product_load_state,
    )


# ── 缓存：分层（分界点变化时重新计算）─────────────────────
# 展示/导出保留2位小数的列(2026-07-11 运营要求;链接投产/全站环比为同族列一并纳入)
ROUND2_COLS = [
    '日销3', '日销7', '日销15', '日均访客3', '日均访客7', '日均访客15',
    '花费3', '花费7', '花费15',
    '预估利润', '推广投产', '链接投产', '日均消耗', '利润率',
    '人群环比', '关键词环比', '全站环比',
    '推广实际投产', '链接实际投产', '购物车价值', '件单价', '倍率',
]
RATE4_COLS = ['商品加购率', '商品支付转化率', '推广加购率']


def _finalize_display_columns(df):
    """补齐文档承诺但在聚合/分层链路中丢失的列(2026-07-10,闭口缺陷 F2/F3):
    - 三排名(销售/流量/消耗,全表 min 并列名次);
    - 三渠道环比((当期-上周期基准)/基准,基准0记0);
    - 双转化率口径:转化率=支付买家数/点击(CVR),商品支付转化率=支付买家数/商品访客数
      (此前 standardize_fields 的列名吞并使两口径只剩一列且语义与文档相反)。
    必须在 segment_products 之后调用——分层内部的列名标准化会再次吞并转化率列。"""
    df = df.copy()

    def _n(col):
        if col not in df.columns:
            return pd.Series(0.0, index=df.index)
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    buyers, clicks, visitors = _n("支付买家数"), _n("点击"), _n("商品访客数")
    df["转化率"] = (buyers / clicks.where(clicks != 0, 1)).where(clicks != 0, 0.0)
    df["商品支付转化率"] = (buyers / visitors.where(visitors != 0, 1)).where(visitors != 0, 0.0)
    for ch in ["人群", "关键词", "全站"]:
        cur, base = _n(ch), _n(f"上周期{ch}")
        df[f"{ch}环比"] = ((cur - base) / base.where(base != 0, 1)).where(base != 0, 0.0)
    if len(df) > 0:
        df["销售排名"] = _n("GMV").rank(ascending=False, method="min").astype(int)
        df["流量排名"] = visitors.rank(ascending=False, method="min").astype(int)
        df["消耗排名"] = _n("付费").rank(ascending=False, method="min").astype(int)
    # 统一2位小数(round 保留 NaN,不破坏"空=无数据"语义;重算分层时重复 round 幂等)
    for col in ROUND2_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)
    for col in RATE4_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(4)
    return df


def _apply_segmentation(df, t_bounds, v_bounds):
    """应用分层（非缓存，因为分界点可能变化）+ 展示列补齐"""
    return _finalize_display_columns(segment_products(df.copy(), t_bounds, v_bounds))


# ── 路径配置 ─────────────────────────────────────────────
DEFAULT_ROOT = r"\\10.1.22.253\天猫mark"
DEFAULT_YEAR = "2026"
DEFAULT_SHOP = "女装店"

# 项目根目录（app.py 所在目录），所有本地缓存都放在这里
APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "cache"
CACHE_RESULT_DIR = APP_DIR / "cache_result"
COLUMN_PREFS_PATH = CACHE_DIR / "column_prefs.json"
BUDGET_DECISION_CONTEXT_CACHE_VERSION = "budget_decision_context_v3_roles"
BUDGET_RANK_BAND_COLUMN = "排名档"
BUDGET_RANK_BAND_LEGACY_COLUMNS = ("销售梯队", "梯队", "层级")
BUDGET_RANK_BAND_VALUE_MAP = {
    "S1": "Top1-5",
    "S2": "Top6-10",
    "S3": "Top11-20",
    "S4": "Top21-50",
    "S5": "Top51-100",
    "S6": "Top101-尾部",
    "Top101以后": "Top101-尾部",
    "Top101后": "Top101-尾部",
    "Top101以上": "Top101-尾部",
}
# 展示前置列留空：商品预算决策的完整列顺序由 core.budget_decision.PRODUCT_COLUMNS 统一决定，
# 避免两处各写一份顺序导致漂移。保持普通赋值（测试的 app 助手加载器按 ast.Assign 提取常量）。
BUDGET_DECISION_LEADING_COLUMNS = []
BUDGET_DECISION_HIDDEN_COLUMNS = {
    "预算风险", "阈值状态", "阈值窗口", "阈值周数", "阈值版本",
    # TH_MAIN / TH_MID 是店铺级常量（每行都一样），不必在列里重复展现；导出仍保留供审计
    "TH_MAIN", "TH_MID",
    # 角色闸门命中只在"原本要加投却被角色层拦下"时才有值；
    # 逐行信号已由 预算匹配状态=角色闸门拦截(角色名) 承载，这里不再单列
    "角色闸门命中",
    "D3商品整体信号",
}
BUDGET_DECISION_HIDDEN_COLUMN_TOKENS = (
    "阈值", "门槛", "threshold",
)


_LEGACY_METOX_PREFIX = "v" + "9" + "." + "3"
DISPLAY_RENAME_MAP = {
    "MetoX" + "信号": "v9.3信号",
    f"{_LEGACY_METOX_PREFIX}算法命中": "MetoX算法命中",
    f"{_LEGACY_METOX_PREFIX}算法分": "MetoX算法分",
    f"{_LEGACY_METOX_PREFIX}算法原因": "MetoX算法原因",
    f"{_LEGACY_METOX_PREFIX}推广花费": "MetoX推广花费",
    f"{_LEGACY_METOX_PREFIX}推广成交金额": "MetoX推广成交金额",
    f"{_LEGACY_METOX_PREFIX}推广加购数": "MetoX推广加购数",
    f"{_LEGACY_METOX_PREFIX}推广访客数": "MetoX推广访客数",
}


def _normalize_display_columns(obj):
    """兼容旧会话/旧列偏好:区分外部 v9.3 信号与内置 MetoX 算法。"""
    if obj is None:
        return obj
    if isinstance(obj, pd.DataFrame):
        rename = {old: new for old, new in DISPLAY_RENAME_MAP.items() if old in obj.columns}
        out = obj.rename(columns=rename) if rename else obj
        if "v9.3信号" not in out.columns:
            out = out.copy()
            out["v9.3信号"] = ""
        return out
    if isinstance(obj, list):
        return [DISPLAY_RENAME_MAP.get(c, c) for c in obj]
    return obj


def _render_new_product_view(new_product_ctx):
    ctx = new_product_ctx if isinstance(new_product_ctx, dict) else _empty_new_product_context()
    status = str(ctx.get('status', 'unavailable'))
    message = str(ctx.get('message') or '').strip()

    header_left, header_right = st.columns([5, 1])
    with header_left:
        st.subheader("新品视图")
    load_state = st.session_state.get('new_product_load_state')
    can_load_history = isinstance(load_state, dict) and bool(load_state.get('date_str'))
    history_loaded = st.session_state.get('new_product_history') is not None \
        or status not in ('unloaded', 'unavailable')
    button_label = '刷新新品历史' if history_loaded else '加载新品历史'
    with header_right:
        load_clicked = st.button(
            button_label,
            key='load_new_product_history_button',
            disabled=not can_load_history,
            width='stretch',
        )

    progress_placeholder = st.empty()
    if load_clicked:
        progress_bar = st.progress(0.0, text='准备加载新品历史...')

        def update_progress(value, text):
            st.session_state.new_product_progress = text
            progress_bar.progress(value, text=text)
            progress_placeholder.caption(text)

        try:
            history, loaded_ctx = _load_new_product_context_from_state(
                st.session_state.get('df'),
                load_state,
                progress_callback=update_progress,
            )
            st.session_state.new_product_history = history
            st.session_state.new_product_ctx = loaded_ctx
            ctx = loaded_ctx if isinstance(loaded_ctx, dict) else _empty_new_product_context()
        except Exception as exc:
            ctx = _empty_new_product_context(
                f'新品历史加载失败：{str(exc)[:160]}',
                status='error',
            )
            st.session_state.new_product_history = None
            st.session_state.new_product_ctx = ctx
            update_progress(1.0, '新品历史加载失败')
        status = str(ctx.get('status', 'unavailable'))
        message = str(ctx.get('message') or '').strip()

    st.caption(
        "只读MVP：按首次在线发现日计算新品年龄，D15起至D45每天更新当前结果；"
        "D1至分析日累计访客>=300为已验证，D45仍不足300且全程无成交则下架；"
        "D45内任意一天有成交均进入动销保护。"
    )
    if message:
        if status in ('complete', 'unloaded'):
            st.info(message)
        elif status == 'error':
            st.error(message)
        else:
            st.warning(message)

    source_status = ctx.get('source_status')
    if source_status is None or (isinstance(source_status, pd.DataFrame) and source_status.empty):
        coverage = ctx.get('coverage')
        source_status = coverage.copy() if isinstance(coverage, pd.DataFrame) else pd.DataFrame(coverage or [])
    if isinstance(source_status, pd.DataFrame) and not source_status.empty:
        st.markdown("#### 新品源覆盖")
        st.dataframe(source_status, width="stretch", hide_index=True)

    lifecycle = _new_product_context_frame(
        ctx, ('lifecycle', 'decision', 'decisions', '新品判定'), NEW_PRODUCT_LIFECYCLE_COLUMNS
    )
    materials = _new_product_context_frame(
        ctx, ('materials', 'material', 'creative', '素材表现'), NEW_PRODUCT_MATERIAL_COLUMNS
    )
    backtest = _new_product_context_frame(
        ctx, ('backtest', 'validation', '回测验证'), NEW_PRODUCT_BACKTEST_COLUMNS
    )
    methodology = _new_product_context_frame(
        ctx, ('methodology', '口径说明'), ['主题', '口径', '说明']
    )

    view_tab1, view_tab2, view_tab3, view_tab4 = st.tabs(
        ["生命周期判定", "素材表现", "历史回放", "新品变化"]
    )

    with view_tab1:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("有效新品数", len(lifecycle))
        c2.metric("D15数量", int((lifecycle.get('判定轮次') == 'D15').sum()) if not lifecycle.empty else 0)
        c3.metric("D30数量", int((lifecycle.get('判定轮次') == 'D30').sum()) if not lifecycle.empty else 0)
        c4.metric("下架建议", int((lifecycle.get('当前结果') == '下架').sum()) if not lifecycle.empty else 0)
        c5.metric("继续观察", int((lifecycle.get('当前结果') == '继续观察').sum()) if not lifecycle.empty else 0)
        c6.metric(
            "样本不足",
            int(lifecycle.get('样本充分性', pd.Series(dtype=object)).astype(str).str.contains('不足', na=False).sum())
            if not lifecycle.empty else 0,
        )
        if lifecycle.empty:
            if status == 'unloaded':
                st.info(NEW_PRODUCT_UNLOADED_MESSAGE)
            else:
                st.info("暂无可判定新品。请确认商品-整体历史源、货号映射和经营日事实覆盖。")
        else:
            f1, f2, f3, f4 = st.columns(4)
            filtered_lifecycle = lifecycle.copy()
            with f1:
                result_filter = st.multiselect(
                    "判定结果",
                    sorted(filtered_lifecycle['当前结果'].dropna().astype(str).unique()),
                    key='new_product_result_filter',
                ) if '当前结果' in filtered_lifecycle.columns else []
            with f2:
                checkpoint_filter = st.multiselect(
                    "判定轮次",
                    sorted(filtered_lifecycle['判定轮次'].dropna().astype(str).unique()),
                    key='new_product_checkpoint_filter',
                ) if '判定轮次' in filtered_lifecycle.columns else []
            with f3:
                reason_filter = st.multiselect(
                    "判定原因",
                    sorted(filtered_lifecycle['判定原因'].dropna().astype(str).unique()),
                    key='new_product_reason_filter',
                ) if '判定原因' in filtered_lifecycle.columns else []
            with f4:
                category_filter = st.multiselect(
                    "淘宝类目",
                    sorted(filtered_lifecycle['淘宝类目'].dropna().astype(str).unique()),
                    key='new_product_category_filter',
                ) if '淘宝类目' in filtered_lifecycle.columns else []
            for selected, column in (
                (result_filter, '当前结果'),
                (checkpoint_filter, '判定轮次'),
                (reason_filter, '判定原因'),
                (category_filter, '淘宝类目'),
            ):
                if selected and column in filtered_lifecycle.columns:
                    filtered_lifecycle = filtered_lifecycle[
                        filtered_lifecycle[column].astype(str).isin(selected)
                    ]
            st.caption(f"筛选结果：{len(filtered_lifecycle)} / {len(lifecycle)} 个款式")
            st.dataframe(
                filtered_lifecycle,
                width="stretch",
                hide_index=True,
                height=620,
                column_config={
                    '款式主图': st.column_config.LinkColumn('款式主图链接'),
                    '图片': st.column_config.ImageColumn('图片'),
                    '日均访客': st.column_config.NumberColumn('日均访客', format='%.2f'),
                    '日均GMV': st.column_config.NumberColumn('日均GMV', format='¥%.2f'),
                    'D1_D30_商品加购率': st.column_config.NumberColumn(
                        'D1_D30加购率', format='percent',
                    ),
                    'W2_商品加购率': st.column_config.NumberColumn('W2加购率', format="percent"),
                    'W4_商品加购率': st.column_config.NumberColumn('W4加购率', format="percent"),
                },
            )
            st.download_button(
                "下载新品判定CSV",
                data=_frame_to_csv_bytes(filtered_lifecycle),
                file_name=f"新品判定_{st.session_state.get('cur_date', '')}.csv",
                mime="text/csv",
                key="download_new_product_lifecycle_csv",
            )

    with view_tab2:
        if materials.empty:
            st.info("暂无素材表现。请确认无界-创意历史源及15天转化周期数据。")
        else:
            st.dataframe(
                materials,
                width="stretch",
                hide_index=True,
                height=620,
                column_config={
                    '无界创意CTR': st.column_config.NumberColumn('无界创意CTR', format="percent"),
                    '上一7天CTR': st.column_config.NumberColumn('上一7天CTR', format="percent"),
                    'CTR环比': st.column_config.NumberColumn('CTR环比', format="percent"),
                    '创意展现占比': st.column_config.NumberColumn('创意展现占比', format="percent"),
                },
            )
            st.download_button(
                "下载素材表现CSV",
                data=_frame_to_csv_bytes(materials),
                file_name=f"素材表现_{st.session_state.get('cur_date', '')}.csv",
                mime="text/csv",
                key="download_new_product_materials_csv",
            )

    with view_tab3:
        if backtest.empty:
            st.info("暂无回测验证结果。")
        else:
            st.dataframe(backtest, width="stretch", hide_index=True)
            st.download_button(
                "下载回测验证CSV",
                data=_frame_to_csv_bytes(backtest),
                file_name=f"回测验证_{st.session_state.get('cur_date', '')}.csv",
                mime="text/csv",
                key="download_new_product_backtest_csv",
            )
        if not methodology.empty:
            with st.expander("新品口径说明", expanded=False):
                st.dataframe(methodology, width="stretch", hide_index=True)

    with view_tab4:
        _render_new_product_change(ctx)


_CHANGE_BASELINE_OPTIONS = {
    '上次运行': None,
    '3 天前': 3,
    '7 天前（上周同期）': 7,
    '14 天前': 14,
}


def _render_new_product_change(ctx):
    """新品变化区（v2.54）：快照 + 差分，只展示有变化的款。"""
    from core import new_product_change as change_core

    meta = (ctx or {}).get('change_meta') or {}
    if not meta:
        st.info("暂无新品变化数据：请先点「加载新品历史」生成快照。")
        return
    if meta.get('状态') == '未生成' or meta.get('状态') == '生成失败':
        st.info(f"暂无新品变化数据：{meta.get('说明') or '快照尚未生成'}")
        return

    st.caption(
        "口径：新增发现拆「真上新 / 历史补发现」；消失＝上次在线且本次非在线或不再出现、"
        f"**连续 {change_core.GONE_CONFIRM_DAYS} 个自然日**；恢复在线＝以前发现过、上次不在线、本次重新在线"
        "（不算全新款）；判定结果（下架/继续观察）翻转不作为变化列。"
    )
    if meta.get('源可用') is False:
        st.warning(meta.get('说明') or '本次分析日的商品-整体源缺失，无法做变化判定（缺失不算消失）。')

    snapshots = change_core.load_snapshots(meta.get('快照路径'))
    current = meta.get('当前快照')
    if current is None and snapshots:
        current = snapshots[-1]
    if current is None:
        st.info(f"暂无新品变化数据：{meta.get('说明') or '快照尚未生成'}")
        return

    pick_cols = st.columns([1.4, 1.6, 3.6])
    with pick_cols[0]:
        baseline_label = st.selectbox(
            "对比基线", list(_CHANGE_BASELINE_OPTIONS), index=0,
            key='new_product_change_baseline',
        )
    baseline = change_core.select_baseline(
        snapshots, current.get('分析日'),
        days_back=_CHANGE_BASELINE_OPTIONS.get(baseline_label),
    )
    result = change_core.diff_snapshot(baseline, current)
    summary = result['summary']
    with pick_cols[1]:
        st.metric("对比区间", f"{result['上次快照日'] or '—'} → {result['本次快照日']}")
    with pick_cols[2]:
        st.caption(result['说明'])
        written = meta.get('快照写入') or {}
        st.caption(
            f"快照：共 {meta.get('快照数', 0)} 份（保留最近 {change_core.SNAPSHOT_KEEP} 份）｜"
            f"{'已写入本次' if written.get('写入') else '未写入本次'}：{written.get('原因', '')}"
        )

    metric_cols = st.columns(5)
    for column, kind in zip(metric_cols, (
        change_core.CHANGE_NEW, change_core.CHANGE_BACKFILL, change_core.CHANGE_GONE,
        change_core.CHANGE_BACK_ONLINE, change_core.CHANGE_WATCH,
    )):
        column.metric(kind, int(summary.get(kind, 0)))

    frame = change_core.change_frame(result['rows'])
    if frame.empty:
        st.info("本次没有新增/消失/恢复在线/观察中的变化款。")
        st.download_button(
            "下载新品变化CSV",
            data=_frame_to_csv_bytes(frame),
            file_name=f"新品变化_{st.session_state.get('cur_date', '')}.csv",
            mime="text/csv",
            key="download_new_product_change_csv",
        )
        return

    filter_cols = st.columns([2.4, 2.0])
    with filter_cols[0]:
        type_filter = st.multiselect(
            "变化类型",
            [kind for kind in change_core.CHANGE_TYPES if int(summary.get(kind, 0)) > 0],
            default=[kind for kind in change_core.CHANGE_TYPES
                     if kind != change_core.CHANGE_WATCH and int(summary.get(kind, 0)) > 0],
            key='new_product_change_type_filter',
        )
    with filter_cols[1]:
        category_filter = st.multiselect(
            "淘宝类目",
            sorted(frame['淘宝类目'].dropna().astype(str).unique()),
            key='new_product_change_category_filter',
        )
    display = frame.copy()
    if type_filter:
        display = display[display['变化类型'].isin(type_filter)]
    if category_filter:
        display = display[display['淘宝类目'].astype(str).isin(category_filter)]
    st.caption(f"筛选结果：{len(display)} / {len(frame)} 个变化款")
    st.dataframe(
        display, width="stretch", hide_index=True, height=620,
        column_config={
            '款式主图': st.column_config.LinkColumn('款式主图链接'),
            '图片': st.column_config.ImageColumn('图片'),
            '离线天数': st.column_config.NumberColumn('离线天数', format='%d'),
            '新品年龄': st.column_config.NumberColumn('新品年龄', format='%d'),
            '快照间隔天数': st.column_config.NumberColumn('快照间隔天数', format='%d'),
            '说明': st.column_config.TextColumn('说明', width='large'),
        },
    )
    st.download_button(
        "下载新品变化CSV",
        data=_frame_to_csv_bytes(display),
        file_name=f"新品变化_{st.session_state.get('cur_date', '')}.csv",
        mime="text/csv",
        key="download_new_product_change_csv",
    )


def _first_existing_dir(*candidates):
    """依次返回第一个存在的目录;都不存在返回空串(UI侧留空即跳过该数据源)。"""
    for cand in candidates:
        if cand and os.path.isdir(str(cand)):
            return str(cand)
    return ""


# 聚水潭直连默认路径(存在才预填;快照=SKU×仓级镜像,商品资料=pid↔款式编码映射)。
# 多机便携解析(2026-07-11),按序取第一个存在的目录:
#   1. 工具目录内 聚水潭/ —— 完全离线场景,把快照文件夹放到 exe 旁即用
#   2. 开发机本地全量历史 —— 仅开发机命中,保留旧行为
#   3. 共享盘 聚水潭/ —— 其他电脑零配置默认,快照由开发机每日同步
# 快照 CSV 由开发机经聚水潭数据库预先导出,工具运行时只读文件、无需联库/令牌
_JST_PORTABLE = APP_DIR / "聚水潭"
_JST_DEV = r"e:\08-推广\天猫运营方法论\聚水潭"
_JST_MARK0706 = Path(r"\\10.1.22.253\天猫mark\mark0706啊\聚水潭")
_JST_SHARE = r"\\10.1.22.253\天猫mark\聚水潭"
DEFAULT_JST_SNAP_DIR = _first_existing_dir(
    _JST_MARK0706 / "库存快照",
    _JST_PORTABLE / "库存快照",
    os.path.join(_JST_DEV, "库存快照"),
    os.path.join(_JST_SHARE, "库存快照"))
DEFAULT_JST_REFUND_DIR = _first_existing_dir(
    _JST_MARK0706 / "退款率快照",
    _JST_PORTABLE / "退款率快照",
    _JST_PORTABLE / "退款率",
    os.path.join(_JST_DEV, "退款率"),
    os.path.join(_JST_SHARE, "退款率"))
# 店铺商品资料由 NAS 每日生成；优先读取共享盘最新映射，随运行日自动更新。
DEFAULT_JST_PRODUCT_DIR = _first_existing_dir(
    _JST_MARK0706,
    _JST_PORTABLE,
    _JST_DEV,
    _JST_SHARE)
# v9.3 日报默认目录(共享盘每日复盘;按分析日自动匹配文件名含日期的 txt)
DEFAULT_V93_DIR = r"\\10.1.22.253\天猫mark\2026\女装店\明日之星-每日数据"


def _load_column_prefs():
    """读执行表列偏好(勾选过的展示列);无文件/损坏/与当前字段全不相交→None(全部列)。"""
    try:
        cols = json.loads(COLUMN_PREFS_PATH.read_text(encoding="utf-8"))
        cols = _normalize_display_columns(cols)
        cols = [c for c in cols if c in EXPORT_COLS]
        return cols or None
    except Exception:
        return None


def _save_column_prefs(cols):
    """列偏好持久化到 cache/column_prefs.json(EXE 重启后仍生效);写失败不阻断。"""
    try:
        COLUMN_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        cols = _normalize_display_columns(list(cols))
        COLUMN_PREFS_PATH.write_text(
            json.dumps(list(cols), ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _clean_text_value(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text in ("", "nan", "None", "<NA>", "NaT") else text


def _rank_band_value(value, gmv=None):
    text = _clean_text_value(value)
    if any(token in text for token in ("零GMV", "零成交", "零销售")):
        return "零GMV回收池"
    if text == "S6" and _as_float(gmv, default=0.0) <= 0:
        return "零GMV回收池"
    return BUDGET_RANK_BAND_VALUE_MAP.get(text, text)


def _normalize_rank_band_dataframe(frame):
    work = _as_dataframe(frame)
    legacy_source_column = next(
        (column for column in BUDGET_RANK_BAND_LEGACY_COLUMNS if column in work.columns),
        None,
    )
    if BUDGET_RANK_BAND_COLUMN not in work.columns:
        if legacy_source_column is None:
            return work
        work.insert(
            work.columns.get_loc(legacy_source_column),
            BUDGET_RANK_BAND_COLUMN,
            work[legacy_source_column],
        )
    elif legacy_source_column is not None:
        blank = work[BUDGET_RANK_BAND_COLUMN].map(_clean_text_value).eq("")
        work.loc[blank, BUDGET_RANK_BAND_COLUMN] = work.loc[blank, legacy_source_column]
    gmv_values = work["GMV"] if "GMV" in work.columns else pd.Series([None] * len(work), index=work.index)
    work[BUDGET_RANK_BAND_COLUMN] = [
        _rank_band_value(value, gmv)
        for value, gmv in zip(work[BUDGET_RANK_BAND_COLUMN], gmv_values)
    ]
    return work.drop(columns=list(BUDGET_RANK_BAND_LEGACY_COLUMNS), errors="ignore")



def _is_budget_decision_hidden_column(column):
    text = str(column)
    if is_removed_budget_column(column) or text in BUDGET_DECISION_HIDDEN_COLUMNS:
        return True
    return any(token in text for token in BUDGET_DECISION_HIDDEN_COLUMN_TOKENS)


def _dedupe_dataframe_columns(frame):
    work = _as_dataframe(frame)
    if getattr(work.columns, "has_duplicates", False):
        work = work.loc[:, ~work.columns.duplicated()].copy()
    return work


def _unique_budget_decision_columns(*column_groups):
    columns = []
    seen = set()
    for group in column_groups:
        for column in group:
            key = str(column)
            if key in seen:
                continue
            seen.add(key)
            columns.append(column)
    return columns


def _threshold_freeze_store():
    """阈值冻结登记：默认落在仓库内 config/（随 git 走），无仓库时回退 cache/。"""
    from core.threshold_store import ThresholdFreezeStore, default_freeze_root

    return ThresholdFreezeStore(default_freeze_root(app_dir=APP_DIR))


def _threshold_freeze_git_status():
    from core.threshold_store import registry_git_status

    try:
        return registry_git_status(app_dir=APP_DIR)
    except Exception as exc:  # pragma: no cover
        return {'in_repo': False, '错误': str(exc), 'path': '', 'relative_path': ''}


def _threshold_freeze_after_change(current, *, action='冻结', commit: bool, push: bool) -> str:
    """冻结/清除登记后：可选把登记提交到 git（提交信息带操作人）。返回提示文案。"""
    from core.threshold_store import commit_freeze_registry, freeze_commit_message

    if not commit:
        return ''
    result = commit_freeze_registry(
        app_dir=APP_DIR,
        message=freeze_commit_message(current, action=action),
        push=push,
    )
    if result.get('ok') and result.get('commit'):
        text = f"登记已提交 git：{result['commit']}"
        if push and result.get('pushed'):
            text += f"（已推送）"
        elif push:
            text += f"（推送未成功：{result.get('detail', '')[:120]}）"
        return text
    return f"登记未提交 git：{result.get('detail', '')}"


def _attach_budget_role_basis(df, day_map, date_str, year=None, shop=None, cache_root=None):
    """把「固定近7日判定基准 + 生效阈值」写进执行表，供预算决策的角色层使用。

    * 判定基准固定近 7 日，与左侧「时间周期」（当日/3天/7天/15天/30天）无关；
    * 阈值由**运营人员冻结**：登记里冻结值优先；没有冻结时用引擎建议值并标记未冻结；
    * 引擎建议值（7天窗口 × 8窗中位数）的取数优先级（v2.54）：
      1. 本次已加载满 56 天 → 直接用本次日事实；
      2. 否则读轻量 GMV 缓存（冻结后主流程只加载 28 天，复核与漂移监控走缓存）；
      3. 两者都不可用 → 引擎无效（`阈值状态=数据不足`），由面板提示去复核。
    * 写入的列：近7日日均GMV、上一7日日均GMV、GMV趋势率，以及 阈值* / TH_MAIN / TH_MID。
      阈值* 列在预算决策视图里默认隐藏，也不会进入执行表（执行表按 EXPORT_COLS 白名单展示）。

    任何一步失败都只回退到"没有阈值"，由角色层返回「未判定 + 放行加投」，
    不影响既有预算决策结果。
    """
    if df is None or len(df) == 0:
        return df
    try:
        from core.budget_role import (
            THRESHOLD_WINDOW_DAYS,
            THRESHOLD_WINDOWS,
            compute_coverage_thresholds_from_day_map,
            compute_coverage_thresholds_from_gmv_daily,
            inject_rolling_daily_gmv,
            period_windows,
            resolve_thresholds,
            role_input_check,
            threshold_columns_from_payload,
        )

        work = inject_rolling_daily_gmv(df, day_map, end_date=date_str)
        engine, engine_source, coverage = _resolve_engine_thresholds(
            day_map, date_str, year, shop, cache_root,
            window_days=int(THRESHOLD_WINDOW_DAYS), windows=int(THRESHOLD_WINDOWS),
        )
        frozen = None
        if year is not None and shop:
            try:
                frozen = _threshold_freeze_store().get(year, shop)
            except Exception as exc:  # pragma: no cover - 登记读失败不阻断
                print(f"[WARNING] 阈值冻结登记读取失败，按未冻结处理：{exc}")
        payload = resolve_thresholds(engine, frozen)
        for column, value in threshold_columns_from_payload(payload).items():
            work[column] = value
        # 供「阈值冻结」面板展示引擎建议值（不必重新读数据）
        st.session_state['threshold_engine_snapshot'] = {
            'TH_MAIN': engine.th_main,
            'TH_MID': engine.th_mid,
            '阈值参照日': engine.end_date,
            '阈值周数': engine.weeks,
            '阈值状态': engine.state,
            '阈值版本': engine.version,
            '引擎建议来源': engine_source,
            '轻量缓存覆盖': coverage,
            '参考日窗口分界': [
                {'周次': row.get('周次'), '起始日': row.get('起始日'), '结束日': row.get('结束日'),
                 'TH_MAIN': row.get('TH_MAIN'), 'TH_MID': row.get('TH_MID')}
                for row in engine.weekly
            ],
            '警告': list(engine.warnings),
        }
        check = role_input_check(work)
        if engine.warnings:
            print(f"[角色层] 阈值提示：{'；'.join(engine.warnings)}")
        if payload.get('阈值复核提示'):
            print(f"[角色层] {payload['阈值复核提示']}")
        if not check.get('ready'):
            print(f"[角色层] 输入自检警告：{'；'.join(check.get('warnings', ()))}")
        return work
    except Exception as exc:  # pragma: no cover - 兜底，绝不阻断主流程
        print(f"[WARNING] 角色层基准/阈值计算失败，已跳过（角色层将不介入）：{exc}")
        return df


def _day_map_covers_role_history(day_map, date_str, *, window_days, windows) -> bool:
    """本次加载的日事实是否已覆盖 8 个完整窗口（缺一天就为 False）。"""
    from core.budget_role import period_windows

    if not day_map:
        return False
    try:
        anchor = pd.Timestamp(str(date_str)[:10]).date()
    except Exception:
        return False
    for start, end in period_windows(anchor, window_days, windows):
        for day in iter_dates(start.isoformat(), end.isoformat()):
            entry = day_map.get(day)
            frame = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            if frame is None or len(frame) == 0:
                return False
    return True


def _resolve_engine_thresholds(day_map, date_str, year, shop, cache_root, *,
                               window_days, windows):
    """按「本次日事实 → 轻量 GMV 缓存 → 不可用」的顺序解析引擎建议阈值。"""
    from core.budget_role import (
        compute_coverage_thresholds_from_day_map,
        compute_coverage_thresholds_from_gmv_daily,
    )

    if _day_map_covers_role_history(
        day_map, date_str, window_days=window_days, windows=windows,
    ):
        return (
            compute_coverage_thresholds_from_day_map(
                day_map, end_date=date_str, window_days=window_days, windows=windows,
            ),
            f'本次加载的 {window_days * windows} 天日事实',
            {},
        )
    coverage = {}
    if cache_root is not None and year is not None and shop:
        try:
            from core.role_history_cache import gmv_daily_coverage, load_gmv_daily

            gmv_daily = load_gmv_daily(cache_root, year, shop)
            coverage = gmv_daily_coverage(
                gmv_daily, date_str, window_days=window_days, windows=windows,
            )
            if gmv_daily:
                engine = compute_coverage_thresholds_from_gmv_daily(
                    gmv_daily, end_date=date_str,
                    window_days=window_days, windows=windows,
                )
                label = '轻量GMV缓存' if coverage.get('可用') else (
                    f"轻量GMV缓存（缺 {len(coverage.get('缺失天数') or [])} 天）"
                )
                return engine, label, coverage
        except Exception as exc:  # pragma: no cover - 缓存读失败退回不可用
            print(f"[角色层] 轻量 GMV 缓存读取失败：{exc}")
    return (
        compute_coverage_thresholds_from_day_map(
            day_map, end_date=date_str, window_days=window_days, windows=windows,
        ),
        '本次加载的日事实（不足 56 天，引擎建议可能不完整）',
        coverage,
    )


def _threshold_freeze_days(frozen: dict | None) -> int | None:
    """冻结已过天数（按「生效日」yyyymmdd，缺失时用「冻结时间」）。"""
    if not frozen:
        return None
    raw = str(frozen.get('生效日') or '').strip()
    stamp = None
    if len(raw) == 8 and raw.isdigit():
        stamp = pd.Timestamp(f'{raw[:4]}-{raw[4:6]}-{raw[6:]}')
    else:
        text = str(frozen.get('冻结时间') or '').strip()[:10]
        try:
            stamp = pd.Timestamp(text)
        except Exception:
            stamp = None
    if stamp is None or pd.isna(stamp):
        return None
    return max(0, int((pd.Timestamp.today().normalize() - stamp.normalize()).days))


def _role_counts_for_thresholds(products, *, th_main, th_mid, store_roi, days=7.0, today=None):
    """按给定阈值重跑角色层，返回角色 → 款数（只用于复核影响预演）。"""
    from core.budget_role import apply_budget_roles

    rows = apply_budget_roles(
        products, th_main=th_main, th_mid=th_mid, store_roi=store_roi,
        days=days, today=today,
    )
    counts: dict[str, int] = {}
    for row in rows:
        role = str(row.get('决策角色') or '')
        counts[role] = counts.get(role, 0) + 1
    return counts


THRESHOLD_REVIEW_REMIND_DAYS = 14


def _render_threshold_review_block(snapshot, frozen, engine_main, engine_mid, year, shop):
    """「🔍 主推线复核」：引擎建议按需重算（读轻量 GMV 缓存），并预演采纳后的角色结构。"""
    from core.budget_role import THRESHOLD_WINDOW_DAYS, THRESHOLD_WINDOWS

    st.markdown("##### 🔍 主推线复核")
    source = str(snapshot.get('引擎建议来源') or '未知')
    coverage = snapshot.get('轻量缓存覆盖') or {}
    info_cols = st.columns(3)
    info_cols[0].metric("引擎建议来源", source)
    if coverage:
        info_cols[1].metric(
            "轻量缓存覆盖",
            f"{coverage.get('覆盖天数', 0)} / {coverage.get('需要天数', 0)} 天",
        )
        missing = coverage.get('缺失天数') or []
        info_cols[2].metric("缓存缺口", f"{len(missing)} 天")
    else:
        info_cols[1].metric("引擎建议参照日", snapshot.get('阈值参照日', ''))
        info_cols[2].metric("窗口数", f"{snapshot.get('阈值周数', 0)} × {int(THRESHOLD_WINDOW_DAYS)}天")

    days_frozen = _threshold_freeze_days(frozen)
    if frozen and days_frozen is not None:
        if days_frozen >= THRESHOLD_REVIEW_REMIND_DAYS:
            st.warning(
                f"当前冻结值已使用 **{days_frozen} 天**（超过 {THRESHOLD_REVIEW_REMIND_DAYS} 天），"
                "建议点下面的「重算引擎建议」做一次主推线复核。"
            )
        else:
            st.caption(f"当前冻结值已使用 {days_frozen} 天（建议每 {THRESHOLD_REVIEW_REMIND_DAYS} 天复核一次）。")

    if st.button("🔍 重算引擎建议（读轻量 GMV 缓存）", key='threshold_review_button'):
        cache_root = st.session_state.get('role_cache_root') or CACHE_DIR
        date_str = st.session_state.get('cur_date') or ''
        try:
            engine, new_source, new_coverage = _resolve_engine_thresholds(
                {}, date_str, year, shop, cache_root,
                window_days=int(THRESHOLD_WINDOW_DAYS), windows=int(THRESHOLD_WINDOWS),
            )
        except Exception as exc:  # pragma: no cover
            st.error(f"引擎建议重算失败：{exc}")
            engine = None
        if engine is not None:
            fresh = dict(snapshot)
            fresh.update({
                'TH_MAIN': engine.th_main,
                'TH_MID': engine.th_mid,
                '阈值参照日': engine.end_date,
                '阈值周数': engine.weeks,
                '阈值状态': engine.state,
                '阈值版本': engine.version,
                '引擎建议来源': new_source,
                '轻量缓存覆盖': new_coverage,
                '参考日窗口分界': [
                    {'周次': row.get('周次'), '起始日': row.get('起始日'), '结束日': row.get('结束日'),
                     'TH_MAIN': row.get('TH_MAIN'), 'TH_MID': row.get('TH_MID')}
                    for row in engine.weekly
                ],
                '警告': list(engine.warnings),
            })
            st.session_state['threshold_engine_snapshot'] = fresh
            _refresh_budget_threshold_columns()
            st.session_state['threshold_review_preview'] = _build_threshold_review_preview(
                engine, frozen, snapshot,
            )
            st.success(f"引擎建议已重算（来源：{new_source}）："
                       f"TH_MAIN={engine.th_main:.4f}、TH_MID={engine.th_mid:.4f}")

    preview = st.session_state.get('threshold_review_preview')
    if preview:
        st.caption(
            f"影响预演（{preview['参照日']}，共 {preview['款数']:,} 款）："
            "同一批商品、只换阈值时的角色结构变化；**采纳建议需回到上面点「🔒 冻结 / 更新」**。"
        )
        st.dataframe(
            preview['frame'], width="stretch", hide_index=True,
            column_config={
                '冻结核角色': st.column_config.TextColumn('角色'),
                '冻结值款数': st.column_config.NumberColumn('按冻结值'),
                '建议值款数': st.column_config.NumberColumn('按引擎建议'),
                '变化': st.column_config.NumberColumn('变化', format='%+d'),
            },
        )


def _build_threshold_review_preview(engine, frozen, snapshot):
    """按「冻结值 vs 引擎建议值」各跑一次角色层，给出角色款数对照。"""
    df = st.session_state.get('df')
    if df is None or len(df) == 0:
        return None
    products = df.to_dict('records')
    date_str = str(snapshot.get('阈值参照日') or st.session_state.get('cur_date') or '')
    try:
        today = pd.Timestamp(date_str).date() if date_str else None
    except Exception:
        today = None
    store_roi = 0.0
    try:
        spend = pd.to_numeric(df.get('付费'), errors='coerce')
        total_spend = float(spend.sum()) if spend is not None else 0.0
        if total_spend > 0:
            promotion = pd.to_numeric(df.get('推广成交金额'), errors='coerce') \
                if '推广成交金额' in df.columns else None
            if promotion is not None and promotion.notna().any():
                store_roi = float(promotion.sum()) / total_spend
            else:
                store_roi = float(pd.to_numeric(df.get('GMV'), errors='coerce').sum()) / total_spend
    except Exception:
        store_roi = 0.0
    frozen_main = float((frozen or {}).get('TH_MAIN') or 0.0)
    frozen_mid = float((frozen or {}).get('TH_MID') or 0.0)
    if not frozen_main and not frozen_mid:
        frozen_main, frozen_mid = float(engine.th_main), float(engine.th_mid)
    try:
        frozen_counts = _role_counts_for_thresholds(
            products, th_main=frozen_main, th_mid=frozen_mid,
            store_roi=store_roi, today=today,
        )
        engine_counts = _role_counts_for_thresholds(
            products, th_main=float(engine.th_main), th_mid=float(engine.th_mid),
            store_roi=store_roi, today=today,
        )
    except Exception as exc:  # pragma: no cover
        print(f"[角色层] 影响预演失败：{exc}")
        return None
    roles = [role for role in
             ('ZT主推', 'ZT趋势预警', 'TC投产', 'TD梯队', 'TD候选', 'CS测试', 'QC回收', '清理', '观察', '未判定')
             if role in frozen_counts or role in engine_counts]
    frame = pd.DataFrame([
        {
            '冻结核角色': role,
            '冻结值款数': int(frozen_counts.get(role, 0)),
            '建议值款数': int(engine_counts.get(role, 0)),
            '变化': int(engine_counts.get(role, 0)) - int(frozen_counts.get(role, 0)),
        }
        for role in roles
    ])
    return {
        '参照日': engine.end_date,
        '款数': len(products),
        'frame': frame,
        '冻结值': (frozen_main, frozen_mid),
        '建议值': (float(engine.th_main), float(engine.th_mid)),
    }


def _refresh_budget_threshold_columns():
    """冻结/清除冻结后，就地刷新执行表里的阈值列并让预算看板缓存失效。"""
    from core.budget_role import (
        THRESHOLD_WINDOW_DAYS,
        resolve_thresholds,
        threshold_columns_from_payload,
    )

    df = st.session_state.get('df')
    snapshot = st.session_state.get('threshold_engine_snapshot') or {}
    if df is None or len(df) == 0 or not snapshot:
        return False
    engine_like = type('ThresholdSnapshot', (), {
        'th_main': snapshot.get('TH_MAIN', 0.0),
        'th_mid': snapshot.get('TH_MID', 0.0),
        'version': snapshot.get('阈值版本', ''),
        'state': snapshot.get('阈值状态', ''),
        'end_date': snapshot.get('阈值参照日', ''),
        'weeks': int(snapshot.get('阈值周数') or 0),
        'window_days': int(THRESHOLD_WINDOW_DAYS),
    })()
    frozen = None
    year, shop = st.session_state.get('cur_year'), st.session_state.get('cur_shop')
    if year is not None and shop:
        frozen = _threshold_freeze_store().get(year, shop)
    payload = resolve_thresholds(engine_like, frozen)
    for column, value in threshold_columns_from_payload(payload).items():
        df[column] = value
    st.session_state.df = df
    _invalidate_budget_decision_context_cache()
    return True


def _render_threshold_freeze_panel():
    """预算决策看板里的「阈值冻结（运营确认）」面板。"""
    from core.budget_role import THRESHOLD_WINDOW_DAYS

    snapshot = st.session_state.get('threshold_engine_snapshot') or {}
    year = st.session_state.get('cur_year')
    shop = st.session_state.get('cur_shop')
    if not snapshot:
        st.caption("阈值信息尚未生成（本次加载未产出引擎建议值）。")
        return

    engine_main = float(snapshot.get('TH_MAIN') or 0.0)
    engine_mid = float(snapshot.get('TH_MID') or 0.0)
    weekly = snapshot.get('参考日窗口分界') or []
    latest = weekly[0] if weekly else {}
    frozen = None
    if year is not None and shop:
        try:
            frozen = _threshold_freeze_store().get(year, shop)
        except Exception as exc:  # pragma: no cover
            st.warning(f"阈值冻结登记读取失败：{exc}")

    with st.expander("🔒 阈值冻结（由运营确认，决定角色判定线）", expanded=False):
        st.caption(
            f"判定口径固定近{int(THRESHOLD_WINDOW_DAYS)}天（与左侧时间周期无关）。"
            "引擎只给建议值，**生效阈值以运营冻结值为准**；冻结后除非运营更新或清除，不会自动变化。"
        )
        info_cols = st.columns(3)
        if frozen:
            info_cols[0].metric("当前生效（运营冻结）", f"{frozen['TH_MAIN']:.2f} / {frozen['TH_MID']:.2f}")
            info_cols[1].metric("冻结人 / 时间", f"{frozen.get('冻结人', '')}",
                                f"{frozen.get('冻结时间', '')}")
            info_cols[2].metric("阈值版本", frozen.get('阈值版本', ''))
        else:
            info_cols[0].metric("当前生效（引擎建议·未冻结）", f"{engine_main:.2f} / {engine_mid:.2f}")
            info_cols[1].metric("引擎建议参照日", snapshot.get('阈值参照日', ''))
            info_cols[2].metric("窗口数", f"{snapshot.get('阈值周数', 0)} 个 × {int(THRESHOLD_WINDOW_DAYS)}天")

        compare = pd.DataFrame([
            {'口径': f'引擎建议（{snapshot.get("阈值周数", 0)}窗中位数，参照 {snapshot.get("阈值参照日", "")}）',
             'TH_MAIN': engine_main, 'TH_MID': engine_mid},
            {'口径': f'最新一周（{latest.get("起始日", "")}~{latest.get("结束日", "")}）',
             'TH_MAIN': latest.get('TH_MAIN'), 'TH_MID': latest.get('TH_MID')},
            {'口径': '当前生效', 'TH_MAIN': (frozen or {}).get('TH_MAIN', engine_main),
             'TH_MID': (frozen or {}).get('TH_MID', engine_mid)},
        ])
        st.dataframe(
            compare, width="stretch", hide_index=True,
            column_config={
                'TH_MAIN': st.column_config.NumberColumn('TH_MAIN（50%覆盖分界）', format='%.4f'),
                'TH_MID': st.column_config.NumberColumn('TH_MID（80%覆盖分界）', format='%.4f'),
            },
        )
        if frozen:
            deviation_main = (frozen['TH_MAIN'] - engine_main) / engine_main if engine_main else 0.0
            deviation_mid = (frozen['TH_MID'] - engine_mid) / engine_mid if engine_mid else 0.0
            hint = f"冻结值与引擎建议偏离：TH_MAIN {deviation_main:+.1%}、TH_MID {deviation_mid:+.1%}"
            if max(abs(deviation_main), abs(deviation_mid)) > 0.30:
                st.warning(hint + "（超过 30%，建议复核是否更新冻结值）")
            else:
                st.caption(hint)
            if frozen.get('备注'):
                st.caption(f"冻结备注：{frozen['备注']}")

        _render_threshold_review_block(snapshot, frozen, engine_main, engine_mid, year, shop)

        default_main = float((frozen or {}).get('TH_MAIN', engine_main) or 0.0)
        default_mid = float((frozen or {}).get('TH_MID', engine_mid) or 0.0)
        input_cols = st.columns(3)
        input_main = input_cols[0].number_input(
            "TH_MAIN（50%覆盖分界，元/天）", value=default_main, min_value=0.0,
            step=10.0, format="%.4f", key='threshold_freeze_main_input',
        )
        input_mid = input_cols[1].number_input(
            "TH_MID（80%覆盖分界，元/天）", value=default_mid, min_value=0.0,
            step=10.0, format="%.4f", key='threshold_freeze_mid_input',
        )
        operator = input_cols[2].text_input("操作人（登记用）", key='threshold_freeze_operator')
        note = st.text_input("备注（为什么定这个值）", key='threshold_freeze_note')

        git_status = _threshold_freeze_git_status()
        if git_status.get('in_repo') and not git_status.get('错误'):
            st.caption(
                f"登记随 git 走：`{git_status.get('relative_path')}`"
                f"（{'已纳入版本管理' if git_status.get('tracked') else '尚未纳入版本管理，首次冻结会自动 add'}）"
            )
            git_cols = st.columns(2)
            commit_registry = git_cols[0].checkbox(
                "冻结时提交到 git（登记随仓库共享/可追溯）", value=True,
                key='threshold_freeze_commit',
            )
            push_registry = git_cols[1].checkbox(
                "同时推送远端", value=False, key='threshold_freeze_push',
            )
        else:
            commit_registry = push_registry = False
            st.caption(
                f"⚠ 未找到 git 仓库，登记只在本机：`{git_status.get('path', '')}`"
                f"（{git_status.get('错误', '')}）"
            )

        action_cols = st.columns(4)
        if action_cols[0].button("🔒 冻结 / 更新", key='threshold_freeze_save', type="primary"):
            try:
                saved = _threshold_freeze_store().save(
                    year, shop, th_main=input_main, th_mid=input_mid,
                    operator=operator, note=note,
                    engine={'TH_MAIN': engine_main, 'TH_MID': engine_mid,
                            '阈值参照日': snapshot.get('阈值参照日', ''),
                            '阈值周数': snapshot.get('阈值周数', 0)},
                )
                _refresh_budget_threshold_columns()
                git_note = _threshold_freeze_after_change(
                    saved, action='冻结', commit=commit_registry, push=push_registry,
                )
                st.success(f"已冻结：TH_MAIN={saved['TH_MAIN']:.4f}、TH_MID={saved['TH_MID']:.4f}"
                           f"（版本 {saved['阈值版本']}）")
                if git_note:
                    st.info(git_note)
                elif commit_registry is False and git_status.get('in_repo'):
                    st.caption("（本次未提交 git，登记仅在本机工作区）")
                st.rerun()
            except Exception as exc:
                st.error(f"冻结失败：{exc}")
        if action_cols[1].button("✅ 采纳引擎建议并冻结", key='threshold_freeze_adopt'):
            try:
                saved = _threshold_freeze_store().save(
                    year, shop, th_main=engine_main, th_mid=engine_mid,
                    operator=operator, note=note or '采纳引擎建议值',
                    engine={'TH_MAIN': engine_main, 'TH_MID': engine_mid,
                            '阈值参照日': snapshot.get('阈值参照日', ''),
                            '阈值周数': snapshot.get('阈值周数', 0)},
                )
                _refresh_budget_threshold_columns()
                git_note = _threshold_freeze_after_change(
                    saved, action='冻结', commit=commit_registry, push=push_registry,
                )
                st.success(f"已按引擎建议冻结（版本 {saved['阈值版本']}）")
                if git_note:
                    st.info(git_note)
                st.rerun()
            except Exception as exc:
                st.error(f"冻结失败：{exc}")
        if action_cols[2].button("↩ 清除冻结", key='threshold_freeze_clear'):
            try:
                previous = frozen
                cleared = _threshold_freeze_store().clear(year, shop, operator=operator, note=note)
                _refresh_budget_threshold_columns()
                git_note = _threshold_freeze_after_change(
                    previous, action='清除', commit=commit_registry, push=push_registry,
                )
                st.success("已清除冻结，改为使用引擎建议值" if cleared else "当前没有冻结记录")
                if git_note:
                    st.info(git_note)
                st.rerun()
            except Exception as exc:
                st.error(f"清除失败：{exc}")

        history = _threshold_freeze_store().history(year, shop, limit=5) if (year and shop) else []
        if history:
            st.caption("最近变更记录（新→旧）")
            st.dataframe(
                pd.DataFrame(list(reversed(history))), width="stretch", hide_index=True,
            )


def _budget_role_interception_counts(products) -> tuple[int, int]:
    """统计本轮被角色闸门实际拦下 / 封顶的款数（DataFrame 或 dict 列表都能吃）。"""
    from core.budget_role import GATE_BLOCK, GATE_CAP

    if isinstance(products, pd.DataFrame):
        if '角色闸门命中' not in products.columns:
            return 0, 0
        hits = products['角色闸门命中'].astype(str).str.strip()
        return int((hits == GATE_BLOCK).sum()), int((hits == GATE_CAP).sum())
    rows = [dict(item) for item in (products or [])]
    blocked = sum(1 for row in rows if str(row.get('角色闸门命中') or '').strip() == GATE_BLOCK)
    capped = sum(1 for row in rows if str(row.get('角色闸门命中') or '').strip() == GATE_CAP)
    return blocked, capped


def _render_budget_role_summary(products):
    """在预算决策看板展示决策角色分布与闸门拦截情况（角色层未启用时不显示）。"""
    if products is None or len(products) == 0:
        return
    if '决策角色' not in getattr(products, 'columns', []):
        return
    try:
        from core.budget_role import role_summary

        rows = role_summary(products)
    except Exception as exc:  # pragma: no cover - 兜底
        print(f"[WARNING] 角色汇总生成失败：{exc}")
        return
    if not rows:
        return
    with st.expander("🎯 决策角色分布（只拦加投，不改回收）", expanded=False):
        summary_frame = pd.DataFrame(rows)
        display_columns = [
            column for column in (
                '决策角色', '款数', '销售份额', '花费份额', '占比差(pp)',
                'ROI', '预算错配金额', '未来7日建议调整金额', '禁止加投款数',
            )
            if column in summary_frame.columns
        ]
        st.dataframe(
            summary_frame[display_columns],
            width="stretch",
            hide_index=True,
            column_config={
                '决策角色': st.column_config.TextColumn('决策角色'),
                '销售份额': st.column_config.PercentColumn('销售份额'),
                '花费份额': st.column_config.PercentColumn('花费份额'),
                '占比差(pp)': st.column_config.NumberColumn('占比差（百分点）', format='%.2f'),
                'ROI': st.column_config.NumberColumn('ROI', format='%.2f'),
                '预算错配金额': st.column_config.NumberColumn('预算错配金额', format='¥%.2f'),
                '未来7日建议调整金额': st.column_config.NumberColumn('建议调整金额', format='¥%.2f'),
                '禁止加投款数': st.column_config.NumberColumn('禁止加投款数'),
            },
        )
        roles = sorted(summary_frame['决策角色'].astype(str))
        blocked, capped = _budget_role_interception_counts(products)
        st.caption(
            "判定基准：固定近7日日均GMV（金额口径）；阈值：7天窗口×8窗覆盖法中位数（店铺级常量，未在列中重复展示）。"
            "闸门语义：禁止加投=归零、测试封顶=压到500元/周、放行=原样。"
            f"本轮角色层实际拦下加投 {blocked} 款、封顶 {capped} 款"
            f"（被拦下的行会在“预算匹配状态”显示为“角色闸门拦截(角色名)”）。"
            f"本轮命中角色：{'、'.join(roles)}"
        )


def _budget_decision_products_for_display(products):
    display = _normalize_rank_band_dataframe(_dedupe_dataframe_columns(products))
    return _hide_budget_decision_columns(display)


def _hide_budget_decision_columns(frame):
    display = _dedupe_dataframe_columns(frame)
    hidden_columns = [
        column for column in display.columns
        if _is_budget_decision_hidden_column(column)
    ]
    if hidden_columns:
        display = display.drop(columns=hidden_columns)
    return display


def _budget_decision_product_columns(display_products, canonical_columns):
    if display_products is None or display_products.empty:
        return []
    available_columns = list(dict.fromkeys(display_products.columns))
    leading = [
        column for column in BUDGET_DECISION_LEADING_COLUMNS
        if column in available_columns and not _is_budget_decision_hidden_column(column)
    ]
    canonical = []
    for column in canonical_columns:
        normalized_column = (
            BUDGET_RANK_BAND_COLUMN
            if column in BUDGET_RANK_BAND_LEGACY_COLUMNS
            else column
        )
        if (
            normalized_column in BUDGET_RANK_BAND_LEGACY_COLUMNS
            or _is_budget_decision_hidden_column(normalized_column)
        ):
            continue
        if normalized_column in available_columns:
            canonical.append(normalized_column)
    remaining = [
        column for column in available_columns
        if column not in leading and column not in canonical
        and column not in BUDGET_RANK_BAND_LEGACY_COLUMNS
        and not _is_budget_decision_hidden_column(column)
        and not str(column).startswith("_")
    ]
    return _unique_budget_decision_columns(leading, canonical, remaining)


def _budget_decision_context_cache_token(df):
    return (
        BUDGET_DECISION_CONTEXT_CACHE_VERSION,
        id(df),
        len(df) if isinstance(df, pd.DataFrame) else 0,
    )


def _invalidate_budget_decision_context_cache():
    st.session_state.budget_decision_ctx_cache = None


def _build_budget_decision_context_cached(df):
    token = _budget_decision_context_cache_token(df)
    plan_config = st.session_state.get("plan_config") or {}
    token = (token, plan_config.get("fingerprint", ""))
    cached = st.session_state.get("budget_decision_ctx_cache")
    if (
        isinstance(cached, dict)
        and cached.get("token") == token
        and isinstance(cached.get("context"), dict)
    ):
        return cached["context"]
    context = build_budget_decision_context(df, roles=True, plan_config=plan_config) or {}
    st.session_state.budget_decision_ctx_cache = {
        "token": token,
        "context": context,
    }
    return context

def _cache_key(base_root, year, shop, date_str):
    """按数据集生成稳定缓存目录名，让相邻分析日复用重叠历史日期。"""
    return dataset_cache_key(base_root, year, shop)


def _load_plan_config_for(base_root, year, shop, date_str, cache_root=None):
    """读「最新」计划列表快照（预算/出价方式），失败不影响主流程。"""
    wujie_root = Path(base_root) / str(year) / str(shop) / '生意参谋' / '无界'
    try:
        return load_plan_config(wujie_root, analysis_date=date_str, cache_root=cache_root)
    except Exception as exc:  # noqa: BLE001 - 计划配置只做展示，读不到就留空
        print(f"[计划列表] 读取失败，预算/出价方式留空：{exc}")
        return {'by_id': {}, 'fingerprint': '',
                'source': {'file': '', 'reason': f'计划列表读取失败：{exc}'}}


def _resolve_v93_report(path, date_str):
    """v9.3 日报路径解析:接受 txt 文件;若填的是文件夹,按分析日自动匹配
    <目录>/<日期>/report.txt(每日流水线布局)或文件名含日期的 *.txt。找不到返回 None。"""
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        import glob as _glob
        candidates = [
            os.path.join(path, date_str, "report.txt"),
            os.path.join(path, f"report_{date_str}.txt"),
        ]
        candidates += sorted(_glob.glob(os.path.join(path, f"*{date_str}*.txt")))
        candidates += sorted(_glob.glob(os.path.join(path, f"*{date_str.replace('-', '')}*.txt")))
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None

def _build_paths(base_root, date_str, year, shop):
    """根据根目录、日期、年份、店铺生成两个目录路径"""
    root = Path(base_root)
    product_dir = root / year / shop / "生意参谋" / "商品" / date_str
    wujie_dir = root / year / shop / "生意参谋" / "无界" / date_str
    return product_dir, wujie_dir


def _render_git_version_management():
    """在高级设置中渲染低频 Git 版本管理功能。"""
    git_repo = find_repository(Path(__file__).resolve().parent)
    if git_repo is None:
        st.warning("未找到 Git 仓库，暂时不能创建版本")
        return

    git_status = get_status(git_repo, project_name=GIT_PROJECT_NAME)
    if git_status.error:
        st.error(f"读取 Git 状态失败：{git_status.error}")
        return

    st.caption(f"仓库：{git_repo}")
    st.caption(
        f"分支：{git_status.branch} | 本地领先 {git_status.ahead} | "
        f"远端领先 {git_status.behind}"
    )
    if git_status.remote_url:
        st.caption(f"远端：{git_status.remote_url}")
    st.write(f"当前版本：{git_status.current_version or '尚未建立（首次为1.0）'}")

    if git_status.changed_files:
        st.write(f"待保存改动：{len(git_status.changed_files)} 个")
        st.code("\n".join(git_status.changed_files[:12]), language=None)
        if len(git_status.changed_files) > 12:
            st.caption(f"另有 {len(git_status.changed_files) - 12} 个文件未展开")
    else:
        st.success("当前代码已是最新保存状态")

    version_note = st.text_input(
        "版本备注", placeholder="例如：修复多日货号匹配",
        key="git_version_note",
    )
    change_label = st.radio(
        "版本调整", ["小改（小数位 +0.1）", "大改（整数位 +1.0）"],
        horizontal=True, key="git_change_kind",
    )
    change_kind = "major" if change_label.startswith("大改") else "minor"
    next_version = next_version_name(git_repo, GIT_PROJECT_NAME, change_kind)
    st.info(f"本次版本：{next_version}")
    push_to_github = st.checkbox(
        "保存后同步 GitHub", value=True, key="git_push_to_github",
    )
    if st.button("保存新版本", width="stretch", key="git_save_version"):
        with st.spinner("正在创建版本..."):
            git_result = save_version(
                git_repo, version_note, push=push_to_github,
                project_name=GIT_PROJECT_NAME, change_kind=change_kind,
            )
        st.session_state["git_action_result"] = git_result

    git_result = st.session_state.get("git_action_result")
    if git_result:
        if git_result.ok:
            st.success(
                f"{git_result.message}：{git_result.version}（{git_result.commit}）"
            )
        else:
            st.error(git_result.message)
        if git_result.details:
            st.code(git_result.details[:2000], language=None)

    if git_status.recent_commits:
        st.write("最近版本")
        for commit, subject, commit_date in git_status.recent_commits:
            st.caption(f"{commit_date} · {commit} · {subject}")

# ══════════════════════════════════════════════════════════
# 侧边栏
# ══════════════════════════════════════════════════════════

with st.sidebar:
    st.header("数据加载")

    date_input = st.text_input(
        "日期",
        placeholder="2026-07-02",
        key="date_input",
        help="输入日期(YYYY-MM-DD)，系统自动生成商品和无界目录路径"
    )

    period_choice = st.selectbox(
        "时间周期",
        ["当日", "3天", "7天", "15天", "30天", "按月", "自定义"],
        index=0,
        key="period_choice_input",
        help="主表/看板/品类分析的展示口径:当日=所填日期单日;3天=日期往前3天累计"
             "(如填2026-07-08则为07-06~07-08,环比对比07-03~07-05),7天/15天/30天同理;"
             "按月=选定月份(历史月取整月,当月取月初~所填日期);自定义=自选起止日期,最长31天"
    )

    # ── v2.66 按月 / 自定义 周期的参数控件 ─────────────────────────────
    period_month = None
    period_custom = None
    if period_choice == "按月":
        _base_for_month = date_input.strip() or datetime.now().strftime("%Y-%m-%d")
        try:
            _base_dt = datetime.strptime(_base_for_month, "%Y-%m-%d").date()
        except ValueError:
            _base_dt = datetime.now().date()
        _month_options = []
        _cursor_year, _cursor_month = _base_dt.year, _base_dt.month
        for _ in range(18):
            _month_options.append(f"{_cursor_year:04d}-{_cursor_month:02d}")
            _cursor_month -= 1
            if _cursor_month == 0:
                _cursor_month = 12
                _cursor_year -= 1
        period_month = st.selectbox(
            "月份", _month_options, index=0, key="period_month_input",
            help=f"历史月取整月；当月取月初~所填日期（{_base_dt.isoformat()}）；最长 {MAX_PERIOD_DAYS} 天",
        )
        _m_start, _m_end = resolve_month_range(period_month, _base_dt)
        st.caption(f"口径：{_m_start.isoformat()} ~ {_m_end.isoformat()}"
                   f"（{( _m_end - _m_start).days + 1} 天）")
    elif period_choice == "自定义":
        _base_for_custom = date_input.strip() or datetime.now().strftime("%Y-%m-%d")
        try:
            _custom_end_default = datetime.strptime(_base_for_custom, "%Y-%m-%d").date()
        except ValueError:
            _custom_end_default = datetime.now().date()
        _start_default, _end_default, _, _ = validate_custom_range(
            (_custom_end_default - timedelta(days=6)).isoformat(),
            _custom_end_default.isoformat(),
        ) if True else (None, None, 0, "")
        _col_a, _col_b = st.columns(2)
        _custom_start = _col_a.date_input(
            "开始日期", value=_start_default or _custom_end_default, key="period_custom_start")
        _custom_end = _col_b.date_input(
            "结束日期", value=_end_default or _custom_end_default, key="period_custom_end")
        _s, _e, _days, _err = validate_custom_range(_custom_start, _custom_end)
        if _err:
            st.error(f"{_err}，已按「7天」加载")
            period_choice = "7天"      # 降级：区间非法时不进入自定义口径
            period_custom = None
        else:
            st.caption(f"口径：{_s.isoformat()} ~ {_e.isoformat()}（{_days} 天）")
            period_custom = (_s.isoformat(), _e.isoformat())

    load_requested = st.button(
        "加载并处理数据",
        type="primary",
        width="stretch",
    )

    # 低频配置和诊断统一收进一个高级设置，缩短日常加载首屏。
    with st.expander("高级设置", expanded=False):
        st.markdown("#### 路径配置")
        base_root = st.text_input(
            "数据根目录",
            value=DEFAULT_ROOT,
            key="base_root_input",
            help="共享盘: \\\\10.1.22.253\\天猫mark   |   本地: D:\\推广数据"
        )
        year = st.text_input("年份", value=DEFAULT_YEAR, key="year_input")
        shop = st.text_input("店铺", value=DEFAULT_SHOP, key="shop_input")
        st.caption(f"商品目录 = {base_root or '(根目录)'}\\{year}\\{shop}\\生意参谋\\商品\\(日期)")
        st.caption(f"无界目录 = {base_root or '(根目录)'}\\{year}\\{shop}\\生意参谋\\无界\\(日期)")

        st.divider()
        st.markdown("#### 数据源目录")
        # 商品资料（独立入口，不随日期变化）
        product_folder = st.text_input(
            "商品资料文件夹（库存+商品资料）",
            value=DEFAULT_JST_PRODUCT_DIR if os.path.isdir(DEFAULT_JST_PRODUCT_DIR) else "",
            placeholder="留空则跳过商品资料",
            key="product_folder_input",
            help="商品库存资料和店铺商品资料所在文件夹路径;聚水潭直连时放 店铺商品资料_聚水潭映射.csv(商品ID↔线上款式编码)"
        )

        # 聚水潭库存快照直连（可选）
        jst_snap_dir = st.text_input(
            "聚水潭库存快照目录（可选）",
            value=DEFAULT_JST_SNAP_DIR if os.path.isdir(DEFAULT_JST_SNAP_DIR) else "",
            placeholder="留空则不直连聚水潭",
            key="jst_snap_input",
            help="每日快照镜像目录(YYYY-MM/库存快照_日期.csv)。自动取分析日快照按公司口径聚合到款式级"
                 "(经营库存公式、排次品仓),经 货号↔款式编码 挂到商品;当日缺失回退最近≤7天并提示"
        )

        jst_refund_dir = st.text_input(
            "聚水潭退款率目录（可选）",
            value=DEFAULT_JST_REFUND_DIR if os.path.isdir(DEFAULT_JST_REFUND_DIR) else "",
            placeholder="留空则退款率列为空(不回退生意参谋)",
            key="jst_refund_input",
            help="每日退款率快照(YYYY-MM/退款率_日期.csv)。30日cohort=SUM(refund_amount)/SUM(amount),"
                 "女装旗舰店;退款率唯一来源(2026-07-10起不再回退生意参谋口径),缺失回退最近≤7天快照;"
                 "快照未覆盖的品(近30日无订单)退款率留空"
        )

        # v9.3 日报（可选，标注正向池信号列）
        v93_report_path = st.text_input(
            "v9.3 日报文件或文件夹（可选）",
            value=DEFAULT_V93_DIR if os.path.isdir(DEFAULT_V93_DIR) else "",
            placeholder="留空则跳过 v9.3 信号标注",
            key="v93_report_input",
            help="daily_review v9.3 日报 txt,或日报所在文件夹(按分析日自动匹配,如 明日之星每日复盘_<日期>_标准版.txt);"
                 "解析全部信号段标注到「v9.3信号」列:明日之星/潜力款/爆发/加预算(正向)、"
                 "亏损风险/饱和观察/保护持有/削减(风险与维持)、溢出(付费带动自然);"
                 "全量分层展示为纯展示段,不解析"
        )

        st.divider()
        st.markdown("#### 自定义分界点")
        t_input = st.text_input(
            "流量分界点(5个,逗号分隔)",
            value="50,150,400,1500,3000",
            key="t_bounds_input",
            help="商品访客数 6 档分界(滞销款/待改善/潜力款/冲刺款/金牛款/明星款)。清空则按全店分位数自动计算",
        )
        v_input = st.text_input(
            "价值分界点(5个,逗号分隔)",
            value="0.5,1.5,3,4.5,6",
            key="v_bounds_input",
            help="访客平均价值(GMV÷访客) 6 档分界。清空则按全店分位数自动计算",
        )

        if st.session_state.loaded:
            # 分位参考和重算动作跟随分界点配置，不再常驻侧边栏。
            if st.session_state.df is not None:
                current_df = st.session_state.df
                t_ref = compute_bounds(current_df['商品访客数'].values)
                v_ref = compute_bounds(current_df['访客平均价值'].values)
                st.caption(f"流量分位参考: {', '.join([f'{b:,.0f}' for b in t_ref])}")
                st.caption(f"价值分位参考: {', '.join([f'{b:.2f}' for b in v_ref])}")

            if st.button(
                "重新应用分界点(不重新读数)",
                width="stretch",
                key="reapply_segmentation_bounds",
            ):
                tb = _parse_bounds(t_input)
                vb = _parse_bounds(v_input)
                st.session_state.traffic_bounds = tb
                st.session_state.value_bounds = vb
                current_df = _apply_segmentation(st.session_state.df, tb, vb)
                st.session_state.df = current_df
                st.session_state.kpis = calc_kpis(current_df, st.session_state.plan_ids)
                st.session_state.kpi_mom = calc_kpi_mom_percent(
                    st.session_state.kpis, st.session_state.prev_kpis
                )
                st.rerun()

        if st.session_state.get('timings'):
            st.divider()
            st.markdown(
                f"#### 性能耗时（总 {st.session_state.get('total_time', 0):.1f} 秒）"
            )
            for label, elapsed, extra in st.session_state.timings:
                extra_str = f" — {extra}" if extra else ""
                st.caption(f"[耗时] {label}：{elapsed}{extra_str}")

        if st.session_state.get('period_logs'):
            st.divider()
            st.markdown("#### 周期环比范围与完整性")
            st.dataframe(
                pd.DataFrame(st.session_state.period_logs),
                width="stretch",
                hide_index=True,
            )

        st.divider()
        st.markdown("#### Git 版本管理")
        _render_git_version_management()

    if load_requested:
        # ── 1. 校验日期格式 ──
        date_str = date_input.strip()
        if not date_str:
            st.error("请输入日期，格式: YYYY-MM-DD，例如 2026-07-02")
        elif not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            st.error(f"日期格式错误: {date_str}，请使用 YYYY-MM-DD 格式，例如 2026-07-02")
        else:
            # ── 2. 校验根目录 ──
            root = base_root.strip()
            if not root:
                st.error("请输入数据根目录")
            else:
                # ── 3. 生成路径 ──
                biz_folder, wujie_folder = _build_paths(root, date_str, year, shop)

                # ── 4. 校验目录是否存在 ──
                df, prev_df, files_info, plan_ids, prev_plan_ids, timings, period_logs, cat_ctx, kpi_trends, new_product_ctx, new_product_load_state = \
                    None, None, [], set(), set(), [], [], {}, pd.DataFrame(), _new_product_unloaded_context(), {}
                errors = []
                validation_error = None
                if not os.path.isdir(str(biz_folder)):
                    errors.append(f"商品目录不存在：{biz_folder}")
                if not os.path.isdir(str(wujie_folder)):
                    errors.append(f"无界目录不存在：{wujie_folder}")

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    # 显示实际路径
                    st.caption(f"商品: {biz_folder}")
                    st.caption(f"无界: {wujie_folder}")

                    # ── 5. 确保本地缓存目录存在 ──
                    ck = _cache_key(root, year, shop, date_str)
                    file_cache_dir = CACHE_DIR / ck
                    file_cache_dir.mkdir(parents=True, exist_ok=True)
                    CACHE_RESULT_DIR.mkdir(parents=True, exist_ok=True)

                    # ── 6. 加载当天数据，并自动计算日/3日/7日/15日环比 ──
                    with st.spinner("读取文件中..."):
                        t_load = time.perf_counter()
                        try:
                            (
                                df, prev_df, files_info, plan_ids, prev_plan_ids,
                                timings, period_logs, cat_ctx, kpi_trends,
                                new_product_ctx, new_product_load_state,
                            ) = _load_folder_cached(
                                root, year, shop, date_str, product_folder,
                                cache_dir=str(file_cache_dir),
                                jst_snap_dir=(jst_snap_dir or "").strip().strip('"') or None,
                                jst_refund_dir=(jst_refund_dir or "").strip().strip('"') or None,
                                period_choice=period_choice,
                                period_month=period_month,
                                period_custom=period_custom,
                            )
                        except DataValidationError as exc:
                            validation_error = str(exc)

                if validation_error:
                    st.error(f"数据验收失败，已停止处理：{validation_error}")
                elif df is None or len(df) == 0:
                    st.error("未找到任何数据文件")
                else:
                    with st.spinner("分层计算中..."):
                        t_seg = time.perf_counter()
                        # 加载时直接应用侧边栏分界点(空/格式错→None→按全店分位数)
                        tb_load = _parse_bounds(t_input)
                        vb_load = _parse_bounds(v_input)
                        df = _apply_segmentation(df, tb_load, vb_load)
                        timings.append(("商品分层", f"{time.perf_counter() - t_seg:.2f} 秒",
                                        f"流量{tb_load or '自动'} 价值{vb_load or '自动'}"))

                    # v9.3 信号标注（可选,解析失败不阻断加载）
                    df["v9.3信号"] = ""   # 列恒在,未提供日报时为空
                    v93_path = (v93_report_path or "").strip().strip('"')
                    if v93_path:
                        resolved = _resolve_v93_report(v93_path, date_str)
                        if resolved:
                            try:
                                signals = parse_v93_report(
                                    Path(resolved).read_text(encoding="utf-8", errors="replace"))
                                df["v9.3信号"] = df["宝贝ID"].astype(str).map(signals).fillna("")
                                st.caption(f"v9.3信号: 日报={os.path.basename(resolved)} | "
                                           f"全信号 {len(signals)} 个pid,"
                                           f"命中执行表 {(df['v9.3信号'] != '').sum()} 个")
                            except Exception as e:
                                st.warning(f"v9.3 日报解析失败(不影响其余数据): {str(e)[:80]}")
                        else:
                            st.warning(
                                f"未找到 {date_str} 的 v9.3 日报(支持 txt 文件路径,"
                                f"或含 <日期>/report.txt、文件名带日期 txt 的文件夹): {v93_path}"
                                " ——「v9.3信号」列本次为空")

                    # 保存耗时日志到 session，让它在主区域持久显示
                    total_time = time.perf_counter() - t_load
                    st.session_state.timings = timings
                    st.session_state.total_time = total_time
                    st.session_state.period_logs = period_logs

                    st.session_state.df = df
                    st.session_state.prev_df = prev_df
                    st.session_state.plan_ids = plan_ids
                    st.session_state.prev_plan_ids = prev_plan_ids
                    st.session_state.kpis = calc_kpis(df, plan_ids)
                    st.session_state.prev_kpis = calc_kpis(prev_df, prev_plan_ids)
                    st.session_state.kpi_mom = calc_kpi_mom_percent(
                        st.session_state.kpis, st.session_state.prev_kpis
                    )
                    st.session_state.cur_date = date_str
                    st.session_state.cur_year = year
                    st.session_state.cur_shop = shop
                    # 2026-11-14：推广计划管理新增「预算 / 出价方式」，只读最新那份计划列表快照
                    st.session_state.plan_config = _load_plan_config_for(
                        root, year, shop, date_str, cache_root=str(file_cache_dir))
                    st.session_state.prev_date = None
                    st.session_state.sources_info = files_info
                    st.session_state.cat_ctx = cat_ctx
                    st.session_state.kpi_trends = kpi_trends
                    # v2.54：角色层轻量历史缓存的根目录，供「主推线复核」按钮读缓存
                    st.session_state.role_cache_root = str(file_cache_dir)
                    st.session_state.new_product_ctx = new_product_ctx
                    st.session_state.new_product_load_state = new_product_load_state
                    st.session_state.new_product_history = None
                    st.session_state.new_product_progress = ''
                    _invalidate_budget_decision_context_cache()
                    st.session_state.loaded = True
                    st.session_state.traffic_bounds = tb_load
                    st.session_state.value_bounds = vb_load

                    st.success(f"成功[{period_choice}]: {len(df)}商品 | "
                               f"花费 ¥{st.session_state.kpis['本期花费']/1e4:.2f}万 | 计划 {len(plan_ids)}")
                    st.rerun()

# ══════════════════════════════════════════════════════════
# 主内容区
# ══════════════════════════════════════════════════════════

if not st.session_state.loaded:
    st.info("👈 在左侧输入日期（如 2026-07-02），点击「加载并处理数据」")
    st.stop()

st.session_state.df = _normalize_display_columns(st.session_state.df)
df = st.session_state.df
try:
    validate_paid_plan_consistency(df, '预算决策看板')
except DataValidationError as exc:
    st.error(f"数据验收失败，预算决策看板已停止生成：{exc}")
    st.stop()
budget_decision_ctx = _build_budget_decision_context_cached(st.session_state.df)
kpis = st.session_state.kpis
kpi_mom = st.session_state.kpi_mom or calc_kpi_mom_percent(kpis, {})

_render_source_compat_banner()

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
    "📈 数据看板", "📋 数据验收", "💰 预算决策看板", "📊 执行表",
    "🎯 商品分层", "📂 品类分析", "⚠ 退款风险", "🔑 渠道分析", "📦 库存动作",
    "🆕 新品视图"
])

# ── Tab 1: 数据看板 ───────────────────────────────────────
with tab1:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    _render_kpi_metric_with_trend(
        c1, "GMV", f"¥{kpis['GMV']/1e4:.2f}万", f"{kpi_mom['GMV']:.2f}% 环比",
        trend_key="GMV", trend_label="GMV（万元）", scale=1e4,
    )
    _render_kpi_metric_with_trend(
        c2, "本期花费", f"¥{kpis['本期花费']/1e4:.2f}万", f"{kpi_mom['本期花费']:.2f}% 环比",
        trend_key="本期花费", trend_label="花费（万元）", scale=1e4,
    )
    _render_kpi_metric_with_trend(
        c3, "在推广计划", kpis['本期在推广计划'], f"{kpi_mom['本期在推广计划']:.2f}% 环比",
        trend_key="本期在推广计划", trend_label="在推广计划（个）",
    )
    _render_kpi_metric_with_trend(
        c4, "店铺ROI", f"{kpis['店铺ROI']:.2f}", f"{kpi_mom['店铺ROI']:.2f}% 环比",
        trend_key="店铺ROI", trend_label="店铺ROI",
    )
    _render_kpi_metric_with_trend(
        c5, "在推广商品", kpis['在推广商品数量'], f"{kpi_mom['在推广商品数量']:.2f}% 环比",
        trend_key="在推广商品数量", trend_label="在推广商品（个）",
    )
    _render_kpi_metric_with_trend(
        c6, "加购件数", f"{kpis['加购件数']:,}", f"{kpi_mom['加购件数']:.2f}% 环比",
        trend_key="加购件数", trend_label="加购件数（件）",
    )

    # 可审计的本地智能摘要：复用当前/上期内存帧，不读文件、不调用外部 AI。
    report_ctx = st.session_state.get('cat_ctx') or {}
    report_period_choice = report_ctx.get('period_choice', '当日')
    report_period_label = report_ctx.get('sel_label', '日环比')
    report_period_log = next(
        (row for row in (st.session_state.get('period_logs') or [])
         if row.get('周期') == report_period_label),
        None,
    )
    operating_report = build_operating_daily_report(
        df,
        st.session_state.get('prev_df'),
        period_choice=report_period_choice,
        period_log=report_period_log,
        risk_spend_floor=100.0,
    )
    st.subheader("🧠 AI经营日报")
    with st.container(border=True):
        st.caption(
            f"口径：{operating_report['period_scope']}｜本地规则化解读，金额可与本期/上期对账；"
            "三渠道表示广告归因成交变化，不等同于全店GMV贡献。"
        )
        if operating_report['status'] != 'complete':
            st.warning(f"周期数据不完整，以下仅显示暂估总览：{operating_report['completeness']}")
            st.markdown(f"**总览（暂估）：** {operating_report['lines']['总览']}")
        else:
            for section in ('总览', '等级', '品类', '渠道', '风险'):
                st.markdown(f"**{section}：** {operating_report['lines'][section]}")

    st.divider()

    # 等级分布卡片
    grades = grade_distribution(df)
    if grades:
        cols = st.columns(len(grades))
        gc = {'S':'#ff4b4b','A+':'#ffab2e','A':'#4b9fff','B':'#2ec4b6','C':'#ff7b4b','D':'#6b7084'}
        for i, g in enumerate(grades):
            with cols[i]:
                st.markdown(f"""
                <div style="background:#262730;border-radius:8px;padding:12px;border-left:3px solid {gc.get(g['grade'],'#666')}">
                <h4 style="color:{gc.get(g['grade'],'#666')};margin:0">{g['grade']}级</h4>
                <p style="font-size:.8rem;color:#a3a8b8;margin:4px 0">数量: <b style="color:#fafafa">{g['cnt']}</b> ({g['pct']})</p>
                <p style="font-size:.8rem;color:#a3a8b8;margin:4px 0">GMV占比: <b style="color:#fafafa">{g['gmv_pct']}</b></p>
                <p style="font-size:.8rem;color:#a3a8b8;margin:4px 0">访客占比: <b style="color:#fafafa">{g['vis_pct']}</b></p>
                <p style="font-size:.8rem;color:#a3a8b8;margin:4px 0">预算占比: <b style="color:#fafafa">{g['spend_pct']}</b></p>
                </div>""", unsafe_allow_html=True)

    st.divider()

    # 分渠道
    channels = channel_summary(df)
    ch_total = sum(c['val'] for c in channels)
    if ch_total > 0:
        st.subheader("分渠道花费")
        for ch in channels:
            pct = ch['val'] / ch_total * 100 if ch_total else 0
            st.progress(pct / 100, text=f"{ch['name']}: ¥{ch['val']/1e4:.2f}万 ({pct:.1f}%)")

# ── Tab 2: 数据验收 ───────────────────────────────────────
with tab2:
    info = st.session_state.get('sources_info', [])
    if info:
        st.subheader("文件验收")
        st.dataframe(pd.DataFrame(info), width="stretch", hide_index=True)

    st.divider()

    st.subheader("数据源验收")
    status_data = build_source_status(
        info,
        [SOURCE_PRODUCT_ROI, SOURCE_WUJIE_SUBJECT, SOURCE_STOCK, SOURCE_PRODUCT_INFO],
    )
    st.dataframe(pd.DataFrame(status_data), width="stretch", hide_index=True)

    st.divider()

    # 全店对比
    st.subheader("全店数据对比")
    exec_gmv = df['GMV'].sum()
    exec_spend = df['付费'].sum()
    st.write(f"执行表 GMV: ¥{exec_gmv/1e4:.2f}万 | 执行表 花费: ¥{exec_spend/1e4:.2f}万")

# ── Tab 3: 预算决策看板 ───────────────────────────────────
with tab3:
    st.subheader("💰 预算决策看板")
    st.caption(
        "口径：不依赖计划组/后台当前预算/目标预算/出价；销售占比=GMV/全店GMV，"
        "花费占比=付费/全店付费，占比差=花费占比-销售占比，正数为超投；"
        "未来7日为所选周期日均等比例换算；库存只提醒不判定。"
        "角色判定与阈值锚定**固定近7日**（与左侧时间周期无关），角色层只拦加投、不改回收。"
    )

    products = _budget_decision_products_for_display(budget_decision_ctx.get('products'))
    tiers = _hide_budget_decision_columns(
        _normalize_rank_band_dataframe(budget_decision_ctx.get('tiers'))
    )
    plans = _hide_budget_decision_columns(budget_decision_ctx.get('plans'))
    raw_summary = budget_decision_ctx.get('summary') or {}
    summary = raw_summary if isinstance(raw_summary, dict) else dict(raw_summary)
    actual_days = budget_decision_ctx.get('actual_days')

    st.markdown("#### 摘要先行")
    summary_cards = [
        ("商品数", f"{int(_as_float(summary.get('商品数', len(products)))):,}"),
        ("周期GMV", _format_money(summary.get('周期GMV', summary.get('全店GMV', summary.get('GMV', 0))))),
        ("周期花费", _format_money(summary.get('周期花费', summary.get('全店付费', summary.get('付费', 0))))),
        ("待回收金额", _format_money(summary.get('待回收金额', summary.get('待回收预算', 0)))),
        ("建议增加金额", _format_money(summary.get('建议增加金额', summary.get('建议增加预算', 0)))),
        ("零GMV消耗", _format_money(summary.get('零GMV消耗', summary.get('零GMV花费', summary.get('零GMV付费', 0))))),
    ]
    metric_cols = st.columns(6)
    for metric_col, (label, value) in zip(metric_cols, summary_cards):
        metric_col.metric(label, value)
    if actual_days:
        st.caption(f"实际数据天数：{actual_days} 天；未来7日字段按所选周期日均等比例换算。")

    _render_threshold_freeze_panel()
    _render_budget_role_summary(products)

    product_tab, tier_tab, plan_tab = st.tabs([
        "商品预算决策", "排名档对齐", "推广计划管理"
    ])

    with product_tab:
        f1, f2 = st.columns(2)
        with f1:
            rank_band_filter = st.multiselect(
                "排名档",
                _option_values(products, '排名档'),
                key='budget_decision_tier_filter',
            )
        with f2:
            category_filter = st.multiselect(
                "淘宝类目",
                _option_values(products, '淘宝类目'),
                key='budget_decision_category_filter',
            )

        product_filtered = products.copy()
        for selected, column in (
            (rank_band_filter, '排名档'),
            (category_filter, '淘宝类目'),
        ):
            if selected and column in product_filtered.columns:
                product_filtered = product_filtered[
                    product_filtered[column].astype(str).isin(selected)
                ]

        st.caption(f"筛选结果：{len(product_filtered)} / {len(products)} 个商品")
        if products.empty:
            st.info("暂无商品预算决策数据。")
        elif product_filtered.empty:
            st.info("当前筛选条件下暂无商品预算决策。")
        else:
            display_products = _dedupe_dataframe_columns(product_filtered)
            product_columns = _budget_decision_product_columns(
                display_products,
                BUDGET_DECISION_MAIN_COLUMNS,
            )
            if not product_columns:
                product_columns = list(display_products.columns)
            if '占比差' in product_columns and '占比差' in display_products.columns:
                display_products['占比差（百分点）'] = (
                    pd.to_numeric(display_products['占比差'], errors='coerce') * 100
                )
                product_columns = [
                    '占比差（百分点）' if column == '占比差' else column
                    for column in product_columns
                ]
            product_table = display_products[product_columns]
            product_column_config = {
                '款式主图': st.column_config.LinkColumn(
                    '款式主图',
                    width='medium',
                    help='仅显示链接，不预加载图片',
                ),
                '链接投产': st.column_config.NumberColumn(
                    '链接投产', format='%.4f',
                    help='全链接口径：执行表 GMV ÷ 付费（含自然流量）；推广投产为纯广告归因口径',
                ),
                '推广投产': st.column_config.NumberColumn('推广投产', format='%.4f'),
                '排名档': st.column_config.TextColumn('排名档'),
                '决策角色': st.column_config.TextColumn(
                    '决策角色',
                    help='按固定近7日基准判定的投放角色：ZT主推/ZT趋势预警/TC投产/TD梯队/TD候选/CS测试/QC回收/清理/观察/未判定',
                ),
                '角色闸门': st.column_config.TextColumn(
                    '角色闸门',
                    help='放行加投=可加；禁止加投=本轮归零；测试封顶=最多500元/周',
                ),
                '决策角色依据': st.column_config.TextColumn('决策角色依据', width='large'),
                '角色闸门依据': st.column_config.TextColumn('角色闸门依据', width='large'),
                '风险命中': st.column_config.TextColumn(
                    '风险命中',
                    help='退款类风险命中时，放行/封顶一律降级为只降不增',
                ),
                '角色闸门命中': st.column_config.TextColumn(
                    '角色闸门命中',
                    help='本轮实际被闸门改动过的行才会显示',
                ),
                '库存提醒': st.column_config.TextColumn(
                    '库存提醒（只提醒不判定）',
                    help='库存只生成运营提醒，不参与预算分配判定',
                ),
                '销售占比': st.column_config.PercentColumn('销售占比'),
                '花费占比': st.column_config.PercentColumn('花费占比'),
                'GMV': st.column_config.NumberColumn('GMV', format='¥%.2f'),
                '付费': st.column_config.NumberColumn('付费', format='¥%.2f'),
                '周期GMV': st.column_config.NumberColumn('周期GMV', format='¥%.2f'),
                '周期花费': st.column_config.NumberColumn('周期花费', format='¥%.2f'),
                '待回收金额': st.column_config.NumberColumn('待回收金额', format='¥%.2f'),
                '建议增加金额': st.column_config.NumberColumn('建议增加金额', format='¥%.2f'),
                '零GMV消耗': st.column_config.NumberColumn('零GMV消耗', format='¥%.2f'),
                '基准应得花费': st.column_config.NumberColumn('基准应得花费', format='¥%.2f'),
                '预算错配金额': st.column_config.NumberColumn('预算错配金额', format='¥%.2f'),
                '未来7日花费基线': st.column_config.NumberColumn('未来7日花费基线', format='¥%.2f'),
                '未来7日建议调整金额': st.column_config.NumberColumn('未来7日建议调整金额', format='¥%.2f'),
                '未来7日建议花费': st.column_config.NumberColumn('未来7日建议花费', format='¥%.2f'),
                '未来7日GMV': st.column_config.NumberColumn('未来7日GMV', format='¥%.2f'),
                '未来7日花费': st.column_config.NumberColumn('未来7日花费', format='¥%.2f'),
                '占比差(pp)': st.column_config.NumberColumn(
                    '占比差（百分点）',
                    format='%.2f',
                    help='花费占比-销售占比，正数为超投',
                ),
                '占比差（百分点）': st.column_config.NumberColumn(
                    '占比差（百分点）',
                    format='%.1f',
                    help='花费占比-销售占比，正数为超投',
                ),
            }
            st.dataframe(
                product_table,
                width="stretch",
                hide_index=True,
                height=650,
                column_config={
                    column: config
                    for column, config in product_column_config.items()
                    if column in product_table.columns
                },
            )

    with tier_tab:
        st.caption(
            "显著错配口径：abs占比差>5个百分点 且 abs错配金额>=500；"
            "排名档为Top1-5、Top6-10、Top11-20、Top21-50、Top51-100、Top101-尾部、零GMV回收池。"
        )
        if tiers.empty:
            st.info("暂无排名档对齐数据。")
        else:
            tier_label_column = next(
                (column for column in ('排名档', '销售梯队', '梯队', '层级') if column in tiers.columns),
                None,
            )
            if tier_label_column and {'销售占比', '花费占比'}.issubset(tiers.columns):
                tier_chart = tiers[[tier_label_column, '销售占比', '花费占比']].copy()
                tier_chart = tier_chart[
                    ~tier_chart[tier_label_column].astype(str).str.contains(
                        '零GMV回收池|零GMV|零成交|零销售',
                        regex=True,
                        na=False,
                    )
                ]
                if tier_chart.empty:
                    st.info("排名档暂无可绘制结构占比。")
                else:
                    st.bar_chart(
                        tier_chart,
                        x=tier_label_column,
                        y=['销售占比', '花费占比'],
                        height=260,
                    )
            else:
                st.info("缺少销售占比或花费占比，暂不绘制结构对齐图。")
            tier_columns = [
                column for column in (
                    '排名档', '商品数', 'GMV', '销售占比', '付费', '花费占比',
                    '占比差', '占比差(pp)', '预算错配金额', 'ROI', '未来7日建议调整金额',
                    '待回收金额', '建议增加金额', '零GMV消耗', '显著错配',
                )
                if column in tiers.columns
            ]
            tier_columns.extend(
                column for column in tiers.columns if column not in tier_columns
            )
            tier_table = tiers[tier_columns] if tier_columns else tiers
            tier_column_config = {
                '销售占比': st.column_config.PercentColumn('销售占比'),
                '花费占比': st.column_config.PercentColumn('花费占比'),
                'GMV': st.column_config.NumberColumn('GMV', format='¥%.2f'),
                '付费': st.column_config.NumberColumn('付费', format='¥%.2f'),
                '占比差': st.column_config.PercentColumn(
                    '占比差',
                    help='花费占比-销售占比，正数为超投',
                ),
                '占比差(pp)': st.column_config.NumberColumn(
                    '占比差（百分点）',
                    format='%.2f',
                    help='花费占比-销售占比，正数为超投',
                ),
                '预算错配金额': st.column_config.NumberColumn('预算错配金额', format='¥%.2f'),
                'ROI': st.column_config.NumberColumn('ROI', format='%.2f'),
                '未来7日建议调整金额': st.column_config.NumberColumn('未来7日建议调整金额', format='¥%.2f'),
                '待回收金额': st.column_config.NumberColumn('待回收金额', format='¥%.2f'),
                '建议增加金额': st.column_config.NumberColumn('建议增加金额', format='¥%.2f'),
                '零GMV消耗': st.column_config.NumberColumn('零GMV消耗', format='¥%.2f'),
            }
            st.dataframe(
                tier_table,
                width="stretch",
                hide_index=True,
                column_config={
                    column: config
                    for column, config in tier_column_config.items()
                    if column in tier_table.columns
                },
            )

    with plan_tab:
        st.caption(
            "合集计划只给父级诊断，勿把商品建议重复落成预算；"
            "「预算 / 出价方式」取自无界目录下最新的计划列表快照，"
            "按计划ID只读匹配，不做推断。"
        )
        st.caption(
            "来源：" + plan_config_caption((st.session_state.get('plan_config') or {}).get('source'))
            + "。注意：这两列是**当前配置**，查历史日期时显示的也是最新值，不是历史值。"
        )
        if plans.empty:
            st.info("暂无推广计划管理数据。")
        else:
            plan_columns = [
                column for column in (
                    '序号', '计划ID', '计划名称', '所属渠道', '商品数', '计划类型', '计划经营结构',
                    '预算', '出价方式',
                    '周期花费', '归因成交', 'ROI', '日均花费', '方向证据', '计划管理建议',
                )
                if column in plans.columns
            ] or list(plans.columns)
            plan_table = plans[plan_columns].copy()
            if '预算' in plan_table.columns:
                # 计划列表里既有「310元」这种数值，也有「不限」这种文字，还可能有没有匹配到的空值。
                # Streamlit 的表格序列化要求整列同类型（mixed 会抛 ArrowInvalid），
                # 所以展示前统一转成字符串：数值→'310'，文字→原样，空值→''。
                plan_table['预算'] = plan_table['预算'].map(format_budget_cell)
            plan_column_config = {
                '序号': st.column_config.NumberColumn('序号', format='%d'),
                '商品数': st.column_config.NumberColumn('商品数', format='%d'),
                '周期花费': st.column_config.NumberColumn('周期花费', format='¥%.2f'),
                '归因成交': st.column_config.NumberColumn('归因成交', format='¥%.2f'),
                'ROI': st.column_config.NumberColumn('ROI', format='%.2f'),
                '日均花费': st.column_config.NumberColumn(
                    '日均花费', format='¥%.2f',
                    help='所选周期花费 ÷ 所选周期天数（当日口径即 1 天）',
                ),
            }
            st.dataframe(
                plan_table,
                width="stretch",
                hide_index=True,
                height=560,
                column_config={
                    column: config
                    for column, config in plan_column_config.items()
                    if column in plan_table.columns
                },
            )

# ── Tab 4: 执行表 ────────────────────────────────────────
with tab4:
    st.subheader(f"商品推广执行表 ({len(df)} 条)")
    execution_df = ensure_export_columns(df)
    with st.expander("查看高/正常/低判定口径", expanded=False):
        st.markdown(
            """
            - **链接投产状态**：链接投产=`GMV÷推广花费`；先按`退款后GSV→退款后销售成本→可投放毛利额`计算盈亏平衡线，高线取`max(盈亏平衡线×1.2，同组商品P75)`，不使用链接实际投产过滤。
            - **可售天数状态**：`<14天=紧急`、`14~28天=需关注`、`28~40天=充足`、`>40天=积压`。
            - **同组口径**：当前所选店铺内，同时满足同淘宝类目、同价格段；过去28天无同组数据时不跨类目或价格段回退。
            - **商品加购率 / 商品支付转化率 / 推广加购率**：与同组过去28天加权基准B比较；`≥1.2B=高`、`0.8B~1.2B=正常`、`<0.8B=低`。
            - 商品或推广访客不足100时显示**样本不足**；链接状态缺退款率时也显示**样本不足**。
            """
        )

    # 筛选 + 列设置(2026-07-11:列设置移到筛选行右侧,popover 下拉形式、占位小;
    # 勾选持久化到 cache/column_prefs.json,展示顺序固定按 EXPORT_COLS,仅影响显示、导出仍全量)
    # 2026-11-11 v2.68:取消硬编码「强制显示」白名单(状态/判定类 12 列此前每次 rerun
    # 都被塞回偏好,导致取消勾选无效)。现在显示严格按勾选;导出仍全量,因为
    # export_execution_table / export_all_sheets 走 execution_export_frame() -> EXPORT_COLS。
    all_cols = [c for c in EXPORT_COLS if c in execution_df.columns]
    if "exec_cols_pref" not in st.session_state:
        st.session_state["exec_cols_pref"] = resolve_display_columns(
            all_cols, _load_column_prefs())
    st.session_state["exec_cols_pref"] = resolve_display_columns(
        all_cols, st.session_state["exec_cols_pref"])

    cf1, cf2, cf3, cf4 = st.columns([3, 2, 2, 1.3], vertical_alignment="bottom")
    with cf1:
        search = st.text_input("搜索", placeholder="宝贝ID / 货号 / 类目...", key="exec_search")
    with cf2:
        level_filter = st.selectbox("等级", ['']+['S','A+','A','B','C','D'], key="exec_level")
    with cf3:
        actions = [''] + list(ACTION_RULES.keys())
        action_filter = st.selectbox("操作", actions, key="exec_action")
    with cf4:
        with st.popover(
                f"⚙️ 列设置({len(st.session_state['exec_cols_pref'])}/{len(all_cols)})",
                width="stretch"):
            picked = st.multiselect(
                "展示列(可输入关键字快速查找;清空=全部列)",
                options=all_cols,
                default=st.session_state["exec_cols_pref"],
                key="exec_cols_ms",
            )
            cb1, cb2 = st.columns(2)
            if cb1.button("应用并记住", width="stretch", key="exec_cols_apply"):
                pref = resolve_display_columns(all_cols, picked)
                st.session_state["exec_cols_pref"] = pref
                _save_column_prefs(pref)
                if pref == list(all_cols):
                    # 勾空=全部列:勾选框同步成全部,避免"框里空着、表里全列"的错位
                    st.session_state.pop("exec_cols_ms", None)
                st.rerun()
            if cb2.button("恢复全部列", width="stretch", key="exec_cols_reset"):
                st.session_state["exec_cols_pref"] = all_cols
                _save_column_prefs(all_cols)
                st.session_state.pop("exec_cols_ms", None)
                st.rerun()
            st.caption("勾选即生效;取消勾选=隐藏该列(含状态/判定列);"
                       "清空=全部列。仅影响表格显示,导出 Excel 仍包含全部字段")


    # 过滤
    filtered = execution_df.copy()
    if level_filter:
        filtered = filtered[filtered['综合等级'] == level_filter]
    if action_filter:
        filtered = filtered[filtered['对应操作'] == action_filter]
    # 搜索加固(2026-07-10):粘贴的ID常带首尾空格/换行导致0命中,统一strip;
    # regex=False 纯子串匹配,避免特殊字符被当正则解析
    search_q = str(search).strip() if search else ""
    if search_q:
        mask = pd.Series(False, index=filtered.index)
        for col in ['宝贝ID','货号','淘宝类目','商品定位','商品名称','v9.3信号','MetoX算法命中','MetoX算法原因']:
            if col in filtered.columns:
                mask |= filtered[col].astype(str).str.contains(
                    search_q, case=False, na=False, regex=False)
        filtered = filtered[mask]

    active_filters = []
    if search_q: active_filters.append(f"搜索「{search_q}」")
    if level_filter: active_filters.append(f"等级={level_filter}")
    if action_filter: active_filters.append(f"操作={action_filter}")
    st.caption(f"筛选结果: {len(filtered)} 条"
               + (f"　|　生效筛选: {'; '.join(active_filters)}" if active_filters else ""))
    st.caption(
        "当前周期 GMV Top15 品类内，各品类 GMV Top50 单品（仅 GMV>0）的指定状态格着色："
        "高、需关注=浅蓝，低、紧急=浅粉；文字使用深色保证对比。其他单元格不打底色，"
        "排名基于完整执行表，不随搜索、等级或操作筛选变化。"
    )

    # 显示列(按 EXPORT_COLS 固定顺序取勾选交集)
    show_cols = resolve_display_columns(all_cols, st.session_state["exec_cols_pref"])
    st.dataframe(
        style_execution_table(filtered[show_cols], ranking_df=execution_df),
        width="stretch",
        hide_index=True,
        height=600,
        column_config={
            "当前有效投放计划数": st.column_config.NumberColumn(
                "当前有效投放计划数",
                format="%d",
            ),
            "商品加购率": st.column_config.NumberColumn(
                "商品加购率",
                format="percent",
            ),
            "推广加购率": st.column_config.NumberColumn(
                "推广加购率",
                format="percent",
            ),
            "商品支付转化率": st.column_config.NumberColumn(
                "商品支付转化率",
                format="percent",
            ),
        },
    )

    # 导出(2026-07-10 修复:此前下载按钮嵌在导出按钮的 if 里,任意页面刷新即消失、
    # 点下载后自身触发的 rerun 也会让它消失导致"下载不了"。改为:生成结果存 session,
    # 下载按钮持久显示;生成时记录行数与时间,筛选变化后重新生成即可)
    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    export_cat_ctx = st.session_state.get('cat_ctx') or {}
    ce1, ce2 = st.columns(2)
    with ce1:
        if st.button("📥 生成执行表 Excel", key="export_exec"):
            with st.spinner(f"生成中({len(filtered)} 行，含品类投放表)..."):
                st.session_state["exec_xlsx"] = _call_export_execution_table(
                    filtered,
                    cat_ctx=export_cat_ctx,
                    ranking_df=execution_df,
                    category_df=df,
                )
                st.session_state["exec_xlsx_meta"] = \
                    f"{len(filtered)}行 {datetime.now().strftime('%H:%M:%S')}"
        if st.session_state.get("exec_xlsx"):
            st.download_button(
                f"⬇️ 下载执行表 ({st.session_state['exec_xlsx_meta']})",
                st.session_state["exec_xlsx"],
                f"推广执行表_{st.session_state.get('cur_date', '')}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                XLSX_MIME, key="dl_exec")
    with ce2:
        st.session_state.pop("all_xlsx", None)
        st.session_state.pop("all_xlsx_meta", None)
        all_sheets_download = partial(
            _call_export_all_sheets_with_budget_context,
            filtered,
            cat_ctx=export_cat_ctx,
            ranking_df=execution_df,
            category_df=df,
            new_product_ctx=_filter_new_product_context(
                st.session_state.get('new_product_ctx'), filtered
            ),
            plan_config=st.session_state.get('plan_config') or {},
        )
        st.download_button(
            f"📦 生成并下载全部 Sheet Excel ({len(filtered)} 行)",
            data=all_sheets_download,
            file_name=(
                f"推广工具全部分析_{st.session_state.get('cur_date', '')}_"
                f"{datetime.now().strftime('%Y%m%d')}.xlsx"
            ),
            mime=XLSX_MIME,
            key="dl_all_deferred",
            on_click="ignore",
        )
    st.caption("提示:执行表仍为先生成后下载;全部Sheet点击后生成并直接下载")

# ── Tab 5: 商品分层 ───────────────────────────────────────
with tab5:
    try:
        seg_data = segment_summary(df)
        if seg_data:
            st.dataframe(pd.DataFrame(seg_data), width="stretch", hide_index=True, height=600)
        else:
            st.info("暂无分层数据")
    except Exception as e:
        st.warning(f"分层数据加载失败: {e}")

# ── Tab 6: 品类分析 ───────────────────────────────────────
with tab6:
    try:
        _ctx = st.session_state.get('cat_ctx') or {}
        if _ctx.get('sel_label'):
            st.caption(f"口径: 主表={_ctx.get('period_choice','当日')} | "
                       f"付费环比按 {_ctx['sel_label']} 窗口 | "
                       "7日GMV/访客数/加购/支付转化率环比按 7日窗口")
        cat = category_analysis(df, cat_ctx=_ctx)
        if cat['basic']:
            st.subheader("品类基础分析")
            basic_category_df = pd.DataFrame(cat['basic'], columns=CATEGORY_BASIC_COLUMNS)
            st.iframe(
                build_dataframe_copy_button_html(
                    basic_category_df,
                    button_key="category-basic",
                ),
                height=1,
            )
            st.dataframe(basic_category_df, width="stretch", hide_index=True)
            t = cat.get('total', {})
            st.caption(f"合计: {t.get('cnt',0)} 商品 | GMV ¥{t.get('gmv',0)/1e4:.2f}万 | ROI {t.get('roi',0)}")
        if cat.get('invest'):
            st.subheader("品类投放占比与ROI")
            category_investment_df = pd.DataFrame(
                cat['invest'], columns=CATEGORY_INVESTMENT_COLUMNS
            )
            st.iframe(
                build_dataframe_copy_button_html(
                    category_investment_df,
                    button_key="category-investment",
                ),
                height=1,
            )
            st.dataframe(category_investment_df, width="stretch", hide_index=True)
    except Exception as e:
        st.warning(f"品类分析加载失败: {e}")

# ── Tab 7: 退款风险 ───────────────────────────────────────
with tab7:
    try:
        ref = refund_risk(df)
        st.caption(f"退款率>70%(聚水潭30日cohort口径): {len(ref)} 个")
        if ref:
            st.dataframe(pd.DataFrame(ref), width="stretch", hide_index=True, height=500)
    except Exception as e:
        st.warning(f"退款风险加载失败: {e}")

# ── Tab 8: 渠道分析 ───────────────────────────────────────
with tab8:
    try:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("关键词 TOP30")
            kw = channel_top(df, '关键词')
            if kw: st.dataframe(pd.DataFrame(kw), width="stretch", hide_index=True)
            else: st.caption("暂无关键词数据")
        with c2:
            st.subheader("人群 TOP30")
            cr = channel_top(df, '人群')
            if cr: st.dataframe(pd.DataFrame(cr), width="stretch", hide_index=True)
            else: st.caption("暂无人群数据")
        st.subheader("全站 TOP30")
        om = channel_top(df, '全站')
        if om: st.dataframe(pd.DataFrame(om), width="stretch", hide_index=True)
        else: st.caption("暂无全站数据")
    except Exception as e:
        st.warning(f"渠道分析加载失败: {e}")

# ── Tab 9: 库存动作 ───────────────────────────────────────
with tab9:
    try:
        inv = inventory_actions(df)
        st.subheader(f"高库存低动销 ({len(inv.get('hi_stock',[]))} 个)")
        if inv.get('hi_stock'): st.dataframe(pd.DataFrame(inv['hi_stock']), width="stretch", hide_index=True)
        st.subheader(f"缺货且在投 ({len(inv.get('no_stock',[]))} 个)")
        if inv.get('no_stock'): st.dataframe(pd.DataFrame(inv['no_stock']), width="stretch", hide_index=True)
    except Exception as e:
        st.warning(f"库存动作加载失败: {e}")

# ── Tab 10: 新品视图 ──────────────────────────────────────
with tab10:
    try:
        _render_new_product_view(st.session_state.get('new_product_ctx'))
    except Exception as e:
        st.warning(f"新品视图加载失败: {e}")
