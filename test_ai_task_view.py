"""AI task view decision-boundary and UI-contract tests."""
from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from core.analysis import AI_TASK_VIEW_COLUMNS, build_ai_task_view


ROOT = Path(__file__).resolve().parent


def _row(**overrides):
    row = {
        '宝贝ID': '1001', '商品名称': '测试商品', '淘宝类目': '连衣裙',
        '货号': 'SKU-001', '款式主图': 'https://img.example/1.jpg', '链接数': 2,
        '综合等级': 'A', '商品定位': '冲刺款', '商品周期': '成长期',
        '流量定位': '冲刺款', '价值定位': '潜力款', '组合': '冲刺款潜力款',
        'MetoX算法命中': '', 'MetoX算法分': 0, 'MetoX算法原因': '', 'v9.3信号': '',
        'GMV': 3000, '付费': 300, '商品访客数': 300, '商品加购件数': 30,
        '访客平均价值': 10, '推广成交金额': 2400, '推广加购数': 24,
        '推广访客数': 150, '推广投产': 8, '链接投产': 10,
        '关键词': 120, '人群': 100, '全站': 80,
        '关键词归因成交': 1200, '人群归因成交': 800, '全站归因成交': 400,
        '上周期关键词': 100, '上周期人群': 100, '上周期全站': 100,
        '关键词环比': 0.2, '人群环比': 0, '全站环比': -0.2,
        '上周期金额': 2500, '上周期付费': 300,
        '日销3': 8, '日销7': 7, '日销15': 6,
        '花费3': 45, '花费7': 42, '花费15': 40,
        '退款率': 0.1, '在仓库存': 100, '在途': 0, '风险标签': '',
    }
    row.update(overrides)
    return row


def _one(**overrides):
    return build_ai_task_view(pd.DataFrame([_row(**overrides)])).iloc[0]


