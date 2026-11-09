"""v2.60 无界报表 zip 兼容：分类、扫描、读取、日期归一、与旧 xlsx 并存。

覆盖点
------
1. ``classify_zip_package`` 按包名前缀判定源类型，且「素材_创意报表」不会被误判成「创意报表」；
2. ``scan_folder`` 把 zip 纳入扫描结果，且只放行工具消费的两类包；
3. ``read_prepared_source`` 能直接读 zip 内 GBK CSV，字段别名（主体ID→宝贝ID、
   花费→付费、总成交金额→GMV、展现量→展现、点击量→点击、总购物车数→商品加购件数、
   直接购物车数→直购数量）全部生效；
4. ``日期=20261105至20261105`` 归一成 ``2026-11-05``，旧格式日期不受影响；
5. 缓存键随 zip 成员变化（同名同大小换成员不会读到旧解析）；
6. 真实 NAS 数据（可选，缺失时跳过）：11-05 商品总表 zip / 创意报表 zip 的字段覆盖。
"""
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from core.loader import (
    _normalize_period_date,
    dedupe_source_files,
    read_prepared_source,
    read_table,
    scan_folder,
)
from core.schema import ALLOWED_SOURCE_TYPES, SOURCE_WUJIE_CREATIVE, SOURCE_WUJIE_SUBJECT, classify_file
from core.zip_sources import classify_zip_package, is_known_zip_package, is_zip_path

NAS_1105 = Path(r"\\10.1.22.253\天猫mark\2026\集团店\生意参谋\无界\2026-11-05")

#: 无界 11-05 实测表头（81 列中的关键列，顺序与真实导出一致）
NEW_SUBJECT_HEADER = [
    "日期", "场景ID", "场景名字", "原二级场景ID", "原二级场景名字", "计划ID", "计划名字",
    "主体ID", "主体类型", "主体名称", "展现量", "点击量", "花费", "点击率", "平均点击花费",
    "千次展现花费", "总成交金额", "总成交笔数", "点击转化率", "投入产出比", "总购物车数",
    "直接购物车数",
]


def _new_style_csv_rows():
    return [
        NEW_SUBJECT_HEADER,
        ["20261105至20261105", "1", "关键词推广", "11", "关键词推广-标准", "82091360671", "ZT_毛呢外套",
         "979734579639", "商品", "百家好羊毛双面呢大衣", "8681", "510", "179.42", "0.0588", "0.35",
         "12.4", "4213.55", "12", "0.0059", "23.5", "37", "29"],
        ["20261105至20261105", "2", "人群推广", "22", "人群推广-重点", "83570921486", "CS_毛衣_趋势",
         "1096615845258", "商品", "百家好针织毛衣", "2044", "163", "87.37", "0.0798", "0.54",
         "18.1", "1506.20", "5", "0.0055", "17.2", "21", "15"],
    ]


def _write_new_style_zip(path: Path, member_name: str = "商品报表_20261106_095456.csv", rows=None):
    rows = rows or _new_style_csv_rows()
    text = "\n".join(",".join(str(cell) for cell in row) for row in rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, text.encode("gbk"))


