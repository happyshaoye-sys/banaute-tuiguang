"""覆盖法阈值层 + 角色闸门层的聚焦测试。

验收原则：
* 阈值判定必须用全精度 float，四舍五入只用于展示；
* 角色层只做减法（拦截 / 封顶加投），不产生新的加投额度、不改变回收；
* 角色层关闭时，预算决策模块输出与既有行为完全一致。
"""
from __future__ import annotations

from datetime import date, timedelta
import unittest

import pandas as pd

from core.budget_decision import (
    BUDGET_DECISION_ROLE_COLUMNS,
    PRODUCT_COLUMNS,
    build_budget_decision_context,
    is_removed_budget_column,
)
from core.budget_role import (
    CLEAROUT_GUARD_KEY,
    DRIFT_BANDS,
    GATE_ALLOW,
    GATE_BLOCK,
    GATE_CAP,
    ROLE_LAYER_COLUMNS,
    THRESHOLD_VERSION,
    THRESHOLD_WINDOW_DAYS,
    apply_role_gate,
    assign_budget_role,
    boundary_for_share,
    compute_coverage_thresholds,
    compute_coverage_thresholds_from_day_map,
    compute_rolling_daily_gmv,
    freeze_drift_report,
    gate_budget_decisions,
    inject_rolling_daily_gmv,
    period_windows,
    recheck_thresholds,
    role_input_check,
    role_summary,
)
from core.plan_metrics import PLAN_ACTUAL_DAYS_COLUMN


#: 集团店真实口径：最近一周 50% 分界 410.22999999999996，80% 分界 71.75714285714285；
#: 8周中位数 568.8949999999999 / 81.39285714285714。
REAL_TH_MAIN_W0 = 410.22999999999996
REAL_TH_MID_W0 = 71.75714285714285
REAL_TH_MAIN_8W = 568.8949999999999
REAL_TH_MID_8W = 81.39285714285714


def _daily_frame(weeks_values, end='2026-09-09'):
    """weeks_values[0] 是最新一周；每项为该周各款的日销7（金额口径）。"""
    rows = []
    for index, values in enumerate(weeks_values):
        window_end = pd.Timestamp(end) - pd.Timedelta(days=7 * index)
        for product_index, value in enumerate(values):
            for offset in range(7):
                rows.append({
                    '统计日期': window_end - pd.Timedelta(days=offset),
                    '宝贝ID': f'P{product_index}',
                    'GMV': value,
                })
    return pd.DataFrame(rows)


def _product(product_id, gmv, spend, attributed=None, **overrides):
    row = {
        '宝贝ID': product_id,
        '淘宝类目': '连衣裙',
        'GMV': gmv,
        '付费': spend,
        '推广成交金额': attributed if attributed is not None else gmv,
        '综合等级': 'A',
        PLAN_ACTUAL_DAYS_COLUMN: 7,
    }
    row.update(overrides)
    return row