class AITaskViewScenarioTests(unittest.TestCase):
    def test_01_a_plus_growth_strong_and_burst_is_scale(self):
        task = _one(
            综合等级='A+', 商品定位='明星款', 商品周期='成长期',
            MetoX算法命中='强主推|爆发', MetoX算法分=40,
        )
        self.assertEqual(task['AI任务类型'], '放量任务')
        self.assertEqual(task['最终推广策略'], '重点放量')
        self.assertEqual(task['执行优先级'], 'P1')

    def test_02_b_test_period_recent_is_test(self):
        task = _one(
            综合等级='B', 商品定位='潜力款', 商品周期='测试期',
            MetoX算法命中='近3日潜力', MetoX算法分=20,
        )
        self.assertEqual(task['AI任务类型'], '测试任务')
        self.assertEqual(task['最终推广策略'], '扩量测试')

    def test_03_a_mature_loss_and_spillover_prioritizes_risk(self):
        task = _one(
            综合等级='A', 商品定位='金牛款', 商品周期='成熟期',
            MetoX算法命中='亏损风险|溢出', MetoX算法分=-10,
            推广成交金额=100, 付费=300,
        )
        self.assertEqual(task['AI任务类型'], '止损任务')
        self.assertNotIn(task['最终推广策略'], ('重点放量', '谨慎放量'))

    def test_04_add_budget_with_saturation_stops_expansion(self):
        task = _one(MetoX算法命中='加预算|饱和观察', MetoX算法分=45)
        self.assertEqual(task['AI任务类型'], '稳量任务')
        self.assertEqual(task['最终推广策略'], '继续观察')

    def test_05_protect_hold_is_stable(self):
        task = _one(MetoX算法命中='保护持有', MetoX算法分=15)
        self.assertEqual(task['AI任务类型'], '稳量任务')
        self.assertEqual(task['最终推广策略'], '保持投放')

    def test_06_reduce_is_loss_or_channel_action(self):
        task = _one(MetoX算法命中='削减', MetoX算法分=-30, 推广成交金额=100, 付费=300)
        self.assertEqual(task['AI任务类型'], '止损任务')
        self.assertEqual(task['最终推广策略'], '削减投放')

    def test_07_opportunity_and_risk_conflict_is_manual_p1(self):
        task = _one(MetoX算法命中='强主推|亏损风险', MetoX算法分=10)
        self.assertEqual(task['AI任务类型'], '人工复核任务')
        self.assertEqual(task['最终推广策略'], '人工复核')
        self.assertEqual(task['执行优先级'], 'P1')

    def test_08_d_weak_opportunity_never_scales(self):
        task = _one(
            综合等级='D', 商品定位='滞销款', 商品周期='观察期',
            MetoX算法命中='明日之星', MetoX算法分=5,
        )
        self.assertEqual(task['AI任务类型'], '人工复核任务')
        self.assertEqual(task['执行优先级'], 'P4')
        self.assertNotIn(task['最终推广策略'], ('重点放量', '谨慎放量'))

    def test_09_low_stock_does_not_cancel_scale_or_change_priority(self):
        normal = _one(
            综合等级='A+', 商品定位='明星款', MetoX算法命中='强主推',
            MetoX算法分=30, 在仓库存=100,
        )
        low = _one(
            综合等级='A+', 商品定位='明星款', MetoX算法命中='强主推',
            MetoX算法分=30, 在仓库存=2,
        )
        self.assertEqual(low['AI任务类型'], '放量任务')
        self.assertEqual(low['最终推广策略'], normal['最终推广策略'])
        self.assertEqual(low['任务优先分'], normal['任务优先分'])
        self.assertEqual(low['关键词动作'], normal['关键词动作'])
        self.assertEqual(low['库存提醒'], '库存偏低，请关注')

    def test_10_high_stock_does_not_override_reduce(self):
        normal = _one(
            MetoX算法命中='削减', MetoX算法分=-30, 推广成交金额=100,
            付费=300, 在仓库存=30, 商品访客数=100,
        )
        high = _one(
            MetoX算法命中='削减', MetoX算法分=-30, 推广成交金额=100,
            付费=300, 在仓库存=500, 商品访客数=5,
        )
        self.assertEqual(high['AI任务类型'], '止损任务')
        self.assertEqual(high['最终推广策略'], normal['最终推广策略'])
        self.assertEqual(high['任务优先分'], normal['任务优先分'])
        self.assertEqual(high['库存提醒'], '库存较高，请关注动销')

    def test_11_sku_image_and_link_count_are_passed_through(self):
        task = _one(
            MetoX算法命中='保护持有', 货号='RAW-SKU',
            款式主图='https://img.example/raw.png', 链接数=7,
        )
        self.assertEqual(task['货号'], 'RAW-SKU')
        self.assertEqual(task['款式主图'], 'https://img.example/raw.png')
        self.assertEqual(task['链接数'], 7)

    def test_12_empty_passthrough_fields_stay_empty(self):
        task = _one(MetoX算法命中='保护持有', 货号=None, 款式主图=float('nan'), 链接数=None)
        self.assertEqual(task['货号'], '')
        self.assertEqual(task['款式主图'], '')
        self.assertEqual(task['链接数'], '')

    def test_13_partial_channel_fields_do_not_raise(self):
        frame = pd.DataFrame([_row(MetoX算法命中='明日之星')]).drop(
            columns=['人群归因成交', '全站', '全站归因成交']
        )
        task = build_ai_task_view(frame).iloc[0]
        self.assertEqual(task['人群动作'], '渠道数据缺失，人工复核')
        self.assertEqual(task['全站动作'], '渠道数据缺失，人工复核')

    def test_14_data_quality_issue_enters_manual_review(self):
        task = _one(宝贝ID='', MetoX算法命中='强主推')
        self.assertEqual(task['AI任务类型'], '人工复核任务')
        self.assertIn('关键字段为空', task['任务触发原因'])

    def test_15_ordinary_product_without_signal_is_excluded(self):
        result = build_ai_task_view(pd.DataFrame([_row()]))
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), AI_TASK_VIEW_COLUMNS)

    def test_16_input_dataframe_is_not_modified(self):
        source = pd.DataFrame([_row(MetoX算法命中='保护持有')])
        before = source.copy(deep=True)
        build_ai_task_view(source)
        assert_frame_equal(source, before)


class AITaskViewUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'app.py').read_text(encoding='utf-8-sig')

    def test_tab_order_places_ai_task_view_between_acceptance_and_execution(self):
        tabs = self.source[self.source.index('st.tabs(['):self.source.index('])', self.source.index('st.tabs(['))]
        self.assertLess(tabs.index('数据验收'), tabs.index('AI任务视图'))
        self.assertLess(tabs.index('AI任务视图'), tabs.index('执行表'))

    def test_view_is_built_from_final_session_dataframe(self):
        main = self.source[self.source.index('# 主内容区'):]
        normalize = main.index('st.session_state.df = _normalize_display_columns')
        build = main.index('build_ai_task_view(st.session_state.df)')
        tabs = main.index('st.tabs([')
        self.assertLess(normalize, build)
        self.assertLess(build, tabs)


if __name__ == '__main__':
    unittest.main()
