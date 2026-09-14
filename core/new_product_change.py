# -*- coding: utf-8 -*-
"""新品变化区（v2.54）：快照 + 差分。

运营口径（已确认）
------------------
* **上次** = 上一次运行留下的快照（默认），也可切到「N 天前 / 上周同期」；
* **新增发现** 拆两类：**真上新**（首现日晚于上次快照日）与**历史补发现**
  （键本次才进索引，但首现日早于上次快照日 —— 换源/补历史文件造成，不算上新）；
* **消失** = 上次在线、本次明确非在线或不再出现，且**连续 2 个自然日**（`离线天数 ≥ 2`）；
* **恢复在线** = 以前发现过（键已在索引里）、上次不在线、本次重新在线 —— **不算全新款**；
* 判定结果（下架/继续观察）翻转**不做**变化列（v2.54 已确认取消）。

三条不可让步的边界
------------------
1. **源文件缺失的那一天不算消失**：整天源不可用时，本次状态一律 `数据缺失`，
   整批变化类型为 `无法判定`，并且**不推进快照**；
2. **回填历史不推进快照**：本次分析日 ≤ 上次快照日时标 `历史回算` 且不写快照；
3. **粒度是款式键**（货号优先、缺失回退宝贝ID）：多链接任一在线即款式在线，
   只有所有链接都非在线/不出现才算消失 —— 否则换链接会误报"消失+新增"。
"""
from __future__ import annotations

import pickle
import tempfile
from pathlib import Path

import pandas as pd

from .new_product_first_seen import (
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    MATCH_CONFIDENCE,
    PRODUCT_ID,
    SKU,
    STYLE_KEY,
    build_style_key_series,
    normalize_identifier,
)

ONLINE_STATE = '当前在线'

STATE_ONLINE = '在线'
STATE_OFFLINE = '已离线'
STATE_ABSENT = '不在源'
STATE_UNKNOWN = '数据缺失'

CHANGE_NEW = '真上新'
CHANGE_BACKFILL = '历史补发现'
CHANGE_GONE = '消失'
CHANGE_BACK_ONLINE = '恢复在线'
CHANGE_WATCH = '观察中（离线1天）'
CHANGE_NONE = '无变化'
CHANGE_UNKNOWN = '无法判定（源缺失）'

CHANGE_TYPES = (
    CHANGE_NEW, CHANGE_BACKFILL, CHANGE_GONE, CHANGE_BACK_ONLINE,
    CHANGE_WATCH, CHANGE_UNKNOWN,
)

GONE_CONFIRM_DAYS = 2
SNAPSHOT_KEEP = 30

CHANGE_COLUMNS = [
    '变化类型', '货号', '款式键', '关联宝贝ID', '款式主图', '图片', '淘宝类目',
    '首次在线发现日', '新品年龄', '上次状态', '本次状态', '最后在线日', '离线天数',
    '当前结果', '上次快照日', '快照间隔天数', '说明',
]

SNAPSHOT_PREFIX = 'new_product_snapshot'


# ── 路径 ────────────────────────────────────────────────
def _safe_token(value) -> str:
    text = str(value or '').strip()
    return ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in text) or 'unknown'


def snapshot_path(cache_root, year, shop) -> Path:
    return Path(cache_root) / f'{SNAPSHOT_PREFIX}_{_safe_token(year)}_{_safe_token(shop)}.pkl'


