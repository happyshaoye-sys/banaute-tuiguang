"""阈值「运营手工冻结」的聚焦测试。

规则：引擎只算建议值；生效阈值以运营冻结值为准；冻结/清除都要留审计记录。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.budget_role import (
    THRESHOLD_WINDOW_DAYS,
    compute_coverage_thresholds,
    resolve_thresholds,
    threshold_columns_from_payload,
)
from core.threshold_store import (
    FALLBACK_DIR_NAME,
    FREEZE_SOURCE_ENGINE,
    FREEZE_SOURCE_MANUAL,
    TRACKED_DIR_NAME,
    ThresholdFreezeError,
    ThresholdFreezeStore,
    commit_freeze_registry,
    default_freeze_root,
    freeze_commit_message,
    freeze_key,
    porcelain_lines,
    registry_git_status,
)


def _daily_frame(weeks_values, end='2026-09-09'):
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


ENGINE = {'TH_MAIN': 568.895, 'TH_MID': 81.39285714285714,
          '阈值参照日': '2026-09-09', '阈值周数': 8}


class ThresholdFreezeStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ThresholdFreezeStore(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_freeze_round_trip_keeps_audit_fields(self):
        saved = self.store.save(
            '2026', '集团店', th_main=520.0, th_mid=78.5,
            operator='张三', note='9月换季按运营口径下调',
            engine=ENGINE,
        )

        self.assertEqual(saved['TH_MAIN'], 520.0)
        self.assertEqual(saved['TH_MID'], 78.5)
        self.assertEqual(saved['阈值来源'], FREEZE_SOURCE_MANUAL)
        self.assertEqual(saved['阈值状态'], '已冻结')
        self.assertEqual(saved['冻结人'], '张三')
        self.assertEqual(saved['备注'], '9月换季按运营口径下调')
        self.assertEqual(saved['引擎建议TH_MAIN'], 568.895)
        self.assertEqual(saved['窗口天数'], THRESHOLD_WINDOW_DAYS)
        self.assertTrue(saved['阈值版本'].startswith('store-manual-'))

        loaded = self.store.get('2026', '集团店')
        self.assertEqual(loaded['TH_MAIN'], 520.0)
        self.assertEqual(loaded['阈值版本'], saved['阈值版本'])
        self.assertEqual(len(self.store.history('2026', '集团店')), 1)

    def test_store_is_isolated_per_year_and_shop(self):
        self.store.save('2026', '集团店', th_main=520.0, th_mid=78.5, operator='张三')
        self.store.save('2026', '女装店', th_main=700.0, th_mid=130.0, operator='李四')

        self.assertEqual(self.store.get('2026', '集团店')['TH_MAIN'], 520.0)
        self.assertEqual(self.store.get('2026', '女装店')['TH_MAIN'], 700.0)
        self.assertIsNone(self.store.get('2025', '集团店'))
        self.assertEqual(freeze_key('2026', '集团店'), '2026|集团店')

    def test_second_freeze_appends_history_and_bumps_version(self):
        first = self.store.save('2026', '集团店', th_main=520.0, th_mid=78.5, operator='张三')
        second = self.store.save('2026', '集团店', th_main=480.0, th_mid=70.0, operator='李四')

        self.assertNotEqual(first['阈值版本'], second['阈值版本'])
        history = self.store.history('2026', '集团店')
        self.assertEqual([item['TH_MAIN'] for item in history], [520.0, 480.0])
        self.assertEqual(self.store.get('2026', '集团店')['TH_MAIN'], 480.0)

    def test_clear_freeze_falls_back_to_engine(self):
        self.store.save('2026', '集团店', th_main=520.0, th_mid=78.5, operator='张三')

        self.assertTrue(self.store.clear('2026', '集团店', operator='张三', note='换季结束'))
        self.assertIsNone(self.store.get('2026', '集团店'))
        self.assertFalse(self.store.clear('2026', '集团店'))
        history = self.store.history('2026', '集团店')
        self.assertEqual(history[-1]['操作'], '清除冻结')
        self.assertEqual(history[-1]['原TH_MAIN'], 520.0)

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ThresholdFreezeError):
            self.store.save('2026', '集团店', th_main=80.0, th_mid=500.0)   # 80% 分界不能高于 50%
        with self.assertRaises(ThresholdFreezeError):
            self.store.save('2026', '集团店', th_main=80.0, th_mid=80.0)
        with self.assertRaises(ThresholdFreezeError):
            self.store.save('2026', '集团店', th_main='abc', th_mid=50.0)
        with self.assertRaises(ThresholdFreezeError):
            self.store.save('2026', '集团店', th_main=0.0, th_mid=10.0)
        with self.assertRaises(ThresholdFreezeError):
            self.store.save('2026', '集团店', th_main=-5.0, th_mid=1.0)
        self.assertIsNone(self.store.get('2026', '集团店'))


class ResolveThresholdsTests(unittest.TestCase):
    def _engine(self):
        return compute_coverage_thresholds(_daily_frame([[600 - 20 * index, 200.0, 100.0] for index in range(8)]))

    def test_without_freeze_uses_engine_suggestion_and_flags_unfrozen(self):
        payload = resolve_thresholds(self._engine(), None)

        self.assertEqual(payload['阈值来源'], FREEZE_SOURCE_ENGINE)
        self.assertIn('尚未冻结', payload['阈值复核提示'])
        self.assertEqual(payload['TH_MAIN'], self._engine().th_main)
        self.assertEqual(payload['阈值冻结人'], '')

    def test_frozen_value_wins_over_engine_suggestion(self):
        engine = self._engine()
        frozen = {'TH_MAIN': 500.0, 'TH_MID': 80.0, '阈值版本': 'store-manual-20260911-1',
                  '阈值窗口': '7天', '窗口周数': 8, '阈值来源': FREEZE_SOURCE_MANUAL,
                  '阈值状态': '已冻结', '冻结人': '张三', '冻结时间': '2026-09-11 10:00:00'}

        payload = resolve_thresholds(engine, frozen)

        self.assertEqual(payload['TH_MAIN'], 500.0)
        self.assertEqual(payload['TH_MID'], 80.0)
        self.assertEqual(payload['阈值来源'], FREEZE_SOURCE_MANUAL)
        self.assertEqual(payload['阈值状态'], '已冻结')
        self.assertEqual(payload['阈值冻结人'], '张三')
        self.assertEqual(payload['阈值版本'], 'store-manual-20260911-1')
        self.assertEqual(payload['引擎建议TH_MAIN'], engine.th_main)
        self.assertAlmostEqual(payload['阈值偏离TH_MAIN'], (500.0 - engine.th_main) / engine.th_main, places=12)

    def test_large_deviation_triggers_recheck_hint(self):
        engine = self._engine()  # 530 / 200
        frozen = {'TH_MAIN': 100.0, 'TH_MID': 50.0, '阈值版本': 'v1', '窗口周数': 8}

        payload = resolve_thresholds(engine, frozen, tolerance=0.30)

        self.assertIn('偏离', payload['阈值复核提示'])
        self.assertIn('TH_MAIN', payload['阈值复核提示'])

        close = dict(frozen, TH_MAIN=engine.th_main * 1.05, TH_MID=engine.th_mid * 1.05)
        self.assertIn('一致', resolve_thresholds(engine, close, tolerance=0.30)['阈值复核提示'])

    def test_engine_failure_falls_back_to_frozen_and_columns_are_extracted(self):
        broken = compute_coverage_thresholds(pd.DataFrame())
        frozen = {'TH_MAIN': 500.0, 'TH_MID': 80.0, '阈值版本': 'store-manual-1',
                  '阈值窗口': '7天', '窗口周数': 8, '冻结人': '张三'}

        payload = resolve_thresholds(broken, frozen)

        self.assertEqual(payload['TH_MAIN'], 500.0)
        self.assertEqual(payload['阈值状态'], '已冻结')
        self.assertIn('算不出建议值', payload['阈值复核提示'])

        columns = threshold_columns_from_payload(payload)
        self.assertIn('TH_MAIN', columns)
        self.assertIn('阈值版本', columns)
        self.assertIn('引擎建议TH_MID', columns)
        self.assertNotIn('阈值复核提示', columns)   # 提示只给界面，不写进执行表

        self.assertEqual(resolve_thresholds(broken, None)['阈值状态'], '数据不足')


class ThresholdFreezeGitTests(unittest.TestCase):
    """登记随 git 走：默认落仓库 config/，冻结时可提交并留审计信息。"""

    def setUp(self):
        import os
        import shutil
        import subprocess
        from unittest import mock

        self._shutil = shutil
        self._subprocess = subprocess
        self._blocked_dir = None
        self.repo = Path(tempfile.mkdtemp(prefix='metox_freeze_git_'))
        git_kwargs = {'cwd': self.repo, 'check': True, 'capture_output': True,
                      'text': True, 'encoding': 'utf-8', 'errors': 'replace'}
        subprocess.run(['git', 'init', '-q'], **git_kwargs)
        for key, value in (('user.email', 'tester@example.com'), ('user.name', 'tester')):
            subprocess.run(['git', 'config', key, value], **git_kwargs)
        self._env = mock.patch.dict(os.environ, {
            'BANAUTE_GIT_REPO': str(self.repo),
            'METOX_THRESHOLD_DIR': '',
        }, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        # 本测试可能给子目录设过拒绝权限：先递归恢复，再删除，避免残留不可读目录
        if self._blocked_dir is not None and self._blocked_dir.exists():
            account = os.environ.get('USERNAME', '')
            for args in (
                ['icacls', str(self._blocked_dir), '/remove:d', account, '/T'],
                ['icacls', str(self._blocked_dir), '/grant', f'{account}:(OI)(CI)F', '/T'],
            ):
                self._subprocess.run(args, capture_output=True, text=True)
        self._shutil.rmtree(self.repo, ignore_errors=True)

    def test_default_root_prefers_repo_config_directory(self):
        root = default_freeze_root(app_dir=self.repo)
        self.assertEqual(root, self.repo / TRACKED_DIR_NAME)
        self.assertEqual(root.name, 'config')

    def test_root_falls_back_to_local_cache_without_repository(self):
        import os
        from unittest import mock

        # 注意：必须放在仓库之外，否则 git 会向上找到父级仓库
        with tempfile.TemporaryDirectory() as outside:
            plain = Path(outside) / 'no_repo_app'
            plain.mkdir()
            with mock.patch.dict(os.environ, {
                'BANAUTE_GIT_REPO': str(plain),   # 不是仓库 → 找不到
                'METOX_THRESHOLD_DIR': '',
            }, clear=False):
                root = default_freeze_root(app_dir=plain)
                status = registry_git_status(root, app_dir=plain)
        self.assertEqual(root, plain / FALLBACK_DIR_NAME)
        self.assertFalse(status['in_repo'])

    def test_freeze_is_committed_to_git_with_operator_in_message(self):
        store = ThresholdFreezeStore(default_freeze_root(app_dir=self.repo))
        saved = store.save('2026', '集团店', th_main=520.0, th_mid=78.5,
                           operator='运营-张三', note='9月换季')

        status_before = registry_git_status(app_dir=self.repo)
        self.assertTrue(status_before['in_repo'])
        self.assertEqual(status_before['relative_path'], 'config/threshold_freeze.json')
        self.assertFalse(status_before['tracked'])

        message = freeze_commit_message(saved)
        self.assertIn('520.0', message)
        self.assertIn('78.5', message)
        self.assertIn('运营-张三', message)
        self.assertIn('9月换季', message)

        result = commit_freeze_registry(app_dir=self.repo, message=message)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['commit'])
        self.assertFalse(result['pushed'])

        status_after = registry_git_status(app_dir=self.repo)
        self.assertTrue(status_after['tracked'])
        self.assertFalse(status_after['dirty'])

        import subprocess

        git_kwargs = {'cwd': self.repo, 'capture_output': True, 'check': True,
                      'text': True, 'encoding': 'utf-8', 'errors': 'replace'}
        tracked = subprocess.run(
            ['git', 'ls-files', 'config/threshold_freeze.json'], **git_kwargs,
        ).stdout.strip()
        self.assertEqual(tracked, 'config/threshold_freeze.json')
        subject = subprocess.run(
            ['git', 'log', '-1', '--pretty=%s'], **git_kwargs,
        ).stdout.strip()
        self.assertIn('运营-张三', subject)

        # 内容没变时不产生空提交
        again = commit_freeze_registry(app_dir=self.repo, message=message)
        self.assertTrue(again['ok'])
        self.assertEqual(again['commit'], '')
        self.assertIn('没有变化', again['detail'])

    def test_clear_freeze_can_be_committed_too(self):
        store = ThresholdFreezeStore(default_freeze_root(app_dir=self.repo))
        store.save('2026', '集团店', th_main=520.0, th_mid=78.5, operator='张三')
        commit_freeze_registry(app_dir=self.repo, message=freeze_commit_message(store.get('2026', '集团店')))

        previous = store.get('2026', '集团店')
        self.assertTrue(store.clear('2026', '集团店', operator='张三', note='换季结束'))
        result = commit_freeze_registry(
            app_dir=self.repo, message=freeze_commit_message(previous, action='清除'),
        )

        self.assertTrue(result['ok'], result)
        self.assertIn('清除预算决策阈值冻结', freeze_commit_message(previous, action='清除'))
        status = registry_git_status(app_dir=self.repo)
        self.assertTrue(status['tracked'])
        self.assertFalse(status['dirty'])

    def test_porcelain_parser_ignores_git_warnings_on_stderr(self):
        """git 把 warning/error/hint 写在 stderr：拼接进状态会让"干净"被误判成"有改动"。"""
        self.assertEqual(porcelain_lines(''), [])
        self.assertEqual(porcelain_lines('warning: could not open directory \'sub/\''), [])
        self.assertEqual(
            porcelain_lines("warning: could not open directory 'sub/'\nhint: something"),
            [],
        )
        self.assertEqual(
            porcelain_lines("warning: LF will be replaced by CRLF\n M config/threshold_freeze.json"),
            [' M config/threshold_freeze.json'],
        )
        self.assertEqual(porcelain_lines('?? config/threshold_freeze.json'), ['?? config/threshold_freeze.json'])
        self.assertEqual(porcelain_lines('error: pathspec did not match'), [])

    @unittest.skipUnless(sys.platform.startswith('win'), '需要 Windows 的 icacls 制造 git 警告')
    def test_clean_registry_is_not_marked_dirty_when_git_warns(self):
        """回归：仓库里存在不可读目录时，git 会给 stderr 警告；真实 porcelain 仍为空。"""
        subprocess = self._subprocess
        git_kwargs = {'cwd': self.repo, 'capture_output': True, 'text': True,
                      'encoding': 'utf-8', 'errors': 'replace'}

        store_root = default_freeze_root(app_dir=self.repo)
        store_root.mkdir(parents=True, exist_ok=True)
        store = ThresholdFreezeStore(store_root)
        store.save('2026', '集团店', th_main=520.0, th_mid=78.5, operator='张三')
        committed = commit_freeze_registry(
            app_dir=self.repo, message=freeze_commit_message(store.get('2026', '集团店')),
        )
        self.assertTrue(committed['ok'], committed)

        blocked = self.repo / 'blocked_dir'
        blocked.mkdir(exist_ok=True)
        (blocked / 'inner.txt').write_text('x\n', encoding='utf-8')
        subprocess.run(['git', 'add', '-A'], **git_kwargs)
        subprocess.run(['git', 'commit', '-qm', 'add blocked dir'], **git_kwargs)

        account = str(os.environ.get('USERNAME', ''))
        self._blocked_dir = blocked
        denied = subprocess.run(
            ['icacls', str(blocked), '/deny', f'{account}:(OI)(CI)(RX)'], **git_kwargs,
        )
        if denied.returncode != 0:
            self._blocked_dir = None
            self.skipTest(f'icacls 无法设置拒绝权限：{denied.stdout or denied.stderr}')

        raw = subprocess.run(
            ['git', 'status', '--short', '--', 'config/threshold_freeze.json'], **git_kwargs,
        )
        self.assertEqual(raw.stdout.strip(), '')          # 真实 porcelain 为空
        status = registry_git_status(app_dir=self.repo)
        self.assertTrue(status['tracked'])
        self.assertFalse(status['dirty'], f'被 stderr 警告误判成脏：{status}')


class ThresholdFreezeAppWiringTests(unittest.TestCase):
    def test_app_wires_manual_freeze_panel_and_store(self):
        source = (Path(__file__).resolve().parent / 'app.py').read_text(encoding='utf-8-sig')

        self.assertIn('def _render_threshold_freeze_panel(', source)
        self.assertIn('_render_threshold_freeze_panel()', source)
        self.assertIn('ThresholdFreezeStore', source)
        self.assertIn('_refresh_budget_threshold_columns', source)
        self.assertIn('_attach_budget_role_basis(df, day_map, date_str, year=year, shop=shop)', source)
        # 冻结/采纳/清除三个动作都要写登记并刷新阈值列（+1 是函数定义本身）
        self.assertEqual(source.count('_threshold_freeze_store().save('), 2)
        self.assertEqual(source.count('_threshold_freeze_store().clear('), 1)
        self.assertEqual(source.count('_refresh_budget_threshold_columns()'), 4)
        self.assertIn('cur_year', source)
        self.assertIn('cur_shop', source)


if __name__ == '__main__':
    unittest.main()
