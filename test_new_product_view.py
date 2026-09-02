from __future__ import annotations

import ast
from datetime import date, timedelta
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

from core.new_product_backtest import (
    BacktestColumns as C,
    DECISION_DOWN,
    DECISION_OBSERVE,
    run_backtest,
)
from core.new_product_first_seen import (
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    IS_HISTORY_TRUNCATED,
    STYLE_KEY,
    build_first_seen_index,
)
from core.schema import (
    SOURCE_PRODUCT_OVERALL,
    SOURCE_PRODUCT_ROI,
    SOURCE_WUJIE_CREATIVE,
    SOURCE_WUJIE_SUBJECT,
)


def _fake_streamlit_modules():
    streamlit = types.ModuleType("streamlit")
    components = types.ModuleType("streamlit.components")
    components_v1 = types.ModuleType("streamlit.components.v1")

    def cache_data(*args, **_kwargs):
        if args and callable(args[0]):
            return args[0]

        def decorator(fn):
            return fn

        return decorator

    streamlit.cache_data = cache_data
    streamlit.components = components
    components.v1 = components_v1
    return {
        "streamlit": streamlit,
        "streamlit.components": components,
        "streamlit.components.v1": components_v1,
    }


def _load_app_helpers():
    app_path = Path(__file__).resolve().with_name("app.py")
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = [target for target in node.targets if isinstance(target, ast.Name)]
            if targets and all(target.id.isupper() or target.id.startswith("_") for target in targets):
                body.append(node)
    helper_tree = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(helper_tree)

    module = types.ModuleType("app_helpers_for_new_product_tests")
    module.__file__ = str(app_path)
    fake_modules = _fake_streamlit_modules()
    previous = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    try:
        exec(compile(helper_tree, str(app_path), "exec"), module.__dict__)
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


APP = _load_app_helpers()


def _product_overall(pid, sku, first_date="2026-08-25", main_ctr="5%"):
    return {
        "统计日期": first_date,
        "宝贝ID": pid,
        "货号": sku,
        "商品名称": f"商品{pid}",
        "淘宝类目": "半身裙",
        "商品状态": "当前在线",
        "主图整体点击率": main_ctr,
    }


def _business_fact(sku, pid, age, first_day, *, carts, buyers, gmv, units=0, label="", visitors=50):
    row = {
        "宝贝ID": pid,
        "货号": sku,
        "_数据日期": (first_day + timedelta(days=age - 1)).isoformat(),
        "店铺": "女装",
        "淘宝类目": "半身裙",
        "价格段": "100-200",
        "商品访客数": visitors,
        "商品加购件数": carts,
        "支付买家数": buyers,
        "支付件数": units,
        "GMV": gmv,
        "商品状态": "当前在线",
        "在仓库存": 1,
    }
    if label:
        row["MetoX算法命中"] = label
    return row


def _first_seen_rows(count=20, first_date="2026-08-01"):
    return pd.DataFrame([
        {
            STYLE_KEY: f"SKU-{idx:02d}",
            FIRST_SEEN_DATE: first_date,
            FIRST_SEEN_PRODUCT_ID: f"10{idx:02d}",
            IS_HISTORY_TRUNCATED: False,
        }
        for idx in range(count)
    ])


