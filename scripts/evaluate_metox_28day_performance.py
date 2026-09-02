from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from typing import Iterable

import pandas as pd
import pandas.testing as pdt


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


try:
    from core.daily_cache import (
        dataset_cache_key,
        load_daily_fact_cache,
        load_shared_dimension_cache,
        save_daily_fact_cache,
        save_shared_dimension_cache,
        source_fingerprint,
    )
    from core.loader import read_prepared_source, scan_folder
    from core.merge import build_daily_facts, build_shared_dimensions, merge_daily_facts
    from core.periods import aggregate_period_frame
    from core.schema import SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE
except Exception as exc:  # pragma: no cover - exercised by the evaluator shell contract.
    print(json.dumps({
        "status": "BLOCKED",
        "reason": "required core daily fact cache APIs are unavailable",
        "error": repr(exc),
    }, ensure_ascii=False))
    sys.exit(2)


DEFAULT_BASE_ROOT = r"\\10.1.22.253\天猫mark"
DEFAULT_YEAR = "2026"
DEFAULT_SHOP = "女装店"


def _date_window(end_date: str, days: int = 28) -> list[str]:
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def _first_existing_dir(candidates: Iterable[Path | str]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_dir():
            return path
    return None


def _default_product_folder(base_root: Path) -> Path | None:
    return _first_existing_dir([
        base_root / "mark0706啊" / "聚水潭",
        REPO_ROOT / "聚水潭",
        Path(r"e:\08-推广\天猫运营方法论\聚水潭"),
        base_root / "聚水潭",
    ])


def _build_day_paths(base_root: Path, year: str, shop: str, day: str) -> tuple[Path, Path]:
    return (
        base_root / year / shop / "生意参谋" / "商品" / day,
        base_root / year / shop / "生意参谋" / "无界" / day,
    )


def _scan_entries(*folders: Path) -> list[dict]:
    entries: list[dict] = []
    for folder in folders:
        if folder and folder.is_dir():
            entries.extend(scan_folder(folder))
    # The production main-table path isolates optional new-product sources.
    # Keep this benchmark aligned with that path so an invalid optional source
    # cannot turn a main-table cache benchmark into a false failure.
    return [
        entry for entry in entries
        if entry["source"] not in {SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE}
    ]


def _read_sources(entries: list[dict], parsed_cache_dir: Path) -> dict[str, list[tuple[str, pd.DataFrame]]]:
    sources: dict[str, list[tuple[str, pd.DataFrame]]] = {}
    for entry in entries:
        frame = read_prepared_source(
            entry["path"],
            entry["source"],
            parsed_cache_dir=parsed_cache_dir,
        )
        sources.setdefault(entry["source"], []).append((entry["path"], frame))
    return sources


def _load_shared_dimensions(
    product_folder: Path | None,
    parsed_cache_dir: Path,
    dimension_cache_root: Path,
):
    if not product_folder or not product_folder.is_dir():
        return build_shared_dimensions({})
    product_entries = _scan_entries(product_folder)
    cache_entries = _cache_entries(product_entries)
    fingerprint_before = source_fingerprint(cache_entries)
    cached = load_shared_dimension_cache(
        dimension_cache_root, cache_entries,
        expected_fingerprint=fingerprint_before,
    )
    if cached is not None:
        return cached["dimensions"]
    product_sources = _read_sources(product_entries, parsed_cache_dir)
    dimensions = build_shared_dimensions(product_sources)
    fingerprint_after = source_fingerprint(cache_entries)
    if fingerprint_after != fingerprint_before:
        raise RuntimeError("shared dimension source changed during evaluator load")
    save_shared_dimension_cache(
        dimension_cache_root,
        cache_entries,
        dimensions,
        files_info=[
            {
                "name": Path(entry["path"]).name,
                "source": entry["source"],
                "path": str(entry["path"]),
            }
            for entry in product_entries
        ],
        total_files=len(product_entries),
        verified_fingerprint=fingerprint_before,
    )
    return dimensions


def _cache_entries(entries: list[dict]) -> list[tuple[Path, str]]:
    return [(Path(entry["path"]), entry["source"]) for entry in entries]


def _unpack_cache_hit(hit):
    if hit is None:
        return None
    if isinstance(hit, dict):
        for frame_key in ("base_df", "facts", "daily_facts", "frame"):
            if frame_key in hit:
                base_df = hit[frame_key]
                break
        else:
            raise ValueError(f"cache hit has no facts DataFrame key: {sorted(hit)}")
        return base_df, set(hit.get("plan_ids", set()))
    if isinstance(hit, tuple) and len(hit) >= 2:
        return hit[0], set(hit[1])
    raise ValueError(f"unsupported cache hit shape: {type(hit)!r}")


def _unpack_merged(result):
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return result, None


def _aggregate_loaded_days(day_frames: list[pd.DataFrame], plan_ids: set[str], days: list[str]) -> tuple[pd.DataFrame, set[str]]:
    if not day_frames:
        return pd.DataFrame(), set(plan_ids)
    combined = pd.concat(day_frames, ignore_index=True)
    period = aggregate_period_frame(
        combined,
        period_days=len(days),
        actual_days=len(day_frames),
        period_start=days[0],
        period_end=days[-1],
    )
    return period, set(plan_ids)


@contextmanager
def _forced_parse_cache(cache_root: Path):
    with tempfile.TemporaryDirectory(prefix="metox_forced_parse_", dir=cache_root) as tmp:
        yield Path(tmp)


def _run_forced_uncached(args, days: list[str], cache_root: Path, fact_cache_root: Path, product_folder: Path | None):
    started = time.perf_counter()
    day_frames: list[pd.DataFrame] = []
    all_plan_ids: set[str] = set()
    file_counts: dict[str, int] = {}

    with _forced_parse_cache(cache_root) as parse_root:
        dimensions = _load_shared_dimensions(
            product_folder,
            parse_root / "_shared_dimensions",
            fact_cache_root,
        )
        for day in days:
            product_dir, wujie_dir = _build_day_paths(args.base_root, args.year, args.shop, day)
            entries = _scan_entries(product_dir, wujie_dir)
            if not entries:
                raise RuntimeError(f"missing source files for {day}: {product_dir} / {wujie_dir}")
            cache_entries = _cache_entries(entries)
            fingerprint_before = source_fingerprint(cache_entries)
            sources = _read_sources(entries, parse_root / day)
            base_df, plan_ids = build_daily_facts(sources)
            merged, merged_plan_ids = _unpack_merged(merge_daily_facts(base_df, plan_ids, dimensions))
            if merged is None or len(merged) == 0:
                raise RuntimeError(f"daily merge produced no rows for {day}")
            merged = merged.copy()
            merged["_数据日期"] = day
            day_frames.append(merged)
            all_plan_ids.update(merged_plan_ids or plan_ids)
            file_counts[day] = len(entries)
            fingerprint_after = source_fingerprint(cache_entries)
            if fingerprint_after != fingerprint_before:
                raise RuntimeError(f"daily source changed during evaluator load: {day}")
            save_daily_fact_cache(
                fact_cache_root,
                day,
                cache_entries,
                base_df=base_df,
                plan_ids=plan_ids,
                files_info=[
                    {
                        "name": Path(entry["path"]).name,
                        "source": entry["source"],
                        "path": str(entry["path"]),
                    }
                    for entry in entries
                ],
                total_files=len(entries),
                verified_fingerprint=fingerprint_before,
            )

    frame, plan_ids = _aggregate_loaded_days(day_frames, all_plan_ids, days)
    return {
        "seconds": time.perf_counter() - started,
        "frame": frame,
        "plan_ids": plan_ids,
        "file_counts": file_counts,
        "rows": int(len(frame)),
    }


def _run_warm_cached(args, days: list[str], cache_root: Path, fact_cache_root: Path, product_folder: Path | None):
    started = time.perf_counter()
    day_frames: list[pd.DataFrame] = []
    all_plan_ids: set[str] = set()
    cache_hits = 0

    dimensions = _load_shared_dimensions(
        product_folder,
        cache_root / "warm_shared_dimensions",
        fact_cache_root,
    )
    for day in days:
        product_dir, wujie_dir = _build_day_paths(args.base_root, args.year, args.shop, day)
        entries = _scan_entries(product_dir, wujie_dir)
        if not entries:
            raise RuntimeError(f"missing source files for {day}: {product_dir} / {wujie_dir}")
        cache_entries = _cache_entries(entries)
        hit = _unpack_cache_hit(load_daily_fact_cache(
            fact_cache_root,
            day,
            cache_entries,
            expected_fingerprint=source_fingerprint(cache_entries),
        ))
        if hit is None:
            raise RuntimeError(f"daily fact cache miss during warm run for {day}")
        base_df, plan_ids = hit
        merged, merged_plan_ids = _unpack_merged(merge_daily_facts(base_df, plan_ids, dimensions))
        if merged is None or len(merged) == 0:
            raise RuntimeError(f"warm daily merge produced no rows for {day}")
        merged = merged.copy()
        merged["_数据日期"] = day
        day_frames.append(merged)
        all_plan_ids.update(merged_plan_ids or plan_ids)
        cache_hits += 1

    frame, plan_ids = _aggregate_loaded_days(day_frames, all_plan_ids, days)
    return {
        "seconds": time.perf_counter() - started,
        "frame": frame,
        "plan_ids": plan_ids,
        "cache_hits": cache_hits,
        "rows": int(len(frame)),
    }


def _assert_equivalent_results(cold_frame: pd.DataFrame, warm_frame: pd.DataFrame, cold_plan_ids: set[str], warm_plan_ids: set[str]) -> None:
    if cold_plan_ids != warm_plan_ids:
        raise AssertionError(
            f"plan_ids differ: cold={len(cold_plan_ids)} warm={len(warm_plan_ids)}"
        )
    if list(cold_frame.columns) != list(warm_frame.columns):
        raise AssertionError("final frame columns differ")
    sort_cols = ["宝贝ID"] if "宝贝ID" in cold_frame.columns else list(cold_frame.columns[:1])
    cold_sorted = cold_frame.sort_values(sort_cols).reset_index(drop=True)
    warm_sorted = warm_frame.sort_values(sort_cols).reset_index(drop=True)
    pdt.assert_frame_equal(
        cold_sorted,
        warm_sorted,
        check_dtype=False,
        check_exact=False,
        rtol=1e-10,
        atol=1e-10,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate MetoX 28-day daily fact cache performance.")
    parser.add_argument("--date", required=True, help="Analysis date in YYYY-MM-DD format.")
    parser.add_argument("--min-speedup", type=float, required=True)
    parser.add_argument("--max-warm-seconds", type=float, required=True)
    parser.add_argument("--base-root", type=Path, default=Path(DEFAULT_BASE_ROOT))
    parser.add_argument("--year", default=DEFAULT_YEAR)
    parser.add_argument("--shop", default=DEFAULT_SHOP)
    parser.add_argument("--product-folder", type=Path, default=None)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "metox_perf_cache" / "metox_28day",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cache_root = args.cache_root
    cache_root.mkdir(parents=True, exist_ok=True)

    try:
        days = _date_window(args.date, 28)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": "invalid --date", "error": str(exc)}, ensure_ascii=False))
        return 2

    product_folder = args.product_folder or _default_product_folder(args.base_root)
    dataset_key = dataset_cache_key(str(args.base_root), args.year, args.shop)
    fact_cache_root = cache_root / "daily_facts" / dataset_key

    try:
        missing_dirs = []
        for day in days:
            product_dir, wujie_dir = _build_day_paths(args.base_root, args.year, args.shop, day)
            if not product_dir.is_dir() and not wujie_dir.is_dir():
                missing_dirs.append(day)
        if missing_dirs:
            raise RuntimeError(f"missing product/wujie directories for days: {', '.join(missing_dirs)}")

        cold = _run_forced_uncached(args, days, cache_root, fact_cache_root, product_folder)
        warm = _run_warm_cached(args, days, cache_root, fact_cache_root, product_folder)
        _assert_equivalent_results(cold["frame"], warm["frame"], cold["plan_ids"], warm["plan_ids"])

        speedup = cold["seconds"] / warm["seconds"] if warm["seconds"] > 0 else float("inf")
        passed = speedup >= args.min_speedup and warm["seconds"] <= args.max_warm_seconds
        result = {
            "status": "PASS" if passed else "FAIL",
            "date": args.date,
            "window_days": 28,
            "base_root": str(args.base_root),
            "year": args.year,
            "shop": args.shop,
            "product_folder": str(product_folder) if product_folder else None,
            "cache_root": str(cache_root),
            "dataset_cache_key": dataset_key,
            "forced_uncached_seconds": round(cold["seconds"], 3),
            "warm_cached_seconds": round(warm["seconds"], 3),
            "speedup": round(speedup, 3),
            "min_speedup": args.min_speedup,
            "max_warm_seconds": args.max_warm_seconds,
            "cold_rows": cold["rows"],
            "warm_rows": warm["rows"],
            "plan_ids": len(cold["plan_ids"]),
            "warm_cache_hits": warm["cache_hits"],
            "days": days,
            "file_counts": cold["file_counts"],
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if passed else 1
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "date": args.date,
            "reason": str(exc),
            "base_root": str(args.base_root),
            "year": args.year,
            "shop": args.shop,
            "product_folder": str(product_folder) if product_folder else None,
            "cache_root": str(cache_root),
            "traceback": traceback.format_exc(limit=8),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
