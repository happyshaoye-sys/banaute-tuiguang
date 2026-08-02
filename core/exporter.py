"""Excel 导出"""
import io
import pandas as pd
from .schema import EXPORT_COLS, safe_div


def style_execution_table(df):
    """页面执行表样式(环比高亮已移除,直接返回原表)。"""
    return df


def export_execution_table(df):
    """导出执行表为 Excel bytes"""
    output = io.BytesIO()
    cols = [c for c in EXPORT_COLS if c in df.columns]
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df[cols].to_excel(writer, sheet_name='商品推广执行表', index=False)
    output.seek(0)
    return output.read()


def export_all_sheets(df):
    """导出全部分析 Sheet"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # 1. 执行表
        cols = [c for c in EXPORT_COLS if c in df.columns]
        df[cols].to_excel(writer, sheet_name='商品推广执行表', index=False)

        # 2. 分层
        seg_cols = [c for c in ['组合','综合等级','商品定位','对应操作'] if c in df.columns]
        if seg_cols and '宝贝ID' in df.columns and 'GMV' in df.columns:
            seg = df.groupby(seg_cols).agg(
                商品数=('宝贝ID','count'), GMV=('GMV','sum')
            ).reset_index()
            seg.to_excel(writer, sheet_name='商品分层明细', index=False)

        # 3. 品类
        if '淘宝类目' in df.columns:
            work = df.copy()
            if '退款率' in work.columns:
                work['_退款率加权金额'] = pd.to_numeric(work['退款率'], errors='coerce').fillna(0.0) \
                    * pd.to_numeric(work['GMV'], errors='coerce').fillna(0.0)
            else:
                work['_退款率加权金额'] = 0.0
            agg = {
                '商品数': ('宝贝ID', 'count'),
                'GMV': ('GMV', 'sum'),
                '付费': ('付费', 'sum'),
                '_退款率加权金额': ('_退款率加权金额', 'sum'),
            }
            if '成功退款金额' in work.columns:
                agg['成功退款金额'] = ('成功退款金额', 'sum')
            cat = work.groupby('淘宝类目').agg(**agg).reset_index()
            cat['退款率'] = cat['_退款率加权金额'] / cat['GMV'].replace(0, float('nan'))
            cat['ROI'] = cat['GMV'] / cat['付费'].replace(0, float('nan'))
            cat = cat.drop(columns=['_退款率加权金额'])
            cat.to_excel(writer, sheet_name='品类分析', index=False)

        # 4. 退款风险（聚水潭cohort退款率>70%，与页面阈值一致）
        if '退款率' in df.columns:
            ref_cols = [c for c in ['宝贝ID','商品名称','淘宝类目','GMV','成功退款金额','退款率','付费','综合等级','对应操作','风险标签'] if c in df.columns]
            if ref_cols:
                ref = df[df['退款率'] > 0.7].sort_values('退款率', ascending=False)[ref_cols]
                ref.to_excel(writer, sheet_name='退款风险', index=False)

        # 5-7. 渠道
        for col, name in [('关键词','关键词建议'),('人群','人群建议'),('全站','全站建议')]:
            if col in df.columns:
                attr_col = f'{col}归因成交'
                ch_cols = [c for c in ['宝贝ID','商品名称',col,attr_col,'GMV','综合等级','对应操作'] if c in df.columns]
                if ch_cols:
                    ch = df[df[col] > 0].sort_values(col, ascending=False).head(500)[ch_cols].copy()
                    if attr_col in ch.columns:
                        ch['渠道投产'] = ch[attr_col] / ch[col].replace(0, float('nan'))
                    ch.to_excel(writer, sheet_name=name, index=False)

        # 8. 库存
        if '在仓库存' in df.columns:
            inv_cols = [c for c in ['宝贝ID','商品名称','在仓库存','在途','商品访客数','GMV','付费','综合等级','风险标签'] if c in df.columns]
            if inv_cols:
                inv = df[(df['在仓库存'] > 50) | (df['在仓库存'] == 0)].sort_values('在仓库存', ascending=False)[inv_cols]
                inv.to_excel(writer, sheet_name='库存动作', index=False)

    output.seek(0)
    return output.read()
