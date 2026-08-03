"""MetoX 半自动推广工具 — Streamlit 版。"""
from __future__ import annotations

import os, re, time, io, json, shutil, tempfile, hashlib
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

from core.loader import read_prepared_source
from core.merge import build_shared_dimensions, merge_sources
from core.periods import (
    aggregate_category_metrics,
    aggregate_period_frame,
    apply_period_comparisons,
    apply_rolling_daily_metrics,
    build_period_ranges,
    iter_dates,
)
from core.segmentation import segment_products
from core.analysis import (
    calc_kpis, calc_kpi_mom_percent, grade_distribution, channel_summary,
    segment_summary, category_analysis, refund_risk, channel_top, inventory_actions,
    build_operating_daily_report, build_ai_task_view,
)
from core.acceptance import build_source_status
from core.v93_signal import parse_v93_report
from core.metox_algorithm import apply_metox_algorithm
from core.task_engine import AI_TASK_MAIN_COLUMNS, parse_plan_execution_details
from core.jst_stock import find_snapshot, load_snapshot_as_stock
from core.jst_refund import find_refund_snapshot, load_refund_rates
from core.exporter import export_execution_table, export_all_sheets, style_execution_table
from core.version_control import find_repository, get_status, next_version_name, save_version
from core.version import APP_VERSION, DISPLAY_VERSION, PRODUCT_NAME
from core.schema import (
    ALLOWED_SOURCE_TYPES, SOURCE_PRODUCT_INFO, SOURCE_PRODUCT_ROI, SOURCE_STOCK,
    SOURCE_WUJIE_SUBJECT, classify_file, EXPORT_COLS, ACTION_RULES, compute_bounds,
)

st.set_page_config(page_title=f"{DISPLAY_VERSION} 半自动推广工具", layout="wide")
st.title(f"{DISPLAY_VERSION} 半自动推广工具")
GIT_PROJECT_NAME = PRODUCT_NAME

# ── 初始化 Session State ─────────────────────────────────
for key in ['df','prev_df','kpis','prev_kpis','kpi_mom','plan_ids','prev_plan_ids','cur_date','prev_date',
            'sources_info','traffic_bounds','value_bounds','traffic_ref','value_ref',
            'loaded','processing_msg','timings','total_time','period_logs','cat_ctx']:
    if key not in st.session_state:
        st.session_state[key] = None if key != 'loaded' else False


def _parse_bounds(s):
    if not s:
        return None
    parts = [float(x.strip()) for x in re.split(r'[,，\s]+', s.strip()) if x.strip()]
    if len(parts) == 5 and all(parts[i] < parts[i + 1] for i in range(4)):
        return parts
    return None


# ── 加载数据 ──────────────────────────────────────────────
def _log_time(timings, label, start, extra=""):
    """记录并打印单步耗时"""
    elapsed = time.perf_counter() - start
    msg = f"[耗时] {label}：{elapsed:.2f} 秒"
    if extra: msg += f" | {extra}"
    print(msg)
    timings.append((label, f"{elapsed:.2f} 秒", extra))
    return elapsed

def _scan_target_files(path):
    files = []
    if not path or not os.path.isdir(str(path)):
        return files
    for fn in sorted(os.listdir(str(path))):
        fp = os.path.join(str(path), fn)
        if not os.path.isfile(fp) or fn.startswith('~$'):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ('.xlsx', '.xls', '.csv'):
            continue
        src_type = classify_file(fn)
        if src_type in ALLOWED_SOURCE_TYPES:
            files.append((fp, fn, src_type))
    return files


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


# ── 日级文件读取缓存（重复读取同一文件时直接复用）──
@st.cache_data(show_spinner=False)
def _load_day_raw_cached(fp, src_type, parsed_cache_dir=None, _mtime=None):
    """缓存单文件读取+标准化结果。_mtime 参与缓存键:源文件更新后旧缓存自动失效
    (此前仅按路径缓存,同名文件改内容后本进程内会拿到旧数据)。"""
    return read_prepared_source(fp, src_type, parsed_cache_dir=parsed_cache_dir)