class NewProductViewIntegrationTests(unittest.TestCase):
    def test_business_history_reuses_loaded_day_map_before_loading_missing_days(self):
        first_loaded = (pd.DataFrame([{"_数据日期": "2026-08-25"}]), [{"name": "25"}])
        latest_loaded = (pd.DataFrame([{"_数据日期": "2026-08-26"}]), [{"name": "26"}])
        existing_day_map = {
            "2026-08-25": first_loaded,
            "2026-08-26": latest_loaded,
        }
        calls = []
        progress = []
        original = APP._load_single_day

        def load_missing_day(_base_root, day, *_args, **_kwargs):
            calls.append(day)
            return pd.DataFrame([{"_数据日期": day}]), [{"name": day}]

        try:
            APP._load_single_day = load_missing_day
            day_map, files_info, errors = APP._load_new_product_business_history(
                Path("root"), "2026", "女装", Path("product"), "2026-08-26", Path("cache"),
                history_days=3,
                existing_day_map=existing_day_map,
                progress_callback=lambda value, text: progress.append((value, text)),
            )
        finally:
            APP._load_single_day = original

        self.assertEqual(calls, ["2026-08-24"])
        self.assertIs(day_map["2026-08-25"], first_loaded)
        self.assertIs(day_map["2026-08-26"], latest_loaded)
        self.assertEqual(len(files_info), 3)
        self.assertEqual(errors, [])
        self.assertTrue(any("正在补读经营日事实 1/3 天：2026-08-24" in text for _value, text in progress))
        self.assertTrue(any("正在复用已加载经营日事实 2/3 天：2026-08-25" in text for _value, text in progress))

    def test_new_product_history_limits_creative_scans_to_active_window(self):
        include_creative_flags = []
        progress = []
        original_paths = APP._build_paths
        original_scan = APP._scan_new_product_source_files
        try:
            APP._build_paths = lambda *_args: (None, None)

            def scan_sources(_biz_folder, _wujie_folder, include_creative=True):
                include_creative_flags.append(include_creative)
                return []

            APP._scan_new_product_source_files = scan_sources
            APP._load_new_product_history(
                Path("root"), "2026", "女装", "2026-08-26", Path("cache"),
                history_days=47,
                progress_callback=lambda value, text: progress.append((value, text)),
            )
        finally:
            APP._build_paths = original_paths
            APP._scan_new_product_source_files = original_scan

        self.assertEqual(include_creative_flags[:2], [False, False])
        self.assertEqual(sum(include_creative_flags), APP.NEW_PRODUCT_CREATIVE_HISTORY_DAYS)
        self.assertTrue(any("正在读取首次发现历史 1/47 天" in text for _value, text in progress))
        self.assertTrue(any("正在读取首次发现与创意历史 3/47 天" in text for _value, text in progress))

    def test_primary_scan_and_single_day_loader_ignore_bad_new_product_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = "2026-08-25"
            product_day = root / "2026" / "女装" / "生意参谋" / "商品" / day
            wujie_day = root / "2026" / "女装" / "生意参谋" / "无界" / day
            product_day.mkdir(parents=True)
            wujie_day.mkdir(parents=True)
            (product_day / "商品-经营投产比-2026-08-25.csv").write_text("", encoding="utf-8")
            (wujie_day / "无界-商品主体-2026-08-25.csv").write_text("", encoding="utf-8")
            (product_day / "商品-整体-2026-08-25.csv").write_text("bad,new,source\n1,2,3\n", encoding="utf-8")
            (wujie_day / "无界-创意-2026-08-25.csv").write_text("bad,new,source\n1,2,3\n", encoding="utf-8")

            primary_sources = {
                source for _fp, _name, source in (
                    APP._scan_target_files(product_day) + APP._scan_target_files(wujie_day)
                )
            }
            new_sources = {
                source for _fp, _name, source in (
                    APP._scan_target_files(product_day, source_types={SOURCE_PRODUCT_OVERALL})
                    + APP._scan_target_files(wujie_day, source_types={SOURCE_WUJIE_CREATIVE})
                )
            }

            self.assertEqual(primary_sources, {SOURCE_PRODUCT_ROI, SOURCE_WUJIE_SUBJECT})
            self.assertEqual(new_sources, {SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE})

            product_only_day = root / "2026" / "女装" / "生意参谋" / "商品" / "2026-08-26"
            product_only_day.mkdir(parents=True)
            (product_only_day / "商品-整体-2026-08-26.csv").write_text("bad,new,source\n1,2,3\n", encoding="utf-8")
            result = APP._load_single_day(
                root,
                "2026-08-26",
                "2026",
                "女装",
                root / "2026" / "女装" / "生意参谋" / "商品",
                root / "cache",
                False,
                "测试",
            )

            self.assertIsNone(result[0])
            self.assertEqual(result[1], [])

    def test_history_start_uses_actual_product_overall_coverage_for_left_censoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_day = root / "2026" / "女装" / "生意参谋" / "商品" / "2026-07-05"
            product_day.mkdir(parents=True)
            pd.DataFrame([{
                "统计日期": "2026-07-05",
                "商品ID": "1001",
                "商品状态": "当前在线",
                "主图整体点击率": "4.2%",
            }]).to_csv(product_day / "商品-整体-2026-07-05.csv", index=False, encoding="utf-8-sig")

            history = APP._load_new_product_history(
                root, "2026", "女装", "2026-08-25", root / "cache", history_days=90
            )
            first_seen = build_first_seen_index(
                history["product_overall_daily"],
                history_start_date=history["history_start_date"],
            ).set_index(STYLE_KEY)

            self.assertEqual(history["requested_history_start_date"], "2026-05-28")
            self.assertEqual(history["history_start_date"], "2026-07-05")
            self.assertTrue(bool(first_seen.loc["1001", IS_HISTORY_TRUNCATED]))

    def test_first_seen_index_path_and_save_stay_under_cache_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / "cache"
            path = APP._first_seen_index_path(cache_root, "2026", "女装")
            frame = pd.DataFrame([{STYLE_KEY: "SKU-1", FIRST_SEEN_DATE: "2026-08-01"}])

            warning = APP._save_first_seen_index(frame, path)

            self.assertEqual(path.parent, cache_root)
            self.assertEqual(path.name, "new_product_first_seen_2026_女装.pkl")
            self.assertEqual(warning, "")
            self.assertTrue(path.exists())

    def test_lifecycle_and_material_views_filter_to_age_1_through_45(self):
        first_seen = pd.DataFrame([
            {STYLE_KEY: "SKU-45", FIRST_SEEN_DATE: "2026-07-12", IS_HISTORY_TRUNCATED: False},
            {STYLE_KEY: "SKU-46", FIRST_SEEN_DATE: "2026-07-11", IS_HISTORY_TRUNCATED: False},
            {STYLE_KEY: "SKU-0", FIRST_SEEN_DATE: "2026-08-26", IS_HISTORY_TRUNCATED: False},
        ])
        lifecycle = pd.DataFrame([
            {STYLE_KEY: "SKU-45", "货号": "sku-45", "新品年龄": 45},
            {STYLE_KEY: "SKU-46", "货号": "sku-46", "新品年龄": 46},
            {STYLE_KEY: "SKU-0", "货号": "sku-0", "新品年龄": 0},
        ])
        product_daily = {
            "2026-08-25": pd.DataFrame([
                _product_overall("10045", "sku-45"),
                _product_overall("10046", "sku-46"),
                _product_overall("10000", "sku-0"),
            ])
        }

        lifecycle_view = APP._format_new_product_lifecycle_view(
            lifecycle, first_seen, pd.DataFrame(), pd.DataFrame(), "2026-08-25"
        )
        material_view = APP._build_material_view({}, product_daily, pd.DataFrame(), first_seen, "2026-08-25")

        self.assertEqual(lifecycle_view["货号"].tolist(), ["sku-45"])
        self.assertEqual(material_view["宝贝ID"].tolist(), ["10045"])

    def test_lifecycle_view_hides_name_and_uses_d30_for_current_result(self):
        first_seen = pd.DataFrame([
            {STYLE_KEY: "SKU-20", FIRST_SEEN_DATE: "2026-08-06", IS_HISTORY_TRUNCATED: False},
            {STYLE_KEY: "SKU-30", FIRST_SEEN_DATE: "2026-07-27", IS_HISTORY_TRUNCATED: False},
            {STYLE_KEY: "SKU-31", FIRST_SEEN_DATE: "2026-07-26", IS_HISTORY_TRUNCATED: False},
        ])
        lifecycle = pd.DataFrame([
            {STYLE_KEY: "SKU-20", "货号": "sku-20", "商品名称": "商品20", "新品年龄": 20, "淘宝类目": "半身裙"},
            {STYLE_KEY: "SKU-30", "货号": "sku-30", "商品名称": "商品30", "新品年龄": 30, "淘宝类目": "半身裙"},
            {STYLE_KEY: "SKU-31", "货号": "sku-31", "商品名称": "商品31", "新品年龄": 31, "淘宝类目": "半身裙"},
        ])
        d15 = pd.DataFrame([
            {
                STYLE_KEY: "SKU-20", "判定结果": "下架", "判定原因": "D15加购弱",
                "动作可执行": "当前有在线链接", "库存提示": "库存正常",
                "样本充分性": "充分", "保护状态": "无保护", "流量验证": "流量达标",
                "本轮成交": "0单0元", "保护证据": "", "下架阻断原因": "",
            },
            {
                STYLE_KEY: "SKU-30", "判定结果": "下架", "判定原因": "D15弱但冻结",
                "样本充分性": "充分", "保护状态": "无保护", "流量验证": "流量达标",
                "本轮成交": "0单0元",
            },
            {
                STYLE_KEY: "SKU-31", "判定结果": "继续观察", "判定原因": "D15正常",
                "样本充分性": "充分", "保护状态": "无保护", "流量验证": "流量达标",
                "本轮成交": "1单100元",
            },
        ])
        d30 = pd.DataFrame([
            {
                STYLE_KEY: "SKU-20", "判定结果": "继续观察", "判定原因": "D30未到",
                "样本充分性": "充分", "保护状态": "动销保护", "流量验证": "流量达标",
                "本轮成交": "2单200元",
            },
            {
                STYLE_KEY: "SKU-30", "判定结果": "继续观察", "判定原因": "D30动销保护",
                "动作可执行": "当前有在线链接", "库存提示": "缺货",
                "样本充分性": "充分", "保护状态": "动销保护", "流量验证": "流量达标",
                "本轮成交": "2单200元", "保护证据": "D30支付买家2人",
                "下架阻断原因": "动销保护优先",
            },
            {
                STYLE_KEY: "SKU-31", "判定结果": "保护观察", "判定原因": "D30非官方结果",
                "样本充分性": "充分", "保护状态": "动销保护", "流量验证": "流量达标",
                "本轮成交": "3单300元",
            },
        ])
        obsolete = pd.DataFrame([
            {STYLE_KEY: "SKU-20", "D14结果": "下架", "D21结果": "下架", "D28结果": "下架"},
            {STYLE_KEY: "SKU-30", "D14结果": "下架", "D21结果": "下架", "D28结果": "下架"},
            {STYLE_KEY: "SKU-31", "D14结果": "下架", "D21结果": "下架", "D28结果": "下架"},
        ])
        lifecycle = lifecycle.merge(obsolete, on=STYLE_KEY, how="left")

        view = APP._format_new_product_lifecycle_view(
            lifecycle, first_seen, d15, d30, "2026-08-25"
        )
        keyed = view.set_index(STYLE_KEY, drop=False)
        csv_header = APP._frame_to_csv_bytes(view).decode("utf-8-sig").splitlines()[0]

        self.assertNotIn("商品名称", view.columns)
        self.assertNotIn("商品名称", csv_header)
        self.assertNotIn("D14结果", view.columns)
        self.assertNotIn("D21结果", view.columns)
        self.assertNotIn("D28结果", view.columns)
        self.assertIn("D15结果", view.columns)
        self.assertIn("D30结果", view.columns)
        self.assertIn("保护状态", view.columns)
        self.assertIn("动作可执行", view.columns)
        self.assertIn("库存提示", view.columns)
        self.assertIn("保护证据", view.columns)
        self.assertIn("下架阻断原因", view.columns)
        self.assertIn("流量验证", view.columns)
        self.assertIn("本轮成交", view.columns)
        self.assertEqual(keyed.loc["SKU-20", "判定轮次"], "D15")
        self.assertEqual(keyed.loc["SKU-20", "当前结果"], "下架")
        self.assertEqual(keyed.loc["SKU-20", "判定原因"], "D15加购弱")
        self.assertEqual(keyed.loc["SKU-20", "动作可执行"], "当前有在线链接")
        self.assertEqual(keyed.loc["SKU-20", "库存提示"], "库存正常")
        self.assertEqual(keyed.loc["SKU-20", "保护状态"], "无保护")
        self.assertEqual(keyed.loc["SKU-20", "本轮成交"], "0单0元")
        self.assertEqual(keyed.loc["SKU-30", "判定轮次"], "D30")
        self.assertEqual(keyed.loc["SKU-30", "当前结果"], "继续观察")
        self.assertEqual(keyed.loc["SKU-30", "判定原因"], "D30动销保护")
        self.assertEqual(keyed.loc["SKU-30", "动作可执行"], "当前有在线链接")
        self.assertEqual(keyed.loc["SKU-30", "库存提示"], "缺货")
        self.assertEqual(keyed.loc["SKU-30", "保护状态"], "动销保护")
        self.assertEqual(keyed.loc["SKU-30", "保护证据"], "D30支付买家2人")
        self.assertEqual(keyed.loc["SKU-30", "下架阻断原因"], "动销保护优先")
        self.assertEqual(keyed.loc["SKU-30", "流量验证"], "流量达标")
        self.assertEqual(keyed.loc["SKU-30", "本轮成交"], "2单200元")
        self.assertEqual(keyed.loc["SKU-31", "D30结果"], "继续观察")
        self.assertEqual(set(view["当前结果"].unique()), {"下架", "继续观察"})

    def test_material_view_uses_clicks_over_impressions_and_keeps_plan_subject_fields(self):
        first_seen = pd.DataFrame([
            {STYLE_KEY: "SKU-1", FIRST_SEEN_DATE: "2026-08-01", IS_HISTORY_TRUNCATED: False},
            {STYLE_KEY: "SKU-2", FIRST_SEEN_DATE: "2026-08-01", IS_HISTORY_TRUNCATED: False},
        ])
        product_daily = {
            "2026-08-25": pd.DataFrame([
                _product_overall("1001", "sku-1", main_ctr="3.5%"),
                _product_overall("1002", "sku-2", main_ctr="4.1%"),
            ])
        }
        creative_daily = {
            "2026-08-18": pd.DataFrame([{
                "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                "主体ID": "1001", "主体名称": "主体A", "主体类型": "商品",
                "创意ID": "C1", "创意名字": "图A", "展现": 100, "点击": 5,
            }]),
            "2026-08-25": pd.DataFrame([
                {
                    "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                    "主体ID": "1001", "主体名称": "主体A", "主体类型": "商品",
                    "创意ID": "C1", "创意名字": "图A", "展现": 300, "点击": 15,
                },
                {
                    "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                    "主体ID": "1001", "主体名称": "主体A", "主体类型": "商品",
                    "创意ID": "C1", "创意名字": "图A", "展现": 100, "点击": 4,
                },
                {
                    "宝贝ID": "1001", "计划ID": "P2", "计划名字": "计划B",
                    "主体ID": "1001", "主体名称": "主体A", "主体类型": "商品",
                    "创意ID": "C1", "创意名字": "图A", "展现": 200, "点击": 20,
                },
                {
                    "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                    "主体ID": "1001", "主体名称": "主体A", "主体类型": "商品",
                    "创意ID": "C2", "创意名字": "图B", "展现": 100, "点击": 20,
                },
            ]),
        }

        material = APP._build_material_view(
            creative_daily, product_daily, pd.DataFrame(), first_seen, "2026-08-25"
        )
        keyed = material.set_index(["宝贝ID", "计划ID", "创意ID"], drop=False)
        material_csv_header = APP._frame_to_csv_bytes(material).decode("utf-8-sig").splitlines()[0]

        self.assertNotIn("商品名称", material.columns)
        self.assertNotIn("商品名称", material_csv_header)
        self.assertIn(("1001", "P1", "C1"), keyed.index)
        self.assertIn(("1001", "P2", "C1"), keyed.index)
        self.assertAlmostEqual(keyed.loc[("1001", "P1", "C1"), "无界创意CTR"], 19 / 400)
        self.assertAlmostEqual(keyed.loc[("1001", "P1", "C1"), "上一7天CTR"], 5 / 100)
        self.assertAlmostEqual(keyed.loc[("1001", "P1", "C1"), "创意展现占比"], 400 / 700)
        self.assertTrue(bool(keyed.loc[("1001", "P1", "C1"), "是否主投创意"]))
        self.assertFalse(bool(keyed.loc[("1001", "P2", "C1"), "是否主投创意"]))
        self.assertEqual(keyed.loc[("1001", "P1", "C1"), "计划名称"], "计划A")
        self.assertEqual(keyed.loc[("1001", "P1", "C1"), "主体ID"], "1001")
        self.assertEqual(keyed.loc[("1001", "P1", "C1"), "主体名称"], "主体A")
        self.assertEqual(keyed.loc[("1001", "P1", "C1"), "主体类型"], "商品")
        main_only = material[material["宝贝ID"].eq("1002")].iloc[0]
        self.assertEqual(main_only["创意ID"], "")
        self.assertTrue(bool(main_only["主图CTR是否有效"]))

    def test_material_view_parses_thousands_separators_before_ctr_aggregation(self):
        first_seen = pd.DataFrame([{
            STYLE_KEY: "SKU-1",
            FIRST_SEEN_DATE: "2026-08-01",
            IS_HISTORY_TRUNCATED: False,
        }])
        product_daily = {
            "2026-08-30": pd.DataFrame([_product_overall("1001", "sku-1")])
        }
        creative_daily = {
            "2026-08-23": pd.DataFrame([{
                "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                "主体ID": "1001", "创意ID": "C1", "创意名字": "图A",
                "展现": "1,000", "点击": "50",
            }]),
            "2026-08-29": pd.DataFrame([
                {
                    "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                    "主体ID": "1001", "创意ID": "C1", "创意名字": "图A",
                    "展现": "3,628", "点击": "163",
                },
                {
                    "宝贝ID": "1001", "计划ID": "P1", "计划名字": "计划A",
                    "主体ID": "1001", "创意ID": "C1", "创意名字": "图A",
                    "展现": "120", "点击": "13",
                },
            ])
        }

        material = APP._build_material_view(
            creative_daily, product_daily, pd.DataFrame(), first_seen, "2026-08-30"
        )
        row = material[
            material["宝贝ID"].eq("1001")
            & material["计划ID"].eq("P1")
            & material["创意ID"].eq("C1")
        ].iloc[0]

        self.assertEqual(row["创意展现量"], 3748)
        self.assertEqual(row["创意点击量"], 176)
        self.assertAlmostEqual(row["无界创意CTR"], 176 / 3748)
        self.assertAlmostEqual(row["上一7天CTR"], 50 / 1000)

    def test_backtest_creative_metrics_parse_thousands_separators(self):
        first_seen = pd.DataFrame([{
            STYLE_KEY: "SKU-1",
            FIRST_SEEN_DATE: "2026-08-01",
            FIRST_SEEN_PRODUCT_ID: "1001",
            IS_HISTORY_TRUNCATED: False,
        }])
        creative_daily = {
            "2026-08-08": pd.DataFrame([{
                "宝贝ID": "1001",
                "创意ID": "C1",
                "展现": "3,628",
                "点击": "1,004",
            }])
        }

        metrics = APP._creative_metrics_by_style_age(creative_daily, first_seen)
        row = metrics.iloc[0]

        self.assertEqual(row[C.style_key], "SKU-1")
        self.assertEqual(row[C.creative_impressions], 3628)
        self.assertEqual(row[C.creative_clicks], 1004)

    def test_backtest_baselines_are_frozen_to_checkpoint_windows_without_future_leak(self):
        start = date(2026, 8, 1)
        facts = []
        first_seen = []
        for idx in range(20):
            sku = f"SKU-{idx:02d}"
            first_seen.append({STYLE_KEY: sku, FIRST_SEEN_DATE: "2026-08-01", IS_HISTORY_TRUNCATED: False})
            for age in range(1, 46):
                if 1 <= age <= 15:
                    carts, buyers, gmv = 1, 1, 100
                elif 16 <= age <= 30:
                    carts, buyers, gmv = 5, 5, 500
                else:
                    carts, buyers, gmv = 9, 9, 900
                facts.append({
                    "货号": sku,
                    "_数据日期": (start + timedelta(days=age - 1)).isoformat(),
                    "店铺": "女装",
                    "淘宝类目": "半身裙",
                    "价格段": "100-200",
                    "商品访客数": 10,
                    "商品加购件数": carts,
                    "支付买家数": buyers,
                    "支付件数": buyers,
                    "GMV": gmv,
                    "商品状态": "当前在线",
                    "在仓库存": 1,
                })

        rows = APP._build_backtest_rows_from_facts(pd.DataFrame(facts), pd.DataFrame(first_seen))
        scored = run_backtest(rows).checkpoint_rows

        d15_row = scored[
            scored[C.style_key].eq("SKU-00") & scored[C.checkpoint].eq(15)
        ].iloc[0]
        d30_row = scored[
            scored[C.style_key].eq("SKU-00") & scored[C.checkpoint].eq(30)
        ].iloc[0]
        self.assertAlmostEqual(d15_row[C.baseline_cart_rate], 0.1)
        self.assertAlmostEqual(d15_row[C.baseline_pay_cvr], 0.1)
        self.assertAlmostEqual(d15_row[C.baseline_uv_value], 10.0)
        self.assertAlmostEqual(d15_row["validation_cart_rate"], 0.5)
        self.assertAlmostEqual(d30_row[C.baseline_cart_rate], 0.5)
        self.assertAlmostEqual(d30_row[C.baseline_pay_cvr], 0.5)
        self.assertAlmostEqual(d30_row[C.baseline_uv_value], 50.0)
        self.assertAlmostEqual(d30_row["validation_cart_rate"], 0.9)
        self.assertAlmostEqual(d30_row["cumulative_cart_rate"], 0.3)

    def test_backtest_baselines_require_twenty_peer_styles(self):
        start = date(2026, 8, 1)
        facts = []
        first_seen = []
        for idx in range(19):
            sku = f"SKU-{idx:02d}"
            first_seen.append({STYLE_KEY: sku, FIRST_SEEN_DATE: "2026-08-01", IS_HISTORY_TRUNCATED: False})
            for age in range(8, 15):
                facts.append({
                    "货号": sku,
                    "_数据日期": (start + timedelta(days=age - 1)).isoformat(),
                    "店铺": "女装",
                    "淘宝类目": "半身裙",
                    "价格段": "100-200",
                    "商品访客数": 10,
                    "商品加购件数": 1,
                    "支付买家数": 1,
                    "GMV": 100,
                    "商品状态": "当前在线",
                    "在仓库存": 1,
                })

        rows = APP._build_backtest_rows_from_facts(pd.DataFrame(facts), pd.DataFrame(first_seen))

        self.assertTrue(rows[C.baseline_cart_rate].isna().all())
        self.assertTrue(rows[C.baseline_pay_cvr].isna().all())
        self.assertTrue(rows[C.baseline_uv_value].isna().all())

    def test_lifecycle_uses_independent_business_history_without_mutating_main_day_map(self):
        first_day = date(2026, 7, 12)
        main_day_map = {
            "2026-08-25": (
                pd.DataFrame([_business_fact("sku-45", "10045", 45, first_day, carts=1, buyers=0, gmv=0)]),
                [], 0, 0, set(), {},
            )
        }
        business_day_map = {}
        for age in range(16, 31):
            day = (first_day + timedelta(days=age - 1)).isoformat()
            business_day_map[day] = (
                pd.DataFrame([_business_fact("sku-45", "10045", age, first_day, carts=2, buyers=1, gmv=200)]),
                [], 0, 0, set(), {},
            )

        with tempfile.TemporaryDirectory() as tmp:
            ctx = APP._build_new_product_context_from_sources(
                pd.DataFrame([_product_overall("10045", "sku-45", first_date="2026-08-25")]),
                main_day_map,
                "2026-08-25",
                {
                    "product_overall_daily": {
                        "2026-07-12": pd.DataFrame([
                            _product_overall("10045", "sku-45", first_date="2026-07-12")
                        ])
                    },
                    "creative_daily": {},
                    "business_day_map": business_day_map,
                    "history_start_date": "2026-07-01",
                    "first_seen_index_path": str(Path(tmp) / "first_seen.pkl"),
                },
            )

        lifecycle = ctx["lifecycle"]
        self.assertEqual(len(main_day_map), 1)
        self.assertEqual(lifecycle.loc[0, "货号"], "sku-45")
        self.assertEqual(int(lifecycle.loc[0, "新品年龄"]), 45)
        self.assertEqual(lifecycle.loc[0, "判定轮次"], "D30")
        self.assertNotIn("D15_D28_有效数据天数", lifecycle.columns)

    def test_backtest_creative_ctr_protects_b_and_d_without_changing_rule_a(self):
        first_day = date(2026, 8, 1)
        facts = []
        creative_daily = {}
        for idx in range(20):
            sku = f"SKU-{idx:02d}"
            pid = f"10{idx:02d}"
            weak = idx == 0
            for age in range(1, 16):
                facts.append(_business_fact(
                    sku, pid, age, first_day,
                    carts=1 if weak else 10,
                    buyers=0 if weak else 5,
                    gmv=0 if weak else 1000,
                ))
                if age >= 8:
                    day = (first_day + timedelta(days=age - 1)).isoformat()
                    creative_daily.setdefault(day, []).append({
                        "宝贝ID": pid,
                        "创意ID": f"C{idx:02d}",
                        "展现": 100,
                        "点击": 50 if weak else 1,
                    })
        creative_daily = {day: pd.DataFrame(rows) for day, rows in creative_daily.items()}

        rows = APP._build_backtest_rows_from_facts(
            pd.DataFrame(facts),
            _first_seen_rows(),
            creative_daily=creative_daily,
        )
        scored = run_backtest(rows).checkpoint_rows
        d15 = scored[
            scored[C.style_key].eq("SKU-00") & scored[C.checkpoint].eq(15)
        ].iloc[0]

        self.assertEqual(d15["rule_A_decision"], DECISION_DOWN)
        self.assertEqual(d15["rule_B_decision"], DECISION_OBSERVE)
        self.assertEqual(d15["rule_D_decision"], DECISION_OBSERVE)
        self.assertGreater(d15["current_creative_ctr"], d15[C.baseline_creative_ctr] * 1.2)

    def test_checkpoint_visible_metox_labels_ignore_future_days(self):
        original = APP.apply_metox_algorithm
        calls = []

        def fake_apply(frame, day_map=None, date_str=None):
            calls.append((date_str, sorted(day_map)))
            out = frame.copy()
            out["MetoX算法命中"] = "强主推" if "2026-08-15" in day_map else ""
            return out

        try:
            APP.apply_metox_algorithm = fake_apply
            day_map = {
                "2026-08-14": (pd.DataFrame([{"宝贝ID": "1001"}]), [], 0, 0, set(), {}),
                "2026-08-15": (pd.DataFrame([{"宝贝ID": "1001"}]), [], 0, 0, set(), {}),
            }
            labeled = APP._with_checkpoint_visible_metox_labels(day_map)
        finally:
            APP.apply_metox_algorithm = original

        self.assertEqual(calls[0], ("2026-08-14", ["2026-08-14"]))
        self.assertEqual(calls[1], ("2026-08-15", ["2026-08-14", "2026-08-15"]))
        self.assertEqual(labeled["2026-08-14"][0].loc[0, "MetoX算法命中"], "")
        self.assertEqual(labeled["2026-08-15"][0].loc[0, "MetoX算法命中"], "强主推")

    def test_metox_positive_at_checkpoint_protects_c_d_and_negative_does_not_down(self):
        first_day = date(2026, 8, 1)
        facts = []
        for idx in range(20):
            sku = f"SKU-{idx:02d}"
            pid = f"10{idx:02d}"
            for age in range(1, 16):
                if idx == 0:
                    facts.append(_business_fact(
                        sku, pid, age, first_day, carts=1, buyers=0, gmv=0,
                        label="强主推" if age == 15 else "",
                    ))
                elif idx == 1:
                    facts.append(_business_fact(
                        sku, pid, age, first_day, carts=10, buyers=0, gmv=0,
                        label="亏损风险" if age == 15 else "",
                    ))
                else:
                    facts.append(_business_fact(sku, pid, age, first_day, carts=10, buyers=5, gmv=1000))

        rows = APP._build_backtest_rows_from_facts(pd.DataFrame(facts), _first_seen_rows())
        scored = run_backtest(rows).checkpoint_rows
        d15 = scored[scored[C.checkpoint].eq(15)].set_index(C.style_key)

        self.assertEqual(d15.loc["SKU-00", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(d15.loc["SKU-00", "rule_C_decision"], DECISION_OBSERVE)
        self.assertEqual(d15.loc["SKU-00", "rule_D_decision"], DECISION_OBSERVE)
        self.assertEqual(d15.loc["SKU-01", "rule_A_decision"], DECISION_OBSERVE)
        self.assertEqual(d15.loc["SKU-01", "rule_C_decision"], DECISION_OBSERVE)
        self.assertEqual(d15.loc["SKU-01", "metox_positive"], False)

    def test_backtest_multilink_creative_is_mapped_to_style_once(self):
        first_day = date(2026, 8, 1)
        facts = []
        creative_daily = {}
        for age in range(1, 15):
            for pid in ("1000", "2000"):
                facts.append(_business_fact(
                    "SKU-00", pid, age, first_day, carts=1, buyers=1, gmv=100,
                ))
            if age >= 8:
                day = (first_day + timedelta(days=age - 1)).isoformat()
                creative_daily[day] = pd.DataFrame([
                    {"宝贝ID": "1000", "创意ID": "C1", "展现": 100, "点击": 10},
                    {"宝贝ID": "2000", "创意ID": "C2", "展现": 100, "点击": 20},
                ])

        rows = APP._build_backtest_rows_from_facts(
            pd.DataFrame(facts),
            _first_seen_rows(count=1),
            creative_daily=creative_daily,
        )

        self.assertEqual(len(rows), 14)
        d14 = rows[rows[C.age_day].eq(14)].iloc[0]
        self.assertEqual(d14[C.creative_impressions], 200)
        self.assertEqual(d14[C.creative_clicks], 30)

    def test_backtest_multilink_rows_do_not_fake_six_valid_peer_days(self):
        first_day = date(2026, 8, 1)
        facts = []
        for idx in range(19):
            for age in range(8, 15):
                facts.append(_business_fact(
                    f"SKU-{idx:02d}", f"10{idx:02d}", age, first_day,
                    carts=10, buyers=5, gmv=1000,
                ))
        for age in range(12, 15):
            for pid in ("1019", "2019"):
                facts.append(_business_fact(
                    "SKU-19", pid, age, first_day, carts=10, buyers=5, gmv=1000,
                ))

        rows = APP._build_backtest_rows_from_facts(
            pd.DataFrame(facts),
            _first_seen_rows(),
        )

        self.assertTrue(rows[C.baseline_cart_rate].isna().all())
        self.assertTrue(rows[C.baseline_pay_cvr].isna().all())
        self.assertTrue(rows[C.baseline_uv_value].isna().all())

    def test_main_image_ctr_is_observational_and_does_not_protect_shadow_rule(self):
        first_day = date(2026, 8, 1)
        facts = []
        for idx in range(20):
            weak = idx == 0
            for age in range(1, 16):
                row = _business_fact(
                    f"SKU-{idx:02d}", f"10{idx:02d}", age, first_day,
                    carts=1 if weak else 10,
                    buyers=0 if weak else 5,
                    gmv=0 if weak else 1000,
                )
                row["主图整体点击率"] = "80%" if weak else "1%"
                facts.append(row)

        rows = APP._build_backtest_rows_from_facts(
            pd.DataFrame(facts),
            _first_seen_rows(),
        )
        scored = run_backtest(rows).checkpoint_rows
        d15 = scored[
            scored[C.style_key].eq("SKU-00") & scored[C.checkpoint].eq(15)
        ].iloc[0]

        self.assertTrue(pd.isna(d15[C.baseline_main_ctr]))
        self.assertEqual(d15["rule_A_decision"], DECISION_DOWN)
        self.assertEqual(d15["rule_B_decision"], DECISION_DOWN)

    def test_backtest_unit_only_sale_is_protected_at_d15(self):
        first_day = date(2026, 8, 1)
        facts = []
        for idx in range(20):
            weak = idx == 0
            for age in range(1, 16):
                facts.append(_business_fact(
                    f"SKU-{idx:02d}",
                    f"10{idx:02d}",
                    age,
                    first_day,
                    carts=1 if weak else 10,
                    buyers=0,
                    units=1 if weak and age == 15 else 0,
                    gmv=0,
                ))

        rows = APP._build_backtest_rows_from_facts(
            pd.DataFrame(facts),
            _first_seen_rows(),
        )
        scored = run_backtest(rows).checkpoint_rows
        d15 = scored[
            scored[C.style_key].eq("SKU-00") & scored[C.checkpoint].eq(15)
        ].iloc[0]

        self.assertEqual(d15["current_units"], 1)
        self.assertEqual(d15["rule_A_decision"], DECISION_OBSERVE)
        self.assertEqual(d15["rule_A_status"], "paid_sale_protected")

    def test_new_product_context_filter_matches_related_product_ids_exactly(self):
        context = {
            "lifecycle": pd.DataFrame([
                {"货号": "SKU-A", "商品名称": "商品A", "关联宝贝ID": "9123,888", "当前结果": "继续观察"},
                {"货号": "SKU-B", "商品名称": "商品B", "关联宝贝ID": "123.0，777", "当前结果": "下架"},
            ]),
            "materials": pd.DataFrame([
                {"宝贝ID": "9123", "商品名称": "商品A", "创意ID": "C1"},
                {"宝贝ID": "123.0", "商品名称": "商品B", "创意ID": "C2"},
            ]),
        }
        execution = pd.DataFrame([{"宝贝ID": "123", "货号": "NO-MATCH-SKU"}])

        filtered = APP._filter_new_product_context(context, execution)

        self.assertEqual(filtered["lifecycle"]["货号"].tolist(), ["SKU-B"])
        self.assertNotIn("商品名称", filtered["lifecycle"].columns)
        self.assertNotIn("商品名称", filtered["materials"].columns)
        self.assertEqual(filtered["materials"]["创意ID"].tolist(), ["C2"])

    def test_primary_folder_load_defers_new_product_history_io(self):
        app_path = Path(__file__).resolve().with_name("app.py")
        tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
        folder_loader = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_load_folder_cached"
        )
        call_names = []
        for node in ast.walk(folder_loader):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    call_names.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    call_names.append(node.func.attr)

        self.assertNotIn("_load_new_product_history", call_names)
        self.assertNotIn("_build_new_product_context", call_names)
        self.assertIn("_build_new_product_load_state", call_names)
        self.assertIn("_new_product_unloaded_context", call_names)

    def test_lazy_new_product_context_reuses_loaded_day_map(self):
        existing_day_map = {
            "2026-08-26": (
                pd.DataFrame([{"_数据日期": "2026-08-26", "宝贝ID": "1000"}]),
                [{"name": "current"}],
                0,
                1,
                set(),
                {},
            )
        }
        shared_dimensions = {"product": pd.DataFrame([{"宝贝ID": "1000"}])}
        load_state = APP._build_new_product_load_state(
            base_root=Path("root"),
            year="2026",
            shop="女装",
            date_str="2026-08-26",
            cache_root=Path("cache"),
            product_folder=Path("product"),
            files_info=[{"name": "current"}],
            cat_ctx={"h7_cur_gmv": 1},
            sources={"商品经营投产比": []},
            day_map=existing_day_map,
            shared_dimensions=shared_dimensions,
        )
        calls = {}
        progress = []
        original_history = APP._load_new_product_history
        original_builder = APP._build_new_product_context

        def fake_history(_base_root, _year, _shop, _date_str, _cache_root, **kwargs):
            calls["existing_day_map"] = kwargs.get("existing_day_map")
            calls["shared_dimensions"] = kwargs.get("shared_dimensions")
            calls["product_folder"] = kwargs.get("product_folder")
            kwargs["progress_callback"](0.5, "正在读取首次发现历史 1/90 天：2026-08-26")
            return {
                "status": "complete",
                "message": "",
                "product_overall_daily": {},
                "creative_daily": {},
                "business_day_map": {},
            }

        def fake_builder(df, day_map, date_str, **kwargs):
            calls["df"] = df
            calls["day_map"] = day_map
            calls["date_str"] = date_str
            calls["new_product_history"] = kwargs.get("new_product_history")
            return APP._empty_new_product_context("ok", status="complete")

        try:
            APP._load_new_product_history = fake_history
            APP._build_new_product_context = fake_builder
            history, context = APP._load_new_product_context_from_state(
                pd.DataFrame([{"宝贝ID": "1000"}]),
                load_state,
                progress_callback=lambda value, text: progress.append((value, text)),
            )
        finally:
            APP._load_new_product_history = original_history
            APP._build_new_product_context = original_builder

        self.assertIs(calls["existing_day_map"], load_state["day_map"])
        self.assertIs(calls["shared_dimensions"], shared_dimensions)
        self.assertEqual(calls["product_folder"], str(Path("product")))
        self.assertIs(calls["day_map"], load_state["day_map"])
        self.assertIs(calls["new_product_history"], history)
        self.assertEqual(context["status"], "complete")
        self.assertTrue(any("正在读取首次发现历史 1/90 天" in text for _value, text in progress))
        self.assertEqual(progress[-1][1], "新品历史加载完成")

    def test_tab10_and_all_sheets_export_are_wired_to_new_product_context(self):
        app_source = Path(__file__).resolve().with_name("app.py").read_text(encoding="utf-8")

        self.assertIn("tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs", app_source)
        self.assertIn('"🆕 新品视图"', app_source)
        self.assertIn("with tab10:", app_source)
        self.assertIn("_render_new_product_view(st.session_state.get('new_product_ctx'))", app_source)
        self.assertIn("加载新品历史", app_source)
        self.assertIn("刷新新品历史", app_source)
        self.assertIn("new_product_load_state", app_source)
        self.assertIn("_load_new_product_context_from_state(", app_source)
        self.assertIn("new_product_ctx=_filter_new_product_context", app_source)


if __name__ == "__main__":
    unittest.main()