# ── 本次在线状态（按分析日）─────────────────────────────
def build_online_status(product_daily, analysis_date) -> dict:
    """从「商品-整体」逐日帧算每个款式键的在线状态、最后在线日。

    返回 ``{'源可用': bool, '覆盖天数': int, '款式': {key: {...}}}``。
    ``源可用`` 是**整天**属性：分析日没有帧/帧为空 → 整天 `数据缺失`。
    """
    as_of = str(analysis_date)[:10]
    frames = [frame for frame in (product_daily or {}).values() if frame is not None and len(frame)]
    if not frames:
        return {'源可用': False, '覆盖天数': 0, '款式': {}}

    records: dict[str, dict] = {}
    covered_days = set()
    as_of_present = False
    for day, frame in sorted((product_daily or {}).items()):
        day_key = str(day)[:10]
        if frame is None or len(frame) == 0:
            continue
        covered_days.add(day_key)
        work = frame.copy()
        keys = build_style_key_series(work)
        work[STYLE_KEY] = keys[STYLE_KEY]
        work = work[work[STYLE_KEY].astype(str).str.strip() != '']
        if work.empty:
            continue
        status = (
            work['商品状态'].fillna('').astype(str).str.strip()
            if '商品状态' in work.columns else pd.Series('', index=work.index)
        )
        work = work.assign(_online=status.eq(ONLINE_STATE))
        if day_key == as_of:
            as_of_present = True
        for style_key, part in work.groupby(STYLE_KEY, sort=False):
            key = str(style_key).strip()
            if not key:
                continue
            item = records.setdefault(key, {
                '首次出现日': day_key,
                '最后出现日': day_key,
                '最后在线日': '',
                '本次有行': False,
                '本次在线': False,
            })
            if day_key < item['首次出现日']:
                item['首次出现日'] = day_key
            if day_key > item['最后出现日']:
                item['最后出现日'] = day_key
            if bool(part['_online'].any()):
                if day_key > item['最后在线日']:
                    item['最后在线日'] = day_key
            if day_key == as_of:
                item['本次有行'] = True
                item['本次在线'] = bool(part['_online'].any())

    source_ok = as_of_present
    for item in records.values():
        if not source_ok:
            item['本次状态'] = STATE_UNKNOWN
        elif item['本次在线']:
            item['本次状态'] = STATE_ONLINE
        elif item['本次有行']:
            item['本次状态'] = STATE_OFFLINE
        else:
            item['本次状态'] = STATE_ABSENT
    return {'源可用': source_ok, '覆盖天数': len(covered_days), '款式': records}


def _style_attr_map(first_seen) -> dict:
    """first_seen 索引 → {款式键: {首现日, 宝贝ID, 可信度}}。"""
    out: dict[str, dict] = {}
    if first_seen is None or getattr(first_seen, 'empty', True):
        return out
    for record in first_seen.to_dict('records'):
        key = str(record.get(STYLE_KEY) or '').strip()
        if not key:
            continue
        out[key] = {
            '首次在线发现日': str(record.get(FIRST_SEEN_DATE) or '')[:10],
            '首次发现宝贝ID': normalize_identifier(record.get(FIRST_SEEN_PRODUCT_ID)),
            '款式匹配可信度': str(record.get(MATCH_CONFIDENCE) or ''),
        }
    return out