def _read_sources_for_date(
    biz_folder, wujie_folder, product_folder, cache_dir, period_label,
    include_product=True,
):
    """读取一天的源文件（使用文件级缓存，不重复解析）"""
    sources = {}
    files_info = []
    copied = 0
    file_list = _scan_target_files(biz_folder) + _scan_target_files(wujie_folder)
    if include_product:
        file_list += _scan_target_files(product_folder)

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
        except Exception as e:
            files_info.append({
                'name': fn, 'source': src_type, 'rows': 0,
                'cols': 0, 'period': f'{period_label} 错误: {str(e)[:80]}',
            })

    return sources, files_info, copied, len(file_list)


def _load_shared_dimensions(product_folder, cache_root, jst_entry=None,
                            jst_refund_entry=None):
    """一次加载并预聚合周期内每天共用的商品资料、库存和退款率维度。"""
    sources, info, copied, total = _read_sources_for_date(
        None, None, product_folder, Path(cache_root) / '_shared_dimensions',
        '共享维度', include_product=True,
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

    return build_shared_dimensions(sources), (info, copied, total)


def _load_single_day(base_root, day, year, shop, product_folder, cache_root,
                     include_product, label_prefix, jst_entry=None, jst_refund_entry=None,
                     shared_dimensions=None, shared_dimension_meta=None):
    """加载单日数据：扫描→读文件→合并→返回 day_df"""
    biz_folder, wujie_folder = _build_paths(base_root, day, year, shop)
    if not os.path.isdir(str(biz_folder)) and not os.path.isdir(str(wujie_folder)):
        return None, [], 0, 0, set()

    day_cache = cache_root / day
    sources, info, copied, total = _read_sources_for_date(
        biz_folder, wujie_folder, product_folder, day_cache,
        f"{label_prefix}:{day}",
        include_product=include_product and shared_dimensions is None,
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
                     'period': f'{label_prefix}:{day}'})

    # 聚水潭退款率直连:30日 cohort 退款率,注入商品资料源
    if shared_dimensions is None and include_product and jst_refund_entry is not None:
        jr_name, jr_df = jst_refund_entry
        sources.setdefault(SOURCE_PRODUCT_INFO, []).append((jr_name, jr_df))
        info.append({'name': jr_name, 'source': SOURCE_PRODUCT_INFO,
                     'rows': len(jr_df), 'cols': len(jr_df.columns),
                     'period': f'{label_prefix}:{day}'})

    if not sources:
        return None, info, copied, total, set()

    if include_product and shared_dimension_meta is not None:
        shared_info, shared_copied, shared_total = shared_dimension_meta
        info.extend(shared_info)
        copied += shared_copied
        total += shared_total

    day_df, day_plan_ids = merge_sources(sources, dimensions=shared_dimensions)
    if day_df is None or len(day_df) == 0:
        return None, info, copied, total, set(day_plan_ids)

    day_df = day_df.copy()
    day_df['_数据日期'] = day
    return day_df, info, copied, total, day_plan_ids


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
        day_df, info, copied, total, pids = entry
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
}


def _cat_sum(frame, col):
    """品类级求和 → {类目原始值: 合计}。frame 为空/缺列时返回空 dict。"""
    if frame is None or len(frame) == 0 or "淘宝类目" not in frame.columns or col not in frame.columns:
        return {}
    w = frame[["淘宝类目", col]].copy()
    w[col] = pd.to_numeric(w[col], errors="coerce").fillna(0.0)
    return w.groupby("淘宝类目")[col].sum().to_dict()