class CoverageThresholdTests(unittest.TestCase):
    def test_boundary_uses_window_gmv_coverage_with_full_precision(self):
        values = [500.0, REAL_TH_MID_W0, 71.0, 50.0, 20.0]
        boundary = boundary_for_share(values, 0.8)

        self.assertEqual(boundary, REAL_TH_MID_W0)
        self.assertNotEqual(boundary, round(boundary, 2))
        self.assertEqual(boundary_for_share([], 0.5), None)
        self.assertEqual(boundary_for_share([0.0, -1.0], 0.5), None)

    def test_median_of_eight_weeks_keeps_crossing_value_precision(self):
        weekly = [
            [600 - 20 * index, 200.0, 100.0]
            for index in range(8)
        ]
        result = compute_coverage_thresholds(_daily_frame(weekly))

        # 每周 TH_MAIN = 600-20k，中位数 = (540+520)/2 = 530（全精度，非四舍五入）
        self.assertEqual(result.th_main, 530.0)
        self.assertEqual(result.th_mid, 200.0)
        self.assertEqual(result.weeks, 8)
        self.assertEqual(result.version, THRESHOLD_VERSION)
        self.assertEqual(result.end_date, '2026-09-09')
        self.assertEqual(len(result.weekly), 8)
        self.assertEqual(result.weekly[0]['周次'], 'W0')
        self.assertEqual(result.weekly[0]['起始日'], '2026-09-03')
        self.assertEqual(result.weekly[0]['结束日'], '2026-09-09')
        self.assertTrue(result.valid)

    def test_missing_columns_or_empty_input_returns_invalid_without_raising(self):
        self.assertFalse(compute_coverage_thresholds(pd.DataFrame()).valid)
        broken = compute_coverage_thresholds(pd.DataFrame([{'宝贝ID': '1'}]))
        self.assertFalse(broken.valid)
        self.assertTrue(broken.warnings)
        self.assertEqual(broken.as_dict()['阈值版本'], THRESHOLD_VERSION)

    def test_out_of_range_thresholds_only_warn(self):
        result = compute_coverage_thresholds(_daily_frame([[5000.0, 100.0]] * 8))
        self.assertEqual(result.th_main, 5000.0)
        self.assertTrue(any('TH_MAIN' in warning for warning in result.warnings))

    def test_freeze_report_bands_and_out_of_band_flag(self):
        weekly = [
            ('W0', [600.0, 250.0, 250.0, 100.0, 50.0, 50.0]),
            ('W1', [100.0, 80.0, 60.0, 40.0, 20.0, 10.0]),
        ]
        coverage_bands = {
            'ZT_GMV覆盖': DRIFT_BANDS['ZT_GMV覆盖'],
            'ZT_TD_GMV覆盖': DRIFT_BANDS['ZT_TD_GMV覆盖'],
        }
        report = freeze_drift_report(weekly, th_main=530.0, th_mid=200.0, bands=coverage_bands)

        self.assertAlmostEqual(report['rows'][0]['ZT_GMV覆盖'], 600.0 / 1300.0, places=12)
        self.assertAlmostEqual(report['rows'][0]['ZT_TD_GMV覆盖'], 1100.0 / 1300.0, places=12)
        self.assertEqual(report['rows'][0]['状态'], '在带内')
        self.assertEqual(report['rows'][1]['状态'], '需复核')
        self.assertEqual(report['越带周数'], 1)
        self.assertIn('连续2周越带', report['结论'])

        default_report = freeze_drift_report(weekly, th_main=530.0, th_mid=200.0)
        self.assertIn('ZT款数占比', default_report['rows'][0]['越带项'])

    def test_recheck_thresholds_flags_relative_change(self):
        latest = compute_coverage_thresholds(_daily_frame([[600.0, 200.0, 100.0]] * 8))
        frozen = type(latest)(
            th_main=REAL_TH_MAIN_W0,
            th_mid=REAL_TH_MID_W0,
            version='store-freeze-v1',
            weeks=8,
            end_date='2026-09-02',
        )
        report = recheck_thresholds(frozen, latest)

        self.assertTrue(report['changes']['TH_MAIN']['是否超线'])
        self.assertFalse(report['维持'])
        self.assertIn('TH_MAIN', report['建议'])

        same = recheck_thresholds(latest, latest)
        self.assertTrue(same['维持'])
        self.assertEqual(same['建议'], '阈值变化在容忍范围内，维持冻结值')

    def test_decision_window_is_fixed_at_seven_days(self):
        self.assertEqual(THRESHOLD_WINDOW_DAYS, 7)
        self.assertEqual(THRESHOLD_VERSION, 'store-coverage-7d-8w-v1')

        windows = period_windows(date(2026, 9, 9))
        self.assertEqual(len(windows), 8)
        self.assertEqual(windows[0], (date(2026, 9, 3), date(2026, 9, 9)))
        self.assertEqual(windows[1], (date(2026, 8, 27), date(2026, 9, 2)))

        result = compute_coverage_thresholds(_daily_frame([[600.0, 200.0, 100.0]] * 8))
        self.assertEqual(result.window_days, 7)
        self.assertEqual(result.window_label, '7天')
        payload = result.as_dict()
        self.assertEqual(payload['阈值窗口'], '7天')
        self.assertEqual(payload['阈值周数'], 8)
        self.assertEqual(payload['阈值版本'], 'store-coverage-7d-8w-v1')
        self.assertTrue(
            payload['阈值状态'] == '正常' or payload['阈值状态'].startswith('存在越带('),
            payload['阈值状态'],
        )

    def test_day_map_entry_point_matches_long_table_thresholds(self):
        weekly = [[600 - 20 * index, 200.0, 100.0] for index in range(8)]
        daily = _daily_frame(weekly)
        expected = compute_coverage_thresholds(daily)

        day_map = {}
        for record in daily.to_dict('records'):
            key = str(record['统计日期'])[:10]
            day_map.setdefault(key, []).append({
                '宝贝ID': record['宝贝ID'],
                'GMV': record['GMV'],
            })
        day_map = {key: (pd.DataFrame(value),) for key, value in day_map.items()}

        from_day_map = compute_coverage_thresholds_from_day_map(
            day_map, end_date='2026-09-09',
        )

        self.assertEqual(from_day_map.th_main, expected.th_main)
        self.assertEqual(from_day_map.th_mid, expected.th_mid)
        self.assertEqual(from_day_map.weeks, 8)
        self.assertEqual(from_day_map.window_days, 7)
        self.assertEqual(from_day_map.as_dict()['阈值窗口'], '7天')

        empty = compute_coverage_thresholds_from_day_map({}, end_date='2026-09-09')
        self.assertFalse(empty.valid)
        self.assertEqual(empty.as_dict()['阈值状态'], '数据不足')


