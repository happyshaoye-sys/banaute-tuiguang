"""数据契约校验：在缺字段或计划链路失效时立即停止处理。"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .plan_metrics import (
    FULL_PLAN_DETAIL_COLUMN,
    PLAN_TOTAL_COUNT_COLUMN,
    parse_full_plan_details,
)
from .schema import (
    SOURCE_PRODUCT_INFO,
    SOURCE_PRODUCT_ROI,
    SOURCE_STOCK,
    SOURCE_WUJIE_SUBJECT,
)


SOURCE_REQUIRED_FIELDS = {
    SOURCE_PRODUCT_ROI: {
        '宝贝ID', '商品名称', 'GMV', '商品访客数', '统计日期', '淘宝类目',
        '成功退款金额', '访客平均价值', '支付买家数', '支付件数',
        '商品加购件数', '商品支付转化率', '搜索引导访客数',
        '推广消耗金额', '推广引导总成交金额', '推广总加购数',
        '关键词推广访客数', '人群推广访客数', '场景推广访客数',
        '关键词推广加购人数', '人群推广加购人数', '场景推广加购人数',
        '关键词推广加购数', '人群推广加购数', '场景推广加购数',
    },
    SOURCE_WUJIE_SUBJECT: {
        '宝贝ID', '付费', '计划ID', '推广投产', 'GMV', '直购数量',
        '场景名字', '转化周期', '展现', '点击', '点击率',
        '商品支付转化率', '商品加购件数', '主体类型',
    },
    SOURCE_STOCK: {
        '宝贝ID', '货号', '在仓库存', '在途', '成本价（含税）', '日销7',
    },
    SOURCE_PRODUCT_INFO: {
        '宝贝ID', '款式主图', '货号', '创建时间', '成本价（含税）',
    },
}


@dataclass(frozen=True)
class DataValidationIssue:
    stage: str
    message: str


class DataValidationError(ValueError):
    """可直接展示给运营的数据契约错误。"""

    def __init__(self, issue: DataValidationIssue):
        self.issue = issue
        super().__init__(f'{issue.stage}：{issue.message}')


def _numbers(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(',', '', regex=False)
        .str.replace('¥', '', regex=False)
        .str.replace('￥', '', regex=False)
        .str.strip(),
        errors='coerce',
    ).fillna(0.0)


def _clean_text(series: pd.Series) -> pd.Series:
    cleaned = series.fillna('').astype(str).str.strip()
    return cleaned.mask(
        cleaned.str.lower().isin({'', 'nan', 'none', 'null', '<na>', '-', '--', '—'}),
        '',
    )


def validate_required_columns(
    columns,
    required,
    stage: str,
) -> None:
    """对任意原始输入执行显式列契约校验，禁止缺列后继续补默认值。"""
    missing = sorted(set(required).difference(columns))
    if missing:
        raise DataValidationError(DataValidationIssue(
            stage,
            '缺少必需原始字段：' + '、'.join(missing),
        ))


def validate_source_dataframe(
    df: pd.DataFrame,
    source_type: str,
    source_name: str = '',
) -> pd.DataFrame:
    """校验标准化后的原始文件字段，并阻断付费记录缺少 PlanID。"""
    label = f'{source_type}文件 {source_name}'.strip()
    if df is None or df.empty:
        raise DataValidationError(DataValidationIssue(label, '文件没有可用数据行'))

    validate_required_columns(
        df.columns,
        SOURCE_REQUIRED_FIELDS.get(source_type, set()),
        label,
    )

    if source_type == SOURCE_WUJIE_SUBJECT:
        paid = _numbers(df['付费']) > 0
        plan_ids = _clean_text(df['计划ID'])
        missing_plan = paid & plan_ids.eq('')
        if missing_plan.any():
            product_ids = _clean_text(df.loc[missing_plan, '宝贝ID'])
            samples = [value for value in product_ids.unique().tolist() if value][:5]
            sample_text = '、'.join(samples) if samples else '无法识别宝贝ID'
            raise DataValidationError(DataValidationIssue(
                label,
                f'发现{int(missing_plan.sum())}条有付费但没有PlanID的记录；样例宝贝ID：{sample_text}',
            ))

    return df


def validate_paid_plan_consistency(
    df: pd.DataFrame,
    stage: str,
) -> pd.DataFrame:
    """确保执行链路中每个有付费商品都有完整、可解析的计划数据。"""
    if df is None or df.empty or '付费' not in df.columns:
        return df

    paid = _numbers(df['付费']) > 0
    if not paid.any():
        return df

    required_columns = {
        '推广明细',
        FULL_PLAN_DETAIL_COLUMN,
        PLAN_TOTAL_COUNT_COLUMN,
    }
    missing_columns = sorted(required_columns.difference(df.columns))
    if missing_columns:
        raise DataValidationError(DataValidationIssue(
            stage,
            '有付费商品存在，但计划链路字段缺失：' + '、'.join(missing_columns),
        ))

    plan_totals = _numbers(df[PLAN_TOTAL_COUNT_COLUMN])
    display_details = _clean_text(df['推广明细'])
    full_counts = df[FULL_PLAN_DETAIL_COLUMN].map(
        lambda value: len(parse_full_plan_details(value))
    )
    invalid = paid & (
        plan_totals.le(0)
        | display_details.eq('')
        | full_counts.le(0)
        | plan_totals.ne(full_counts)
    )
    if invalid.any():
        product_ids = (
            _clean_text(df.loc[invalid, '宝贝ID'])
            if '宝贝ID' in df.columns
            else pd.Series('', index=df.index[invalid])
        )
        samples = [value for value in product_ids.unique().tolist() if value][:5]
        sample_text = '、'.join(samples) if samples else '无法识别宝贝ID'
        raise DataValidationError(DataValidationIssue(
            stage,
            f'发现{int(invalid.sum())}个有付费但计划数据缺失或不一致的商品；'
            f'样例宝贝ID：{sample_text}',
        ))

    return df