def build_snapshot(lifecycle_frame, first_seen, online, analysis_date) -> dict:
    """生成本次快照：款式键 → 静态属性 + 在线状态 + 当前结果。"""
    as_of = str(analysis_date)[:10]
    attributes = _style_attr_map(first_seen)
    result_map: dict[str, dict] = {}
    if lifecycle_frame is not None and not getattr(lifecycle_frame, 'empty', True):
        for record in lifecycle_frame.to_dict('records'):
            sku = normalize_identifier(record.get(SKU))
            pid = normalize_identifier(record.get('关联宝贝ID') or record.get(PRODUCT_ID))
            key = sku or pid
            if not key:
                continue
            result_map[key] = {
                '货号': str(record.get(SKU) or '').strip(),
                '关联宝贝ID': str(record.get('关联宝贝ID') or '').strip(),
                '款式主图': str(record.get('款式主图') or '').strip(),
                '淘宝类目': str(record.get('淘宝类目') or '').strip(),
                '新品年龄': record.get('新品年龄'),
                '当前结果': str(record.get('当前结果') or '').strip(),
                '当前商品状态': str(record.get('当前商品状态') or '').strip(),
            }

    styles: dict[str, dict] = {}
    for key in sorted(set(attributes) | set(result_map) | set((online or {}).get('款式') or {})):
        attr = attributes.get(key, {})
        result = result_map.get(key, {})
        live = ((online or {}).get('款式') or {}).get(key, {})
        first_seen_date = attr.get('首次在线发现日') or ''
        styles[key] = {
            '货号': result.get('货号') or (key if attr.get('款式匹配可信度') != '低' else ''),
            '关联宝贝ID': result.get('关联宝贝ID') or attr.get('首次发现宝贝ID') or '',
            '款式主图': result.get('款式主图') or '',
            '淘宝类目': result.get('淘宝类目') or '',
            '首次在线发现日': first_seen_date,
            '款式匹配可信度': attr.get('款式匹配可信度') or '',
            '本次状态': live.get('本次状态') or STATE_ABSENT,
            '最后在线日': live.get('最后在线日') or first_seen_date,
            '最后出现日': live.get('最后出现日') or '',
            '当前结果': result.get('当前结果') or '',
            '当前商品状态': result.get('当前商品状态') or '',
            '新品年龄': result.get('新品年龄'),
        }
    return {
        '分析日': as_of,
        '源可用': bool((online or {}).get('源可用')),
        '源覆盖天数': int((online or {}).get('覆盖天数') or 0),
        '款式': styles,
    }


# ── 快照存取 ────────────────────────────────────────────
def load_snapshots(path) -> list[dict]:
    """读快照历史（按分析日升序）；文件缺失/损坏返回空列表。"""
    try:
        with Path(path).open('rb') as handle:
            payload = pickle.load(handle)
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = payload.get('快照')
    if not isinstance(payload, list):
        return []
    snapshots = [item for item in payload if isinstance(item, dict) and item.get('分析日')]
    snapshots.sort(key=lambda item: str(item.get('分析日')))
    return snapshots


def save_snapshots(path, snapshots, *, keep: int = SNAPSHOT_KEEP) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = sorted(snapshots, key=lambda item: str(item.get('分析日')))[-max(int(keep), 1):]
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=path.name + '.', suffix='.tmp', delete=False,
    )
    try:
        handle.write(pickle.dumps({'快照': trimmed}, protocol=4))
        handle.flush()
    finally:
        handle.close()
    Path(handle.name).replace(path)
    return path


def append_snapshot(path, snapshot, *, keep: int = SNAPSHOT_KEEP) -> dict:
    """写入本次快照；同一天重复运行覆盖当天，回填历史（≤ 上次分析日）不推进。"""
    as_of = str(snapshot.get('分析日') or '')[:10]
    existing = load_snapshots(path)
    latest = existing[-1] if existing else None
    if latest and as_of and as_of <= str(latest.get('分析日'))[:10]:
        same_day = as_of == str(latest.get('分析日'))[:10]
        if same_day and snapshot.get('源可用'):
            merged = [item for item in existing if str(item.get('分析日'))[:10] != as_of]
            merged.append(snapshot)
            save_snapshots(path, merged, keep=keep)
            return {'写入': True, '原因': '同一天重复运行，覆盖当天快照', '快照数': len(merged),
                    '历史回算': False}
        return {
            '写入': False,
            '原因': f'本次分析日 {as_of} 不晚于快照最新日 {str(latest.get("分析日"))[:10]}，按历史回算处理，不推进快照',
            '快照数': len(existing),
            '历史回算': True,
        }
    if not snapshot.get('源可用'):
        return {
            '写入': False,
            '原因': '分析日源不可用（数据缺失），本次不写快照，避免把"缺失"记成"消失"',
            '快照数': len(existing),
            '历史回算': False,
        }
    merged = existing + [snapshot]
    save_snapshots(path, merged, keep=keep)
    return {'写入': True, '原因': '新增当天快照', '快照数': min(len(merged), keep), '历史回算': False}