class RoleWaterfallTests(unittest.TestCase):
    TODAY = date(2026, 9, 9)

    def _role(self, row):
        return assign_budget_role(
            row,
            th_main=REAL_TH_MAIN_8W,
            th_mid=REAL_TH_MID_8W,
            store_roi=13.07,
            category_cart_median=0.05,
            today=self.TODAY,
        )

    def test_hard_risk_cleanup_beats_everything_else(self):
        decision = self._role(_product('R1', 0, 300, 0, 商品周期='成熟期'))
        self.assertEqual(decision.role, '清理')
        self.assertEqual(decision.gate, GATE_BLOCK)
        self.assertIn('零成交', decision.reason)

    def test_hard_risk_requires_zero_gmv_streak_or_big_spend(self):
        # 本期零成交但上一周期有成交、花费很小 → 属新起量/波动款，不做硬风险一票否决
        new_ramp = self._role(_product(
            'R1B', 0, 5, 0, 上一7日日均GMV=3.0,
            创建时间=(self.TODAY - timedelta(days=3)).isoformat(),
        ))
        self.assertEqual(new_ramp.role, 'CS测试')
        self.assertEqual(new_ramp.gate, GATE_CAP)

        # 本期零成交 + 大额花费 → 无论上一周期如何都按硬风险处理
        big_spend = self._role(_product('R1C', 0, 300, 0, 上一7日日均GMV=50.0))
        self.assertEqual(big_spend.role, '清理')
        self.assertEqual(big_spend.gate, GATE_BLOCK)
        self.assertIn('达门槛', big_spend.reason)

        # 本期与上一周期都零成交 → 硬风险（长尾档也拦）
        streak = self._role(_product('R1D', 0, 3, 0, 上一7日日均GMV=0.0))
        self.assertEqual(streak.role, '清理')
        self.assertEqual(streak.evidence['角色_硬风险档'], '长尾')

    def test_clearout_and_new_product_take_priority(self):
        clearout = self._role(_product('R2', 5000, 100, 9000, 商品周期='清退期'))
        self.assertEqual(clearout.role, '清理')
        new_product = self._role(_product(
            'R3', 5000, 100, 9000, 创建时间=(self.TODAY - timedelta(days=5)).isoformat(),
        ))
        self.assertEqual(new_product.role, 'CS测试')
        self.assertEqual(new_product.gate, GATE_CAP)
        self.assertEqual(new_product.evidence['角色_上架天数'], 5)

    # ── 清仓护栏（v2.54）────────────────────────────────────
    def test_clearout_guard_releases_decline_warning_still_above_threshold(self):
        """衰退预警但仍在线上的款不能被锁成清理。

        真实样本：集团店 2026-09-11 宝贝ID 1026876223060——日销7 1384.3857
        （TH_MAIN 的 2.78 倍）、环比 +75.51%，原逻辑却判「清理 + 禁止加投」。
        """
        row = _product(
            'R2G', 9690.70, 822.48, 9000,
            商品周期='衰退预警', 商品定位='潜力款',
            近7日日均GMV=1384.3857142857141,
            上一7日日均GMV=788.7771428571428,
            GMV趋势率=0.7551037410530584,
        )
        decision = self._role(row)
        self.assertEqual(decision.role, 'ZT主推')
        self.assertEqual(decision.gate, GATE_ALLOW)
        self.assertIn('清仓护栏放行', decision.reason)
        self.assertIn('日销7 1384.3857', decision.evidence[CLEAROUT_GUARD_KEY])

    def test_clearout_guard_releases_decline_warning_above_mid_line_when_falling(self):
        """跌破主推线但在腰线以上且环比下跌 → 放行给瀑布，按趋势破位只拦加投。"""
        row = _product(
            'R2H', 5968.08, 453.79, 5900,
            商品周期='衰退预警', 商品定位='潜力款',
            近7日日均GMV=852.58, 上一7日日均GMV=1347.87, GMV趋势率=-0.3672,
        )
        decision = self._role(row)
        self.assertEqual(decision.role, 'ZT趋势预警')
        self.assertEqual(decision.gate, GATE_BLOCK)
        self.assertIn('清仓护栏放行', decision.reason)

    def test_clearout_guard_keeps_weak_decline_warning_below_mid_line(self):
        """腰线以下且环比下跌：衰退预警照旧判清理（护栏不放行）。"""
        row = _product(
            'R2I', 200.0, 30.0, 100,
            商品周期='衰退预警', 商品定位='滞销款',
            近7日日均GMV=20.0, 上一7日日均GMV=40.0, GMV趋势率=-0.5,
        )
        decision = self._role(row)
        self.assertEqual(decision.role, '清理')
        self.assertEqual(decision.gate, GATE_BLOCK)
        self.assertNotIn(CLEAROUT_GUARD_KEY, decision.evidence)

    def test_clearout_guard_never_applies_to_real_clearout_labels(self):
        """清退期 / 淘汰 / 观察期 等强标记不受护栏影响，量级再大也判清理。"""
        for label in ('清退期', '淘汰', '观察期'):
            with self.subTest(label=label):
                row = _product(
                    f'R2-{label}', 5000, 100, 9000,
                    商品周期=label, 商品定位='潜力款',
                    近7日日均GMV=1384.3857142857141, GMV趋势率=0.7551,
                )
                decision = self._role(row)
                self.assertEqual(decision.role, '清理')
                self.assertEqual(decision.gate, GATE_BLOCK)
                self.assertNotIn(CLEAROUT_GUARD_KEY, decision.evidence)

    def test_main_bucket_trend_alert_blocks_increase(self):
        alert = self._role(_product('R4', 5000, 100, 9000, GMV趋势率=-0.2))
        self.assertEqual(alert.role, 'ZT趋势预警')
        self.assertEqual(alert.gate, GATE_BLOCK)

        main = self._role(_product('R5', 5000, 100, 9000, GMV趋势率=0.05))
        self.assertEqual(main.role, 'ZT主推')
        self.assertEqual(main.gate, GATE_ALLOW)

    def test_mid_buckets_split_by_trend_traffic_and_cart(self):
        thin = self._role(_product(
            'R6', 840, 3, 600, 商品周期='成熟期', 花费占比=0.01, 销售占比=0.10,
        ))
        self.assertEqual(thin.role, 'TC投产')
        self.assertEqual(thin.gate, GATE_ALLOW)

        # 零花费但日销7达标：自然成交强，同样属投产型候选
        natural = self._role(_product(
            'R6B', 840, 0, 600, 商品周期='成熟期', 花费占比=0.0, 销售占比=0.10,
        ))
        self.assertEqual(natural.role, 'TC投产')
        self.assertEqual(natural.gate, GATE_ALLOW)

        ladder = self._role(_product(
            'R7', 700, 100, 900, GMV趋势率=0.02, 商品访客数=50, 商品加购率=0.08,
        ))
        self.assertEqual(ladder.role, 'TD梯队')
        self.assertEqual(ladder.gate, GATE_ALLOW)

        candidate = self._role(_product('R8', 700, 100, 900, GMV趋势率=-0.05))
        self.assertEqual(candidate.role, 'TD候选')
        self.assertEqual(candidate.gate, GATE_BLOCK)

    def test_low_efficiency_and_bystander_buckets(self):
        recycled = self._role(_product('R9', 500, 300, 30, 商品访客数=10))
        self.assertEqual(recycled.role, 'QC回收')
        self.assertEqual(recycled.gate, GATE_BLOCK)

        standby = self._role(_product('R10', 70, 20, 60, 商品访客数=3))
        self.assertEqual(standby.role, '观察')
        self.assertEqual(standby.gate, GATE_BLOCK)

    def test_missing_thresholds_never_intervene(self):
        decision = assign_budget_role(_product('R11', 0, 500, 0), th_main=0, th_mid=0)
        self.assertEqual(decision.role, '未判定')
        self.assertEqual(decision.gate, GATE_ALLOW)
        self.assertIn('无阈值不介入', decision.gate_reason)

    def test_refund_risk_downgrades_gate_but_stock_risk_does_not(self):
        refund = self._role(_product(
            'R12', 5000, 100, 9000, GMV趋势率=0.05, 风险标签='退款风险',
        ))
        self.assertEqual(refund.role, 'ZT主推')
        self.assertEqual(refund.gate, GATE_BLOCK)
        self.assertIn('只降不增', refund.gate_reason)
        self.assertEqual(refund.evidence['角色_风险命中'], '风险标签=退款风险')

        stock = self._role(_product(
            'R13', 5000, 100, 9000, GMV趋势率=0.05, 风险标签='库存风险',
        ))
        self.assertEqual(stock.role, 'ZT主推')
        self.assertEqual(stock.gate, GATE_ALLOW)
        self.assertEqual(stock.evidence['角色_风险命中'], '')

    def test_no_threshold_keeps_refund_risk_from_intervening(self):
        decision = assign_budget_role(
            _product('R14', 5000, 100, 9000, 风险标签='退款风险'),
            th_main=0, th_mid=0,
        )
        self.assertEqual(decision.role, '未判定')
        self.assertEqual(decision.gate, GATE_ALLOW)

    def test_role_columns_are_not_retired_budget_columns(self):
        for column in BUDGET_DECISION_ROLE_COLUMNS:
            self.assertFalse(is_removed_budget_column(column), column)
            self.assertIn(column, list(ROLE_LAYER_COLUMNS) + ['角色闸门命中'])

    def test_input_check_flags_missing_thresholds_and_period_mismatch(self):
        aligned = [
            _product('C1', 840, 3, 600, 近7日日均GMV=120.0, 上一7日日均GMV=110.0,
                     TH_MAIN=410.23, TH_MID=71.757, 阈值窗口='7天'),
        ]
        ready = role_input_check(aligned)
        self.assertTrue(ready['ready'])
        self.assertEqual(ready['warnings'], ())
        self.assertEqual(ready['TH_MAIN'], 410.23)
        self.assertEqual(ready['判定窗口天数'], 7)
        self.assertEqual(ready['阈值窗口标注'], 7)

        # 看数周期切到 30 天但没注入近7日口径 → 角色层会退回 30 日均，与固定 7 天阈值不同窗
        monthly = [
            _product('C2', 840, 3, 600, 统计日期='2026-08-11~2026-09-09',
                     TH_MAIN=410.23, TH_MID=71.757, 阈值窗口='30天'),
        ]
        guarded = role_input_check(monthly)
        self.assertFalse(guarded['ready'])
        self.assertEqual(guarded['看数周期天数'], 30)
        joined = '；'.join(guarded['warnings'])
        self.assertIn('阈值窗口标注为 30 天', joined)
        self.assertIn('近7日日均GMV', joined)
        self.assertIn('inject_rolling_daily_gmv', joined)
        self.assertIn('上一7日日均GMV', joined)

    def test_rolling_daily_gmv_is_fixed_to_seven_days_regardless_of_view_period(self):
        # day_map 模拟工具内「日期 → (日帧, ...)」结构：每天 2 款
        days = pd.date_range('2026-08-26', '2026-09-09', freq='D')
        day_map = {}
        for offset, day in enumerate(days):
            key = day.strftime('%Y-%m-%d')
            day_map[key] = (
                pd.DataFrame([
                    {'宝贝ID': 'P1', 'GMV': 700.0, '转化周期': '15天转化'},
                    {'宝贝ID': 'P1', 'GMV': 999999.0, '转化周期': '7天转化'},
                    {'宝贝ID': 'P2', 'GMV': 70.0, '转化周期': '15天转化'},
                ]),
            )

        rolling = compute_rolling_daily_gmv(day_map, end_date='2026-09-09')
        self.assertEqual(rolling['window_days'], 7)
        self.assertEqual(rolling['current_range'], '2026-09-03~2026-09-09')
        self.assertEqual(rolling['previous_range'], '2026-08-27~2026-09-02')
        self.assertEqual(rolling['current']['P1'], 700.0)   # 7天转化行被排除，未被放大
        self.assertEqual(rolling['current']['P2'], 70.0)
        self.assertEqual(rolling['trend']['P1'], 0.0)

        df = pd.DataFrame([
            {'宝贝ID': 'P1', 'GMV': 4900.0, '统计日期': '2026-08-11~2026-09-09'},
            {'宝贝ID': 'P2', 'GMV': 490.0, '统计日期': '2026-08-11~2026-09-09'},
            {'宝贝ID': 'P3', 'GMV': 30000.0, '统计日期': '2026-08-11~2026-09-09'},
        ])
        injected = inject_rolling_daily_gmv(df, day_map, end_date='2026-09-09')

        self.assertNotIn('近7日日均GMV', df.columns)          # 入参未被修改
        # P3 在窗口内没有成交 → 写 0，而不是留空后回退成「月度GMV ÷ 30」
        self.assertEqual(list(injected['近7日日均GMV']), [700.0, 70.0, 0.0])
        self.assertEqual(list(injected['上一7日日均GMV']), [700.0, 70.0, 0.0])
        self.assertEqual(injected['GMV趋势率'].iloc[0], 0.0)
        self.assertTrue(pd.isna(injected['GMV趋势率'].iloc[2]))  # 上一周期为 0 → 趋势无定义
        # 注入后自检通过：判定基准被固定成近7日，而不是看数周期的 30 日均
        check = role_input_check(injected, th_main=410.23, th_mid=71.757)
        self.assertTrue(check['ready'], check['warnings'])

        # 注入的 0 必须当真零成交，不能回退成「月度GMV ÷ 30」把它算成有量
        idle = pd.DataFrame([
            {'宝贝ID': 'P9', 'GMV': 30000.0, '近7日日均GMV': 0.0,
             '上一7日日均GMV': 0.0, 'TH_MAIN': 410.23, 'TH_MID': 71.757},
        ])
        idle_rows = gate_budget_decisions(idle, store_roi=13.07)
        self.assertEqual(idle_rows[0]['决策角色'], '观察')  # 而不是 ZT主推（30000/30=1000）
        self.assertEqual(idle_rows[0]['角色闸门'], GATE_BLOCK)