def _load_folder_cached(base_root, year, shop, date_str, product_folder, cache_dir=None,
                        jst_snap_dir=None, jst_refund_dir=None, period_choice="当日"):
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

    shared_dimensions, shared_dimension_meta = _load_shared_dimensions(
        product_folder, cache_root, jst_entry=jst_entry,
        jst_refund_entry=jst_refund_entry,
    )

    periods = build_period_ranges(date_str)
    sel_label = PERIOD_LABEL_MAP.get(period_choice, "日环比")
    comparison_labels = [sel_label] if sel_label in periods else ["日环比"]

    # ── 1. 收集所有需要的日期（去重）──
    t0 = time.perf_counter()
    all_needed_dates = {date_str}  # 本期当天
    # 日销/花费滚动字段固定需要近 15 天；环比只读取当前选择周期，避免 7 天口径额外读取 30 天历史。
    rolling_start = periods.get("15日环比", {}).get("current_start", date_str)
    for d in iter_dates(rolling_start, date_str):
        all_needed_dates.add(d)
    for label in comparison_labels:
        p = periods[label]
        for d in iter_dates(p['current_start'], p['current_end']):
            all_needed_dates.add(d)
        for d in iter_dates(p['prev_start'], p['prev_end']):
            all_needed_dates.add(d)
    _log_time(timings, "收集所有日期", t0, f"{len(all_needed_dates)} 个不同日期")

    # ── 2. 一次性加载每个日期的数据（仅 include_product=True 的首日）──
    t0 = time.perf_counter()
    day_map = {}   # {date: (day_df, info, copied, total, plan_ids)}
    first_day_sources = None  # 本期当天的 sources（用于 includes_product）
    sorted_needed_dates = sorted(all_needed_dates)
    load_progress = st.progress(0, text=f"准备读取 {len(sorted_needed_dates)} 天数据...")
    for idx, day in enumerate(sorted_needed_dates, start=1):
        include_product = (day == date_str)  # 只有本期首日加载商品资料
        entry = _load_single_day(
            base_root, day, year, shop, product_folder, cache_root,
            include_product, '周期', jst_entry=jst_entry, jst_refund_entry=jst_refund_entry,
            shared_dimensions=shared_dimensions,
            shared_dimension_meta=shared_dimension_meta,
        )
        day_map[day] = entry
        if include_product and entry[0] is not None:
            first_day_sources = entry
        load_progress.progress(
            idx / max(len(sorted_needed_dates), 1),
            text=f"读取文件中 {idx}/{len(sorted_needed_dates)}：{day}"
        )
    load_progress.empty()
    _log_time(timings, "批量加载所有日期", t0, f"{len(day_map)} 天")

    # ── 3. 本期当天 ──
    current_days = list(iter_dates(date_str, date_str))
    cur_result = load_period_data(current_days, day_map)
    df = cur_result['df']
    if df is None or len(df) == 0:
        return None, None, [], set(), set(), timings, period_logs, {}

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
    df = apply_metox_algorithm(df, day_map, date_str)
    _log_time(timings, "MetoX算法打标", t_step, f"{len(df)} 行")
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
        ["GMV"],
    )
    h7_previous = aggregate_category_metrics(
        day_map,
        iter_dates(h7_period["prev_start"], h7_period["prev_end"]),
        ["GMV", "付费", "关键词", "人群", "全站"],
    )
    cat_ctx = {
        'period_choice': period_choice,
        'sel_label': sel_label,
        'sel_prev_spend': _cat_sum(sel_prev_frame, '付费'),
        'h7_cur_gmv': h7_current['GMV'],
        'h7_prev_gmv': h7_previous['GMV'],
        'h7_prev_spend': h7_previous['付费'],
        'h7_prev_keyword': h7_previous['关键词'],
        'h7_prev_crowd': h7_previous['人群'],
        'h7_prev_omni': h7_previous['全站'],
    }

    return df, sel_prev_frame, files_info, plan_ids, sel_prev_plan_ids, timings, period_logs, cat_ctx


# ── 缓存：分层（分界点变化时重新计算）─────────────────────
# 展示/导出保留2位小数的列(2026-07-11 运营要求;链接投产/全站环比为同族列一并纳入)
ROUND2_COLS = [
    '日销3', '日销7', '日销15', '花费3', '花费7', '花费15',
    '预估利润', '推广投产', '链接投产', '日均消耗', '利润率',
    '人群环比', '关键词环比', '全站环比',
    '推广实际投产', '链接实际投产', '购物车价值', '件单价', '倍率',
]


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


