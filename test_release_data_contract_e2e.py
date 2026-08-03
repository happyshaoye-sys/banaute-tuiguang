"""发布门禁：数据契约与 Excel 原始文件到全部 Sheet 导出的完整链路。"""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from core.analysis import build_ai_task_view
from core.data_validation import (
    DataValidationError,
    validate_paid_plan_consistency,
)
from core.exporter import export_all_sheets
from core.loader import read_prepared_source, scan_folder
from core.jst_refund import load_refund_rates
from core.jst_stock import load_snapshot_as_stock
from core.merge import merge_sources
from core.metox_algorithm import apply_metox_algorithm
from core.periods import aggregate_period_frame
from core.schema import SOURCE_WUJIE_SUBJECT
from core.segmentation import segment_products


def _product_roi_row() -> dict:
    return {
        '统计日期': '2026-08-01', '宝贝ID': '1001', '商品名称': '测试商品',
        '淘宝类目': '连衣裙', 'GMV': 1000, '支付件数': 12, '支付买家数': 10,
        '成功退款金额': 0, '商品加购件数': 20, '商品访客数': 100,
        '商品支付转化率': 0.1, '访客平均价值': 10, '搜索引导访客数': 50,
        '关键词推广访客数': 40, '关键词推广加购人数': 8,
        '人群推广访客数': 20, '人群推广加购人数': 2,
        '场景推广访客数': 10, '场景推广加购人数': 1,
        '推广消耗金额': 300, '推广总加购数': 11, '推广引导总成交金额': 150,
        '关键词推广加购数': 8, '人群推广加购数': 2, '场景推广加购数': 1,
    }


def _wujie_row(plan_id: str = 'P1001') -> dict:
    return {
        '转化周期': '15天转化', '场景名字': '关键词推广', '计划ID': plan_id,
        '主体ID': '1001', '主体类型': '商品', '展现': 5000, '点击': 100,
        '付费': 300, '点击率': 0.02, '总成交金额': 150,
        '点击转化率': 0.1, '投入产出比': 0.5,
        '总购物车数': 10, '直接购物车数': 5,
    }


def _write_sources(root: Path, wujie_row: dict | None = None) -> None:
    pd.DataFrame([_product_roi_row()]).to_excel(
        root / '商品-经营投产比-2026-08-01.xlsx', index=False
    )
    pd.DataFrame([wujie_row or _wujie_row()]).to_excel(
        root / '无界-商品主体-2026-08-01.xlsx', index=False
    )
    pd.DataFrame([{
        '宝贝ID': '1001', '货号': 'SKU-1001', '在仓库存': 100,
        '在途': 0, '成本价（含税）': 50, '日销7': 2,
    }]).to_excel(root / '库存明细-2026-08-01.xlsx', index=False)
    pd.DataFrame([{
        '宝贝ID': '1001', '款式主图': 'https://img.example/1001.jpg',
        '货号': 'SKU-1001', '创建时间': '2026-01-01 10:00:00',
        '成本价（含税）': 50,
    }]).to_excel(root / '店铺商品资料-2026-08-01.xlsx', index=False)


class ReleaseDataContractE2ETests(unittest.TestCase):
    def test_missing_required_source_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '无界-商品主体-缺字段.xlsx'
            row = _wujie_row()
            row.pop('计划ID')
            pd.DataFrame([row]).to_excel(path, index=False)

            with self.assertRaisesRegex(DataValidationError, '缺少必需原始字段：计划ID'):
                read_prepared_source(
                    path, SOURCE_WUJIE_SUBJECT, parsed_cache_dir=Path(tmp) / 'parsed'
                )

    def test_paid_source_row_without_plan_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '无界-商品主体-空计划.xlsx'
            pd.DataFrame([_wujie_row(plan_id='')]).to_excel(path, index=False)

            with self.assertRaisesRegex(DataValidationError, '有付费但没有PlanID'):
                read_prepared_source(
                    path, SOURCE_WUJIE_SUBJECT, parsed_cache_dir=Path(tmp) / 'parsed'
                )

    def test_paid_execution_row_without_plan_metadata_fails_closed(self):
        broken = pd.DataFrame([{'宝贝ID': '1001', '付费': 100}])
        with self.assertRaisesRegex(DataValidationError, '计划链路字段缺失'):
            validate_paid_plan_consistency(broken, '发布测试')

    def test_jst_stock_missing_required_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '库存快照_2026-08-01_款式级.csv'
            pd.DataFrame([{
                '宝贝ID': '1001', '货号': 'SKU-1001', '在仓库存': 100,
            }]).to_csv(path, index=False, encoding='utf-8-sig')
            with self.assertRaisesRegex(DataValidationError, '缺少必需原始字段：在途'):
                load_snapshot_as_stock(path)

    def test_jst_refund_missing_required_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '退款率_2026-08-01.csv'
            pd.DataFrame([{'宝贝ID': '1001', '其他比率': 0.2}]).to_csv(
                path, index=False, encoding='utf-8-sig'
            )
            with self.assertRaisesRegex(DataValidationError, '缺少必需原始字段：退款率'):
                load_refund_rates(path)

    def test_excel_files_to_ai_task_sheet_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sources(root)
            sources = {}
            scanned = scan_folder(root)
            self.assertEqual(len(scanned), 4)
            for entry in scanned:
                frame = read_prepared_source(
                    entry['path'], entry['source'], parsed_cache_dir=root / 'parsed'
                )
                sources.setdefault(entry['source'], []).append((entry['path'], frame))

            daily, plan_ids = merge_sources(sources)
            self.assertEqual(plan_ids, {'P1001'})
            daily['_数据日期'] = '2026-08-01'
            period = aggregate_period_frame(
                daily, 1, actual_days=1,
                period_start='2026-08-01', period_end='2026-08-01',
            )
            final = segment_products(period)
            final = apply_metox_algorithm(final)
            tasks = build_ai_task_view(final)
            self.assertEqual(len(tasks), 1)
            self.assertIn('P1001', tasks.iloc[0]['计划执行明细'])

            workbook = export_all_sheets(final)
            excel = pd.ExcelFile(io.BytesIO(workbook))
            self.assertIn('商品推广执行表', excel.sheet_names)
            self.assertIn('AI任务视图', excel.sheet_names)
            exported_tasks = pd.read_excel(
                io.BytesIO(workbook), sheet_name='AI任务视图'
            )
            self.assertEqual(exported_tasks['宝贝ID'].astype(str).tolist(), ['1001'])
            self.assertIn('P1001', exported_tasks.loc[0, '计划执行明细'])


if __name__ == '__main__':
    unittest.main()
