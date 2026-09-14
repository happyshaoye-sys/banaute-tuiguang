from __future__ import annotations

import inspect
import os
import pickle
from pathlib import Path
import tempfile
import unittest

import pandas as pd
import pandas.testing as pdt

from core.daily_cache import (
    DAILY_FACT_CACHE_VERSION,
    dataset_cache_key,
    load_daily_fact_cache,
    load_shared_dimension_cache,
    save_daily_fact_cache,
    save_shared_dimension_cache,
    source_fingerprint,
)
from core.merge import (
    FULL_PLAN_DETAIL_COLUMN,
    _numeric_series as merge_numeric_series,
    build_daily_facts,
    build_shared_dimensions,
    merge_daily_facts,
    merge_sources,
)
from core.periods import _numeric_series as period_numeric_series
from core.schema import to_num
from core.schema import (
    SOURCE_PRODUCT_INFO,
    SOURCE_PRODUCT_ROI,
    SOURCE_STOCK,
    SOURCE_WUJIE_SUBJECT,
)


DAY = "2026-08-13"


def _daily_sources(include_dimensions: bool = True) -> dict[str, list[tuple[str, pd.DataFrame]]]:
    product = pd.DataFrame([
        {
            "宝贝ID": "1001",
            "商品名称": "contract product",
            "统计日期": DAY,
            "淘宝类目": "连衣裙",
            "GMV": 1000.0,
            "商品访客数": 100.0,
            "支付买家数": 10.0,
            "支付件数": 12.0,
            "成功退款金额": 20.0,
            "商品加购件数": 25.0,
            "搜索引导访客数": 40.0,
            "推广引导总成交金额": 150.0,
            "关键词推广访客数": 20.0,
            "人群推广访客数": 10.0,
            "场景推广访客数": 5.0,
            "关键词推广加购人数": 4.0,
            "人群推广加购人数": 2.0,
            "场景推广加购人数": 1.0,
            "关键词推广加购数": 5.0,
            "人群推广加购数": 3.0,
            "场景推广加购数": 2.0,
        }
    ])
    wujie = pd.DataFrame([
        {
            "宝贝ID": "1001",
            "主体类型": "商品",
            "转化周期": "15天",
            "场景名字": "关键词推广",
            "计划ID": "P1001",
            "计划名字": "连衣裙-主推-精准",
            "付费": 100.0,
            "GMV": 80.0,
            "推广投产": 0.8,
            "商品加购件数": 6.0,
            "直购数量": 2.0,
            "展现": 1000.0,
            "点击": 100.0,
        },
        {
            "宝贝ID": "1001",
            "主体类型": "商品",
            "转化周期": "15天",
            "场景名字": "人群推广",
            "计划ID": "P1002",
            "付费": 50.0,
            "GMV": 0.0,
            "推广投产": 0.6,
            "商品加购件数": 3.0,
            "直购数量": 1.0,
            "展现": 500.0,
            "点击": 50.0,
        },
    ])
    sources: dict[str, list[tuple[str, pd.DataFrame]]] = {
        SOURCE_PRODUCT_ROI: [("product.xlsx", product)],
        SOURCE_WUJIE_SUBJECT: [("wujie.xlsx", wujie)],
    }
    if include_dimensions:
        sources.update(_dimension_sources(stock=30, refund_rate=0.12, cost=50))
    return sources


def _dimension_sources(stock: float, refund_rate: float, cost: float) -> dict[str, list[tuple[str, pd.DataFrame]]]:
    product_info = pd.DataFrame([
        {
            "宝贝ID": "1001",
            "货号": "SKU-1001",
            "款式主图": "https://example.invalid/1001.jpg",
            "创建时间": "2026-01-01 10:00:00",
            "退款率": refund_rate,
            "在途": 2.0,
            "成本价（含税）": cost,
        }
    ])
    stock_frame = pd.DataFrame([
        {
            "货号": "SKU-1001",
            "在仓库存": stock,
            "在途": 4.0,
            "成本价（含税）": cost + 1,
            "日销7": 3.0,
        }
    ])
    return {
        SOURCE_PRODUCT_INFO: [("product_info.csv", product_info)],
        SOURCE_STOCK: [("stock.csv", stock_frame)],
    }


