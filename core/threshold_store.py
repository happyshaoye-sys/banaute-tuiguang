"""预算决策阈值的人工冻结登记。

口径分工：**引擎只算「建议值」，生效的阈值由运营人员确认并冻结**。
登记按「年份|店铺」分别保存（集团店与女装店量级不同，不能共用一套）。

**登记随 git 走**：默认落在仓库内的 `config/threshold_freeze.json`（受版本管理，
换机器/换人都在，改动历史就是 git 提交历史，提交信息里带操作人）。
只有在找不到 git 仓库时（例如第三方打包版离线运行）才回退到工具目录的 `cache/`，
而且 `cache/` 是被 gitignore 的，不会污染仓库。

写失败不阻断主流程；每次冻结/变更都留一条审计记录。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
from threading import Lock
from typing import Mapping

from .budget_role import THRESHOLD_WINDOW_DAYS, THRESHOLD_WINDOWS


FREEZE_FILE_NAME = 'threshold_freeze.json'
#: 登记在仓库内的目录（跟随 git，随仓库共享与审计）
TRACKED_DIR_NAME = 'config'
#: 找不到 git 仓库时的回退目录（被 .gitignore 忽略，仅本机）
FALLBACK_DIR_NAME = 'cache'
#: 冻结来源标记（写入执行表的「阈值来源」列）
FREEZE_SOURCE_MANUAL = '运营冻结'
FREEZE_SOURCE_ENGINE = '引擎建议(未冻结)'
#: 冻结值与引擎建议偏离超过该比例时提示复核（只提示，不自动改）
DEFAULT_RECHECK_TOLERANCE = 0.30
#: 每个店铺保留的变更历史条数
HISTORY_LIMIT = 50

_WRITE_LOCK = Lock()
#: git 把提示信息写在 stderr（如 `warning: could not open directory ...`、CRLF 提示），
#: 而 `core.version_control._run_git` 会把 stdout 与 stderr 拼成一个字符串。
#: 判断"有没有改动"必须只看真正的 porcelain 行，不能把提示当成脏状态。
_GIT_NOTICE_PREFIXES = ('warning:', 'error:', 'hint:', 'fatal:', 'info:', 'note:')


def porcelain_lines(text: str) -> list[str]:
    """从 git 输出里挑出真正的 porcelain 行，忽略 warning/error/hint 等提示行。"""
    lines: list[str] = []
    for raw in str(text or '').splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        lowered = line.strip().lower()
        if any(lowered.startswith(prefix) for prefix in _GIT_NOTICE_PREFIXES):
            continue
        lines.append(line)
    return lines


def _git_capture(
    repo: Path,
    *args: str,
    timeout: int = 30,
) -> tuple[str, str, int]:
    """运行 git 并**分离** stdout / stderr，返回 (stdout, stderr, returncode)。

    不抛出异常：调用方按返回码判断，避免把 git 的提示当成失败。
    """
    env = os.environ.copy()
    env.update({'GIT_TERMINAL_PROMPT': '0', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
    try:
        completed = subprocess.run(
            ['git', *args], cwd=str(repo), env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - git 不可用
        return '', str(exc), 1
    return completed.stdout or '', completed.stderr or '', int(completed.returncode)


class ThresholdFreezeError(ValueError):
    """阈值冻结登记不合法。"""


def default_freeze_root(
    environ: Mapping[str, str] | None = None,
    app_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """登记目录：优先仓库内 config/，其次随包分发的 config/，最后工具目录 cache/。

    v2.54 起，第三方打包版（没有 `.git`）若自带随包分发的
    `config/threshold_freeze.json`，就直接读它——这样"别人发来的已冻结阈值"
    能被继承，而不是被忽略后重新冻结一套。
    """
    env = os.environ if environ is None else environ
    explicit = str(env.get('METOX_THRESHOLD_DIR', '') or '').strip()
    if explicit:
        return Path(explicit)
    base = Path(app_dir) if app_dir is not None else Path(__file__).resolve().parents[1]
    repo = find_repository(base)
    if repo is not None:
        return Path(repo) / TRACKED_DIR_NAME
    packaged = Path(base) / TRACKED_DIR_NAME
    if (packaged / FREEZE_FILE_NAME).exists():
        return packaged
    configured_app_dir = str(env.get('METOX_APP_DIR', '') or '').strip()
    if configured_app_dir:
        return Path(configured_app_dir) / FALLBACK_DIR_NAME
    return base / FALLBACK_DIR_NAME


def find_repository(app_dir: str | os.PathLike[str] | None = None) -> Path | None:
    """复用工具的受控 git 封装查找仓库；不可用时返回 None。"""
    try:
        from .version_control import find_repository as _find_repository
    except Exception:  # pragma: no cover - 理论上不会发生
        return None
    base = Path(app_dir) if app_dir is not None else Path(__file__).resolve().parents[1]
    try:
        return _find_repository(base)
    except Exception:  # pragma: no cover - git 不可用时按"没有仓库"处理
        return None


def freeze_key(year, shop) -> str:
    """登记键：年份|店铺。"""
    return f'{str(year).strip()}|{str(shop).strip()}'


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')


def _positive_number(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ThresholdFreezeError(f'{field} 必须是数字：{value!r}') from exc
    if not math.isfinite(number) or number <= 0:
        raise ThresholdFreezeError(f'{field} 必须是大于 0 的有限数字：{value!r}')
    return number


def _positive_int(value, field: str, default: int) -> int:
    if value in (None, ''):
        return int(default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ThresholdFreezeError(f'{field} 必须是整数：{value!r}') from exc
    if number < 1:
        raise ThresholdFreezeError(f'{field} 必须大于等于 1：{value!r}')
    return number


class ThresholdFreezeStore:
    """阈值冻结登记（JSON 单文件，按 年份|店铺 分键）。"""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root) if root is not None else default_freeze_root()

    @property
    def path(self) -> Path:
        return self.root / FREEZE_FILE_NAME

    # ── 读写 ────────────────────────────────────────────
    def _read_all(self) -> dict:
        path = self.path
        if not path.exists():
            return {}
        try:
            with path.open('r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_all(self, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.json.tmp')
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with _WRITE_LOCK:
            with tmp.open('w', encoding='utf-8', newline='') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)

    # ── 查询 ────────────────────────────────────────────
    def get(self, year, shop) -> dict | None:
        """返回当前生效的冻结记录（没有则 None）。"""
        record = self._read_all().get(freeze_key(year, shop))
        if not isinstance(record, dict) or not record.get('current'):
            return None
        current = record.get('current')
        return dict(current) if isinstance(current, dict) else None

    def history(self, year, shop, limit: int | None = None) -> list[dict]:
        record = self._read_all().get(freeze_key(year, shop))
        if not isinstance(record, dict):
            return []
        items = [item for item in (record.get('history') or []) if isinstance(item, dict)]
        if limit is not None and limit >= 0:
            return items[-limit:]
        return items

    # ── 写入 ────────────────────────────────────────────
    def save(
        self,
        year,
        shop,
        *,
        th_main,
        th_mid,
        operator: str = '',
        note: str = '',
        window_days: int | None = None,
        windows: int | None = None,
        engine: Mapping | None = None,
        effective_from: str = '',
    ) -> dict:
        """冻结/更新阈值。th_mid 必须小于 th_main（80% 覆盖分界必然低于 50% 分界）。"""
        main_value = _positive_number(th_main, 'TH_MAIN')
        mid_value = _positive_number(th_mid, 'TH_MID')
        if mid_value >= main_value:
            raise ThresholdFreezeError(
                f'TH_MID（{mid_value:g}）必须小于 TH_MAIN（{main_value:g}）：'
                'TH_MID 是 80% 覆盖分界，必然低于 50% 覆盖分界'
            )
        resolved_window = _positive_int(window_days, '窗口天数', THRESHOLD_WINDOW_DAYS)
        resolved_windows = _positive_int(windows, '窗口周数', THRESHOLD_WINDOWS)
        engine = dict(engine or {})
        key = freeze_key(year, shop)
        all_records = self._read_all()
        entry = all_records.get(key)
        history = list(entry.get('history') or []) if isinstance(entry, dict) else []

        frozen_at = _now_text()
        stamp = datetime.now().strftime('%Y%m%d')
        version = f'store-manual-{stamp}-{len(history) + 1}'
        current = {
            'TH_MAIN': main_value,
            'TH_MID': mid_value,
            '阈值版本': version,
            '阈值窗口': f'{resolved_window}天',
            '窗口天数': resolved_window,
            '窗口周数': resolved_windows,
            '阈值来源': FREEZE_SOURCE_MANUAL,
            '阈值状态': '已冻结',
            '冻结人': str(operator or '').strip() or '未署名',
            '冻结时间': frozen_at,
            '生效日': str(effective_from or '').strip() or stamp,
            '备注': str(note or '').strip(),
            '引擎建议TH_MAIN': engine.get('TH_MAIN'),
            '引擎建议TH_MID': engine.get('TH_MID'),
            '引擎建议参照日': engine.get('阈值参照日') or engine.get('参照日') or '',
            '引擎建议窗口周数': engine.get('阈值周数') or engine.get('窗口周数'),
        }
        for field, value in (
            ('引擎建议TH_MAIN', current['引擎建议TH_MAIN']),
            ('引擎建议TH_MID', current['引擎建议TH_MID']),
        ):
            if value is not None:
                current[field] = _positive_number(value, field)
        history.append(dict(current))
        all_records[key] = {
            'year': str(year),
            'shop': str(shop),
            'current': current,
            'history': history[-HISTORY_LIMIT:],
        }
        self._write_all(all_records)
        return current

    def clear(self, year, shop, *, operator: str = '', note: str = '') -> bool:
        """清除冻结：之后回到「引擎建议(未冻结)」。返回是否真的清除了。"""
        key = freeze_key(year, shop)
        all_records = self._read_all()
        entry = all_records.get(key)
        if not isinstance(entry, dict) or not entry.get('current'):
            return False
        history = list(entry.get('history') or [])
        history.append({
            '操作': '清除冻结',
            '冻结人': str(operator or '').strip() or '未署名',
            '冻结时间': _now_text(),
            '备注': str(note or '').strip(),
            '原TH_MAIN': (entry.get('current') or {}).get('TH_MAIN'),
            '原TH_MID': (entry.get('current') or {}).get('TH_MID'),
        })
        all_records[key] = {
            'year': str(year),
            'shop': str(shop),
            'current': None,
            'history': history[-HISTORY_LIMIT:],
        }
        self._write_all(all_records)
        return True


def registry_git_status(root=None, app_dir: str | os.PathLike[str] | None = None) -> dict:
    """登记文件的 git 状态：是否在仓库内、相对路径、是否已跟踪、是否有未提交改动。"""
    store_root = Path(root) if root is not None else default_freeze_root(app_dir=app_dir)
    path = store_root / FREEZE_FILE_NAME
    status = {
        'path': str(path),
        'root': str(store_root),
        '追踪目录': store_root.name,
        'in_repo': False,
        'repo': '',
        'relative_path': '',
        'tracked': False,
        'dirty': False,
        '错误': '',
    }
    repo = find_repository(app_dir if app_dir is not None else store_root)
    if repo is None:
        status['错误'] = '未找到 git 仓库：登记仅保存在本机（cache/ 已被 gitignore）'
        return status
    status['in_repo'] = True
    status['repo'] = str(repo)
    try:
        relative = path.resolve().relative_to(Path(repo).resolve())
    except ValueError:
        status['错误'] = f'登记目录不在仓库内：{store_root}'
        return status
    status['relative_path'] = relative.as_posix()
    repo_path = Path(repo)
    # 已跟踪：以 git 的返回码为准（未跟踪时 stdout 为空、returncode 非 0）
    _, _, tracked_code = _git_capture(
        repo_path, 'ls-files', '--error-unmatch', '--', status['relative_path'],
    )
    status['tracked'] = tracked_code == 0
    # 脏状态：只看 stdout 里的 porcelain 行；stderr 的 warning/hint 不算改动
    stdout, stderr, code = _git_capture(
        repo_path, 'status', '--short', '--', status['relative_path'],
    )
    status['dirty'] = bool(porcelain_lines(stdout))
    if code != 0 and not status['dirty'] and stderr.strip():
        status['错误'] = stderr.strip()
    return status


def freeze_commit_message(current: Mapping | None, *, action: str = '冻结') -> str:
    """提交信息：把操作人、阈值与来源写清楚，便于 git 历史审计。"""
    record = dict(current or {})
    operator = str(record.get('冻结人') or '').strip() or '未署名'
    if action == '清除':
        return f'清除预算决策阈值冻结（{operator}）'
    main = record.get('TH_MAIN')
    mid = record.get('TH_MID')
    note = str(record.get('备注') or '').strip()
    text = f'冻结预算决策阈值 TH_MAIN={main}、TH_MID={mid}（{operator}）'
    return f'{text}：{note}' if note else text


def commit_freeze_registry(
    root=None,
    *,
    app_dir: str | os.PathLike[str] | None = None,
    message: str,
    push: bool = False,
    timeout: int = 60,
) -> dict:
    """把登记文件提交到 git（可选推送）。返回执行结果，失败只报错不抛出。"""
    result = {'ok': False, 'commit': '', 'pushed': False, 'message': message, 'detail': ''}
    status = registry_git_status(root, app_dir=app_dir)
    if not status['in_repo'] or not status['relative_path']:
        result['detail'] = status['错误'] or '登记不在 git 仓库内，跳过提交'
        return result
    repo = Path(status['repo'])
    relative = status['relative_path']
    try:
        _, stderr, code = _git_capture(repo, 'add', '--', relative, timeout=timeout)
        if code != 0:
            result['detail'] = stderr.strip() or 'git add 失败'
            return result
        staged_stdout, _, _ = _git_capture(
            repo, 'diff', '--cached', '--name-only', '--', relative, timeout=timeout,
        )
        if not porcelain_lines(staged_stdout):
            result['detail'] = '登记内容没有变化，无需提交'
            result['ok'] = True
            return result
        _, commit_stderr, commit_code = _git_capture(repo, 'commit', '-m', message, timeout=timeout)
        if commit_code != 0:
            result['detail'] = commit_stderr.strip() or 'git commit 失败'
            return result
        head_stdout, _, _ = _git_capture(repo, 'rev-parse', '--short', 'HEAD', timeout=timeout)
        result['commit'] = head_stdout.strip()
        result['ok'] = True
        if push:
            push_stdout, push_stderr, push_code = _git_capture(repo, 'push', timeout=timeout)
            combined = f'{push_stdout}\n{push_stderr}'.lower()
            result['pushed'] = push_code == 0 and 'rejected' not in combined
            result['detail'] = (push_stdout.strip() or push_stderr.strip() or '已推送')
            if not result['pushed']:
                result['message'] = f'{message}（已提交 {result["commit"]}，推送未成功）'
        else:
            result['detail'] = f'已提交 {result["commit"]}（未推送）'
    except Exception as exc:
        result['detail'] = str(exc)
    return result


__all__ = [
    'FREEZE_FILE_NAME',
    'FREEZE_SOURCE_ENGINE',
    'FREEZE_SOURCE_MANUAL',
    'DEFAULT_RECHECK_TOLERANCE',
    'FALLBACK_DIR_NAME',
    'HISTORY_LIMIT',
    'TRACKED_DIR_NAME',
    'ThresholdFreezeError',
    'ThresholdFreezeStore',
    'commit_freeze_registry',
    'default_freeze_root',
    'find_repository',
    'freeze_commit_message',
    'freeze_key',
    'porcelain_lines',
    'registry_git_status',
]
