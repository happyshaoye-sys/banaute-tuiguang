# -*- coding: utf-8 -*-
"""推广计划配置（预算 / 出价方式）读取。

业务背景（2026-11-14 起）
------------------------
无界目录里新增一份后台计划列表导出：`计划列表_<YYYY-MM-DDTHH-MM-SS>.xlsx`，
列为 `模块 / 计划名称 / 计划ID / 预算 / 出价方式 / 计划组 / 状态 / bizCode`。

它给的是**当前配置**（日预算、出价方式），不是历史值，所以口径是：

* **只读最新那份**：从分析日往前回溯 ``lookback_days`` 天找 ``计划列表_*``，
  连同分析日之后的日期目录一起看，取目录日期最新、目录内 mtime 最新的那份；
* **每节只读一次**：不进日事实缓存（既不重复读，也不会把「当前配置」错记成历史）；
* **按计划ID左连接**进「推广计划管理」表：匹配不到留空，不推断、不编造；
* 只做展示：不参与 GMV / 花费 / ROI / 预算决策的任何计算。

找不到文件时返回空配置，页面照常出表（两列为空）并在说明里写清原因。
"""
from __future__ import annotations

import hashlib
import os
import pickle
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

PLAN_CONFIG_CACHE_VERSION = 'metox2.69-plan-config-v1'
PLAN_LIST_KEYWORD = '计划列表'
PLAN_LIST_CACHE_DIRNAME = '_plan_config'
PLAN_CONFIG_COLUMNS = ('预算', '出价方式')
DEFAULT_LOOKBACK_DAYS = 7
_DATE_DIR_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_READABLE_SUFFIXES = ('.xlsx', '.xls', '.csv')


# ── 单文件读取 ──────────────────────────────────────────────────────
def _is_plan_list_file(path: Path) -> bool:
    name = path.name
    if not name or name.startswith('~$'):
        return False
    if path.suffix.lower() not in _READABLE_SUFFIXES:
        return False
    return PLAN_LIST_KEYWORD in name


def find_latest_plan_list(wujie_root, analysis_date=None, lookback_days=DEFAULT_LOOKBACK_DAYS):
    """在无界根目录下找最新的「计划列表」文件。

    返回 ``(path, folder_date)``；找不到返回 ``(None, '')``。
    folder_date 是文件所在日期目录的名字，用于界面提示来源。
    """
    if not wujie_root:
        return None, ''
    root = Path(wujie_root)
    if not root.is_dir():
        return None, ''
    floor = None
    if analysis_date:
        try:
            floor = pd.Timestamp(str(analysis_date)).normalize()
        except (ValueError, TypeError):
            floor = None
    candidates = []
    for entry in root.iterdir():
        if not entry.is_dir() or not _DATE_DIR_RE.match(entry.name):
            continue
        if floor is not None:
            try:
                day = pd.Timestamp(entry.name)
            except (ValueError, TypeError):
                continue
            if (floor - day).days > max(int(lookback_days), 0):
                continue
        files = [p for p in entry.iterdir() if p.is_file() and _is_plan_list_file(p)]
        if not files:
            continue
        newest = max(files, key=lambda p: (p.stat().st_mtime, p.name))
        candidates.append((entry.name, newest))
    if not candidates:
        return None, ''
    candidates.sort(key=lambda item: item[0], reverse=True)
    folder_date, path = candidates[0]
    return path, folder_date


# ── 内容解析 ────────────────────────────────────────────────────────
def parse_budget(value):
    """「310元」→310；「不限」→'不限'；空值→''。"""
    text = str(value if value is not None else '').strip()
    if not text or text.lower() in ('nan', 'none'):
        return ''
    if '不限' in text:
        return '不限'
    cleaned = re.sub(r'[,，\s元¥￥]', '', text)
    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return text
    return int(number) if float(number).is_integer() else number


def _clean_text(value) -> str:
    text = str(value if value is not None else '').strip()
    return '' if text.lower() in ('nan', 'none') else text


