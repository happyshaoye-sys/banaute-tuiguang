# -*- coding: utf-8 -*-
"""无界「营销场景报表」读取与分渠道对账（2026-11-16 新增）。

背景
----
无界新增 `营销场景报表_*.zip`（店铺级，一天 4 行）：
`超级直播 / 超级短视频 / 关键词推广 / 人群推广`，含 `花费 / 总成交金额` 等 71 列。
它是**店铺级**口径：超级直播、超级短视频 的花费在 `商品总表` 里没有对应商品行，
所以只靠商品总表会把这两块漏掉（实测 11-17 漏 1,364.70 元 ≈ 6.9%）。

口径（2026-11-16 与运营确认）
--------------------------
* 只用于**看板「分渠道花费」面板**：渠道为 关键词 / 人群 / 超级直播 / 超级短视频 / 全站 / 店铺直达；
* 执行表的商品级 `关键词`/`人群`/`全站`/`店铺直达` 列、`付费`、ROI 一律**不动**（保持商品级口径）；
* 与同窗口的 `计划报表` 花费合计做**相互校验**（两者应完全一致，实测 11-17 均为 19,843.60）；
* 当期窗口内若有任一天缺 `营销场景报表`，**整块回退**到商品总表口径，并在面板上标注来源。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

SCENE_FILE_KEYWORD = '营销场景报表'
PLAN_FILE_KEYWORD = '计划报表'
SCENE_CHANNEL_ORDER = ('关键词', '人群', '超级直播', '超级短视频', '全站', '店铺直达')
LEGACY_CHANNEL_ORDER = ('关键词', '人群', '全站', '店铺直达')
# 营销场景报表里的场景名 → 面板渠道名
SCENE_NAME_TO_CHANNEL = {
    '关键词推广': '关键词',
    '人群推广': '人群',
    '超级直播': '超级直播',
    '超级短视频': '超级短视频',
    '货品全站推广': '全站',
    '店铺直达': '店铺直达',
}
_MONEY_COLUMNS = {'花费': '花费', '总成交金额': '成交'}


def _read_csv_member(zip_path: Path) -> pd.DataFrame:
    """读取无界 zip 里的单个 CSV（GBK）。"""
    with zipfile.ZipFile(zip_path) as zf:
        members = [name for name in zf.namelist() if name.lower().endswith('.csv')]
        if not members:
            raise ValueError(f'{zip_path.name} 内没有 CSV')
        with zf.open(members[0]) as handle:
            frame = pd.read_csv(handle, dtype=str, encoding='gbk')
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def find_report(wujie_root, day: str, keyword: str):
    """在 `<无界>/<day>/` 下找文件名含 keyword 的 zip；没有返回 None。"""
    if not wujie_root:
        return None
    folder = Path(wujie_root) / str(day)
    if not folder.is_dir():
        return None
    candidates = sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() == '.zip' and keyword in path.name
    )
    return candidates[-1] if candidates else None


def read_scene_report(path):
    """读营销场景报表 → DataFrame[场景名字, 花费, 成交]。"""
    frame = _read_csv_member(Path(path))
    if '场景名字' not in frame.columns:
        raise ValueError('营销场景报表缺少「场景名字」列')
    out = pd.DataFrame({
        '场景名字': frame['场景名字'].astype(str).str.strip(),
        '花费': pd.to_numeric(frame.get('花费'), errors='coerce').fillna(0.0),
        '成交': pd.to_numeric(frame.get('总成交金额'), errors='coerce').fillna(0.0),
    })
    return out


def read_plan_report_total(path) -> float:
    """计划报表（计划级）花费合计。"""
    frame = _read_csv_member(Path(path))
    if '花费' not in frame.columns:
        raise ValueError('计划报表缺少「花费」列')
    return float(pd.to_numeric(frame['花费'], errors='coerce').fillna(0.0).sum())


def summarize_scene_channels(wujie_root, days):
    """按窗口汇总营销场景渠道花费，并用计划报表做校验。

    返回字典：
      * ``ok``：True=窗口内每天都有营销场景报表，可用；False=需回退商品总表口径
      * ``channels``：``[{'name', 'val'}]``（按 SCENE_CHANNEL_ORDER 顺序，只含有值或用到的渠道）
      * ``total`` / ``scene_days`` / ``missing_days``
      * ``plan_total`` / ``plan_days`` / ``check``（'一致' / '差异' / '未校验'）
      * ``note``：给界面看的来源与校验说明
    """
    day_list = [str(day) for day in (days or [])]
    result = {
        'ok': False,
        'channels': [],
        'total': 0.0,
        'scene_days': [],
        'missing_days': list(day_list),
        'plan_total': None,
        'plan_days': [],
        'check': '未校验',
        'note': '',
    }
    if not day_list:
        result['note'] = '所选窗口没有日期，分渠道花费回退商品总表口径'
        return result

    scene_totals = {name: 0.0 for name in SCENE_CHANNEL_ORDER}
    missing = []
    found_days = []
    plan_total = 0.0
    plan_days = []
    for day in day_list:
        scene_path = find_report(wujie_root, day, SCENE_FILE_KEYWORD)
        if scene_path is None:
            missing.append(day)
        else:
            try:
                frame = read_scene_report(scene_path)
            except Exception as exc:  # noqa: BLE001 - 单个文件坏了当缺失处理
                print(f'[营销场景] {day} 读取失败：{exc}')
                missing.append(day)
            else:
                found_days.append(day)
                for _, row in frame.iterrows():
                    channel = SCENE_NAME_TO_CHANNEL.get(str(row['场景名字']).strip())
                    scene_totals[channel or str(row['场景名字']).strip()] = (
                        scene_totals.get(channel or str(row['场景名字']).strip(), 0.0)
                        + float(row['花费'])
                    )
        plan_path = find_report(wujie_root, day, PLAN_FILE_KEYWORD)
        if plan_path is not None:
            try:
                plan_total += read_plan_report_total(plan_path)
                plan_days.append(day)
            except Exception as exc:  # noqa: BLE001
                print(f'[营销场景] {day} 计划报表读取失败：{exc}')

    result['scene_days'] = found_days
    result['missing_days'] = missing
    result['plan_days'] = plan_days
    if missing:
        result['note'] = (
            f'当期窗口有 {len(missing)} 天没有营销场景报表'
            f'（{"、".join(missing[:5])}），分渠道花费回退商品总表口径'
        )
        return result

    channels = [{'name': name, 'val': round(scene_totals.get(name, 0.0), 2)}
                for name in SCENE_CHANNEL_ORDER if scene_totals.get(name, 0.0) > 0]
    total = round(sum(item['val'] for item in channels), 2)
    result.update({
        'ok': True,
        'channels': channels,
        'total': total,
        'plan_total': round(plan_total, 2) if plan_days else None,
    })
    parts = [f'来源：营销场景报表（{len(found_days)} 天：{"、".join(found_days[:7])}）']
    if plan_days and plan_total:
        diff = round(total - plan_total, 2)
        if abs(diff) < 0.5:
            result['check'] = '一致'
            parts.append(f'与计划报表（{len(plan_days)} 天）合计一致：¥{plan_total:,.2f}')
        else:
            result['check'] = '差异'
            parts.append(
                f'与计划报表合计 ¥{plan_total:,.2f} 差 {diff:+,.2f} 元，请核对数据源'
            )
    else:
        parts.append('计划报表缺失，未做相互校验')
    result['note'] = '；'.join(parts)
    return result


def legacy_channels_from_frame(df):
    """商品总表口径的渠道花费（回退用），返回 ``[{'name', 'val'}]``。"""
    channels = []
    for name in LEGACY_CHANNEL_ORDER:
        if df is not None and hasattr(df, 'columns') and name in df.columns:
            value = float(pd.to_numeric(df[name], errors='coerce').fillna(0.0).sum())
        else:
            value = 0.0
        channels.append({'name': name, 'val': round(value, 2)})
    return channels


__all__ = [
    'LEGACY_CHANNEL_ORDER',
    'SCENE_CHANNEL_ORDER',
    'SCENE_FILE_KEYWORD',
    'find_report',
    'legacy_channels_from_frame',
    'read_plan_report_total',
    'read_scene_report',
    'summarize_scene_channels',
]
