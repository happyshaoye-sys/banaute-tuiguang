"""商品预算决策 · 阈值层 + 角色层（可选增强，默认不介入既有决策）。

本模块把「覆盖法阈值（TH_MAIN / TH_MID）+ 五桶角色瀑布 + 风险闸门」这套算法，
作为 ``core.budget_decision`` 的可选闸门层嵌套进去，用来回答一个既有模块回答不了的
问题：**这笔钱该不该加、加给谁**。

三条硬约束（改造时必须遵守）
--------------------------------
1. **只读**：本模块不修改入参 DataFrame，只返回新的字典 / 新的列表。
2. **默认关闭**：角色层由 :data:`ROLE_LAYER_ENABLED` 控制，关闭时
   ``core.budget_decision`` 的输出与 v2.40 完全一致（回归零变化）。
3. **只做减法**：角色层只能「拦截加投」或「给加投封顶」，
   不能凭空创造加投额度；回收仍然由既有错配逻辑与硬风险规则负责。

口径提醒（容易搞错）
--------------------
* 阈值与角色判定用的 **日销7 是金额口径**（近7日日均GMV = 窗口GMV ÷ 7），
  与 :data:`core.schema.FIELD_ALIASES` 里件数口径的 ``日销7`` 不是一回事，
  不要混用。
* 阈值判定一律用 float 全精度比较，展示时才四舍五入（71.75714285714285 而不是 71.76）。
* 盈亏平衡线、库存字段**不参与**角色判定，只做展示提示（业务口径约定）。
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime

import pandas as pd

from .schema import safe_div, to_num


# ── 开关与版本 ────────────────────────────────────────────
#: 角色层总开关。默认 False：库层面的既有调用方行为完全不变（含导出兜底路径）。
#: MetoX 工具侧不改这个常量，而是在调用处显式 opt-in：
#: app.py 的 `_build_budget_decision_context_cached` 与
#: `_call_export_all_sheets_with_budget_context` 都传 `roles=True`，
#: 保证「看板看到的」与「导出的」一致。
#: 回滚 = 把这两处传参去掉（或传 False），不需要回退数据或表结构。
ROLE_LAYER_ENABLED = False

ROLE_ALGORITHM_VERSION = 'role-waterfall-v4'

#: 阈值默认口径：7 天窗口，**固定**，不随工具侧边栏「时间周期」变化。
#: 界面上的 当日/3天/7天/15天/30天 只是看数口径；角色判定与预算决策
#: （未来7日）统一锚定近 7 日，避免换个显示周期就换一套角色结论。
THRESHOLD_WINDOW_DAYS = 7
THRESHOLD_WINDOWS = 8


def threshold_version(window_days: int = THRESHOLD_WINDOW_DAYS, windows: int = THRESHOLD_WINDOWS) -> str:
    """阈值版本号：自带窗口长度与窗口数，便于回溯是哪一套阈值。"""
    return f'store-coverage-{int(window_days)}d-{int(windows)}w-v1'


#: 默认（7天 × 8窗口）版本号。
THRESHOLD_VERSION = threshold_version()

# ── 阈值层参数 ────────────────────────────────────────────
THRESHOLD_WEEKS = THRESHOLD_WINDOWS
TH_MAIN_SHARE = 0.5
TH_MID_SHARE = 0.8
#: 有效周数低于此值时只给提示，不阻断判定。
THRESHOLD_MIN_WEEKS = 4
#: 阈值合理性区间（集团店女装量级），超出只警告不自动裁剪。
TH_MAIN_RANGE = (250.0, 1000.0)
TH_MID_RANGE = (50.0, 110.0)
#: 月度复核触发线：相对变化超过该比例即建议换阈值并升版本号。
RECHECK_REL_TOLERANCE_MAIN = 0.05
RECHECK_REL_TOLERANCE_MID = 0.08

#: 冻结期漂移带：冻结阈值期间**不要求**每周仍严格覆盖 50% / 80%，
#: 只要求落在这三条带内；越带才提示复核。
#: 默认带按**工具当前采用的「8 周中位数冻结」**标定（2026-09-09 实测包络：
#: ZT 37.0%~60.4%、ZT+TD 74.9%~83.1%、ZT款数占比 3.4%~5.1%，各留约 5pp 余量）。
#: 若改回「最新一周分界冻结」的复盘口径，对应带是 ZT 40%~70%、ZT+TD 75%~87%、
#: 款数占比 4%~8%，调用时用 `bands=` 传入即可。
DRIFT_BANDS = {
    'ZT_GMV覆盖': (0.32, 0.65),
    'ZT_TD_GMV覆盖': (0.70, 0.87),
    'ZT款数占比': (0.02, 0.07),
}

# ── 角色层参数 ────────────────────────────────────────────
ROLES = (
    'ZT主推',
    'ZT趋势预警',
    'TC投产',
    'TD梯队',
    'TD候选',
    'CS测试',
    'QC回收',
    '清理',
    '观察',
    '未判定',
)
ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}

GATE_ALLOW = '放行加投'
GATE_BLOCK = '禁止加投'
GATE_CAP = '测试封顶'

#: 角色层新增列。刻意避开 ``budget_decision`` 里已退役的「预算角色 / 角色状态 / 角色原因 /
#: 角色风险」等列名，也不会被 ``is_removed_budget_column`` 判为退役字段。
ROLE_LAYER_COLUMNS = (
    '决策角色',
    '决策角色依据',
    '角色闸门',
    '角色闸门依据',
    '风险命中',
)

#: 硬风险门槛：零成交且花费达到该金额，判为「清理」并禁止加投。
QC_SPEND_MIN = 200.0
#: 新品测试期：上架天数小于等于该值判为 CS 测试。
CS_NEW_DAYS = 30
#: CS 测试单周期加投上限（元 / 未来7日）。
CS_WEEKLY_CAP = 500.0
#: 趋势预警线：主推款趋势跌破该值即禁止加投。
TREND_ALERT = -0.10
#: 中腰段有效访客下限（占位值，需业务确认后固化为配置）。
MIN_UV = 20.0
#: TC 投产的错配触发比：花费份额 < 销售份额 × 该系数。
TC_SPEND_SHARE_FACTOR = 0.5
#: 成熟口径：上架满该天数视为成熟款。
MATURE_DAYS = 60
#: 成熟口径：近7日有成交天数达到该值视为成熟款（上架时间缺失时的兜底）。
MATURE_ACTIVE_DAYS = 5
#: 类目加购率中位数的最少样本数；不足则不参与判档（避免小类目误杀）。
CATEGORY_MIN_SAMPLES = 5

#: 内部钩子：调用方（如 ``budget_decision``）可按行注入「该行口径天数」，
#: 用于把周期快照GMV折算成金额口径日销7，避免全店天数掩盖单商品天数。
ROLE_DAYS_KEY = '_角色周期天数'

_MATURE_TOKENS = ('成熟', '稳定', '承接', '日常')
_CLEAROUT_TOKENS = ('清退', '清仓', '衰退', '淘汰', '观察期', '清退期')
#: 清仓护栏（v2.54）：`衰退预警` 是 36 宫格位置变化产生的**弱信号**，单独命中不足以
#: 判定清仓。款若仍在腰线以上、或环比没有下跌，就放行给正常瀑布判定——否则会出现
#: 「日销7 是主推线 2.8 倍、环比 +75% 」的款被判清仓并禁止加投的误杀。
#: 只对 `衰退预警` 生效；`清退/清仓/衰退期/淘汰/观察期` 等强标记仍然直接判清仓。
CLEAROUT_GUARD_TOKEN = '衰退预警'
CLEAROUT_GUARD_KEY = '角色_清仓护栏'
_REFUND_RISK_TOKENS = ('退款', '退货', '品质')
_RISK_FIELDS = ('风险标签', '退款风险', '风控标签', '预算风险')


def _text(value) -> str:
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return '' if text in ('', 'nan', 'None', '<NA>', 'NaT') else text


def _blank(value) -> bool:
    return _text(value) == ''


def _to_date(value) -> date | None:
    if value is None or _blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    stamp = pd.to_datetime(_text(value), errors='coerce')
    if stamp is pd.NaT or pd.isna(stamp):
        return None
    return stamp.date()


# ── 阈值层：覆盖法 ────────────────────────────────────────
@dataclass(frozen=True)
class ThresholdResult:
    """覆盖法阈值结果。全精度保存，展示时再四舍五入。

    ``window_days`` 是这套阈值对应的**周期长度**（1/3/7/15/30），
    必须与工具侧边栏所选「时间周期」一致，判定才成立。
    """

    th_main: float
    th_mid: float
    version: str
    weeks: int
    end_date: str
    weekly: tuple[dict, ...] = ()
    warnings: tuple[str, ...] = ()
    window_days: int = THRESHOLD_WINDOW_DAYS
    state: str = ''

    @property
    def valid(self) -> bool:
        return self.th_main > 0 and self.th_mid > 0

    @property
    def window_label(self) -> str:
        return f'{int(self.window_days)}天'

    def as_dict(self) -> dict:
        """写成执行表可直接使用的透传列（阈值* 列在商品预算视图里默认隐藏）。"""
        return {
            'TH_MAIN': self.th_main,
            'TH_MID': self.th_mid,
            '阈值版本': self.version,
            '阈值窗口': self.window_label,
            '阈值周数': self.weeks,
            '阈值状态': self.state or ('数据不足' if not self.valid else '正常'),
            '阈值参照日': self.end_date,
            '阈值警告': '；'.join(self.warnings),
        }


def boundary_for_share(values: Sequence[float], share: float) -> float | None:
    """覆盖法分界值：按日销7降序累加，取第一个使累计GMV覆盖达到 ``share`` 的款的日销7。

    返回 **原始精度** 的 float；累计口径是窗口GMV（不是款数）。没有正GMV时返回 None。
    """
    positive = sorted((float(v) for v in values if float(v) > 0), reverse=True)
    total = sum(positive)
    if total <= 0 or not positive:
        return None
    target = total * float(share)
    cumulative = 0.0
    for value in positive:
        cumulative += value
        if cumulative >= target:
            return value
    return positive[-1]


def period_windows(
    end: date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
) -> list[tuple[date, date]]:
    """按固定窗口长度切滚动窗口：W0 = [end-window_days+1, end]，逐窗前移。

    默认 7 天：W0=09-03~09-09、W1=08-27~09-02，依此往前 8 个窗口。
    """
    span = max(int(window_days), 1)
    out: list[tuple[date, date]] = []
    for index in range(max(int(windows), 1)):
        window_end = (pd.Timestamp(end) - pd.Timedelta(days=span * index))
        start = window_end - pd.Timedelta(days=span - 1)
        out.append((start.date(), window_end.date()))
    return out


def _window_daily_gmv(
    daily: pd.DataFrame,
    start: date,
    end: date,
    columns: dict,
    window_days: int,
) -> dict[str, float]:
    """窗口内每个宝贝的**金额口径日均GMV** = 窗口GMV ÷ 窗口天数。

    除以窗口天数后，日/3日/7日/15日/30日 五种周期得到的是同一量纲
    （元/天），可以直接与同一窗口算出的 TH_MAIN / TH_MID 比较。
    """
    date_column = columns['date']
    product_column = columns['product']
    gmv_column = columns['gmv']
    stamps = daily[date_column]
    mask = (stamps >= pd.Timestamp(start)) & (stamps <= pd.Timestamp(end))
    window = daily.loc[mask]
    if window.empty:
        return {}
    grouped = window.groupby(product_column, sort=False)[gmv_column].sum()
    divisor = float(max(int(window_days), 1))
    return {
        str(product_id): to_num(value) / divisor
        for product_id, value in grouped.items()
        if to_num(value) > 0
    }


def _thresholds_from_window_values(
    window_values: Sequence[dict],
    *,
    window_days: int,
    windows: int,
    end_date,
    main_share: float = TH_MAIN_SHARE,
    mid_share: float = TH_MID_SHARE,
    version: str | None = None,
) -> ThresholdResult:
    """从「每个窗口的日均GMV分布」算覆盖法阈值：逐窗取分界，再取窗口中位数。"""
    resolved_version = version or threshold_version(window_days, windows)
    weekly: list[dict] = []
    drift_input: list[tuple[str, list[float]]] = []
    main_boundaries: list[float] = []
    mid_boundaries: list[float] = []
    for index, item in enumerate(window_values):
        label = item.get('label') or f'W{index}'
        start, window_end = item.get('start'), item.get('end')
        values = list(item.get('values') or ())
        row = {
            '周次': label,
            '窗口天数': int(window_days),
            '起始日': start.isoformat() if hasattr(start, 'isoformat') else str(start or ''),
            '结束日': window_end.isoformat() if hasattr(window_end, 'isoformat') else str(window_end or ''),
            '有效款数': len(values),
            '窗口GMV': sum(values),
            'TH_MAIN': None,
            'TH_MID': None,
            '提示': '',
        }
        if not values:
            row['提示'] = '该窗口无正GMV数据'
            weekly.append(row)
            continue
        main_boundary = boundary_for_share(values, main_share)
        mid_boundary = boundary_for_share(values, mid_share)
        if main_boundary is not None:
            main_boundaries.append(main_boundary)
        if mid_boundary is not None:
            mid_boundaries.append(mid_boundary)
        row['TH_MAIN'] = main_boundary
        row['TH_MID'] = mid_boundary
        weekly.append(row)
        drift_input.append((label, values))

    warnings: list[str] = []
    if len(main_boundaries) < THRESHOLD_MIN_WEEKS:
        warnings.append(f'有效窗口仅{len(main_boundaries)}个，阈值仅作参考')
    th_main = float(statistics.median(main_boundaries)) if main_boundaries else 0.0
    th_mid = float(statistics.median(mid_boundaries)) if mid_boundaries else 0.0
    if th_main and not (TH_MAIN_RANGE[0] <= th_main <= TH_MAIN_RANGE[1]):
        warnings.append(f'TH_MAIN={th_main:.4f} 超出常见区间 {TH_MAIN_RANGE}，请核对取数口径')
    if th_mid and not (TH_MID_RANGE[0] <= th_mid <= TH_MID_RANGE[1]):
        warnings.append(f'TH_MID={th_mid:.4f} 超出常见区间 {TH_MID_RANGE}，请核对取数口径')
    if int(window_days) <= 1:
        warnings.append(
            '所选周期为「当日」：单日GMV波动大，覆盖法阈值稳定性差；'
            f'建议改用 3天/7天 口径判定，或把窗口数提高到 {max(int(windows), 14)} 个以上'
        )

    result = ThresholdResult(
        th_main=th_main,
        th_mid=th_mid,
        version=resolved_version,
        weeks=len(main_boundaries),
        end_date=end_date.isoformat() if hasattr(end_date, 'isoformat') else str(end_date or ''),
        weekly=tuple(weekly),
        warnings=tuple(warnings),
        window_days=int(window_days),
    )
    return _merge_drift_metrics(result, weekly, drift_input)


def compute_coverage_thresholds(
    daily_df: pd.DataFrame,
    *,
    end_date=None,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
    weeks: int | None = None,
    main_share: float = TH_MAIN_SHARE,
    mid_share: float = TH_MID_SHARE,
    date_column: str = '统计日期',
    product_column: str = '宝贝ID',
    gmv_column: str = 'GMV',
) -> ThresholdResult:
    """按覆盖法计算 TH_MAIN / TH_MID：逐窗口取分界值，再取窗口中位数（全精度）。

    ``window_days`` 必须等于工具侧边栏所选的周期长度（当日=1、3天=3、7天=7、
    15天=15、30天=30）。传入的应当是**按日**的商品级明细（一行 = 一个宝贝
    一条日期），例如 ``商品-经营投产比-YYYY-MM-DD`` 每日导出合并后的长表。
    只用到 GMV，不需要花费字段。
    """
    resolved_windows = int(windows if weeks is None else weeks)
    version = threshold_version(window_days, resolved_windows)
    empty_window_days = int(window_days)
    if daily_df is None or len(daily_df) == 0:
        return ThresholdResult(
            0.0, 0.0, version, 0, '', (), ('缺少可用的日粒度数据',),
            window_days=empty_window_days, state='数据不足',
        )
    missing = [
        name for name in (date_column, product_column, gmv_column) if name not in daily_df.columns
    ]
    if missing:
        return ThresholdResult(
            0.0, 0.0, version, 0, '', (), (f'缺少列：{"、".join(missing)}',),
            window_days=empty_window_days, state='数据不足',
        )

    daily = daily_df.loc[:, [date_column, product_column, gmv_column]].copy()
    daily[date_column] = pd.to_datetime(daily[date_column], errors='coerce')
    daily = daily.dropna(subset=[date_column])
    if daily.empty:
        return ThresholdResult(
            0.0, 0.0, version, 0, '', (), ('缺少可用的日粒度数据',),
            window_days=empty_window_days, state='数据不足',
        )
    daily['_gmv'] = daily[gmv_column].map(to_num)
    columns = {'date': date_column, 'product': product_column, 'gmv': '_gmv'}

    if end_date is None:
        anchor = daily[date_column].max().date()
    else:
        parsed = _to_date(end_date)
        anchor = parsed or daily[date_column].max().date()

    window_values: list[dict] = []
    for index, (start, window_end) in enumerate(
        period_windows(anchor, window_days, resolved_windows)
    ):
        window_values.append({
            'label': f'W{index}',
            'start': start,
            'end': window_end,
            'values': list(_window_daily_gmv(daily, start, window_end, columns, window_days).values()),
        })
    return _thresholds_from_window_values(
        window_values,
        window_days=window_days,
        windows=resolved_windows,
        end_date=anchor,
        main_share=main_share,
        mid_share=mid_share,
        version=version,
    )


def period_window_gmv_from_day_map(
    day_map,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
    product_column: str = '宝贝ID',
    gmv_column: str = 'GMV',
    trading_period_column: str = '转化周期',
    trading_period_value: str = '15天转化',
) -> list[dict]:
    """工具内 ``day_map``（日期 → 日帧）→ 每个窗口的宝贝GMV。

    ``day_map[day]`` 由 ``load_period_data`` 产生：取第 0 项为日帧（DataFrame）。
    与复盘口径一致：当日帧含 ``转化周期`` 列时，只取 15 天转化行，避免花费翻倍。
    """
    anchor = _to_date(end_date)
    if anchor is None:
        return []
    out: list[dict] = []
    for index, (start, window_end) in enumerate(period_windows(anchor, window_days, windows)):
        totals: dict[str, float] = {}
        for day in pd.date_range(start=start, end=window_end, freq='D'):
            entry = day_map.get(day.strftime('%Y-%m-%d')) if hasattr(day_map, 'get') else None
            frame = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            values = gmv_by_product(
                frame,
                product_column=product_column,
                gmv_column=gmv_column,
                trading_period_column=trading_period_column,
                trading_period_value=trading_period_value,
            )
            for product_id, value in values.items():
                totals[product_id] = totals.get(product_id, 0.0) + to_num(value)
        divisor = float(max(int(window_days), 1))
        out.append({
            'label': f'W{index}',
            'start': start,
            'end': window_end,
            'values': [value / divisor for value in totals.values() if value > 0],
        })
    return out


def compute_coverage_thresholds_from_windows(
    window_values: Sequence[dict],
    *,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int | None = None,
    end_date=None,
    main_share: float = TH_MAIN_SHARE,
    mid_share: float = TH_MID_SHARE,
) -> ThresholdResult:
    """直接传「每窗日均GMV分布」算阈值（供脚本或自定义取数使用）。

    每项形如 ``{'label': 'W0', 'start': date, 'end': date, 'values': [日均GMV, ...]}``。
    """
    items = [dict(item) for item in (window_values or [])]
    resolved_windows = int(windows if windows is not None else (len(items) or THRESHOLD_WINDOWS))
    if end_date is None:
        ends = [item.get('end') for item in items if item.get('end') is not None]
        end_date = max(ends) if ends else ''
    return _thresholds_from_window_values(
        items,
        window_days=window_days,
        windows=resolved_windows,
        end_date=end_date,
        main_share=main_share,
        mid_share=mid_share,
    )


def _clean_id(value) -> str:
    """宝贝ID 归一化：去掉 Excel 数值单元格带来的 '.0'，避免注入时对不上。"""
    text = _text(value)
    if text.endswith('.0') and text[:-2].isdigit():
        return text[:-2]
    return text


def gmv_by_product(
    frame,
    *,
    product_column: str = '宝贝ID',
    gmv_column: str = 'GMV',
    trading_period_column: str = '转化周期',
    trading_period_value: str = '15天转化',
) -> dict[str, float]:
    """单日日帧 → ``{宝贝ID: GMV}``（阈值口径唯一入口）。

    与复盘口径一致：当日帧含 ``转化周期`` 列时，只取 15 天转化行，避免花费翻倍。
    ``day_map`` 入口与轻量 GMV 缓存入口都走这里，保证两条路径逐位一致。
    """
    if frame is None or len(frame) == 0:
        return {}
    if product_column not in frame.columns or gmv_column not in frame.columns:
        return {}
    subset = frame
    if trading_period_column in frame.columns:
        mask = frame[trading_period_column].astype(str).str.strip() == trading_period_value
        if mask.any():
            subset = frame.loc[mask]
    totals: dict[str, float] = {}
    for product_id, value in subset.groupby(product_column, sort=False)[gmv_column].sum().items():
        key = _clean_id(product_id)
        totals[key] = totals.get(key, 0.0) + to_num(value)
    return totals


def gmv_daily_from_day_map(day_map, *, dates=None) -> dict[str, dict[str, float]]:
    """``day_map`` → ``{日期: {宝贝ID: GMV}}``（轻量历史缓存的存储形态）。

    ``dates`` 给了就只取这些日期（用于只沉淀本次真正加载过的天）。
    """
    out: dict[str, dict[str, float]] = {}
    if not hasattr(day_map, 'get'):
        return out
    wanted = list(dates) if dates is not None else sorted(day_map.keys())
    for day in wanted:
        key = str(day)[:10]
        entry = day_map.get(key)
        if entry is None:
            entry = day_map.get(str(day))
        frame = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        values = gmv_by_product(frame)
        if values:
            out[key] = values
    return out


def period_window_gmv_from_gmv_daily(
    gmv_daily,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
) -> list[dict]:
    """轻量 GMV 缓存（``{日期: {宝贝ID: GMV}}``）→ 每个窗口的宝贝 GMV。"""
    anchor = _to_date(end_date)
    if anchor is None:
        return []
    getter = gmv_daily.get if hasattr(gmv_daily, 'get') else (lambda _key: None)
    out: list[dict] = []
    for index, (start, window_end) in enumerate(period_windows(anchor, window_days, windows)):
        totals: dict[str, float] = {}
        for day in pd.date_range(start=start, end=window_end, freq='D'):
            values = getter(day.strftime('%Y-%m-%d'))
            if not values:
                continue
            for product_id, value in values.items():
                key = _clean_id(product_id)
                totals[key] = totals.get(key, 0.0) + to_num(value)
        divisor = float(max(int(window_days), 1))
        out.append({
            'label': f'W{index}',
            'start': start,
            'end': window_end,
            'values': [value / divisor for value in totals.values() if value > 0],
        })
    return out


def compute_coverage_thresholds_from_gmv_daily(
    gmv_daily,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
    main_share: float = TH_MAIN_SHARE,
    mid_share: float = TH_MID_SHARE,
) -> ThresholdResult:
    """轻量 GMV 缓存入口：与 ``compute_coverage_thresholds_from_day_map`` 同口径同结果。"""
    return compute_coverage_thresholds_from_windows(
        period_window_gmv_from_gmv_daily(
            gmv_daily, end_date=end_date, window_days=window_days, windows=windows,
        ),
        window_days=window_days,
        windows=windows,
        end_date=end_date,
        main_share=main_share,
        mid_share=mid_share,
    )


def _sum_window_gmv(
    day_map,
    start: date,
    end: date,
    *,
    product_column: str,
    gmv_column: str,
    trading_period_column: str,
    trading_period_value: str,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for day in pd.date_range(start=start, end=end, freq='D'):
        entry = day_map.get(day.strftime('%Y-%m-%d')) if hasattr(day_map, 'get') else None
        frame = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        # day_map 已在 merge 阶段按 15 天转化过滤；gmv_by_product 里再兜一次，避免花费/成交翻倍。
        values = gmv_by_product(
            frame,
            product_column=product_column,
            gmv_column=gmv_column,
            trading_period_column=trading_period_column,
            trading_period_value=trading_period_value,
        )
        for product_id, value in values.items():
            totals[product_id] = totals.get(product_id, 0.0) + to_num(value)
    return totals


def compute_rolling_daily_gmv(
    day_map,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    product_column: str = '宝贝ID',
    gmv_column: str = 'GMV',
    trading_period_column: str = '转化周期',
    trading_period_value: str = '15天转化',
) -> dict:
    """固定近 7 日口径的金额日均GMV + 上一 7 日 + 趋势率（与界面周期无关）。

    返回 ``{'current': {pid: 日均GMV}, 'previous': {...}, 'trend': {pid: 趋势率},
    'window_days': 7, 'current_range': '2026-09-03~2026-09-09', ...}``。
    """
    anchor_date = _to_date(end_date)
    if anchor_date is None:
        return {'current': {}, 'previous': {}, 'trend': {}, 'window_days': int(window_days)}
    anchor = pd.Timestamp(anchor_date)
    span = max(int(window_days), 1)
    current_end = anchor
    current_start = anchor - pd.Timedelta(days=span - 1)
    previous_end = current_start - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(days=span - 1)
    kwargs = {
        'product_column': product_column,
        'gmv_column': gmv_column,
        'trading_period_column': trading_period_column,
        'trading_period_value': trading_period_value,
    }
    current_raw = _sum_window_gmv(day_map, current_start, current_end, **kwargs)
    previous_raw = _sum_window_gmv(day_map, previous_start, previous_end, **kwargs)
    divisor = float(span)
    current = {key: value / divisor for key, value in current_raw.items()}
    previous = {key: value / divisor for key, value in previous_raw.items()}
    trend = {
        key: safe_div(current.get(key, 0.0) - value, value)
        for key, value in previous.items()
        if value > 0
    }
    return {
        'current': current,
        'previous': previous,
        'trend': trend,
        'window_days': span,
        'current_range': f'{current_start.date()}~{current_end.date()}',
        'previous_range': f'{previous_start.date()}~{previous_end.date()}',
    }


def inject_rolling_daily_gmv(
    df,
    day_map,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    product_column: str = '宝贝ID',
) -> pd.DataFrame:
    """把固定近 7 日口径的日均GMV 三列写入执行表（返回副本，不改入参）。

    写入 ``近7日日均GMV / 上一7日日均GMV / GMV趋势率`` —— 这三列正是
    :data:`core.budget_decision.PASSTHROUGH_PRODUCT_COLUMNS` 期待、但工具此前无人计算的口径。
    写进去之后，无论侧边栏选 当日/3天/7天/15天/30天，角色判定用的都是同一条近 7 日基准。
    """
    if df is None:
        return df
    source = df.copy(deep=True)
    if product_column not in source.columns:
        return source
    rolling = compute_rolling_daily_gmv(day_map, end_date=end_date, window_days=window_days)
    keys = source[product_column].map(_clean_id)
    # 未在窗口内出现的款一律写 0（近7日确实没成交），而不是留空：
    # 留空会让角色层退回「周期GMV ÷ 周期天数」，把没有近期成交的款算成有量。
    source['近7日日均GMV'] = keys.map(rolling['current']).astype(float).fillna(0.0)
    source['上一7日日均GMV'] = keys.map(rolling['previous']).astype(float).fillna(0.0)
    # 趋势率在上一周期为 0 时无定义，保持空值，交给角色层按"无趋势约束"处理
    source['GMV趋势率'] = keys.map(rolling['trend'])
    return source


def compute_coverage_thresholds_from_day_map(
    day_map,
    *,
    end_date,
    window_days: int = THRESHOLD_WINDOW_DAYS,
    windows: int = THRESHOLD_WINDOWS,
    main_share: float = TH_MAIN_SHARE,
    mid_share: float = TH_MID_SHARE,
) -> ThresholdResult:
    """工具侧入口：``day_map`` + 固定 7 天窗口 → TH_MAIN / TH_MID。"""
    return compute_coverage_thresholds_from_windows(
        period_window_gmv_from_day_map(
            day_map, end_date=end_date, window_days=window_days, windows=windows,
        ),
        window_days=window_days,
        windows=windows,
        end_date=end_date,
        main_share=main_share,
        mid_share=mid_share,
    )


def _merge_drift_metrics(
    result: ThresholdResult,
    weekly: list[dict],
    weekly_values: Sequence[tuple[str, Sequence[float]]],
) -> ThresholdResult:
    """用「冻结值 = 本次窗口中位数」回放各窗口，把漂移带指标并回 weekly。"""
    if not result.valid:
        return replace(result, state='数据不足')
    drift = freeze_drift_report(weekly_values, th_main=result.th_main, th_mid=result.th_mid)
    by_week = {row['周次']: row for row in drift['rows']}
    merged: list[dict] = []
    for week in weekly:
        row = dict(week)
        drift_row = by_week.get(row['周次'])
        for name in ('ZT_GMV覆盖', 'ZT_TD_GMV覆盖', 'ZT款数占比', 'ZT款数', 'ZT+TD款数', '状态'):
            row[name] = '' if drift_row is None else drift_row[name]
        merged.append(row)
    state = '正常' if not drift['越带周数'] else f'存在越带({drift["越带周数"]}窗口)'
    return replace(result, weekly=tuple(merged), state=state)


def freeze_drift_report(
    weekly_values: Iterable[tuple[str, Sequence[float]]],
    *,
    th_main: float,
    th_mid: float,
    bands: dict | None = None,
) -> dict:
    """冻结阈值期间的漂移带检查。

    传入每周的（周次, 该周各款的金额口径日销7列表），用**固定阈值**回放，
    得到 ZT / ZT+TD 的GMV覆盖与ZT款数占比，再比对漂移带。

    冻结期**不要求**每周仍覆盖 50% / 80%：结构与季节变化必然带来漂移，
    只要落在带内就视为正常，越带才提示复核。
    """
    active_bands = bands or DRIFT_BANDS
    rows: list[dict] = []
    for label, values in weekly_values:
        series = [float(value) for value in values if float(value) > 0]
        total = sum(series)
        zt = [value for value in series if value >= th_main]
        zt_td = [value for value in series if value >= th_mid]
        coverage_main = safe_div(sum(zt), total)
        coverage_mid = safe_div(sum(zt_td), total)
        zt_count_share = safe_div(len(zt), len(series))
        metrics = {
            'ZT_GMV覆盖': coverage_main,
            'ZT_TD_GMV覆盖': coverage_mid,
            'ZT款数占比': zt_count_share,
        }
        outside = [
            name for name, (low, high) in active_bands.items()
            if not (low <= metrics[name] <= high)
        ]
        rows.append({
            '周次': label,
            '有效款数': len(series),
            '窗口GMV': total,
            'ZT_GMV覆盖': coverage_main,
            'ZT_TD_GMV覆盖': coverage_mid,
            'ZT款数占比': zt_count_share,
            'ZT款数': len(zt),
            'ZT+TD款数': len(zt_td),
            '越带项': '、'.join(outside),
            '状态': '需复核' if outside else '在带内',
        })
    return {
        'rows': rows,
        '冻结TH_MAIN': th_main,
        '冻结TH_MID': th_mid,
        '漂移带': dict(active_bands),
        '越带周数': sum(1 for row in rows if row['状态'] == '需复核'),
        '结论': (
            '全部周次落在漂移带内，阈值可继续冻结'
            if rows and all(row['状态'] == '在带内' for row in rows)
            else '存在越带周次，按「连续2周越带→提前复核」处理'
        ),
    }


def recheck_thresholds(previous: ThresholdResult, current: ThresholdResult) -> dict:
    """月度复核：重算后阈值相对变化超线则建议换阈值并升版本号。"""
    changes = {}
    for name, old, new, tolerance in (
        ('TH_MAIN', previous.th_main, current.th_main, RECHECK_REL_TOLERANCE_MAIN),
        ('TH_MID', previous.th_mid, current.th_mid, RECHECK_REL_TOLERANCE_MID),
    ):
        relative = safe_div(new - old, old)
        changes[name] = {
            '原值': old,
            '新值': new,
            '相对变化': relative,
            '触发线': tolerance,
            '是否超线': bool(old > 0 and abs(relative) > tolerance),
        }
    triggered = [name for name, item in changes.items() if item['是否超线']]
    return {
        'changes': changes,
        '建议': (
            f'{"、".join(triggered)} 变化超过触发线，建议启用新阈值并升级阈值版本号'
            if triggered
            else '阈值变化在容忍范围内，维持冻结值'
        ),
        '维持': not triggered,
    }


# ── 角色层：五桶瀑布 ──────────────────────────────────────
@dataclass(frozen=True)
class RoleDecision:
    """单个商品的决策角色 + 闸门。"""

    role: str
    reason: str
    gate: str
    gate_reason: str
    evidence: dict = field(default_factory=dict)

    @property
    def blocks_increase(self) -> bool:
        return self.gate == GATE_BLOCK

    def columns(self) -> dict:
        return {
            '决策角色': self.role,
            '决策角色依据': self.reason,
            '角色闸门': self.gate,
            '角色闸门依据': self.gate_reason,
            '风险命中': _text(self.evidence.get('角色_风险命中')),
        }

    def as_dict(self) -> dict:
        return {
            '决策角色': self.role,
            '决策角色依据': self.reason,
            '角色闸门': self.gate,
            '角色闸门依据': self.gate_reason,
            '角色算法版本': ROLE_ALGORITHM_VERSION,
            **self.evidence,
        }


def _daily_gmv(row: dict, days: float) -> float:
    """金额口径近7日日均GMV。

    只要执行表**提供了**「近7日日均GMV」这一列（哪怕是 0），就直接采信——
    0 表示近 7 日确实没成交，不能回退成月度日均把它算成有量。
    只有整列缺失时才退回「周期GMV ÷ 周期天数」。
    """
    value = row.get('近7日日均GMV')
    if not _blank(value):
        return to_num(value)
    row_days = to_num(row.get(ROLE_DAYS_KEY)) or days or 7.0
    return safe_div(to_num(row.get('GMV')), row_days)


def _trend_rate(row: dict) -> float | None:
    value = row.get('GMV趋势率')
    if not _blank(value):
        return to_num(value)
    previous = to_num(row.get('上一7日日均GMV'))
    if previous > 0:
        return safe_div(_daily_gmv(row, 7.0) - previous, previous)
    return None


def _risk_hit(row: dict) -> str:
    """只认退款类风险标签；库存与盈亏平衡线不参与角色判定。"""
    for name in _RISK_FIELDS:
        text = _text(row.get(name))
        if not text:
            continue
        if any(token in text for token in _REFUND_RISK_TOKENS):
            return f'{name}={text}'
    return ''


def _lifecycle_text(row: dict) -> str:
    return ' '.join(
        text for text in (_text(row.get('商品周期')), _text(row.get('商品定位'))) if text
    )


def _mature_launch_date(row: dict) -> date | None:
    for name in ('创建时间', '上架时间', '首次上架时间'):
        if not _blank(row.get(name)):
            return _to_date(row.get(name))
    return None


def _is_mature(row: dict, today: date | None = None) -> bool:
    """成熟口径（与复盘 v3 对齐）：生命周期标记 / 近7日有成交天数 / 上架满 60 天。"""
    if any(token in _lifecycle_text(row) for token in _MATURE_TOKENS):
        return True
    if to_num(row.get('近7日有成交天数')) >= MATURE_ACTIVE_DAYS:
        return True
    launch = _mature_launch_date(row)
    if launch is not None and today is not None:
        return (today - launch).days >= MATURE_DAYS
    return False


def _is_clearout(row: dict) -> bool:
    text = _lifecycle_text(row)
    return any(token in text for token in _CLEAROUT_TOKENS)


def _clearout_guard_release(row: dict, *, daily7: float, trend: float | None,
                            th_mid: float) -> str:
    """清仓护栏（v2.54）：衰退预警款的放行条件，返回放行依据（空串 = 不放行）。

    放行条件（任一成立即放行，因为两条都说明"这款还在卖或还在涨"）：

    * 近7日日均GMV ≥ TH_MID（仍在腰线以上）；
    * GMV趋势率 ≥ 0（环比没有下跌）。
    """
    if CLEAROUT_GUARD_TOKEN not in _lifecycle_text(row):
        return ''
    reasons = []
    if th_mid > 0 and daily7 >= th_mid:
        reasons.append(f'日销7 {daily7:.4f} ≥ TH_MID {th_mid:.4f}')
    if trend is not None and trend >= 0:
        reasons.append(f'趋势 {trend:.4f} ≥ 0')
    return '；'.join(reasons)


def _append_clearout_guard_note(decision: RoleDecision) -> RoleDecision:
    """把清仓护栏的放行依据并回角色理由（``columns()`` 不展开 evidence）。"""
    note = _text(decision.evidence.get(CLEAROUT_GUARD_KEY))
    if not note:
        return decision
    return replace(decision, reason=f'{decision.reason}（清仓护栏放行：{note}）')


def _launch_days(row: dict, today: date | None) -> int | None:
    launch = _mature_launch_date(row)
    if launch is None or today is None:
        return None
    return (today - launch).days


def assign_budget_role(
    row: dict,
    *,
    th_main: float = 0.0,
    th_mid: float = 0.0,
    store_roi: float = 0.0,
    category_cart_median: float | None = None,
    days: float = 7.0,
    today: date | None = None,
    min_uv: float = MIN_UV,
    new_days: int = CS_NEW_DAYS,
) -> RoleDecision:
    """决策角色入口：先跑优先级瀑布，再叠加风险闸门。

    风险闸门只对「退款类风险」生效（只降不增）；库存风险按业务口径只做提示，
    不参与判定。阈值缺失时直接返回「未判定 + 放行加投」，风险闸门也不介入，
    以保证「没有阈值就不改变既有决策」。
    """
    decision = _append_clearout_guard_note(_waterfall_decision(
        row,
        th_main=th_main,
        th_mid=th_mid,
        store_roi=store_roi,
        category_cart_median=category_cart_median,
        days=days,
        today=today,
        min_uv=min_uv,
        new_days=new_days,
    ))
    if decision.role == '未判定':
        return decision
    risk = _text(decision.evidence.get('角色_风险命中'))
    if not risk or decision.gate == GATE_BLOCK:
        return decision
    return RoleDecision(
        role=decision.role,
        reason=f'{decision.reason}；命中退款风险（{risk}）',
        gate=GATE_BLOCK,
        gate_reason=f'退款风险（{risk}）→只降不增，禁止新增预算',
        evidence=dict(decision.evidence),
    )


def _waterfall_decision(
    row: dict,
    *,
    th_main: float = 0.0,
    th_mid: float = 0.0,
    store_roi: float = 0.0,
    category_cart_median: float | None = None,
    days: float = 7.0,
    today: date | None = None,
    min_uv: float = MIN_UV,
    new_days: int = CS_NEW_DAYS,
) -> RoleDecision:
    """优先级瀑布（命中即停），返回决策角色与闸门。

    顺序：硬风险清理 → 清仓标记 → CS测试 → ZT趋势预警 → ZT主推 → TC投产
    → TD梯队 → TD候选（中腰保持）→ QC回收 → 观察。
    阈值缺失（TH_MAIN / TH_MID 都为 0）时返回「未判定 + 放行加投」，
    即完全不介入既有决策 —— 没有阈值就不做角色判定。
    退款风险不在这里判定，由 :func:`assign_budget_role` 的风险闸门统一处理。
    """
    gmv = to_num(row.get('GMV'))
    spend = to_num(row.get('付费'))
    daily7 = _daily_gmv(row, days)
    trend = _trend_rate(row)
    visitors = to_num(row.get('商品访客数'))
    cart_rate = row.get('商品加购率')
    cart_rate = None if _blank(cart_rate) else to_num(cart_rate)
    risk = _risk_hit(row)
    evidence = {
        '角色_日销7': round(daily7, 6),
        '角色_趋势率': None if trend is None else round(trend, 6),
        '角色_阈值TH_MAIN': round(th_main, 6),
        '角色_阈值TH_MID': round(th_mid, 6),
        '角色_风险命中': risk,
    }

    if not th_main and not th_mid:
        return RoleDecision(
            '未判定',
            '执行表未提供 TH_MAIN / TH_MID，本次不做角色判定',
            GATE_ALLOW,
            '无阈值不介入：仅保留既有错配与回收规则',
            evidence,
        )

    has_main = th_main > 0
    has_mid = th_mid > 0
    is_main = has_main and daily7 >= th_main
    is_mid = has_mid and daily7 >= th_mid

    # 1) 硬风险：零成交仍在花钱，必须排在测试期/新品之前。
    #    「零成交」与判定基准一致，读固定近7日口径（缺列时才退回周期GMV）。
    #    近7日零成交 + 上一7日也零成交 → 直接清理；若上一周期数据缺失，
    #    只在本期零成交**且花费达门槛**时按硬风险处理，避免误杀新起量款。
    has_previous = not _blank(row.get('上一7日日均GMV'))
    previous_daily = to_num(row.get('上一7日日均GMV'))
    zero_now = daily7 <= 0
    zero_previous = bool(has_previous and previous_daily <= 0)
    big_spend = spend >= QC_SPEND_MIN
    if spend > 0 and zero_now and (zero_previous or big_spend):
        tier = '大额' if big_spend else '长尾'
        streak = '近7日与上一7日均零成交' if zero_previous else '近7日零成交且花费达门槛'
        return RoleDecision(
            '清理',
            f'{streak}（{tier}档），花费 {spend:.2f} 元',
            GATE_BLOCK,
            '硬风险款不得新增预算（既有规则已按全额回收处理）',
            {**evidence, '角色_硬风险档': tier},
        )

    # 2) 清仓/衰退标记（v2.54 起「衰退预警」带护栏：仍在腰线以上或环比未跌则放行）
    if _is_clearout(row):
        release = _clearout_guard_release(
            row, daily7=daily7, trend=trend, th_mid=th_mid,
        )
        if not release:
            return RoleDecision(
                '清理',
                f'生命周期标记命中清仓/衰退：{_lifecycle_text(row)}',
                GATE_BLOCK,
                '清仓方向只做回收，不新增预算',
                evidence,
            )
        evidence = {**evidence, CLEAROUT_GUARD_KEY: release}

    # 3) CS 测试：新品（上架不超过 new_days 天）
    launch_days = _launch_days(row, today)
    if launch_days is not None and 0 <= launch_days <= new_days:
        return RoleDecision(
            'CS测试',
            f'上架 {launch_days} 天 ≤ {new_days} 天，属新品测试期',
            GATE_CAP,
            f'测试期加投按 {CS_WEEKLY_CAP:.0f} 元/周封顶，不做大额放量',
            {**evidence, '角色_上架天数': launch_days},
        )

    # 4) ZT 趋势预警：主推量级但趋势破位
    if is_main and trend is not None and trend < TREND_ALERT:
        return RoleDecision(
            'ZT趋势预警',
            f'日销7 {daily7:.4f} ≥ TH_MAIN {th_main:.4f}，但趋势 {trend:.4f} < {TREND_ALERT:.0%}',
            GATE_BLOCK,
            '主推款趋势破位，只允许保持或回收，不加投',
            evidence,
        )

    # 5) ZT 主推
    if is_main:
        return RoleDecision(
            'ZT主推',
            f'日销7 {daily7:.4f} ≥ TH_MAIN {th_main:.4f}',
            GATE_ALLOW,
            '主推款可承接加投，额度仍由错配测算决定',
            evidence,
        )

    spend_share = to_num(row.get('花费占比'))
    sales_share = to_num(row.get('销售占比'))
    roi = to_num(row.get('推广投产'))
    if roi <= 0 and spend > 0:
        roi = safe_div(to_num(row.get('推广成交金额')), spend)
    # 零花费但日销7达标 = 自然成交强，属投产型候选（与复盘口径一致）
    thin_spend = spend_share <= 0 or (
        sales_share > 0 and spend_share < sales_share * TC_SPEND_SHARE_FACTOR
    )

    # 6) TC 投产：中腰以上、成熟、花费显著少于销售贡献
    mature = _is_mature(row, today)
    if is_mid and mature and thin_spend:
        return RoleDecision(
            'TC投产',
            f'日销7 {daily7:.4f} ≥ TH_MID {th_mid:.4f}，成熟款且花费份额 {spend_share:.4f} '
            f'< 销售份额 {sales_share:.4f} × {TC_SPEND_SHARE_FACTOR:.1f}',
            GATE_ALLOW,
            '投产型可补预算，补多少仍看错配测算',
            {**evidence, '角色_成熟': True},
        )

    # 7) TD 梯队：中腰 + 趋势不破 + 流量与加购达标
    cart_ok = True
    if cart_rate is not None and category_cart_median:
        cart_ok = cart_rate >= category_cart_median
    if is_mid and (trend is None or trend >= 0) and visitors >= min_uv and cart_ok:
        return RoleDecision(
            'TD梯队',
            f'中腰保持：日销7 {daily7:.4f} ≥ TH_MID {th_mid:.4f}，趋势 {0 if trend is None else trend:.4f}，'
            f'访客 {visitors:.0f} ≥ {min_uv:.0f}，加购率达标',
            GATE_ALLOW,
            '梯队款可小步加投，用于承接主推外溢',
            evidence,
        )

    # 8) TD 候选：中腰但条件不足，保持不加投
    if is_mid:
        return RoleDecision(
            'TD候选',
            f'中腰保持：日销7 {daily7:.4f} ≥ TH_MID {th_mid:.4f}，但趋势/流量/加购未达标，先保持',
            GATE_BLOCK,
            '中腰观察期不新增预算，达标后再转梯队',
            evidence,
        )

    # 9) QC 回收：有实质花费但低效（零成交已在第 1 步处理，小额花费不单独成桶）
    if spend >= QC_SPEND_MIN and store_roi > 0 and roi < store_roi * 0.5:
        return RoleDecision(
            'QC回收',
            f'付费 {spend:.2f} 元 ≥ 门槛 {QC_SPEND_MIN:.0f} 元，但投产 {roi:.4f} '
            f'低于店均 {store_roi:.4f} 的50%',
            GATE_BLOCK,
            '低效款按既有错配规则回收，本层只封住加投',
            evidence,
        )

    # 10) 观察
    return RoleDecision(
        '观察',
        f'未进入任何投放桶：日销7 {daily7:.4f} 低于 TH_MID {th_mid:.4f}，'
        f'付费 {spend:.2f} 元未达回收门槛',
        GATE_BLOCK,
        '未成桶的商品不新增预算',
        evidence,
    )


def apply_role_gate(
    *,
    action: str,
    adjustment: float,
    state: str,
    reason: str,
    decision: RoleDecision,
    baseline: float = 0.0,
    cap: float = CS_WEEKLY_CAP,
) -> dict:
    """把角色闸门套在既有动作结果上。**只做减法**：只拦截或封顶加投。"""
    result = {
        '预算匹配状态': state,
        '未来7日建议调整金额': adjustment,
        '动作依据': reason,
        '动作': action,
        '角色闸门命中': '',
    }
    if adjustment <= 0:
        return result

    if decision.blocks_increase:
        result.update({
            '预算匹配状态': f'角色闸门拦截({decision.role})',
            '未来7日建议调整金额': 0.0,
            '动作依据': f'{reason}；决策角色「{decision.role}」{decision.gate_reason}，本轮不加投',
            '动作': '保持',
            '角色闸门命中': decision.gate,
        })
        return result

    if decision.gate == GATE_CAP and adjustment > cap:
        result.update({
            '预算匹配状态': f'角色闸门封顶({decision.role})',
            '未来7日建议调整金额': float(cap),
            '动作依据': f'{reason}；决策角色「{decision.role}」{decision.gate_reason}',
            '角色闸门命中': decision.gate,
        })
    return result


def _category_cart_medians(products: Sequence[dict], min_uv: float = MIN_UV) -> dict[str, float]:
    """类目加购率中位数：只在有效访客达标的款上统计，样本不足则该类目不参与判档。"""
    buckets: dict[str, list[float]] = {}
    for row in products:
        category = _text(row.get('淘宝类目'))
        rate = row.get('商品加购率')
        if not category or _blank(rate):
            continue
        if to_num(row.get('商品访客数')) < min_uv:
            continue
        buckets.setdefault(category, []).append(to_num(rate))
    return {
        category: float(statistics.median(values))
        for category, values in buckets.items()
        if len(values) >= CATEGORY_MIN_SAMPLES
    }


def _coerce_rows(products) -> list[dict]:
    """接受 DataFrame 或 dict 序列，统一成新的 dict 列表（不修改入参）。"""
    if isinstance(products, pd.DataFrame):
        return [dict(item) for item in products.to_dict('records')]
    return [dict(item) for item in (products or [])]


def apply_budget_roles(
    products: Sequence[dict],
    *,
    th_main: float | None = None,
    th_mid: float | None = None,
    store_roi: float = 0.0,
    days: float = 7.0,
    today: date | None = None,
    min_uv: float = MIN_UV,
) -> list[dict]:
    """给商品决策行补上角色列（不改变金额）。返回新的列表。"""
    rows = _coerce_rows(products)
    resolved_main = to_num(th_main) if th_main is not None else 0.0
    resolved_mid = to_num(th_mid) if th_mid is not None else 0.0
    if not resolved_main and not resolved_mid:
        resolved_main, resolved_mid = _thresholds_from_rows(rows)
    cart_medians = _category_cart_medians(rows, min_uv=min_uv)
    for row in rows:
        decision = assign_budget_role(
            row,
            th_main=resolved_main,
            th_mid=resolved_mid,
            store_roi=store_roi,
            category_cart_median=cart_medians.get(_text(row.get('淘宝类目'))),
            days=days,
            today=today,
            min_uv=min_uv,
        )
        row.update(decision.columns())
        row['_role_decision'] = decision
    return rows


def resolve_thresholds(
    engine_result: ThresholdResult,
    frozen: Mapping | None = None,
    *,
    tolerance: float = 0.30,
) -> dict:
    """决定「最终生效的阈值」：运营冻结值优先，引擎值只作建议与监控。

    * ``frozen`` 为 None → 用引擎建议值，标记 ``引擎建议(未冻结)``；
    * ``frozen`` 有值 → 一律以运营冻结值为准，同时回报引擎建议值与偏离度用于复核。

    返回可直接写进执行表的列（``阈值*`` 系列）加对照字段。
    """
    engine_main = to_num(getattr(engine_result, 'th_main', 0.0))
    engine_mid = to_num(getattr(engine_result, 'th_mid', 0.0))
    engine_version = _text(getattr(engine_result, 'version', ''))
    engine_state = _text(getattr(engine_result, 'state', ''))
    engine_reference = _text(getattr(engine_result, 'end_date', ''))
    engine_windows = int(getattr(engine_result, 'weeks', 0) or 0)

    payload = {
        'TH_MAIN': engine_main,
        'TH_MID': engine_mid,
        '阈值版本': engine_version,
        '阈值窗口': f'{int(getattr(engine_result, "window_days", THRESHOLD_WINDOW_DAYS))}天',
        '阈值周数': engine_windows,
        '阈值状态': engine_state or ('数据不足' if not engine_main else '未冻结(引擎建议)'),
        '阈值来源': '引擎建议(未冻结)',
        '阈值冻结人': '',
        '阈值冻结时间': '',
        '引擎建议TH_MAIN': engine_main,
        '引擎建议TH_MID': engine_mid,
        '引擎建议参照日': engine_reference,
        '阈值偏离TH_MAIN': '',
        '阈值偏离TH_MID': '',
        '阈值复核提示': '',
    }
    if not engine_main and not engine_mid:
        payload['阈值状态'] = '数据不足'
        if frozen:
            payload.update(_frozen_payload(frozen))
            payload['阈值复核提示'] = '引擎本次算不出建议值，按运营冻结值执行'
        return payload

    if not frozen:
        if engine_state and engine_state != '正常':
            payload['阈值复核提示'] = (
                f'尚未冻结：当前用引擎建议值；引擎建议已{engine_state}，'
                '建议复核后由运营确认冻结'
            )
        else:
            payload['阈值复核提示'] = '尚未冻结：当前用引擎建议值，建议运营确认后冻结'
        return payload

    frozen_payload = _frozen_payload(frozen)
    payload.update(frozen_payload)
    notes: list[str] = []
    deviated = False
    for field, name, engine_value, frozen_value in (
        ('阈值偏离TH_MAIN', 'TH_MAIN', engine_main, to_num(frozen_payload.get('TH_MAIN'))),
        ('阈值偏离TH_MID', 'TH_MID', engine_mid, to_num(frozen_payload.get('TH_MID'))),
    ):
        deviation = safe_div(frozen_value - engine_value, engine_value)
        payload[field] = deviation
        if engine_value > 0 and abs(deviation) > float(tolerance):
            deviated = True
            notes.append(f'{name} 冻结值 {frozen_value:.4f} 与引擎建议 {engine_value:.4f} '
                         f'偏离 {deviation:+.1%}')
    if engine_state and engine_state != '正常':
        notes.append(f'引擎建议{engine_state}')
    if deviated:
        payload['阈值复核提示'] = '；'.join(notes)
    else:
        payload['阈值复核提示'] = '；'.join(['冻结值与引擎建议一致，可继续冻结', *notes])
    return payload


def _frozen_payload(frozen: Mapping) -> dict:
    return {
        'TH_MAIN': to_num(frozen.get('TH_MAIN')),
        'TH_MID': to_num(frozen.get('TH_MID')),
        '阈值版本': _text(frozen.get('阈值版本')),
        '阈值窗口': _text(frozen.get('阈值窗口')) or f'{THRESHOLD_WINDOW_DAYS}天',
        '阈值周数': int(to_num(frozen.get('窗口周数')) or THRESHOLD_WINDOWS),
        '阈值状态': _text(frozen.get('阈值状态')) or '已冻结',
        '阈值来源': _text(frozen.get('阈值来源')) or '运营冻结',
        '阈值冻结人': _text(frozen.get('冻结人')),
        '阈值冻结时间': _text(frozen.get('冻结时间')),
    }


def threshold_columns_from_payload(payload: Mapping) -> dict:
    """从 :func:`resolve_thresholds` 结果里取出要写进执行表的列。

    ``阈值复核提示`` 只给界面看，不写进执行表（避免每行重复一段长文案）。
    """
    return {
        key: value for key, value in payload.items()
        if (key.startswith('阈值') or key.startswith('TH_') or key.startswith('引擎建议'))
        and key != '阈值复核提示'
    }


def _thresholds_from_rows(rows: Sequence[dict]) -> tuple[float, float]:
    """从执行表透传列里取阈值（同一店铺口径下应全店一致，取首个非空）。"""
    main = mid = 0.0
    for row in rows:
        if not main:
            main = to_num(row.get('TH_MAIN'))
        if not mid:
            mid = to_num(row.get('TH_MID'))
        if main and mid:
            break
    return main, mid


def gate_budget_decisions(
    products: Sequence[dict],
    *,
    th_main: float | None = None,
    th_mid: float | None = None,
    store_roi: float = 0.0,
    days: float = 7.0,
    today: date | None = None,
    min_uv: float = MIN_UV,
    cap: float = CS_WEEKLY_CAP,
) -> list[dict]:
    """补角色列 **并** 用闸门约束「未来7日建议调整金额」。

    这是嵌入 ``core.budget_decision`` 时真正调用的入口：
    先让既有错配逻辑算出建议调整，再由角色层决定放行 / 拦截 / 封顶。
    """
    rows = apply_budget_roles(
        products,
        th_main=th_main,
        th_mid=th_mid,
        store_roi=store_roi,
        days=days,
        today=today,
        min_uv=min_uv,
    )
    for row in rows:
        decision = row.pop('_role_decision', None)
        if decision is None:
            continue
        adjustment = to_num(row.get('未来7日建议调整金额'))
        baseline = to_num(row.get('未来7日花费基线'))
        gated = apply_role_gate(
            action=_text(row.get('动作')) or _text(row.get('预算匹配状态')),
            adjustment=adjustment,
            state=_text(row.get('预算匹配状态')),
            reason=_text(row.get('动作依据')),
            decision=decision,
            baseline=baseline,
            cap=cap,
        )
        row.update({
            '预算匹配状态': gated['预算匹配状态'],
            '未来7日建议调整金额': gated['未来7日建议调整金额'],
            '动作依据': gated['动作依据'],
            '角色闸门命中': gated['角色闸门命中'],
        })
        if '未来7日建议花费' in row or baseline:
            row['未来7日建议花费'] = max(0.0, baseline + gated['未来7日建议调整金额'])
    return rows


def _period_span_days(products: Sequence[dict]) -> tuple[int | None, str]:
    """从「统计日期」文本推断执行表周期天数（如 '2026-08-01~2026-08-31' → 31）。"""
    for row in products:
        text = _text(row.get('统计日期'))
        if not text:
            continue
        parts = [part for part in text.replace('至', '~').split('~') if part.strip()]
        if len(parts) != 2:
            continue
        start, end = (_to_date(parts[0]), _to_date(parts[1]))
        if start and end:
            return (end - start).days + 1, text
    return None, ''


def _threshold_window_from_rows(rows: Sequence[dict]) -> int | None:
    """读取执行表里的「阈值窗口」标注（如 '7天'、'7'），用于核对是否与固定窗口一致。"""
    for row in rows:
        text = _text(row.get('阈值窗口'))
        if not text:
            continue
        match = pd.Series([text]).str.extract(r'(\d+)', expand=False).iloc[0]
        if isinstance(match, str) and match:
            return int(match)
    return None


def role_input_check(
    products,
    *,
    th_main: float = 0.0,
    th_mid: float = 0.0,
    days: float | None = None,
    window_days: int = THRESHOLD_WINDOW_DAYS,
) -> dict:
    """上线前自检：阈值是否存在、判定基准是否是固定的近 7 日口径。

    角色层的判定量是**金额口径近 7 日日均GMV**，阈值也是 7 天窗口的覆盖分界。
    界面上选 当日/3天/7天/15天/30天 只改看数口径，不改判定基准，所以：

    * 若执行表已注入 ``近7日日均GMV``（用 :func:`inject_rolling_daily_gmv`）→ 正常；
    * 若没注入，角色层会退回「周期GMV ÷ 所选周期天数」，选 30 天时就是 30 日均，
      与固定 7 天阈值不同窗 → 这里报警。
    """
    rows = _coerce_rows(products)
    total = len(rows)
    resolved_main, resolved_mid = to_num(th_main), to_num(th_mid)
    if not resolved_main and not resolved_mid:
        resolved_main, resolved_mid = _thresholds_from_rows(rows)
    # 只看「有没有这一列」：注入后大量款本来就是 0（近7日确实没成交），
    # 0 是有效口径，不能当成"没注入"。
    with_daily_gmv = sum(1 for row in rows if not _blank(row.get('近7日日均GMV')))
    with_previous = sum(1 for row in rows if not _blank(row.get('上一7日日均GMV')))
    span_days, span_text = _period_span_days(rows)
    threshold_window = _threshold_window_from_rows(rows)
    selected_days = to_num(days) or to_num(span_days)

    warnings: list[str] = []
    if not resolved_main and not resolved_mid:
        warnings.append(
            '缺少 TH_MAIN / TH_MID：角色层会全部返回「未判定 + 放行加投」，不介入任何决策'
        )
    if threshold_window is not None and threshold_window != int(window_days):
        warnings.append(
            f'阈值窗口标注为 {threshold_window} 天，与固定判定窗口 {int(window_days)} 天不一致：'
            '请用同一窗口重算阈值'
        )
    if total and with_daily_gmv / total < 0.5:
        mismatch = (
            f'（当前看数周期 = {span_text or f"{selected_days:g}天"}）'
            if (span_days or selected_days)
            else ''
        )
        warnings.append(
            f'仅 {with_daily_gmv}/{total} 款有「近7日日均GMV」：角色层会退回'
            f'「周期GMV ÷ {selected_days or 1:g}天」{mismatch}，与固定 {int(window_days)} 天阈值不同窗；'
            '请用 inject_rolling_daily_gmv(day_map, ...) 注入近7日口径'
        )
    if total and with_previous / total < 0.5:
        warnings.append(
            f'仅 {with_previous}/{total} 款有「上一7日日均GMV」：零成交款的硬风险判定会退化为只看本期；'
            '同一次注入即可补齐'
        )
    return {
        'ready': not warnings,
        '总款数': total,
        '有近7日日均GMV款数': with_daily_gmv,
        '有上一7日日均GMV款数': with_previous,
        '判定窗口天数': int(window_days),
        '阈值窗口标注': threshold_window,
        '看数周期文本': span_text,
        '看数周期天数': span_days,
        'TH_MAIN': resolved_main,
        'TH_MID': resolved_mid,
        'warnings': tuple(warnings),
    }


def role_summary(products) -> list[dict]:
    """按角色汇总款数 / 花费份额 / 销售份额 / 错配 / 建议调整。"""
    products = _coerce_rows(products)
    total_gmv = sum(to_num(row.get('GMV')) for row in products)
    total_spend = sum(to_num(row.get('付费')) for row in products)
    rows: list[dict] = []
    for role in ROLES:
        group = [row for row in products if _text(row.get('决策角色')) == role]
        if not group:
            continue
        gmv = sum(to_num(row.get('GMV')) for row in group)
        spend = sum(to_num(row.get('付费')) for row in group)
        adjustments = [to_num(row.get('未来7日建议调整金额')) for row in group]
        sales_share = safe_div(gmv, total_gmv)
        spend_share = safe_div(spend, total_spend)
        rows.append({
            '决策角色': role,
            '款数': len(group),
            'GMV': round(gmv, 2),
            '销售份额': round(sales_share, 6),
            '付费': round(spend, 2),
            '花费份额': round(spend_share, 6),
            '占比差(pp)': round((spend_share - sales_share) * 100, 2),
            '预算错配金额': round(sum(to_num(row.get('预算错配金额')) for row in group), 2),
            'ROI': round(safe_div(gmv, spend), 4),
            '未来7日建议调整金额': round(sum(adjustments), 2),
            '禁止加投款数': sum(
                1 for row in group if _text(row.get('角色闸门')) == GATE_BLOCK
            ),
        })
    return sorted(rows, key=lambda item: ROLE_ORDER.get(item['决策角色'], 99))


__all__ = [
    'ROLE_LAYER_ENABLED',
    'ROLE_ALGORITHM_VERSION',
    'CLEAROUT_GUARD_TOKEN',
    'CLEAROUT_GUARD_KEY',
    'THRESHOLD_VERSION',
    'THRESHOLD_WINDOW_DAYS',
    'THRESHOLD_WINDOWS',
    'ROLE_LAYER_COLUMNS',
    'ROLES',
    'GATE_ALLOW',
    'GATE_BLOCK',
    'GATE_CAP',
    'DRIFT_BANDS',
    'ThresholdResult',
    'RoleDecision',
    'threshold_version',
    'boundary_for_share',
    'period_windows',
    'compute_coverage_thresholds',
    'compute_coverage_thresholds_from_windows',
    'compute_coverage_thresholds_from_day_map',
    'period_window_gmv_from_day_map',
    'compute_rolling_daily_gmv',
    'inject_rolling_daily_gmv',
    'freeze_drift_report',
    'recheck_thresholds',
    'resolve_thresholds',
    'threshold_columns_from_payload',
    'assign_budget_role',
    'apply_role_gate',
    'apply_budget_roles',
    'gate_budget_decisions',
    'role_input_check',
    'role_summary',
]