class RoleGateTests(unittest.TestCase):
    def _decision(self, role_row):
        return assign_budget_role(
            role_row, th_main=REAL_TH_MAIN_8W, th_mid=REAL_TH_MID_8W,
            store_roi=13.07, category_cart_median=0.05, today=date(2026, 9, 9),
        )

    def test_gate_blocks_increase_for_unqualified_role(self):
        decision = self._decision(_product('G1', 700, 100, 900, GMV趋势率=-0.05))
        gated = apply_role_gate(
            action='加投', adjustment=500.0, state='花费少投',
            reason='花费占比低于销售占比', decision=decision,
        )

        self.assertEqual(gated['未来7日建议调整金额'], 0.0)
        self.assertEqual(gated['预算匹配状态'], '角色闸门拦截(TD候选)')
        self.assertEqual(gated['角色闸门命中'], GATE_BLOCK)
        self.assertIn('TD候选', gated['动作依据'])

    def test_gate_caps_test_bucket_increase(self):
        decision = self._decision(_product(
            'G2', 5000, 100, 9000, 创建时间='2026-09-04',
        ))
        gated = apply_role_gate(
            action='加投', adjustment=900.0, state='花费少投',
            reason='少投', decision=decision,
        )

        self.assertEqual(gated['未来7日建议调整金额'], 500.0)
        self.assertEqual(gated['预算匹配状态'], '角色闸门封顶(CS测试)')

    def test_gate_allows_qualified_role_and_never_touches_recovery(self):
        allowed = self._decision(_product('G3', 5000, 100, 9000, GMV趋势率=0.05))
        gated = apply_role_gate(
            action='加投', adjustment=500.0, state='花费少投',
            reason='少投', decision=allowed,
        )
        self.assertEqual(gated['未来7日建议调整金额'], 500.0)
        self.assertEqual(gated['预算匹配状态'], '花费少投')
        self.assertEqual(gated['角色闸门命中'], '')

        recovery = apply_role_gate(
            action='降投', adjustment=-800.0, state='花费超配',
            reason='超配回收', decision=allowed,
        )
        self.assertEqual(recovery['未来7日建议调整金额'], -800.0)
        self.assertEqual(recovery['预算匹配状态'], '花费超配')

    def test_gate_budget_decisions_rewrites_suggested_spend_and_summary(self):
        products = [
            _product(
                'H1', 1000, 100, 2000,
                花费占比=0.10, 销售占比=0.90, 推广投产=20.0,
                预算匹配状态='花费少投', 动作依据='花费占比低于销售占比',
                未来7日建议调整金额=404.5, 未来7日花费基线=100.0,
                TH_MAIN=REAL_TH_MAIN_8W, TH_MID=REAL_TH_MID_8W,
            ),
            _product(
                'H2', 5000, 100, 9000, GMV趋势率=0.05,
                花费占比=0.10, 销售占比=0.10, 推广投产=90.0,
                预算匹配状态='花费少投', 动作依据='花费占比低于销售占比',
                未来7日建议调整金额=300.0, 未来7日花费基线=100.0,
                TH_MAIN=REAL_TH_MAIN_8W, TH_MID=REAL_TH_MID_8W,
            ),
        ]
        rows = {row['宝贝ID']: row for row in gate_budget_decisions(products, store_roi=13.07)}

        self.assertEqual(rows['H1']['决策角色'], 'TD候选')
        self.assertEqual(rows['H1']['未来7日建议调整金额'], 0.0)
        self.assertEqual(rows['H1']['未来7日建议花费'], 100.0)
        self.assertEqual(rows['H2']['决策角色'], 'ZT主推')
        self.assertEqual(rows['H2']['未来7日建议调整金额'], 300.0)
        self.assertEqual(rows['H2']['未来7日建议花费'], 400.0)

        summary = {row['决策角色']: row for row in role_summary(list(rows.values()))}
        self.assertEqual(summary['TD候选']['禁止加投款数'], 1)
        self.assertEqual(summary['ZT主推']['未来7日建议调整金额'], 300.0)
        self.assertAlmostEqual(
            summary['TD候选']['销售份额'] + summary['ZT主推']['销售份额'], 1.0, places=9,
        )