def select_baseline(snapshots, analysis_date, *, days_back: int | None = None) -> dict | None:
    """选对比基线：默认最近一次更早的快照；``days_back`` 给定时取 ≥ 该间隔的最近一次。"""
    as_of = str(analysis_date)[:10]
    candidates = [
        item for item in (snapshots or [])
        if str(item.get('分析日'))[:10] < as_of
    ]
    if not candidates:
        return None
    if not days_back:
        return candidates[-1]
    try:
        threshold = (pd.Timestamp(as_of) - pd.Timedelta(days=int(days_back))).date().isoformat()
    except Exception:
        return candidates[-1]
    eligible = [item for item in candidates if str(item.get('分析日'))[:10] <= threshold]
    return eligible[-1] if eligible else candidates[0]


# ── 差分 ────────────────────────────────────────────────
def _days_between(start: str, end: str) -> int | None:
    if not start or not end:
        return None
    try:
        return int((pd.Timestamp(str(end)[:10]) - pd.Timestamp(str(start)[:10])).days)
    except Exception:
        return None


def diff_snapshot(previous, current, *, gone_confirm_days: int = GONE_CONFIRM_DAYS) -> dict:
    """两份快照 → 变化行 + 汇总。只返回**有变化**的行（含"观察中"）。"""
    as_of = str((current or {}).get('分析日') or '')[:10]
    prev_date = str((previous or {}).get('分析日') or '')[:10]
    current_styles = (current or {}).get('款式') or {}
    previous_styles = (previous or {}).get('款式') or {}

    summary = {kind: 0 for kind in CHANGE_TYPES}
    summary['无变化'] = 0
    rows: list[dict] = []

    if previous is None:
        return {
            'rows': [],
            'summary': summary,
            '状态': '首个快照',
            '说明': '本次为首个快照，已记录但没有可对比的上次；下次运行即可看到新增/消失/恢复在线。',
            '上次快照日': '',
            '本次快照日': as_of,
        }
    if not (current or {}).get('源可用'):
        return {
            'rows': [],
            'summary': summary,
            '状态': '源缺失',
            '说明': f'{as_of} 的「商品-整体」源缺失或为空：本次不做变化判定（缺失不能算消失）。',
            '上次快照日': prev_date,
            '本次快照日': as_of,
        }

    gap = _days_between(prev_date, as_of)
    for key in sorted(set(current_styles) | set(previous_styles)):
        now = current_styles.get(key)
        before = previous_styles.get(key)
        if now is None and before is None:
            continue
        display = dict(now or before)
        first_seen_date = str((now or before).get('首次在线发现日') or '')[:10]
        current_state = (now or {}).get('本次状态') or STATE_ABSENT
        previous_state = (before or {}).get('本次状态') or ''
        result = (now or before).get('当前结果') or ''
        last_online = ''
        reason = ''

        if before is None:
            # 本次才进索引：按首现日区分「真上新」与「历史补发现」
            if first_seen_date and first_seen_date > prev_date:
                kind, reason = CHANGE_NEW, f'{first_seen_date} 首次在线（晚于上次快照 {prev_date}）'
            else:
                kind = CHANGE_BACKFILL
                reason = (
                    f'本次才进入首次发现索引，首现日 {first_seen_date or "未知"}'
                    f'，不晚于上次快照 {prev_date} → 属换源/补历史的补发现，不算上新'
                )
        elif before.get('本次状态') == STATE_ONLINE and current_state != STATE_ONLINE:
            last_online = str((now or {}).get('最后在线日') or before.get('最后在线日') or '')[:10]
            offline_days = _days_between(last_online, as_of)
            if offline_days is None:
                offline_days = 0
            if offline_days >= int(gone_confirm_days):
                kind = CHANGE_GONE
                reason = (
                    f'上次在线（{prev_date}），本次{current_state}，'
                    f'最后在线 {last_online}，已连续 {offline_days} 天非在线'
                )
            else:
                kind = CHANGE_WATCH
                reason = f'上次在线，本次{current_state}，离线 {offline_days} 天（未达 {gone_confirm_days} 天，仅观察）'
        elif before.get('本次状态') != STATE_ONLINE and current_state == STATE_ONLINE:
            kind = CHANGE_BACK_ONLINE
            last_online = str((now or {}).get('最后在线日') or '')[:10]
            reason = f'以前发现过（首现 {first_seen_date or "未知"}），上次{ before.get("本次状态")}，本次重新在线'
        else:
            kind, reason = CHANGE_NONE, ''

        summary[kind] = summary.get(kind, 0) + 1
        if kind == CHANGE_NONE:
            continue
        rows.append({
            '变化类型': kind,
            '货号': display.get('货号') or '',
            '款式键': key,
            '关联宝贝ID': display.get('关联宝贝ID') or '',
            '款式主图': display.get('款式主图') or '',
            '图片': display.get('款式主图') or '',
            '淘宝类目': display.get('淘宝类目') or '',
            '首次在线发现日': first_seen_date,
            '新品年龄': _age(first_seen_date, as_of),
            '上次状态': previous_state,
            '本次状态': current_state,
            '最后在线日': last_online or str((now or {}).get('最后在线日') or '')[:10],
            '离线天数': _days_between(
                str((now or {}).get('最后在线日') or (before or {}).get('最后在线日') or '')[:10], as_of,
            ),
            '当前结果': result,
            '上次快照日': prev_date,
            '快照间隔天数': gap,
            '说明': reason,
        })

    order = {kind: index for index, kind in enumerate(CHANGE_TYPES)}
    rows.sort(key=lambda row: (order.get(row['变化类型'], 99), -(_to_float(row.get('离线天数')) or 0)))
    return {
        'rows': rows,
        'summary': summary,
        '状态': '正常',
        '说明': f'对比上次快照 {prev_date}（间隔 {gap} 天）：'
                f"真上新 {summary.get(CHANGE_NEW, 0)}、补发现 {summary.get(CHANGE_BACKFILL, 0)}、"
                f"消失 {summary.get(CHANGE_GONE, 0)}、恢复在线 {summary.get(CHANGE_BACK_ONLINE, 0)}、"
                f"观察中 {summary.get(CHANGE_WATCH, 0)}",
        '上次快照日': prev_date,
        '本次快照日': as_of,
    }


