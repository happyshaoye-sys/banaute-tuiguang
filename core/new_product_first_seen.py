"""新品款式首次在线发现索引。

本模块只处理标准化 DataFrame，不做文件读取，也不依赖 Streamlit。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

import pandas as pd


STYLE_KEY = "style_key"
PRODUCT_ID = "宝贝ID"
SKU = "货号"
PRODUCT_STATUS = "商品状态"
ONLINE_STATUS = "当前在线"
DATA_DATE = "_数据日期"
STAT_DATE = "统计日期"

FIRST_SEEN_DATE = "首次在线发现日"
FIRST_SEEN_PRODUCT_ID = "首次发现宝贝ID"
ERP_CREATED_AT = "ERP建档时间"
HISTORY_START_DATE = "历史数据最早日期"
IS_HISTORY_TRUNCATED = "是否历史截断"
LEFT_CENSORED = "left_censored"
MATCH_CONFIDENCE = "款式匹配可信度"
STYLE_KEY_SOURCE = "款式键来源"

MATCH_CONFIDENCE_HIGH = "高"
MATCH_CONFIDENCE_LOW = "低"
SOURCE_SKU = "货号"
SOURCE_PRODUCT_ID = "宝贝ID"

FIRST_SEEN_COLUMNS = [
    STYLE_KEY,
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    ERP_CREATED_AT,
    HISTORY_START_DATE,
    IS_HISTORY_TRUNCATED,
    MATCH_CONFIDENCE,
    STYLE_KEY_SOURCE,
]


@dataclass(frozen=True)
class FirstSeenBuildConfig:
    """首次发现索引构建配置。"""

    online_status_values: tuple[str, ...] = (ONLINE_STATUS,)


def normalize_identifier(value) -> str:
    """把 Excel/CSV 常见 ID 值规范为稳定字符串。"""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    if text.endswith(".0"):
        head = text[:-2]
        if head.isdigit():
            return head
    return text.upper()


def build_style_key_series(frame: pd.DataFrame) -> pd.DataFrame:
    """返回 style_key、来源与匹配可信度；货号缺失时回退宝贝ID。"""
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=[STYLE_KEY, STYLE_KEY_SOURCE, MATCH_CONFIDENCE])

    sku = (
        frame[SKU].map(normalize_identifier)
        if SKU in frame.columns
        else pd.Series("", index=frame.index, dtype=object)
    )
    pid = (
        frame[PRODUCT_ID].map(normalize_identifier)
        if PRODUCT_ID in frame.columns
        else pd.Series("", index=frame.index, dtype=object)
    )
    use_sku = sku != ""
    out = pd.DataFrame(index=frame.index)
    out[STYLE_KEY] = sku.where(use_sku, pid)
    out[STYLE_KEY_SOURCE] = SOURCE_SKU
    out.loc[~use_sku, STYLE_KEY_SOURCE] = SOURCE_PRODUCT_ID
    out[MATCH_CONFIDENCE] = MATCH_CONFIDENCE_HIGH
    out.loc[~use_sku, MATCH_CONFIDENCE] = MATCH_CONFIDENCE_LOW
    return out


def _parse_iso_date(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()


def _date_from_frame(frame: pd.DataFrame, fallback) -> pd.Series:
    if DATA_DATE in frame.columns:
        source = frame[DATA_DATE]
    elif STAT_DATE in frame.columns:
        source = frame[STAT_DATE]
    else:
        source = pd.Series(fallback, index=frame.index)
    return source.map(_parse_iso_date)


def _iter_daily_frames(daily_frames) -> Iterable[tuple[str | None, pd.DataFrame]]:
    if daily_frames is None:
        return []
    if isinstance(daily_frames, Mapping):
        return daily_frames.items()
    return daily_frames


def _first_non_empty(values) -> str:
    for value in values:
        text = str(value).strip() if value is not None and not pd.isna(value) else ""
        if text and text.lower() not in {"nan", "none", "null"}:
            return text
    return ""


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "是", "历史截断"}


def build_first_seen_index(
    daily_frames,
    existing_index: pd.DataFrame | None = None,
    history_start_date: str | date | None = None,
    config: FirstSeenBuildConfig | None = None,
) -> pd.DataFrame:
    """从多日“商品-整体”标准帧构建或更新款式首次在线发现索引。

    daily_frames 可为 {date: frame} 或 [(date, frame)]。索引更新时同一款式的
    首次在线发现日只会提前，不会因后续缺失或晚日期而推迟。
    """
    config = config or FirstSeenBuildConfig()
    history_start = _parse_iso_date(history_start_date) if history_start_date else ""
    candidates = []

    for fallback_date, frame in _iter_daily_frames(daily_frames):
        if frame is None or len(frame) == 0:
            continue
        work = frame.copy()
        dates = _date_from_frame(work, fallback_date)
        keys = build_style_key_series(work)
        work[STYLE_KEY] = keys[STYLE_KEY]
        work[STYLE_KEY_SOURCE] = keys[STYLE_KEY_SOURCE]
        work[MATCH_CONFIDENCE] = keys[MATCH_CONFIDENCE]
        work["_first_seen_date"] = dates

        if PRODUCT_STATUS in work.columns:
            status = work[PRODUCT_STATUS].fillna("").astype(str).str.strip()
            online = status.isin(config.online_status_values)
            work = work.loc[online].copy()
        work = work[(work[STYLE_KEY] != "") & (work["_first_seen_date"] != "")]
        if work.empty:
            continue

        work[FIRST_SEEN_DATE] = work["_first_seen_date"]
        work[FIRST_SEEN_PRODUCT_ID] = (
            work[PRODUCT_ID].map(normalize_identifier)
            if PRODUCT_ID in work.columns
            else ""
        )
        work[ERP_CREATED_AT] = (
            work["创建时间"].map(lambda v: "" if pd.isna(v) else str(v).strip())
            if "创建时间" in work.columns
            else ""
        )
        work[HISTORY_START_DATE] = history_start
        work[IS_HISTORY_TRUNCATED] = bool(history_start) & (
            work[FIRST_SEEN_DATE] <= history_start
        )
        candidates.append(work[FIRST_SEEN_COLUMNS])

    new_index = _collapse_first_seen_candidates(candidates)
    if existing_index is None or len(existing_index) == 0:
        return _normalize_first_seen_index(new_index)
    if new_index.empty:
        return _normalize_first_seen_index(existing_index)
    combined = pd.concat(
        [_normalize_first_seen_index(existing_index), new_index],
        ignore_index=True,
    )
    return _collapse_first_seen_candidates([combined])


def _normalize_first_seen_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(columns=FIRST_SEEN_COLUMNS)
    if frame is None or len(frame) == 0:
        return out
    for column in FIRST_SEEN_COLUMNS:
        out[column] = frame[column] if column in frame.columns else ""
    out[STYLE_KEY] = out[STYLE_KEY].map(normalize_identifier)
    out[FIRST_SEEN_DATE] = out[FIRST_SEEN_DATE].map(_parse_iso_date)
    out[FIRST_SEEN_PRODUCT_ID] = out[FIRST_SEEN_PRODUCT_ID].map(normalize_identifier)
    out[HISTORY_START_DATE] = out[HISTORY_START_DATE].map(_parse_iso_date)
    out[IS_HISTORY_TRUNCATED] = out[IS_HISTORY_TRUNCATED].map(_to_bool)
    if LEFT_CENSORED in frame.columns:
        out[IS_HISTORY_TRUNCATED] = (
            out[IS_HISTORY_TRUNCATED] | frame[LEFT_CENSORED].map(_to_bool)
        )
    out = out[(out[STYLE_KEY] != "") & (out[FIRST_SEEN_DATE] != "")]
    return out[FIRST_SEEN_COLUMNS].reset_index(drop=True)


def _collapse_first_seen_candidates(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=FIRST_SEEN_COLUMNS)
    work = pd.concat(frames, ignore_index=True)
    work = _normalize_first_seen_index(work)
    if work.empty:
        return work
    sku_rows = work[
        (work[STYLE_KEY_SOURCE] == SOURCE_SKU)
        & (work[FIRST_SEEN_PRODUCT_ID] != "")
        & (work[STYLE_KEY] != "")
    ]
    pid_to_sku_style = sku_rows.drop_duplicates(
        FIRST_SEEN_PRODUCT_ID, keep="first"
    ).set_index(FIRST_SEEN_PRODUCT_ID)[STYLE_KEY].to_dict()
    if pid_to_sku_style:
        mapped_style = work[FIRST_SEEN_PRODUCT_ID].map(pid_to_sku_style).fillna("")
        can_promote = (work[STYLE_KEY_SOURCE] == SOURCE_PRODUCT_ID) & (mapped_style != "")
        work.loc[can_promote, STYLE_KEY] = mapped_style.loc[can_promote]
        work.loc[can_promote, STYLE_KEY_SOURCE] = SOURCE_SKU
        work.loc[can_promote, MATCH_CONFIDENCE] = MATCH_CONFIDENCE_HIGH
    work = work.sort_values([STYLE_KEY, FIRST_SEEN_DATE], kind="mergesort")
    rows = []
    for style_key, part in work.groupby(STYLE_KEY, sort=False):
        first = part.iloc[0].copy()
        first[STYLE_KEY] = style_key
        first[ERP_CREATED_AT] = _first_non_empty(part[ERP_CREATED_AT])
        first[FIRST_SEEN_PRODUCT_ID] = _first_non_empty(part[FIRST_SEEN_PRODUCT_ID])
        first[HISTORY_START_DATE] = _first_non_empty(part[HISTORY_START_DATE])
        computed_truncated = bool(
            first[HISTORY_START_DATE]
            and first[FIRST_SEEN_DATE] <= first[HISTORY_START_DATE]
        )
        first[IS_HISTORY_TRUNCATED] = computed_truncated or bool(
            part[IS_HISTORY_TRUNCATED].map(_to_bool).any()
        )
        if SOURCE_SKU in set(part[STYLE_KEY_SOURCE]):
            first[STYLE_KEY_SOURCE] = SOURCE_SKU
            first[MATCH_CONFIDENCE] = MATCH_CONFIDENCE_HIGH
        rows.append(first)
    return pd.DataFrame(rows, columns=FIRST_SEEN_COLUMNS).reset_index(drop=True)


__all__ = [
    "ERP_CREATED_AT",
    "FIRST_SEEN_COLUMNS",
    "FIRST_SEEN_DATE",
    "FIRST_SEEN_PRODUCT_ID",
    "FirstSeenBuildConfig",
    "HISTORY_START_DATE",
    "IS_HISTORY_TRUNCATED",
    "MATCH_CONFIDENCE",
    "MATCH_CONFIDENCE_HIGH",
    "MATCH_CONFIDENCE_LOW",
    "STYLE_KEY",
    "STYLE_KEY_SOURCE",
    "build_first_seen_index",
    "build_style_key_series",
    "normalize_identifier",
]