def _cache_key(base_root, year, shop, date_str):
    """生成安全的缓存目录名，避免不同来源数据互相覆盖"""
    raw = f"{base_root}|{year}|{shop}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


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
    if st.button("保存新版本", use_container_width=True, key="git_save_version"):
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
        ["当日", "3天", "7天", "15天", "30天"],
        index=0,
        key="period_choice_input",
        help="主表/看板/品类分析的展示口径:当日=所填日期单日;3天=日期往前3天累计"
             "(如填2026-07-08则为07-06~07-08,环比对比07-03~07-05),7天/15天/30天同理"
    )

    load_requested = st.button(
        "加载并处理数据",
        type="primary",
        use_container_width=True,
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
                use_container_width=True,
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
                use_container_width=True,
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
                df, prev_df, files_info, plan_ids, prev_plan_ids, timings, period_logs, cat_ctx = \
                    None, None, [], set(), set(), [], [], {}
                errors = []
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
                        df, prev_df, files_info, plan_ids, prev_plan_ids, timings, period_logs, cat_ctx = \
                            _load_folder_cached(
                                root, year, shop, date_str, product_folder,
                                cache_dir=str(file_cache_dir),
                                jst_snap_dir=(jst_snap_dir or "").strip().strip('"') or None,
                                jst_refund_dir=(jst_refund_dir or "").strip().strip('"') or None,
                                period_choice=period_choice,
                            )

                if df is None or len(df) == 0:
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
                    st.session_state.prev_date = None
                    st.session_state.sources_info = files_info
                    st.session_state.cat_ctx = cat_ctx
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
ai_task_df = build_ai_task_view(st.session_state.df)
kpis = st.session_state.kpis
kpi_mom = st.session_state.kpi_mom or calc_kpi_mom_percent(kpis, {})

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📈 数据看板", "📋 数据验收", "🤖 AI任务视图", "📊 执行表",
    "🎯 商品分层", "📂 品类分析", "⚠ 退款风险", "🔑 渠道分析", "📦 库存动作"
])

