# -*- coding: utf-8 -*-
"""角色层历史轻量缓存（v2.54）。

为什么要这个模块
----------------
覆盖法阈值（TH_MAIN / TH_MID）需要 **7 天窗口 × 8 窗 = 56 天** 的历史，而看板
其余部分最多只要 28 天（同层基准）。历史事实是"阈值不冻结时才需要"，但引擎建议值
与冻结期漂移监控又要求它**随时可算**。于是把 56 天压缩成两个小缓存：

* ``metox_gmv_daily_{年}_{店}.pkl``：``{日期: {宝贝ID: GMV}}`` —— 覆盖法引擎只需要这两列；
* ``metox_kpi_daily_{年}_{店}.json``：``{日期: {指标: 值}}`` —— 看板日趋势图只需要每天 6 个数。

主流程每天把**本次真正加载过的天**沉淀进去，所以缓存始终跟着源数据走；需要复核时
直接读缓存（毫秒级），不必再读 56 天全量日事实（冷读约 5.7 秒/天）。

口径边界
--------
* GMV 一律经 ``core.budget_role.gmv_by_product`` 抽取（含"转化周期=15天转化"过滤），
  与 ``day_map`` 入口逐位一致；两条路径有单测锁定。
* 缓存只用于**阈值引擎**与**看板趋势展示**，不参与任何商品判定。
* 历史日回改（把 30 天前的源文件换掉）不会被自动发现——与日事实缓存同级别的假设：
  缓存按"日期"覆盖写，只有该天被重新加载时才会刷新。
"""
from __future__ import annotations

import json
import os
import pickle
import tempfile
from pathlib import Path

#: 缓存保留天数（>56 天，留出复盘与跨周对比余量）。
ROLE_HISTORY_KEEP_DAYS = 70

GMV_DAILY_PREFIX = 'metox_gmv_daily'
KPI_DAILY_PREFIX = 'metox_kpi_daily'


def _safe_token(value) -> str:
    text = str(value or '').strip()
    return ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in text) or 'unknown'


def _cache_root(cache_root) -> Path:
    return Path(cache_root) if cache_root is not None else Path('cache')


def gmv_daily_path(cache_root, year, shop) -> Path:
    return _cache_root(cache_root) / f'{GMV_DAILY_PREFIX}_{_safe_token(year)}_{_safe_token(shop)}.pkl'


def kpi_daily_path(cache_root, year, shop) -> Path:
    return _cache_root(cache_root) / f'{KPI_DAILY_PREFIX}_{_safe_token(year)}_{_safe_token(shop)}.json'


def _prune(payload: dict, keep_days: int) -> dict:
    """只保留最近 keep_days 天（按日期字符串排序，ISO 日期可直接比较）。"""
    keys = sorted(str(key)[:10] for key in payload)
    if keep_days and len(keys) > int(keep_days):
        keys = keys[-int(keep_days):]
    return {key: payload[key] for key in keys if key in payload}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=path.name + '.', suffix='.tmp', delete=False,
    )
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


# ── 轻量日粒度 GMV ────────────────────────────────────────
def load_gmv_daily(cache_root, year, shop) -> dict:
    """读轻量 GMV 缓存；文件缺失/损坏/格式异常一律返回空 dict（不阻断）。"""
    path = gmv_daily_path(cache_root, year, shop)
    try:
        with path.open('rb') as handle:
            payload = pickle.load(handle)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for key, values in payload.items():
        if not isinstance(values, dict):
            continue
        cleaned = {}
        for product_id, value in values.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                cleaned[str(product_id)] = number
        if cleaned:
            out[str(key)[:10]] = cleaned
    return out


def save_gmv_daily(cache_root, year, shop, payload: dict, *,
                   keep_days: int = ROLE_HISTORY_KEEP_DAYS) -> Path:
    path = gmv_daily_path(cache_root, year, shop)
    _atomic_write_bytes(path, pickle.dumps(_prune(payload, keep_days), protocol=4))
    return path


def update_gmv_daily(cache_root, year, shop, day_map, *,
                     dates=None, keep_days: int = ROLE_HISTORY_KEEP_DAYS) -> dict:
    """把本次加载过的天沉淀进缓存，返回统计信息（供日志/自检）。"""
    from core.budget_role import gmv_daily_from_day_map

    fresh = gmv_daily_from_day_map(day_map, dates=dates)
    existing = load_gmv_daily(cache_root, year, shop)
    merged = dict(existing)
    merged.update(fresh)
    merged = _prune(merged, keep_days)
    if fresh or not existing:
        save_gmv_daily(cache_root, year, shop, merged, keep_days=keep_days)
    return {
        '本次新增/更新天数': len(fresh),
        '缓存总天数': len(merged),
        '缓存最早日': min(merged) if merged else '',
        '缓存最新日': max(merged) if merged else '',
        '缓存路径': str(gmv_daily_path(cache_root, year, shop)),
    }