class ZipPackageClassifyTest(unittest.TestCase):
    def test_real_package_names(self):
        cases = {
            "商品总表_20261106_093332.zip": SOURCE_WUJIE_SUBJECT,
            "创意报表_20261106_101309.zip": SOURCE_WUJIE_CREATIVE,
            "计划报表_20261106_095347.zip": None,
            "人群报表_20261106_093537.zip": None,
            "关键词报表_20261106_093525.zip": None,
            # 素材包含「创意报表」子串，必须命中更长前缀 → 不消费
            "素材_创意报表_20261106_101344.zip": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_zip_package(name), expected)

    def test_unknown_zip_is_not_known_package(self):
        self.assertFalse(is_known_zip_package("随便什么_20261106.zip"))
        self.assertIsNone(classify_zip_package("随便什么_20261106.zip"))

    def test_classify_file_routes_zip(self):
        self.assertEqual(classify_file("商品总表_20261106_093332.zip"), SOURCE_WUJIE_SUBJECT)
        self.assertEqual(classify_file("创意报表_20261106_101309.zip"), SOURCE_WUJIE_CREATIVE)
        # 识别但不消费 → 其他数据（不能进 ALLOWED_SOURCE_TYPES）
        self.assertNotIn(classify_file("人群报表_20261106_093537.zip"), ALLOWED_SOURCE_TYPES)
        self.assertNotIn(classify_file("素材_创意报表_20261106_101344.zip"), ALLOWED_SOURCE_TYPES)

    def test_old_xlsx_classification_unchanged(self):
        self.assertEqual(classify_file("无界-商品主体-2026-11-04.xlsx"), SOURCE_WUJIE_SUBJECT)
        self.assertEqual(classify_file("无界-创意-2026-11-04.xlsx"), SOURCE_WUJIE_CREATIVE)
        self.assertEqual(classify_file("商品-经营投产比-2026-11-04.xlsx"), "商品-经营投产比")

    def test_is_zip_path(self):
        self.assertTrue(is_zip_path("a/b/商品总表_1.zip"))
        self.assertTrue(is_zip_path(Path("x.ZIP")))
        self.assertFalse(is_zip_path("无界-商品主体-2026-11-04.xlsx"))


class PeriodDateNormalizeTest(unittest.TestCase):
    def test_range_and_single(self):
        self.assertEqual(_normalize_period_date("20261105至20261105"), "2026-11-05")
        self.assertEqual(_normalize_period_date("20261105~20261106"), "2026-11-05")
        self.assertEqual(_normalize_period_date("20261105"), "2026-11-05")
        self.assertEqual(_normalize_period_date("2026-11-05"), "2026-11-05")
        self.assertEqual(_normalize_period_date(""), "")
        self.assertEqual(_normalize_period_date(None), None)


class ZipReadTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="metox_zip_"))
        self.cache_dir = self.tmp / "_cache"
        self.zip_path = self.tmp / "商品总表_20261106_093332.zip"
        _write_new_style_zip(self.zip_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_table_maps_new_columns(self):
        df = read_table(str(self.zip_path), source_type=SOURCE_WUJIE_SUBJECT)
        # read_table 只做「表头定位 + 原始列白名单过滤」，标准列名由 prepare_source 归一
        for raw in ("主体ID", "花费", "总成交金额", "计划ID", "展现量", "点击量", "直接购物车数"):
            self.assertIn(raw, df.columns, raw)
        # 新导出无转化周期列，由 loader 补常量以沿用「只取 15 天」链路
        self.assertEqual(set(df["转化周期"]), {"15天转化"})
        self.assertEqual(len(df), 2)
        self.assertEqual(df["计划ID"].tolist(), ["82091360671", "83570921486"])

    def test_read_prepared_source_zip_end_to_end(self):
        df = read_prepared_source(str(self.zip_path), SOURCE_WUJIE_SUBJECT,
                                  parsed_cache_dir=str(self.cache_dir))
        self.assertEqual(len(df), 2)
        self.assertEqual(set(df["宝贝ID"]), {"979734579639", "1096615845258"})
        self.assertAlmostEqual(pd.to_numeric(df["付费"], errors="coerce").sum(), 179.42 + 87.37, places=2)
        self.assertAlmostEqual(pd.to_numeric(df["GMV"], errors="coerce").sum(), 4213.55 + 1506.20, places=2)
        self.assertAlmostEqual(pd.to_numeric(df["展现"], errors="coerce").sum(), 8681 + 2044, places=2)
        self.assertEqual(df["场景名字"].tolist(), ["关键词推广", "人群推广"])
        self.assertEqual(df["计划名字"].tolist(), ["ZT_毛呢外套", "CS_毛衣_趋势"])
        self.assertEqual(df["主体类型"].tolist(), ["商品", "商品"])
        self.assertEqual(pd.to_numeric(df["商品加购件数"], errors="coerce").tolist(), [37.0, 21.0])
        self.assertEqual(pd.to_numeric(df["直购数量"], errors="coerce").tolist(), [29.0, 15.0])

    def test_creative_source_keeps_normalized_date(self):
        zip_creative = self.tmp / "创意报表_20261106_101309.zip"
        rows = [
            ["日期", "场景名字", "计划ID", "计划名字", "创意ID", "创意名字", "主体ID", "主体名称",
             "主体类型", "展现量", "点击量", "花费", "点击率"],
            ["20261105至20261105", "关键词推广", "82091360671", "ZT_毛呢外套", "68387030238",
             "创意_20260225120206_1018609467605", "1018609467605", "百家好米白色长袖T恤", "商品",
             "8681", "510", "179.42", "0.0588"],
        ]
        text = "\n".join(",".join(row) for row in rows)
        with zipfile.ZipFile(zip_creative, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("创意报表_20261106_101309.csv", text.encode("gbk"))
        df = read_prepared_source(str(zip_creative), SOURCE_WUJIE_CREATIVE,
                                  parsed_cache_dir=str(self.cache_dir))
        self.assertIn("统计日期", df.columns)
        self.assertEqual(set(df["统计日期"]), {"2026-11-05"})
        self.assertEqual(df["创意ID"].tolist(), ["68387030238"])
        self.assertEqual(df["宝贝ID"].tolist(), ["1018609467605"])

    def test_zip_cache_key_changes_with_member(self):
        """同名同大小但成员/内容不同的 zip，必须产生不同缓存键（不读旧解析）。"""
        first = read_prepared_source(str(self.zip_path), SOURCE_WUJIE_SUBJECT,
                                     parsed_cache_dir=str(self.cache_dir))
        rows = _new_style_csv_rows()
        rows[1][2] = "人群推广"
        _write_new_style_zip(self.zip_path, member_name="商品报表_20261106_100000.csv", rows=rows)
        second = read_prepared_source(str(self.zip_path), SOURCE_WUJIE_SUBJECT,
                                      parsed_cache_dir=str(self.cache_dir))
        self.assertEqual(first["场景名字"].tolist()[0], "关键词推广")
        self.assertEqual(second["场景名字"].tolist()[0], "人群推广")

    def test_scan_folder_includes_zip_and_filters_unused(self):
        (self.tmp / "人群报表_20261106_093537.zip").write_bytes(b"PK\x03\x04")
        (self.tmp / "商品-经营投产比-2026-11-04.xlsx").write_bytes(b"stub")
        found = scan_folder(str(self.tmp))
        names = {item["name"] for item in found}
        self.assertIn("商品总表_20261106_093332.zip", names)
        self.assertIn("商品-经营投产比-2026-11-04.xlsx", names)
        self.assertNotIn("人群报表_20261106_093537.zip", names)

    def test_dedupe_prefers_zip_over_xlsx(self):
        """同日同源类型 zip 与 xlsx 并存（同一批数据）时只取 zip，避免花费翻倍。"""
        xlsx_same_source = self.tmp / "无界-商品主体-2026-11-05.xlsx"
        xlsx_same_source.write_bytes(b"stub")
        kept, dropped = dedupe_source_files([
            {"path": str(self.zip_path), "name": self.zip_path.name, "source": SOURCE_WUJIE_SUBJECT},
            {"path": str(xlsx_same_source), "name": xlsx_same_source.name, "source": SOURCE_WUJIE_SUBJECT},
        ])
        self.assertEqual([e["name"] for e in kept], [self.zip_path.name])
        self.assertEqual([e["name"] for e in dropped], [xlsx_same_source.name])

        found = scan_folder(str(self.tmp))
        self.assertEqual([item["name"] for item in found if item["source"] == SOURCE_WUJIE_SUBJECT],
                         [self.zip_path.name])

    def test_dedupe_keeps_single_xlsx_when_no_zip(self):
        only_xlsx = self.tmp / "无界-商品主体-2026-11-04.xlsx"
        only_xlsx.write_bytes(b"stub")
        kept, dropped = dedupe_source_files([
            {"path": str(only_xlsx), "name": only_xlsx.name, "source": SOURCE_WUJIE_SUBJECT},
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])


class GoodsOverallNamingTest(unittest.TestCase):
    """2026-11-05 起的「商品_整体_<起>_<止>.xls」新命名 + 前 4 行空、表头在第 5 行的新布局。"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="metox_goods_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_naming_variants_classify_as_product_overall(self):
        for name in ("商品_整体_2026-11-05_2026-11-05.xls",
                     "商品_整体_2026-11-05_2026-11-05.xlsx",
                     "商品-整体-2026-11-05.xlsx"):
            with self.subTest(name=name):
                self.assertEqual(classify_file(name), "商品-整体", name)

    def test_same_prefix_derivative_not_misclassified(self):
        for name in ("商品-整体退款分布-2026-11-05.xlsx",
                     "商品_整体退款分布_2026-11-05.xlsx"):
            with self.subTest(name=name):
                self.assertNotEqual(classify_file(name), "商品-整体", name)

    def test_leading_blank_rows_header_on_row5(self):
        """前 4 行空、表头在第 5 行 + 无「主图整体点击率」列时仍能读，且该列留空不补 0。"""
        from openpyxl import Workbook
        path = self.tmp / "商品_整体_2026-11-05_2026-11-05.xlsx"
        wb = Workbook()
        ws = wb.active
        header = ["统计日期", "商品ID", "商品名称", "商品类型", "货号", "商品状态",
                  "商品访客数", "支付买家数", "支付金额"]
        ws.append([])   # 1
        ws.append([])   # 2
        ws.append([])   # 3
        ws.append([])   # 4
        ws.append(header)                                         # 5 = 表头
        ws.append(["2026-11-05", "979734579639", "百家好双面呢大衣", "主商品",
                   "B0625B5JUV2", "当前在线", "22679", "413", "85000"])
        wb.save(path)

        df = read_prepared_source(str(path), "商品-整体",
                                  parsed_cache_dir=str(self.tmp / "_cache"))
        self.assertEqual(len(df), 1)
        self.assertEqual(df["宝贝ID"].tolist(), ["979734579639"])
        self.assertEqual(df["商品状态"].tolist(), ["当前在线"])
        self.assertIn("主图整体点击率", df.columns)
        # 留空（不是 0）：下游按无数据处理
        self.assertEqual(df["主图整体点击率"].fillna("").astype(str).str.strip().tolist(), [""])


@unittest.skipUnless(NAS_1105.is_dir(), "无 NAS 11-05 数据，跳过真实数据用例")
class RealZipDataTest(unittest.TestCase):
    def test_real_subject_zip_has_plan_ids_and_spend(self):
        target = next(NAS_1105.glob("商品总表*.zip"), None)
        self.assertIsNotNone(target, "未找到商品总表 zip")
        df = read_prepared_source(str(target), SOURCE_WUJIE_SUBJECT)
        self.assertGreater(len(df), 1000)
        plan = df["计划ID"].astype(str).str.strip()
        filled = (plan.ne("") & plan.str.upper().ne("NULL")).mean()
        self.assertGreater(filled, 0.95, f"计划ID 覆盖率过低: {filled:.3f}")
        spend = pd.to_numeric(df["付费"], errors="coerce").sum()
        self.assertGreater(spend, 0, "花费合计应大于 0")
        self.assertGreater(df["宝贝ID"].nunique(), 900)
        self.assertTrue({"关键词推广", "人群推广"}.issubset(set(df["场景名字"].dropna())))

    def test_real_creative_zip(self):
        target = next(NAS_1105.glob("创意报表*.zip"), None)
        if target is None:
            self.skipTest("未找到创意报表 zip")
        df = read_prepared_source(str(target), SOURCE_WUJIE_CREATIVE)
        self.assertGreater(len(df), 1000)
        self.assertGreater(df["创意ID"].nunique(), 1000)
        self.assertEqual(set(df["统计日期"].dropna()), {"2026-11-05"})

    def test_old_xlsx_still_readable(self):
        old = NAS_1105.parent / "2026-11-04" / "无界-商品主体-2026-11-04.xlsx"
        if not old.exists():
            self.skipTest("无 11-04 旧格式样本")
        df = read_prepared_source(str(old), SOURCE_WUJIE_SUBJECT)
        self.assertGreater(len(df), 1000)
        self.assertIn("计划ID", df.columns)


if __name__ == "__main__":
    unittest.main()
