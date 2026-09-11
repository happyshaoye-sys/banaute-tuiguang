"""库存只能提醒，不能改变推广与预算判断。"""
from __future__ import annotations

import unittest

import pandas as pd

from core.analysis import channel_top
from core.segmentation import segment_products


class InventoryDecisionBoundaryTests(unittest.TestCase):
    def test_inventory_does_not_change_segmentation_budget_priority(self):
        source = pd.DataFrame([
            {
                '宝贝ID': 'low-stock', 'GMV': 1000, '商品访客数': 100,
                '付费': 200, '推广投产': 5, '退款率': 0.1,
                '在仓库存': 0, '在途': 0,
            },
            {
                '宝贝ID': 'normal-stock', 'GMV': 1000, '商品访客数': 100,
                '付费': 200, '推广投产': 5, '退款率': 0.1,
                '在仓库存': 100, '在途': 0,
            },
            {
                '宝贝ID': 'peer', 'GMV': 1, '商品访客数': 1,
                '付费': 0, '推广投产': 0, '退款率': 0.1,
                '在仓库存': 10, '在途': 0,
            },
        ])

        result = segment_products(
            source,
            traffic_bounds=[1, 2, 3, 4, 5],
            value_bounds=[1, 2, 3, 4, 5],
        ).set_index('宝贝ID')

        low = result.loc['low-stock']
        normal = result.loc['normal-stock']
        self.assertEqual(low['商品定位'], normal['商品定位'])
        self.assertEqual(low['商品周期'], normal['商品周期'])
        self.assertEqual(low['推广动作'], normal['推广动作'])
        self.assertEqual(low['预算优先级'], normal['预算优先级'])
        self.assertIn('缺货', low['风险标签'])
        self.assertNotIn('缺货', normal['风险标签'])

    def test_inventory_risk_label_does_not_change_channel_budget_action(self):
        base = {
            '宝贝ID': '1001', '商品名称': '测试商品', '关键词': 100,
            '关键词归因成交': 200, 'GMV': 500, '综合等级': 'S',
            '对应操作': '放量', 'MetoX算法命中': '强主推',
        }
        normal = channel_top(pd.DataFrame([{**base, '风险标签': ''}]), '关键词')[0]
        no_stock = channel_top(pd.DataFrame([{**base, '风险标签': '🆘缺货 📦库存不足'}]), '关键词')[0]

        self.assertEqual(normal['渠道判断'], no_stock['渠道判断'])
        self.assertEqual(normal['渠道动作'], no_stock['渠道动作'])
        self.assertEqual(normal['渠道预算建议'], no_stock['渠道预算建议'])


if __name__ == '__main__':
    unittest.main()