def read_plan_list(path):
    """读取计划列表，返回 ``(by_id, meta)``。

    by_id: ``{计划ID: {'预算': 310|'不限'|'', '出价方式': '稳定投产比 投产比6'}}``
    """
    path = Path(path)
    if not path.is_file():
        return {}, {'file': '', 'reason': '计划列表文件不存在'}
    if path.suffix.lower() == '.csv':
        frame = pd.read_csv(path, dtype=str, encoding='utf-8-sig')
    else:
        frame = pd.read_excel(path, dtype=str)
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [c for c in ('计划ID', '预算', '出价方式') if c not in frame.columns]
    if missing:
        return {}, {
            'file': path.name,
            'reason': f'计划列表缺少列：{"、".join(missing)}',
        }
    by_id = {}
    for _, row in frame.iterrows():
        plan_id = _clean_text(row.get('计划ID'))
        if not plan_id:
            continue
        by_id[plan_id] = {
            '预算': parse_budget(row.get('预算')),
            '出价方式': _clean_text(row.get('出价方式')),
        }
    meta = {
        'file': path.name,
        'path': str(path),
        'rows': len(frame),
        'matched_ids': len(by_id),
        'exported_at': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
    }
    return by_id, meta


# ── 缓存 + 对外入口 ────────────────────────────────────────────────
def _fingerprint(path: Path) -> str:
    stat = path.stat()
    raw = f'{os.path.normcase(str(path))}|{stat.st_size}|{stat.st_mtime_ns}'
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _cache_path(cache_root) -> Path:
    return Path(cache_root) / PLAN_LIST_CACHE_DIRNAME / 'latest.pkl'


def _read_cache(cache_root, fingerprint):
    if not cache_root:
        return None
    path = _cache_path(cache_root)
    if not path.is_file():
        return None
    try:
        with path.open('rb') as handle:
            payload = pickle.load(handle)
    except Exception:  # noqa: BLE001 - 缓存坏了就当没有
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get('version') != PLAN_CONFIG_CACHE_VERSION:
        return None
    if payload.get('fingerprint') != fingerprint:
        return None
    return payload


def _write_cache(cache_root, fingerprint, by_id, source):
    if not cache_root:
        return
    path = _cache_path(cache_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': PLAN_CONFIG_CACHE_VERSION,
            'fingerprint': fingerprint,
            'by_id': by_id,
            'source': source,
        }
        tmp = path.with_suffix('.pkl.tmp')
        with tmp.open('wb') as handle:
            pickle.dump(payload, handle, protocol=4)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - 缓存写失败不影响展示
        pass


def load_plan_config(wujie_root, analysis_date=None, cache_root=None,
                     lookback_days=DEFAULT_LOOKBACK_DAYS):
    """读取「最新」计划列表，返回 ``{'by_id', 'source', 'fingerprint'}``。

    任何时候都返回字典（找不到就是空 by_id + 说明），调用方不需要 try。
    """
    path, folder_date = find_latest_plan_list(
        wujie_root, analysis_date=analysis_date, lookback_days=lookback_days)
    if path is None:
        return {
            'by_id': {},
            'fingerprint': '',
            'source': {
                'file': '',
                'folder_date': '',
                'reason': f'无界目录下未找到「{PLAN_LIST_KEYWORD}_*.xlsx」'
                          f'（回溯 {lookback_days} 天）',
            },
        }
    try:
        fingerprint = _fingerprint(path)
    except OSError as exc:
        return {
            'by_id': {},
            'fingerprint': '',
            'source': {'file': path.name, 'folder_date': folder_date,
                       'reason': f'计划列表读取失败：{exc}'},
        }
    cached = _read_cache(cache_root, fingerprint)
    if cached is not None:
        source = dict(cached.get('source') or {})
        source['folder_date'] = folder_date
        source['from_cache'] = True
        return {
            'by_id': dict(cached.get('by_id') or {}),
            'fingerprint': fingerprint,
            'source': source,
        }
    by_id, meta = read_plan_list(path)
    source = dict(meta)
    source['folder_date'] = folder_date
    source['from_cache'] = False
    if by_id:
        _write_cache(cache_root, fingerprint, by_id, source)
    return {'by_id': by_id, 'fingerprint': fingerprint, 'source': source}


def plan_config_caption(source) -> str:
    """界面用的一句话来源说明。"""
    source = source or {}
    if not source.get('file'):
        return str(source.get('reason') or '未找到计划列表文件，预算/出价方式留空')
    parts = [f"预算/出价方式来自 {source['file']}"]
    if source.get('folder_date'):
        parts.append(f"目录 {source['folder_date']}")
    if source.get('exported_at'):
        parts.append(f"导出 {source['exported_at']}")
    if source.get('matched_ids'):
        parts.append(f"{int(source['matched_ids'])} 个计划ID")
    return '（' + '，'.join(parts) + '）'


__all__ = [
    'DEFAULT_LOOKBACK_DAYS',
    'PLAN_CONFIG_CACHE_VERSION',
    'PLAN_CONFIG_COLUMNS',
    'find_latest_plan_list',
    'load_plan_config',
    'parse_budget',
    'plan_config_caption',
    'read_plan_list',
]
