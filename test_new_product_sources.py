from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from core.data_validation import DataValidationError
from core.loader import prepare_source, read_prepared_source, scan_folder
from core.schema import (
    SOURCE_PRODUCT_OVERALL,
    SOURCE_WUJIE_CREATIVE,
    classify_file,
)


class NewProductSourceContractTests(unittest.TestCase):
    def test_classifies_and_scans_new_product_sources(self):
        self.assertEqual(classify_file("商品-整体-2026-08-25.xlsx"), SOURCE_PRODUCT_OVERALL)
        self.assertEqual(classify_file("无界-创意-2026-08-25.csv"), SOURCE_WUJIE_CREATIVE)
        self.assertEqual(classify_file("商品-整体退款分布-2026-08-25.xlsx"), "其他数据")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame([{
                "统计日期": "2026-08-25",
                "店铺": "女装店",
                "商品ID": "1001",
                "商品状态": "当前在线",
                "主图整体点击率": "3.5%",
            }]).to_csv(root / "商品-整体-2026-08-25.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame([{
                "统计日期": "2026-08-25",
                "转化周期": "15天",
                "计划ID": "P1001",
                "创意ID": "C1001",
                "创意名字": "主推图",
                "主体ID": "1001",
                "主体类型": "商品",
                "展现量": 1000,
                "点击量": 50,
            }]).to_csv(root / "无界-创意-2026-08-25.csv", index=False, encoding="utf-8-sig")

            scanned = scan_folder(root)
            self.assertEqual(
                {entry["source"] for entry in scanned},
                {SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE},
            )

    def test_product_overall_preserves_product_id_and_standardizes_item_id(self):
        raw = pd.DataFrame([{
            "统计日期": "2026-08-25",
            "店铺名称": "女装店",
            "商品ID": "1001.0",
            "商品名称": "测试商品",
            "叶子类目名称": "连衣裙",
            "商品状态": "当前在线",
            "主图整体点击率": "4.2%",
            "无关列": "ignore-me",
        }])

        prepared = prepare_source(raw, SOURCE_PRODUCT_OVERALL)

        self.assertEqual(prepared.loc[0, "商品ID"], "1001")
        self.assertEqual(prepared.loc[0, "宝贝ID"], "1001")
        self.assertEqual(prepared.loc[0, "店铺"], "女装店")
        self.assertEqual(prepared.loc[0, "商品名称"], "测试商品")
        self.assertEqual(prepared.loc[0, "淘宝类目"], "连衣裙")
        self.assertEqual(prepared.loc[0, "商品状态"], "当前在线")
        self.assertEqual(prepared.loc[0, "主图整体点击率"], "4.2%")

    def test_wujie_creative_keeps_only_15_day_rows_and_preserves_subject_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "无界-创意-2026-08-25.csv"
            pd.DataFrame([
                {
                    "统计日期": "2026-08-25",
                    "转化周期": "15天",
                    "计划ID": "P1001",
                    "计划名字": "新品测试计划",
                    "创意ID": "C1001.0",
                    "创意名字": "主推图",
                    "主体ID": "1001.0",
                    "主体名称": "测试商品",
                    "主体类型": "商品",
                    "场景名字": "关键词推广",
                    "展现量": 1000,
                    "点击量": 50,
                    "点击率": "5%",
                },
                {
                    "统计日期": "2026-08-25",
                    "转化周期": "30天",
                    "计划ID": "P1002",
                    "创意ID": "C1002",
                    "创意名字": "非15天",
                    "主体ID": "1002",
                    "主体类型": "商品",
                    "展现量": 2000,
                    "点击量": 100,
                },
            ]).to_csv(path, index=False, encoding="utf-8-sig")

            prepared = read_prepared_source(
                path, SOURCE_WUJIE_CREATIVE, parsed_cache_dir=root / "parsed"
            )

        self.assertEqual(len(prepared), 1)
        row = prepared.iloc[0]
        self.assertEqual(row["宝贝ID"], "1001")
        self.assertEqual(row["主体ID"], "1001")
        self.assertEqual(row["创意ID"], "C1001")
        self.assertEqual(row["创意名字"], "主推图")
        self.assertEqual(row["计划名字"], "新品测试计划")
        self.assertEqual(row["主体名称"], "测试商品")
        self.assertEqual(row["展现"], "1000")
        self.assertEqual(row["点击"], "50")

    def test_missing_required_new_source_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_path = root / "商品-整体-缺字段.csv"
            pd.DataFrame([{
                "统计日期": "2026-08-25",
                "商品ID": "1001",
                "商品状态": "当前在线",
            }]).to_csv(product_path, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(DataValidationError, "主图整体点击率"):
                read_prepared_source(
                    product_path, SOURCE_PRODUCT_OVERALL, parsed_cache_dir=root / "parsed"
                )

            creative_path = root / "无界-创意-缺字段.csv"
            pd.DataFrame([{
                "统计日期": "2026-08-25",
                "转化周期": "15天",
                "计划ID": "P1001",
                "创意名字": "主推图",
                "主体ID": "1001",
                "主体类型": "商品",
                "展现量": 100,
                "点击量": 5,
            }]).to_csv(creative_path, index=False, encoding="utf-8-sig")
            with self.assertRaisesRegex(DataValidationError, "创意ID"):
                read_prepared_source(
                    creative_path, SOURCE_WUJIE_CREATIVE, parsed_cache_dir=root / "parsed"
                )


if __name__ == "__main__":
    unittest.main()
