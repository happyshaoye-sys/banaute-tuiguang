"""Disk cache helpers for per-day merged facts."""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import tempfile
from numbers import Integral
from pathlib import Path
from typing import Iterable

import pandas as pd


#: 日事实缓存版本。口径/取数白名单变化时必须自增，否则旧缓存（按源文件指纹命中）
#: 会继续返回旧口径的日事实。
#: v4：无界-商品主体放行「计划名字」；v5：v4 是在解析层缓存尚未换代时重建的
#: （仍取自缺列的老帧），必须再换代一次才能真正吃到新列。
DAILY_FACT_CACHE_VERSION = "metox2.65-daily-facts-v8"
SHARED_DIMENSION_CACHE_VERSION = "metox2.30-shared-dimensions-v2"


def dataset_cache_key(base_root, year, shop):
    """Return the stable dataset cache key shared by all selected dates."""
    raw = f"{base_root}|{year}|{shop}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _normalized_abs_path(path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).expanduser().resolve(strict=False))))


def _fingerprint_entries(entries: Iterable[tuple]) -> list[dict]:
    payload = []
    for path, source_type in entries:
        file_path = Path(path)
        stat = file_path.stat()
        payload.append({
            "path": _normalized_abs_path(file_path),
            "source_type": str(source_type),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
    payload.sort(key=lambda item: (item["path"], item["source_type"]))
    return payload


def source_fingerprint(entries: Iterable[tuple]) -> str:
    """Fingerprint source identity and file metadata without reading contents."""
    payload = _fingerprint_entries(entries)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_path(cache_root, day) -> Path:
    return Path(cache_root) / "_daily_facts" / f"{day}.pkl"


def _shared_dimension_cache_path(cache_root) -> Path:
    return Path(cache_root) / "_shared_dimensions" / "_dimensions.pkl"


def _atomic_pickle_dump(cache_path: Path, payload: dict, prefix: str) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=prefix,
            suffix=".tmp",
            dir=str(cache_path.parent),
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, cache_path)
        return cache_path
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def schema_fingerprint():
    """取数 schema 指纹（v2.56）：白名单/别名一改，两层缓存自动失效。

    函数内延迟导入，避免 loader <-> daily_cache 的导入环；导入失败时返回空串
    （退回"只看版本号"的老行为，不让缓存机制本身把主流程弄挂）。
    """
    try:
        from .loader import source_schema_fingerprint

        return source_schema_fingerprint()
    except Exception:  # pragma: no cover - 理论上不会发生
        return ""


def load_daily_fact_cache(cache_root, day, entries, expected_fingerprint=None):
    """Return a cached daily fact payload, or None when stale/unreadable."""
    try:
        expected_fingerprint = expected_fingerprint or source_fingerprint(entries)
        cache_path = _cache_path(cache_root, day)
        if not cache_path.exists():
            return None

        with cache_path.open("rb") as fh:
            payload = pickle.load(fh)

        if not isinstance(payload, dict):
            return None
        if payload.get("version") != DAILY_FACT_CACHE_VERSION:
            return None
        if payload.get("source_fingerprint") != expected_fingerprint:
            return None
        # v2.56：schema/白名单指纹不一致 → 视为未命中。
        # 旧载荷没有这个字段时按**兼容命中**处理（不与版本换代叠加，避免用户重复付一次重建成本）；
        # 从 v2.56 起写入的载荷都带指纹，之后任何白名单/别名改动都会自动失效。
        stored_schema = payload.get("schema_fingerprint")
        if stored_schema is not None and stored_schema != schema_fingerprint():
            return None

        base_df = payload.get("base_df")
        plan_ids = payload.get("plan_ids")
        files_info = payload.get("files_info")
        total_files = payload.get("total_files")

        if not isinstance(base_df, pd.DataFrame):
            return None
        if not isinstance(plan_ids, (set, list, tuple)):
            return None
        if not isinstance(files_info, list):
            return None
        if not isinstance(total_files, Integral):
            return None

        return {
            "version": payload["version"],
            "source_fingerprint": payload["source_fingerprint"],
            "base_df": base_df,
            "plan_ids": set(plan_ids),
            "files_info": files_info,
            "total_files": int(total_files),
        }
    except Exception:
        return None


def save_daily_fact_cache(
    cache_root, day, entries, base_df, plan_ids, files_info, total_files,
    *, verified_fingerprint,
):
    """Persist a daily fact payload by replacing the cache file atomically."""
    if source_fingerprint(entries) != verified_fingerprint:
        raise RuntimeError("daily fact sources changed before cache write")
    cache_path = _cache_path(cache_root, day)
    payload = {
        "version": DAILY_FACT_CACHE_VERSION,
        "source_fingerprint": str(verified_fingerprint),
        "schema_fingerprint": schema_fingerprint(),
        "base_df": base_df.copy(deep=True) if isinstance(base_df, pd.DataFrame) else base_df,
        "plan_ids": sorted(str(plan_id) for plan_id in (plan_ids or set())),
        "files_info": list(files_info or []),
        "total_files": int(total_files),
    }
    return _atomic_pickle_dump(cache_path, payload, prefix=f"{day}.")


def load_shared_dimension_cache(cache_root, entries, expected_fingerprint=None):
    """Return cached shared dimensions, or None when stale/unreadable."""
    try:
        expected_fingerprint = expected_fingerprint or source_fingerprint(entries)
        cache_path = _shared_dimension_cache_path(cache_root)
        if not cache_path.exists():
            return None
        with cache_path.open("rb") as fh:
            payload = pickle.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != SHARED_DIMENSION_CACHE_VERSION:
            return None
        if payload.get("source_fingerprint") != expected_fingerprint:
            return None
        # v2.56：schema/白名单指纹不一致 → 视为未命中（旧载荷缺字段时兼容命中，同日报事实缓存）
        stored_schema = payload.get("schema_fingerprint")
        if stored_schema is not None and stored_schema != schema_fingerprint():
            return None
        dimensions = payload.get("dimensions")
        files_info = payload.get("files_info")
        total_files = payload.get("total_files")
        required = {
            "product_info_by_pid", "product_info_by_sku",
            "stock_by_pid", "stock_by_sku",
        }
        if not isinstance(dimensions, dict) or not required.issubset(dimensions):
            return None
        if not all(isinstance(dimensions[key], pd.DataFrame) for key in required):
            return None
        if not isinstance(files_info, list) or not isinstance(total_files, Integral):
            return None
        return {
            "version": payload["version"],
            "source_fingerprint": payload["source_fingerprint"],
            "dimensions": dimensions,
            "files_info": files_info,
            "total_files": int(total_files),
        }
    except Exception:
        return None


def save_shared_dimension_cache(
    cache_root, entries, dimensions, files_info, total_files,
    *, verified_fingerprint,
):
    """Persist shared dimensions atomically under the dataset cache root."""
    if source_fingerprint(entries) != verified_fingerprint:
        raise RuntimeError("shared dimension sources changed before cache write")
    payload = {
        "version": SHARED_DIMENSION_CACHE_VERSION,
        "source_fingerprint": str(verified_fingerprint),
        "schema_fingerprint": schema_fingerprint(),
        "dimensions": {
            key: value.copy(deep=True) if isinstance(value, pd.DataFrame) else value
            for key, value in dimensions.items()
        },
        "files_info": list(files_info or []),
        "total_files": int(total_files),
    }
    return _atomic_pickle_dump(
        _shared_dimension_cache_path(cache_root), payload,
        prefix="dimensions.",
    )


__all__ = [
    "DAILY_FACT_CACHE_VERSION",
    "SHARED_DIMENSION_CACHE_VERSION",
    "dataset_cache_key",
    "source_fingerprint",
    "schema_fingerprint",
    "load_daily_fact_cache",
    "save_daily_fact_cache",
    "load_shared_dimension_cache",
    "save_shared_dimension_cache",
]