# ── Tab 1: 数据看板 ───────────────────────────────────────
with tab1:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("GMV", f"¥{kpis['GMV']/1e4:.2f}万", f"{kpi_mom['GMV']:.2f}% 环比")
    c2.metric("本期花费", f"¥{kpis['本期花费']/1e4:.2f}万", f"{kpi_mom['本期花费']:.2f}% 环比")
    c3.metric("在推广计划", kpis['本期在推广计划'], f"{kpi_mom['本期在推广计划']:.2f}% 环比")
    c4.metric("店铺ROI", f"{kpis['店铺ROI']:.2f}", f"{kpi_mom['店铺ROI']:.2f}% 环比")
    c5.metric("在推广商品", kpis['在推广商品数量'], f"{kpi_mom['在推广商品数量']:.2f}% 环比")
    c6.metric("加购件数", f"{kpis['加购件数']:,}", f"{kpi_mom['加购件数']:.2f}% 环比")

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
        st.dataframe(pd.DataFrame(info), use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("数据源验收")
    status_data = build_source_status(
        info,
        [SOURCE_PRODUCT_ROI, SOURCE_WUJIE_SUBJECT, SOURCE_STOCK, SOURCE_PRODUCT_INFO],
    )
    st.dataframe(pd.DataFrame(status_data), use_container_width=True, hide_index=True)

    st.divider()

    # 全店对比
    st.subheader("全店数据对比")
    exec_gmv = df['GMV'].sum()
    exec_spend = df['付费'].sum()
    st.write(f"执行表 GMV: ¥{exec_gmv/1e4:.2f}万 | 执行表 花费: ¥{exec_spend/1e4:.2f}万")

# ── Tab 3: AI任务视图 ────────────────────────────────────
with tab3:
    st.subheader("🤖 AI任务视图")
    st.caption(
        "只读决策视图：仅基于最终执行表生成商品级建议，不重读数据、不重跑MetoX或商品分层，"
        "也不会修改执行表及推广平台预算。"
    )
    if not ai_task_df.empty:
        source_sample = ai_task_df.iloc[0]
        st.caption(
            f"AI规则数据来源｜商品策略：{source_sample['商品策略数据源']}｜"
            f"渠道动作：{source_sample['渠道动作数据源']}｜"
            f"计划动作：{source_sample['计划动作数据源']}"
        )

    task_counts = {
        '全部任务': len(ai_task_df),
        'P1': int((ai_task_df['执行优先级'] == 'P1').sum()),
        '放量': int((ai_task_df['AI任务类型'] == '放量任务').sum()),
        '测试': int((ai_task_df['AI任务类型'].isin(['测试任务', '恢复测试任务'])).sum()),
        '稳量': int((ai_task_df['AI任务类型'] == '稳量任务').sum()),
        '渠道调整': int((ai_task_df['AI任务类型'] == '渠道调整任务').sum()),
        '止损': int((ai_task_df['AI任务类型'] == '止损任务').sum()),
        '人工复核': int((ai_task_df['AI任务类型'] == '人工复核任务').sum()),
    }
    metric_cols = st.columns(len(task_counts))
    for metric_col, (label, value) in zip(metric_cols, task_counts.items()):
        metric_col.metric(label, value)

    def _task_options(column):
        if column not in ai_task_df.columns:
            return []
        return sorted({
            str(value).strip() for value in ai_task_df[column].dropna()
            if str(value).strip() not in ('', 'nan', 'None')
        })

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        task_type_filter = st.multiselect(
            "AI任务类型", _task_options('AI任务类型'), key='ai_task_type_filter'
        )
    with f2:
        task_priority_filter = st.multiselect(
            "执行优先级", ['P1', 'P2', 'P3', 'P4'], key='ai_task_priority_filter'
        )
    with f3:
        task_grade_filter = st.multiselect(
            "综合等级", _task_options('综合等级'), key='ai_task_grade_filter'
        )
    with f4:
        task_position_filter = st.multiselect(
            "商品定位", _task_options('商品定位'), key='ai_task_position_filter'
        )

    f5, f6, f7 = st.columns(3)
    with f5:
        task_cycle_filter = st.multiselect(
            "商品周期", _task_options('商品周期'), key='ai_task_cycle_filter'
        )
    with f6:
        metox_filter_options = [
            label for label in (
                '明日之星', '强主推', '近3日潜力', '爆发', '亏损风险',
                '加预算', '饱和观察', '保护持有', '削减', '溢出',
            )
            if ai_task_df['MetoX算法命中'].astype(str).str.contains(
                label, regex=False, na=False
            ).any()
        ]
        task_metox_filter = st.multiselect(
            "MetoX算法命中", metox_filter_options, key='ai_task_metox_filter'
        )
    with f7:
        task_category_filter = st.multiselect(
            "淘宝类目", _task_options('淘宝类目'), key='ai_task_category_filter'
        )

    task_filtered = ai_task_df.copy()
    for selected, column in (
        (task_type_filter, 'AI任务类型'),
        (task_priority_filter, '执行优先级'),
        (task_grade_filter, '综合等级'),
        (task_position_filter, '商品定位'),
        (task_cycle_filter, '商品周期'),
        (task_category_filter, '淘宝类目'),
    ):
        if selected:
            task_filtered = task_filtered[task_filtered[column].astype(str).isin(selected)]
    if task_metox_filter:
        task_filtered = task_filtered[
            task_filtered['MetoX算法命中'].astype(str).apply(
                lambda text: any(label in text for label in task_metox_filter)
            )
        ]

    st.caption(f"筛选结果：{len(task_filtered)} / {len(ai_task_df)} 条任务")
    if task_filtered.empty:
        st.info("当前筛选条件下暂无具有明确处理价值的商品任务")
    else:
        task_main = task_filtered[AI_TASK_MAIN_COLUMNS].copy()
        st.dataframe(
            task_main,
            use_container_width=True,
            hide_index=True,
            height=650,
            column_config={
                '款式主图': st.column_config.LinkColumn(
                    '款式主图链接',
                    width='medium',
                    help='仅显示链接，不预加载图片',
                ),
                '商品级总策略': st.column_config.TextColumn('商品级策略'),
                'AI可信度': st.column_config.TextColumn(
                    'AI可信度',
                    help=(
                        '综合商品、渠道、商品-计划、Top3覆盖、周期一致性、'
                        '数据缺失和MetoX完整性。选择商品后可查看详细依据。'
                    ),
                ),
            },
        )

        st.markdown("#### 计划执行明细")
        st.caption(
            "主表保持一行一个宝贝ID；计划动作对象为商品-计划级，不代表修改整个共享计划。"
            "计划指标先按所选周期完整累计，再按周期花费展示Top3关键计划（按周期花费排序）；"
            "计划名称、预算和出价未提供时不会推断。"
        )
        task_choices = task_filtered.reset_index(drop=True)
        selected_task_index = st.selectbox(
            "选择商品查看计划动作",
            options=list(task_choices.index),
            format_func=lambda idx: (
                f"{task_choices.at[idx, '宝贝ID']}｜"
                f"{task_choices.at[idx, '货号']}｜"
                f"{task_choices.at[idx, '商品名称']}｜"
                f"{task_choices.at[idx, '计划执行摘要']}"
            ),
            key='ai_task_plan_detail_product',
        )
        selected_task = task_choices.iloc[selected_task_index]
        st.caption(
            f"动作对象：{selected_task['动作对象']}｜{selected_task['计划数据说明']}｜"
            f"计划覆盖率：{selected_task['计划覆盖率']}"
        )
        with st.expander("AI可信度说明", expanded=False):
            st.markdown(f"**AI可信度：{selected_task['AI可信度']}**")
            for confidence_item in selected_task['AI可信度说明'].splitlines():
                st.write(confidence_item)
        selected_plan_details = parse_plan_execution_details(selected_task['计划执行明细'])
        if selected_plan_details.empty:
            st.info(selected_task['计划执行摘要'])
        else:
            st.dataframe(
                selected_plan_details,
                use_container_width=True,
                hide_index=True,
                height=min(420, 72 + 36 * len(selected_plan_details)),
            )

# ── Tab 4: 执行表 ────────────────────────────────────────
with tab4:
    st.subheader(f"商品推广执行表 ({len(df)} 条)")

    # 筛选 + 列设置(2026-07-11:列设置移到筛选行右侧,popover 下拉形式、占位小;
    # 勾选持久化到 cache/column_prefs.json,展示顺序固定按 EXPORT_COLS,仅影响显示、导出仍全量)
    all_cols = [c for c in EXPORT_COLS if c in df.columns]
    if "exec_cols_pref" not in st.session_state:
        saved = _load_column_prefs()
        pref = [c for c in (saved or all_cols) if c in all_cols] or all_cols
        st.session_state["exec_cols_pref"] = pref
    st.session_state["exec_cols_pref"] = [
        c for c in st.session_state["exec_cols_pref"] if c in all_cols
    ] or all_cols
    for c in ["v9.3信号", "MetoX算法命中", "MetoX算法分", "MetoX算法原因"]:
        if c in all_cols and c not in st.session_state["exec_cols_pref"]:
            st.session_state["exec_cols_pref"].append(c)

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
                use_container_width=True):
            picked = st.multiselect(
                "展示列(可输入关键字快速查找;清空=全部列)",
                options=all_cols,
                default=st.session_state["exec_cols_pref"],
                key="exec_cols_ms",
            )
            cb1, cb2 = st.columns(2)
            if cb1.button("应用并记住", use_container_width=True, key="exec_cols_apply"):
                st.session_state["exec_cols_pref"] = picked or all_cols
                _save_column_prefs(st.session_state["exec_cols_pref"])
                st.rerun()
            if cb2.button("恢复全部列", use_container_width=True, key="exec_cols_reset"):
                st.session_state["exec_cols_pref"] = all_cols
                _save_column_prefs(all_cols)
                st.rerun()
            st.caption("仅影响表格显示;导出 Excel 仍包含全部字段")

    # 过滤
    filtered = df.copy()
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

    # 显示列(按 EXPORT_COLS 固定顺序取勾选交集)
    show_cols = [c for c in all_cols if c in st.session_state["exec_cols_pref"]] or all_cols
    st.dataframe(
        style_execution_table(filtered[show_cols]),
        use_container_width=True,
        hide_index=True,
        height=600,
    )

    # 导出(2026-07-10 修复:此前下载按钮嵌在导出按钮的 if 里,任意页面刷新即消失、
    # 点下载后自身触发的 rerun 也会让它消失导致"下载不了"。改为:生成结果存 session,
    # 下载按钮持久显示;生成时记录行数与时间,筛选变化后重新生成即可)
    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ce1, ce2 = st.columns(2)
    with ce1:
        if st.button("📥 生成执行表 Excel", key="export_exec"):
            with st.spinner(f"生成中({len(filtered)} 行)..."):
                st.session_state["exec_xlsx"] = export_execution_table(filtered)
                st.session_state["exec_xlsx_meta"] = \
                    f"{len(filtered)}行 {datetime.now().strftime('%H:%M:%S')}"
        if st.session_state.get("exec_xlsx"):
            st.download_button(
                f"⬇️ 下载执行表 ({st.session_state['exec_xlsx_meta']})",
                st.session_state["exec_xlsx"],
                f"推广执行表_{st.session_state.get('cur_date', '')}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                XLSX_MIME, key="dl_exec")
    with ce2:
        if st.button("📦 生成全部 Sheet Excel", key="export_all"):
            with st.spinner(f"生成中({len(filtered)} 行 × 9 sheet)..."):
                st.session_state["all_xlsx"] = export_all_sheets(filtered)
                st.session_state["all_xlsx_meta"] = \
                    f"{len(filtered)}行 {datetime.now().strftime('%H:%M:%S')}"
        if st.session_state.get("all_xlsx"):
            st.download_button(
                f"⬇️ 下载全部分析 ({st.session_state['all_xlsx_meta']})",
                st.session_state["all_xlsx"],
                f"推广工具全部分析_{st.session_state.get('cur_date', '')}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                XLSX_MIME, key="dl_all")
    st.caption("提示:生成后下载按钮会一直保留;若改了筛选条件,重新点「生成」即可刷新内容")

# ── Tab 5: 商品分层 ───────────────────────────────────────
with tab5:
    try:
        seg_data = segment_summary(df)
        if seg_data:
            st.dataframe(pd.DataFrame(seg_data), use_container_width=True, hide_index=True, height=600)
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
                       f"付费环比按 {_ctx['sel_label']} 窗口 | 7日GMV环比按 7日窗口")
        cat = category_analysis(df, cat_ctx=_ctx)
        if cat['basic']:
            st.subheader("品类基础分析")
            st.dataframe(pd.DataFrame(cat['basic']), use_container_width=True, hide_index=True)
            t = cat.get('total', {})
            st.caption(f"合计: {t.get('cnt',0)} 商品 | GMV ¥{t.get('gmv',0)/1e4:.2f}万 | ROI {t.get('roi',0)}")
        if cat.get('invest'):
            st.subheader("品类投放占比与ROI")
            st.dataframe(pd.DataFrame(cat['invest']), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"品类分析加载失败: {e}")

# ── Tab 7: 退款风险 ───────────────────────────────────────
with tab7:
    try:
        ref = refund_risk(df)
        st.caption(f"退款率>70%(聚水潭30日cohort口径): {len(ref)} 个")
        if ref:
            st.dataframe(pd.DataFrame(ref), use_container_width=True, hide_index=True, height=500)
    except Exception as e:
        st.warning(f"退款风险加载失败: {e}")

# ── Tab 8: 渠道分析 ───────────────────────────────────────
with tab8:
    try:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("关键词 TOP30")
            kw = channel_top(df, '关键词')
            if kw: st.dataframe(pd.DataFrame(kw), use_container_width=True, hide_index=True)
            else: st.caption("暂无关键词数据")
        with c2:
            st.subheader("人群 TOP30")
            cr = channel_top(df, '人群')
            if cr: st.dataframe(pd.DataFrame(cr), use_container_width=True, hide_index=True)
            else: st.caption("暂无人群数据")
        st.subheader("全站 TOP30")
        om = channel_top(df, '全站')
        if om: st.dataframe(pd.DataFrame(om), use_container_width=True, hide_index=True)
        else: st.caption("暂无全站数据")
    except Exception as e:
        st.warning(f"渠道分析加载失败: {e}")

# ── Tab 9: 库存动作 ───────────────────────────────────────
with tab9:
    try:
        inv = inventory_actions(df)
        st.subheader(f"高库存低动销 ({len(inv.get('hi_stock',[]))} 个)")
        if inv.get('hi_stock'): st.dataframe(pd.DataFrame(inv['hi_stock']), use_container_width=True, hide_index=True)
        st.subheader(f"缺货且在投 ({len(inv.get('no_stock',[]))} 个)")
        if inv.get('no_stock'): st.dataframe(pd.DataFrame(inv['no_stock']), use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"库存动作加载失败: {e}")