def _age(first_seen_date: str, as_of: str) -> int | None:
    days = _days_between(first_seen_date, as_of)
    return None if days is None else days + 1


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def change_frame(rows, *, include_watch: bool = True) -> pd.DataFrame:
    """变化行 → 展示用 DataFrame（固定列序）。"""
    records = [dict(row) for row in (rows or [])]
    if not include_watch:
        records = [row for row in records if row.get('变化类型') != CHANGE_WATCH]
    if not records:
        return pd.DataFrame(columns=CHANGE_COLUMNS)
    frame = pd.DataFrame(records)
    for column in CHANGE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ''
    return frame[CHANGE_COLUMNS]


def change_summary_rows(summary) -> list[dict]:
    """汇总卡片用的 (类型, 款数) 列表。"""
    data = summary or {}
    return [
        {'变化类型': kind, '款数': int(data.get(kind, 0))}
        for kind in CHANGE_TYPES
    ]


__all__ = [
    'ONLINE_STATE',
    'STATE_ONLINE', 'STATE_OFFLINE', 'STATE_ABSENT', 'STATE_UNKNOWN',
    'CHANGE_NEW', 'CHANGE_BACKFILL', 'CHANGE_GONE', 'CHANGE_BACK_ONLINE',
    'CHANGE_WATCH', 'CHANGE_NONE', 'CHANGE_UNKNOWN', 'CHANGE_TYPES', 'CHANGE_COLUMNS',
    'GONE_CONFIRM_DAYS', 'SNAPSHOT_KEEP',
    'snapshot_path', 'build_online_status', 'build_snapshot', 'load_snapshots',
    'save_snapshots', 'append_snapshot', 'select_baseline', 'diff_snapshot',
    'change_frame', 'change_summary_rows',
]