class BudgetDecisionRoleLayerTests(unittest.TestCase):
    def test_app_wires_role_layer_before_launch_without_changing_library_default(self):
        """工具侧在调用处 opt-in，库默认保持关闭：看板与全量导出必须一致打开。"""
        from pathlib import Path

        import core.budget_role as budget_role

        self.assertFalse(budget_role.ROLE_LAYER_ENABLED)
        app_source = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8-sig')
        self.assertEqual(app_source.count('build_budget_decision_context(df, roles=True)'), 2)
        self.assertIn('def _attach_budget_role_basis(', app_source)
        self.assertIn('_attach_budget_role_basis(', app_source)
        self.assertIn('date_str, year=year, shop=shop, cache_root=cache_root,', app_source)
        self.assertIn('inject_rolling_daily_gmv', app_source)
        self.assertIn('compute_coverage_thresholds_from_day_map', app_source)
        self.assertIn('budget_decision_context_v3_roles', app_source)
        # v2.54：冻结后按轻量 GMV 缓存算引擎建议 + 复核入口
        self.assertIn('def _resolve_engine_thresholds(', app_source)
        self.assertIn('compute_coverage_thresholds_from_gmv_daily', app_source)
        self.assertIn('def _render_threshold_review_block(', app_source)
        # 覆盖判定是独立函数，必须自己 import period_windows（否则运行时 NameError，
        # 而 NameError 会被"角色层不介入"的兜底吞掉，页面只表现为角色列缺失）
        helper = app_source.split('def _day_map_covers_role_history(')[1].split('\ndef ')[0]
        self.assertIn('from core.budget_role import period_windows', helper)
        self.assertIn('period_windows(', helper)

    def _source(self):
        return pd.DataFrame([
            _product(
                'A', 1000, 100, 2000,
                TH_MAIN=REAL_TH_MAIN_8W, TH_MID=REAL_TH_MID_8W,
                商品周期='成长期', 商品访客数=0,
            ),
            _product(
                'C', 8000, 2000, 6000,
                TH_MAIN=REAL_TH_MAIN_8W, TH_MID=REAL_TH_MID_8W,
                近7日日均GMV=REAL_TH_MAIN_8W + 50,
            ),
            _product(
                'B', 100, 900, 90,
                TH_MAIN=REAL_TH_MAIN_8W, TH_MID=REAL_TH_MID_8W,
            ),
        ])

    def test_roles_off_by_default_keeps_output_shape(self):
        source = self._source()
        default = build_budget_decision_context(source)
        explicit_off = build_budget_decision_context(source, roles=False)

        rows = {item['宝贝ID']: item for item in default['products']}
        # 列顺序固定（含角色列与阈值列），角色层关闭时角色列存在但取值为空
        self.assertEqual(list(rows['A'].keys()), list(PRODUCT_COLUMNS))
        for column in BUDGET_DECISION_ROLE_COLUMNS:
            self.assertEqual(rows['A'][column], '')
        self.assertEqual(default['products'], explicit_off['products'])
        self.assertGreater(rows['A']['未来7日建议调整金额'], 0)

    def test_roles_on_gate_increase_and_keep_columns(self):
        source = self._source()
        context = build_budget_decision_context(source, roles=True)
        rows = {item['宝贝ID']: item for item in context['products']}

        for column in BUDGET_DECISION_ROLE_COLUMNS:
            self.assertIn(column, rows['A'])

        self.assertEqual(rows['A']['决策角色'], 'TD候选')
        self.assertEqual(rows['A']['角色闸门'], GATE_BLOCK)
        self.assertEqual(rows['A']['未来7日建议调整金额'], 0)
        self.assertIn('角色闸门拦截', rows['A']['预算匹配状态'])

        self.assertEqual(rows['C']['决策角色'], 'ZT主推')
        self.assertGreater(rows['C']['未来7日建议调整金额'], 0)

        self.assertEqual(rows['B']['决策角色'], 'QC回收')
        self.assertLess(rows['B']['未来7日建议调整金额'], 0)

    def test_roles_on_without_thresholds_changes_nothing(self):
        source = pd.DataFrame([
            _product('A', 1000, 100, 2000, 商品周期='成长期'),
            _product('B', 100, 900, 90),
        ])
        base = build_budget_decision_context(source)
        roles = build_budget_decision_context(source, roles=True)
        base_rows = {item['宝贝ID']: item for item in base['products']}
        role_rows = {item['宝贝ID']: item for item in roles['products']}

        for product_id, base_row in base_rows.items():
            self.assertEqual(role_rows[product_id]['决策角色'], '未判定')
            self.assertEqual(
                role_rows[product_id]['未来7日建议调整金额'],
                base_row['未来7日建议调整金额'],
            )
            self.assertEqual(
                role_rows[product_id]['预算匹配状态'], base_row['预算匹配状态'],
            )


if __name__ == '__main__':
    unittest.main()