def _assert_equivalent_frame(testcase: unittest.TestCase, expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    testcase.assertEqual(list(expected.columns), list(actual.columns))
    expected_rows = expected.sort_values("宝贝ID").reset_index(drop=True)
    actual_rows = actual.sort_values("宝贝ID").reset_index(drop=True)
    pdt.assert_frame_equal(
        expected_rows,
        actual_rows,
        check_dtype=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def _unpack_cache_hit(hit):
    if hit is None:
        return None
    if isinstance(hit, dict):
        for frame_key in ("base_df", "facts", "daily_facts", "frame"):
            if frame_key in hit:
                base_df = hit[frame_key]
                break
        else:
            raise AssertionError(f"cache hit has no facts DataFrame key: {sorted(hit)}")
        plan_ids = hit.get("plan_ids", set())
        return base_df, set(plan_ids)
    if isinstance(hit, tuple) and len(hit) >= 2:
        return hit[0], set(hit[1])
    raise AssertionError(f"unsupported cache hit shape: {type(hit)!r}")


def _unpack_merged(result):
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return result, None


class DailyFactPipelineContractTests(unittest.TestCase):
    def test_vectorized_numeric_helpers_match_scalar_numeric_contract(self):
        values = pd.Series(["1,234", " ￥5.50 ", "12%", "bad", None, 3.0])
        expected = pd.Series([to_num(value) for value in values], dtype=float)

        pdt.assert_series_equal(merge_numeric_series(values), expected)
        pdt.assert_series_equal(period_numeric_series(values), expected)

    def test_split_daily_fact_pipeline_matches_legacy_merge_sources(self):
        sources = _daily_sources(include_dimensions=True)
        expected_df, expected_plan_ids = merge_sources(sources)

        base_df, plan_ids = build_daily_facts(sources)
        dimensions = build_shared_dimensions(sources)
        actual_df, actual_plan_ids = _unpack_merged(merge_daily_facts(base_df, plan_ids, dimensions))

        self.assertEqual(set(plan_ids), expected_plan_ids)
        self.assertEqual(actual_plan_ids, expected_plan_ids)
        _assert_equivalent_frame(self, expected_df, actual_df)
        row = actual_df.iloc[0]
        self.assertEqual(row["宝贝ID"], "1001")
        self.assertEqual(row["货号"], "SKU-1001")
        self.assertEqual(row["GMV"], 1000.0)
        self.assertEqual(row["付费"], 150.0)
        self.assertEqual(row["推广成交金额"], 110.0)
        self.assertEqual(row["在仓库存"], 30.0)
        self.assertAlmostEqual(row["退款率"], 0.12)
        self.assertEqual(row["成本价（含税）"], 50.0)
        self.assertIn("计划ID:P1001", row["推广明细"])
        self.assertIn("计划ID:P1002", row["推广明细"])
        # v2.54：明细记录带出计划名称（此前只有计划ID），推广计划管理/全量明细才有名称
        self.assertIn("计划名:连衣裙-主推-精准", row["推广明细"])
        self.assertIn("连衣裙-主推-精准", str(row[FULL_PLAN_DETAIL_COLUMN]))

    def test_cached_facts_use_latest_shared_dimensions_on_each_merge(self):
        daily_sources = _daily_sources(include_dimensions=False)
        base_df, plan_ids = build_daily_facts(daily_sources)

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "product.xlsx"
            source_path.write_text("source-v1", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_ROI)]
            fingerprint = source_fingerprint(entries)
            save_daily_fact_cache(
                cache_root,
                DAY,
                entries,
                base_df=base_df,
                plan_ids=plan_ids,
                files_info=[{"name": source_path.name, "source": SOURCE_PRODUCT_ROI}],
                total_files=len(entries),
                verified_fingerprint=fingerprint,
            )

            hit = _unpack_cache_hit(load_daily_fact_cache(cache_root, DAY, entries))
            self.assertIsNotNone(hit)
            cached_base_df, cached_plan_ids = hit

            first_dims = build_shared_dimensions(_dimension_sources(stock=30, refund_rate=0.12, cost=50))
            second_dims = build_shared_dimensions(_dimension_sources(stock=99, refund_rate=0.34, cost=80))

            first, first_plan_ids = _unpack_merged(
                merge_daily_facts(cached_base_df, cached_plan_ids, first_dims)
            )
            second, second_plan_ids = _unpack_merged(
                merge_daily_facts(cached_base_df, cached_plan_ids, second_dims)
            )

            self.assertEqual(first.loc[0, "宝贝ID"], "1001")
            self.assertEqual(first.loc[0, "在仓库存"], 30.0)
            self.assertEqual(second.loc[0, "在仓库存"], 99.0)
            self.assertAlmostEqual(first.loc[0, "退款率"], 0.12)
            self.assertAlmostEqual(second.loc[0, "退款率"], 0.34)
            self.assertEqual(first.loc[0, "GMV"], second.loc[0, "GMV"])
            self.assertEqual(set(cached_plan_ids), {"P1001", "P1002"})
            self.assertEqual(first_plan_ids, second_plan_ids)


class DailyFactDiskCacheTests(unittest.TestCase):
    def test_cache_miss_save_hit_invalidates_on_size_or_mtime_change_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "product-source.csv"
            source_path.write_text("source-v1", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_ROI)]
            base_df = pd.DataFrame([{"宝贝ID": "1001", "GMV": 100.0}])
            plan_ids = {"P1001"}

            self.assertIsNone(load_daily_fact_cache(cache_root, DAY, entries))
            before = source_fingerprint(entries)

            save_daily_fact_cache(
                cache_root,
                DAY,
                entries,
                base_df=base_df,
                plan_ids=plan_ids,
                files_info=[{"name": source_path.name, "source": SOURCE_PRODUCT_ROI}],
                total_files=len(entries),
                verified_fingerprint=before,
            )

            hit = _unpack_cache_hit(load_daily_fact_cache(cache_root, DAY, entries))
            self.assertIsNotNone(hit)
            cached_base_df, cached_plan_ids = hit
            pdt.assert_frame_equal(base_df, cached_base_df, check_dtype=False)
            self.assertEqual(cached_plan_ids, plan_ids)
            self.assertEqual(list(cache_root.rglob("*.tmp")), [])

            next_mtime = source_path.stat().st_mtime + 2
            source_path.write_text("source-v1-with-more-bytes", encoding="utf-8")
            os.utime(source_path, (next_mtime, next_mtime))
            after = source_fingerprint(entries)

            self.assertNotEqual(before, after)
            self.assertIsNone(load_daily_fact_cache(cache_root, DAY, entries))

    def test_corrupt_pickle_is_treated_as_cache_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "product-source.csv"
            source_path.write_text("source-v1", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_ROI)]
            fingerprint = source_fingerprint(entries)

            save_daily_fact_cache(
                cache_root,
                DAY,
                entries,
                base_df=pd.DataFrame([{"宝贝ID": "1001", "GMV": 100.0}]),
                plan_ids={"P1001"},
                files_info=[],
                total_files=len(entries),
                verified_fingerprint=fingerprint,
            )

            pickle_files = list(cache_root.rglob("*.pkl"))
            self.assertTrue(pickle_files, "daily fact cache should persist a pickle payload")
            for path in pickle_files:
                path.write_bytes(b"not a pickle")

            self.assertIsNone(load_daily_fact_cache(cache_root, DAY, entries))

    def test_old_cache_versions_are_treated_as_cache_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "product-source.csv"
            source_path.write_text("source-v1", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_ROI)]
            fingerprint = source_fingerprint(entries)

            daily_path = cache_root / "_daily_facts" / f"{DAY}.pkl"
            daily_path.parent.mkdir(parents=True)
            with daily_path.open("wb") as fh:
                pickle.dump({
                    "version": "metox2.25-daily-facts-v1",
                    "source_fingerprint": fingerprint,
                    "base_df": pd.DataFrame([{"宝贝ID": "1001"}]),
                    "plan_ids": [],
                    "files_info": [],
                    "total_files": 1,
                }, fh)
            self.assertIsNone(load_daily_fact_cache(cache_root, DAY, entries))

            shared_path = cache_root / "_shared_dimensions" / "_dimensions.pkl"
            shared_path.parent.mkdir(parents=True)
            empty = pd.DataFrame()
            with shared_path.open("wb") as fh:
                pickle.dump({
                    "version": "metox2.25-shared-dimensions-v1",
                    "source_fingerprint": fingerprint,
                    "dimensions": {
                        "product_info_by_pid": empty,
                        "product_info_by_sku": empty,
                        "stock_by_pid": empty,
                        "stock_by_sku": empty,
                    },
                    "files_info": [],
                    "total_files": 1,
                }, fh)
            self.assertIsNone(load_shared_dimension_cache(cache_root, entries))

    def test_verified_fingerprint_cannot_be_relabelled_after_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "source.csv"
            source_path.write_text("before", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_ROI)]
            verified_before_read = source_fingerprint(entries)

            source_path.write_text("changed-during-read", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "sources changed"):
                save_daily_fact_cache(
                    cache_root,
                    DAY,
                    entries,
                    base_df=pd.DataFrame([{"宝贝ID": "1001", "GMV": 100.0}]),
                    plan_ids={"P1001"},
                    files_info=[],
                    total_files=1,
                    verified_fingerprint=verified_before_read,
                )

            self.assertIsNone(load_daily_fact_cache(cache_root, DAY, entries))

    def test_shared_dimensions_cache_hits_then_invalidates_with_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            source_path = cache_root / "product-info.csv"
            source_path.write_text("dimensions-v1", encoding="utf-8")
            entries = [(source_path, SOURCE_PRODUCT_INFO)]
            fingerprint = source_fingerprint(entries)
            dimensions = build_shared_dimensions(
                _dimension_sources(stock=30, refund_rate=0.12, cost=50)
            )

            save_shared_dimension_cache(
                cache_root,
                entries,
                dimensions,
                files_info=[{"name": source_path.name, "source": SOURCE_PRODUCT_INFO}],
                total_files=1,
                verified_fingerprint=fingerprint,
            )
            hit = load_shared_dimension_cache(cache_root, entries)
            self.assertIsNotNone(hit)
            pdt.assert_frame_equal(
                hit["dimensions"]["product_info_by_pid"],
                dimensions["product_info_by_pid"],
                check_dtype=False,
            )
            self.assertEqual(list(cache_root.rglob("*.tmp")), [])

            source_path.write_text("dimensions-v2-larger", encoding="utf-8")
            self.assertIsNone(load_shared_dimension_cache(cache_root, entries))

    def test_dataset_cache_key_is_stable_for_dataset_without_analysis_date(self):
        signature = inspect.signature(dataset_cache_key)
        parameter_text = " ".join(signature.parameters)
        self.assertNotIn("date", parameter_text.lower())
        self.assertNotIn("day", parameter_text.lower())

        key_a = dataset_cache_key(r"\\10.1.22.253\天猫mark", "2026", "女装店")
        key_b = dataset_cache_key(r"\\10.1.22.253\天猫mark", "2026", "女装店")
        key_c = dataset_cache_key(r"\\10.1.22.253\天猫mark", "2026", "集团店")

        self.assertIsInstance(DAILY_FACT_CACHE_VERSION, str)
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)
        self.assertNotIn("2026-08-13", key_a)


if __name__ == "__main__":
    unittest.main()