def gmv_daily_coverage(gmv_daily, end_date, *, window_days: int = 7,
                       windows: int = 8) -> dict:
    """缓存对指定分析日的覆盖情况：引擎是否能算、缺哪几天。"""
    import pandas as pd

    from core.budget_role import THRESHOLD_WINDOW_DAYS, THRESHOLD_WINDOWS, period_windows

    span = int(window_days or THRESHOLD_WINDOW_DAYS)
    count = int(windows or THRESHOLD_WINDOWS)
    try:
        anchor = pd.Timestamp(str(end_date)[:10])
    except Exception:
        return {'可用': False, '缺失天数': [], '窗口数': count, '覆盖天数': 0, '需要天数': span * count}
    needed = []
    for start, window_end in period_windows(anchor.date(), span, count):
        for day in pd.date_range(start=start, end=window_end, freq='D'):
            key = day.strftime('%Y-%m-%d')
            if key not in needed:
                needed.append(key)
    missing = [day for day in needed if day not in (gmv_daily or {})]
    present = [day for day in needed if day in (gmv_daily or {})]
    return {
        '可用': not missing,
        '缺失天数': missing,
        '窗口数': count,
        '覆盖天数': len(present),
        '需要天数': len(needed),
        '窗口天数': span,
    }


# ── 逐日 KPI（看板日趋势）────────────────────────────────
def load_kpi_daily(cache_root, year, shop) -> dict:
    path = kpi_daily_path(cache_root, year, shop)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_kpi_daily(cache_root, year, shop, payload: dict, *,
                   keep_days: int = ROLE_HISTORY_KEEP_DAYS) -> Path:
    path = kpi_daily_path(cache_root, year, shop)
    _atomic_write_bytes(
        path,
        json.dumps(_prune(payload, keep_days), ensure_ascii=False).encode('utf-8'),
    )
    return path


def update_kpi_daily(cache_root, year, shop, frame, *,
                     keep_days: int = ROLE_HISTORY_KEEP_DAYS) -> dict:
    """把本帧已算出的逐日 KPI 写进缓存（frame 需含 ``日期`` 列）。"""
    if frame is None or getattr(frame, 'empty', True) or '日期' not in getattr(frame, 'columns', ()):
        return {'写入天数': 0, '缓存总天数': len(load_kpi_daily(cache_root, year, shop))}
    fresh = {}
    for record in frame.to_dict('records'):
        day = str(record.get('日期'))[:10]
        if not day:
            continue
        values = {
            key: (None if record.get(key) is None else float(record[key]))
            for key in record
            if key != '日期' and isinstance(record.get(key), (int, float))
        }
        if values:
            fresh[day] = values
    existing = load_kpi_daily(cache_root, year, shop)
    merged = dict(existing)
    for day, values in fresh.items():
        merged[day] = {**merged.get(day, {}), **values}
    merged = _prune(merged, keep_days)
    if merged:
        save_kpi_daily(cache_root, year, shop, merged, keep_days=keep_days)
    return {
        '写入天数': len(fresh),
        '缓存总天数': len(merged),
        '缓存最早日': min(merged) if merged else '',
        '缓存最新日': max(merged) if merged else '',
        '缓存路径': str(kpi_daily_path(cache_root, year, shop)),
    }


def merge_kpi_trend(frame, cache_root, year, shop):
    """用缓存补齐趋势帧里**缺失的日期**（本次未加载的天），已有的天以本帧为准。

    这样"冻结后只加载 28 天"时，看板日趋势图仍能看到 56 天。
    """
    cached = load_kpi_daily(cache_root, year, shop)
    if not cached:
        return frame
    if frame is None or getattr(frame, 'empty', True):
        return frame
    try:
        import pandas as pd
    except Exception:  # pragma: no cover
        return frame
    columns = list(frame.columns)
    have = {str(value)[:10] for value in frame['日期']}
    extra = []
    for day in sorted(cached):
        if day in have:
            continue
        values = cached[day]
        row = {'日期': day}
        for column in columns:
            if column == '日期':
                continue
            value = values.get(column)
            row[column] = None if value is None else float(value)
        extra.append(row)
    if not extra:
        return frame
    merged = pd.concat([frame, pd.DataFrame(extra, columns=columns)], ignore_index=True)
    merged = merged.sort_values('日期').reset_index(drop=True)
    return merged[columns]


__all__ = [
    'ROLE_HISTORY_KEEP_DAYS',
    'gmv_daily_path',
    'kpi_daily_path',
    'load_gmv_daily',
    'save_gmv_daily',
    'update_gmv_daily',
    'gmv_daily_coverage',
    'load_kpi_daily',
    'save_kpi_daily',
    'update_kpi_daily',
    'merge_kpi_trend',
]
