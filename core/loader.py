"""文件读取与列名标准化"""
import csv
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree.ElementTree import iterparse

import pandas as pd
from openpyxl import load_workbook
from .schema import (
    ALLOWED_SOURCE_TYPES, FIELD_ALIASES, SOURCE_WUJIE_SUBJECT,
    WUJIE_REMAP, classify_file, SOURCE_PRODUCT_ROI, SOURCE_STOCK,
    SOURCE_PRODUCT_INFO, SOURCE_PRODUCT_OVERALL, SOURCE_WUJIE_CREATIVE,
    schema_fingerprint,
)
from .data_validation import (
    SOURCE_REQUIRED_FIELDS,
    validate_source_dataframe,
)


XLSX_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RELS_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
OFFICE_RELS_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
COL_REF_RE = re.compile(r'([A-Z]+)')
#: 解析层缓存版本。**改动列白名单/标准化口径时必须自增**：解析缓存按
#: 版本+路径+源类型+大小+mtime 命中，白名单变了但版本没变会继续返回旧列集合的帧。
#: v12：无界-商品主体 放行「计划名字」（计划明细/推广计划管理需要计划名称）。
PARSED_CACHE_VERSION = 'v12'  # v11: 新品商品整体/无界创意源字段契约


SOURCE_ALLOWED_FIELDS = {
    SOURCE_PRODUCT_ROI: {
        '宝贝ID', '商品名称', 'GMV', '商品访客数', '统计日期', '淘宝类目',
        '成功退款金额', '访客平均价值', '成本价（含税）', '支付买家数',
        '支付件数', '商品加购件数', '商品支付转化率',
        '搜索引导访客数',
        '推广消耗金额', '推广引导总成交金额', '推广总加购数',
        '关键词推广访客数', '人群推广访客数', '场景推广访客数',
        '关键词推广加购人数', '人群推广加购人数', '场景推广加购人数',
        '关键词推广加购数', '人群推广加购数', '场景推广加购数',
    },
    SOURCE_WUJIE_SUBJECT: {
        '宝贝ID', '付费', '计划ID', '商品支付转化率', '投入产出比',
        '直接购物车数', '场景名字', '转化周期',
        '展现', '点击', '点击率', '转化率', '商品加购件数',
        # v2.55：计划名字此前被白名单丢掉，导致「推广计划管理 / 推广明细」只有计划ID；
        # 源文件（无界-商品主体）确实带这一列，放行后由 merge 汇总到计划明细记录里。
        '计划名字',
        '主体类型',  # P0-1: 供 merge 剔除"店铺"主体假商品(店铺首页混入商品池)
    },
    SOURCE_PRODUCT_OVERALL: {
        '统计日期', '店铺', '商品ID', '宝贝ID', '商品名称', '淘宝类目',
        '商品状态', '主图整体点击率',
    },
    SOURCE_WUJIE_CREATIVE: {
        '统计日期', '店铺', '转化周期', '计划ID', '计划名字', '创意ID', '创意名字',
        '主体ID', '主体名称', '宝贝ID', '主体类型', '场景名字', '展现', '点击', '点击率',
    },
    SOURCE_STOCK: {
        '宝贝ID', '货号', '在仓库存', '在途', '成本价（含税）', '日销7',
    },
    SOURCE_PRODUCT_INFO: {
        '宝贝ID', '款式主图', '货号', '创建时间', '在途', '成本价（含税）',
    },
}


def _allowed_raw_columns(source_type):
    fields = SOURCE_ALLOWED_FIELDS.get(source_type)
    if not fields:
        return None

    allowed = set(fields)
    for field in fields:
        allowed.update(FIELD_ALIASES.get(field, []))
    if source_type == SOURCE_WUJIE_SUBJECT:
        allowed.update(WUJIE_REMAP.keys())
    return {str(col).strip().lower() for col in allowed}


def _filter_source_columns(df, source_type):
    allowed = _allowed_raw_columns(source_type)
    if not allowed:
        return df
    keep = [col for col in df.columns if str(col).strip().lower() in allowed]
    return df.loc[:, keep] if keep else df


def _coalesce_duplicate_columns(df):
    if not df.columns.has_duplicates:
        return df

    out = pd.DataFrame(index=df.index)
    for col in dict.fromkeys(df.columns):
        part = df.loc[:, df.columns == col]
        if isinstance(part, pd.Series) or part.shape[1] == 1:
            out[col] = part.iloc[:, 0] if isinstance(part, pd.DataFrame) else part
            continue
        clean = part.replace(r'^\s*$', pd.NA, regex=True)
        out[col] = clean.bfill(axis=1).iloc[:, 0].fillna('')
    return out


def _is_15_day_period(value):
    text = str(value)
    digits = ''.join(ch for ch in text if ch.isdigit())
    return digits == '15'


_PERIOD_RANGE_RE = re.compile(r'^\s*(\d{4})[-/]?(\d{2})[-/]?(\d{2})\s*[至~\-—]\s*(\d{4})[-/]?(\d{2})[-/]?(\d{2})\s*$')
_SINGLE_DATE8_RE = re.compile(r'^\s*(\d{4})(\d{2})(\d{2})\s*$')


def _normalize_period_date(value):
    """把「20261105至20261105」这类区间串 / 8 位日期归一成 YYYY-MM-DD。

    区间跨多天时取起始日（无界按日导出，起止恒为同一天）；非匹配值原样返回，
    保证旧格式（已是 2026-11-05）行为完全不变。
    """
    if value is None:
        return value
    text = str(value).strip()
    if not text or text.lower() in ('nan', 'nat', 'none'):
        return ''
    match = _PERIOD_RANGE_RE.match(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = _SINGLE_DATE8_RE.match(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return text


def _xlsx_col_to_index(cell_ref):
    match = COL_REF_RE.match(cell_ref or '')
    if not match:
        return None
    value = 0
    for ch in match.group(1):
        value = value * 26 + ord(ch) - 64
    return value - 1


def _xlsx_first_sheet_path(zf):
    workbook = 'xl/workbook.xml'
    rels = 'xl/_rels/workbook.xml.rels'
    if workbook not in zf.namelist() or rels not in zf.namelist():
        return 'xl/worksheets/sheet1.xml'

    first_rid = None
    with zf.open(workbook) as fh:
        for _, elem in iterparse(fh, events=('end',)):
            if elem.tag == XLSX_NS + 'sheet':
                first_rid = elem.attrib.get(OFFICE_RELS_NS + 'id')
                elem.clear()
                break
            elem.clear()
    if not first_rid:
        return 'xl/worksheets/sheet1.xml'

    with zf.open(rels) as fh:
        for _, elem in iterparse(fh, events=('end',)):
            if elem.tag == RELS_NS + 'Relationship' and elem.attrib.get('Id') == first_rid:
                target = elem.attrib.get('Target', 'worksheets/sheet1.xml')
                if target.startswith('/'):
                    return target.lstrip('/')
                return 'xl/' + target
            elem.clear()
    return 'xl/worksheets/sheet1.xml'


def _xlsx_shared_strings(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []

    values = []
    texts = []
    with zf.open('xl/sharedStrings.xml') as fh:
        for _, elem in iterparse(fh, events=('end',)):
            if elem.tag == XLSX_NS + 't':
                texts.append(elem.text or '')
            elif elem.tag == XLSX_NS + 'si':
                values.append(''.join(texts))
                texts = []
                elem.clear()
    return values


def _xlsx_cell_value(cell, shared_strings):
    cell_type = cell.attrib.get('t')
    if cell_type == 'inlineStr':
        return ''.join(text.text or '' for text in cell.iter(XLSX_NS + 't'))

    value = cell.find(XLSX_NS + 'v')
    if value is None or value.text is None:
        return ''
    if cell_type == 's':
        try:
            return shared_strings[int(value.text)]
        except Exception:
            return ''
    return value.text


def _read_xlsx_selected(filepath, source_type):
    allowed = _allowed_raw_columns(source_type)
    if not allowed:
        raise ValueError('source_type has no column whitelist')

    rows = []
    header = None
    keep_indexes = []
    with zipfile.ZipFile(filepath) as zf:
        sheet_path = _xlsx_first_sheet_path(zf)
        shared_strings = _xlsx_shared_strings(zf)
        with zf.open(sheet_path) as fh:
            for _, row in iterparse(fh, events=('end',)):
                if row.tag != XLSX_NS + 'row':
                    continue

                row_values = {}
                for cell in row.findall(XLSX_NS + 'c'):
                    idx = _xlsx_col_to_index(cell.attrib.get('r', ''))
                    if idx is not None:
                        row_values[idx] = _xlsx_cell_value(cell, shared_strings)

                if header is None:
                    if any(str(v).strip().lower() in allowed for v in row_values.values()):
                        max_idx = max(row_values) if row_values else -1
                        raw_header = [str(row_values.get(i, '')).strip() for i in range(max_idx + 1)]
                        keep_indexes = [
                            idx for idx, col in enumerate(raw_header)
                            if col.strip().lower() in allowed
                        ]
                        header = [raw_header[idx] for idx in keep_indexes]
                    row.clear()
                    continue

                values = [row_values.get(idx, '') for idx in keep_indexes]
                if any(str(v).strip() for v in values):
                    rows.append(values)
                row.clear()

    if header is None:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=header, dtype=str)


def _find_header_row(df):
    """自动查找表头行"""
    for i in range(min(len(df), 15)):
        row_str = '|'.join(df.iloc[i].dropna().astype(str).values)
        if any(kw in row_str for kw in [
            '宝贝ID','商品ID','主体ID','GMV','花费','日期','货号','库存',
            '计划ID','创意ID','商品状态','主图整体点击率','款式编码','款式主图',
        ]):
            return i
    return 0


def _find_excel_header_row(filepath):
    wb = load_workbook(filepath, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = []
        for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True)):
            rows.append(list(row))
            row_str = '|'.join(str(v) for v in row if v is not None)
            if any(kw in row_str for kw in [
                '宝贝ID','商品ID','主体ID','GMV','花费','日期','货号','库存',
                '计划ID','创意ID','商品状态','主图整体点击率','款式编码','款式主图',
            ]):
                return idx
        return _find_header_row(pd.DataFrame(rows))
    finally:
        wb.close()


def read_table(filepath, source_type=None):
    """读取 Excel/CSV/zip(内嵌 CSV)，返回标准化 DataFrame"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.zip':
        # v2.60：无界报表 zip —— 成员只有 1 个 CSV（GBK），读出后走与 CSV 相同的表头探测
        from .zip_sources import WUJIE_ZIP_PERIOD, read_zip_table
        df = read_zip_table(filepath)
        hr = _find_header_row(df)
        df.columns = df.iloc[hr].astype(str).str.strip()
        df = df.iloc[hr+1:].reset_index(drop=True).dropna(how='all')
        # 新导出为单转化窗口（实测等价 15 天转化），补常量列以沿用「只取 15 天」链路，
        # 同时满足数据契约对『转化周期』的必需字段校验
        if source_type in (SOURCE_WUJIE_SUBJECT, SOURCE_WUJIE_CREATIVE) and '转化周期' not in df.columns:
            df['转化周期'] = WUJIE_ZIP_PERIOD
        return _filter_source_columns(df, source_type)
    if ext == '.csv':
        last_error = None
        # utf-8-sig 置首:剥离 BOM,否则首列名带 \ufeff 会被列白名单静默丢弃(2026-07-07 验证发现 D1)
        for enc in ['utf-8-sig','gbk','gb18030','utf-16']:
            try:
                with open(filepath, 'r', encoding=enc, newline='') as f:
                    rows = list(csv.reader(f))
                width = max((len(r) for r in rows), default=0)
                df = pd.DataFrame([r + [''] * (width - len(r)) for r in rows], dtype=str)
                break
            except Exception as exc:
                last_error = exc
                continue
        else:
            raise last_error or UnicodeDecodeError("csv", b"", 0, 1, "unable to decode")
    else:
        if source_type and ext == '.xlsx':
            try:
                return _read_xlsx_selected(filepath, source_type).dropna(how='all').reset_index(drop=True)
            except Exception:
                hr = _find_excel_header_row(filepath)
                allowed = _allowed_raw_columns(source_type)
                df = pd.read_excel(
                    filepath,
                    dtype=str,
                    header=hr,
                    usecols=(lambda col: str(col).strip().lower() in allowed) if allowed else None,
                )
                df.columns = df.columns.astype(str).str.strip()
                return df.dropna(how='all').reset_index(drop=True)
        df = pd.read_excel(filepath, dtype=str, header=None)

    hr = _find_header_row(df)
    df.columns = df.iloc[hr].astype(str).str.strip()
    df = df.iloc[hr+1:].reset_index(drop=True).dropna(how='all')
    return _filter_source_columns(df, source_type)


def _parsed_cache_path(filepath, source_type, parsed_cache_dir=None, extra_token=None):
    path = Path(filepath)
    stat = path.stat()
    raw = (f"{PARSED_CACHE_VERSION}|{source_schema_fingerprint()}|"
           f"{path.resolve()}|{source_type}|{stat.st_size}|{stat.st_mtime_ns}")
    if extra_token:
        # v2.60：zip 在「同名同大小」下仍可能换成员，把成员名/CRC/大小补进键
        raw = f"{raw}|{extra_token}"
    key = hashlib.md5(raw.encode('utf-8')).hexdigest()
    cache_dir = Path(parsed_cache_dir) if parsed_cache_dir else path.parent / '_parsed'
    return cache_dir / f'{key}.pkl'


def source_schema_fingerprint() -> str:
    """取数 schema 指纹 = schema 表指纹 + 本模块「源类型→允许列」白名单指纹。

    v2.56：编进解析层缓存键与日事实缓存校验，改动白名单/别名后缓存自动失效，
    不需要人工记得升 `PARSED_CACHE_VERSION` / `DAILY_FACT_CACHE_VERSION`
    （v2.55 就是因为漏升一次，出现"改了白名单但结果没变"）。
    """
    payload = {
        'schema': schema_fingerprint(),
        'allowed': {
            str(source): sorted(str(column) for column in columns)
            for source, columns in sorted(SOURCE_ALLOWED_FIELDS.items())
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]


def read_prepared_source(filepath, source_type, parsed_cache_dir=None):
    """读取并标准化单个源文件，使用磁盘缓存避免重复解析 Excel。"""
    extra_token = None
    if os.path.splitext(str(filepath))[1].lower() == '.zip':
        from .zip_sources import zip_member_token
        extra_token = zip_member_token(filepath)
    cache_path = _parsed_cache_path(filepath, source_type, parsed_cache_dir=parsed_cache_dir,
                                    extra_token=extra_token)
    if cache_path.exists():
        cached = pd.read_pickle(cache_path)
        return validate_source_dataframe(cached, source_type, str(filepath))

    raw = read_table(filepath, source_type=source_type)
    df = prepare_source(raw, source_type, filepath=filepath)
    validate_source_dataframe(df, source_type, str(filepath))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix('.tmp')
    df.to_pickle(tmp_path)
    os.replace(tmp_path, cache_path)
    return df


def normalize_columns(df):
    """列名标准化"""
    rename = {}
    for col in df.columns:
        s = str(col).strip()
        for std, aliases in FIELD_ALIASES.items():
            if s in aliases or s.lower() in [a.lower() for a in aliases]:
                rename[col] = std
                break
    df = df.rename(columns=rename)
    return _coalesce_duplicate_columns(df)


_GOODS_OVERALL_SINGLE_FILE_RE = re.compile(
    r'商品[-_]?整体[-_]?\d{4}[-_]?\d{2}[-_]?\d{2}[-_]\d{4}[-_]?\d{2}[-_]?\d{2}$'
)


def prepare_source(df, source_type, *, filepath=None):
    """对特定数据源做额外处理

    ``filepath``（可选）：个别处理需要按文件名判断格式，例如 2026-11-05 起的
    「商品_整体_<起>_<止>.xls」单文件导出。
    """
    df = df.copy()
    preserved_product_id = None
    if source_type == SOURCE_PRODUCT_OVERALL and '商品ID' in df.columns:
        preserved_product_id = df['商品ID'].copy()
        df = df.rename(columns={'商品ID': '__商品ID__'})
        if '宝贝ID' not in df.columns:
            df['宝贝ID'] = preserved_product_id

    preserved_subject_id = None
    if source_type == SOURCE_WUJIE_CREATIVE and '主体ID' in df.columns:
        preserved_subject_id = df['主体ID'].copy()
        df = df.rename(columns={'主体ID': '__主体ID__'})
        if '宝贝ID' not in df.columns:
            df['宝贝ID'] = preserved_subject_id

    df = normalize_columns(df)

    if source_type == SOURCE_PRODUCT_OVERALL and preserved_product_id is not None:
        df['商品ID'] = preserved_product_id.astype(str).str.replace(r'\.0$','', regex=True).str.strip()
        df['商品ID'] = df['商品ID'].replace('nan','').replace('None','')
        df['宝贝ID'] = df['商品ID']
        if '__商品ID__' in df.columns:
            df = df.drop(columns=['__商品ID__'])

    if (source_type == SOURCE_PRODUCT_OVERALL and '主图整体点击率' not in df.columns
            and filepath is not None
            and _GOODS_OVERALL_SINGLE_FILE_RE.search(Path(str(filepath)).stem)):
        # 新单文件导出（商品_整体_<起>_<止>.xls）不含该列；留空而非补 0，
        # 下游按「无数据」处理。旧命名（商品-整体-YYYY-MM-DD）仍按契约缺字段报错。
        df['主图整体点击率'] = ''

    if source_type == SOURCE_WUJIE_CREATIVE and preserved_subject_id is not None:
        df['主体ID'] = preserved_subject_id.astype(str).str.replace(r'\.0$','', regex=True).str.strip()
        df['主体ID'] = df['主体ID'].replace('nan','').replace('None','')
        df['宝贝ID'] = df['主体ID']
        if '__主体ID__' in df.columns:
            df = df.drop(columns=['__主体ID__'])

    if source_type == SOURCE_WUJIE_SUBJECT:
        remap = {k: v for k, v in WUJIE_REMAP.items() if k in df.columns}
        df = df.rename(columns=remap)

    # 标准化ID列
    for c in ['宝贝ID','商品ID','货号','计划ID','创意ID','主体ID']:
        if c in df.columns:
            df[c] = df[c].astype(str).str.replace(r'\.0$','', regex=True).str.strip()
            df[c] = df[c].replace('nan','').replace('None','')

    if source_type == SOURCE_WUJIE_CREATIVE and '转化周期' in df.columns:
        df = df[df['转化周期'].map(_is_15_day_period)].reset_index(drop=True)

    # v2.60：无界 zip 导出的「日期」列是区间串（20261105至20261105），归一成单日
    # YYYY-MM-DD，否则按日匹配会全部落空（旧 xlsx 已是 2026-11-05，原样通过）
    if '统计日期' in df.columns:
        df['统计日期'] = df['统计日期'].map(_normalize_period_date)

    return df


def dedupe_source_files(entries):
    """同一目录下同一源类型出现多份时择一（zip 与 xlsx 并存场景）。

    背景：2026-11-05 起无界既有 zip 新包，也可能残留同日 xlsx 导出，两者是**同一批数据**
    （实测 zip 1,570 行 / 花费 16,950.21 = xlsx 15 天转化子集），同时读入会把花费算两遍。

    优先级：``.zip``（新导出）> ``.xlsx/.xls`` > 其它；同级按 mtime 取最新。
    返回 ``(kept, dropped)``，条目保持传入顺序。
    """
    def _rank(entry):
        name = str(entry.get('name') or entry.get('path') or '')
        ext = os.path.splitext(name)[1].lower()
        rank = 0 if ext == '.zip' else 1 if ext in ('.xlsx', '.xls') else 2
        try:
            mtime = os.path.getmtime(str(entry.get('path')))
        except OSError:
            mtime = 0.0
        return (rank, -mtime, name)

    best = {}
    for entry in entries:
        source = entry.get('source')
        current = best.get(source)
        if current is None or _rank(entry) < _rank(current):
            best[source] = entry

    kept, dropped = [], []
    for entry in entries:
        (kept if best.get(entry.get('source')) is entry else dropped).append(entry)
    return kept, dropped


def scan_folder(path):
    """非递归扫描文件夹中的 Excel/CSV/无界报表 zip 文件（同源类型择一）"""
    if not path or not os.path.isdir(path):
        return []
    files = []
    for fn in sorted(os.listdir(path)):
        fp = os.path.join(path, fn)
        if os.path.isfile(fp) and not fn.startswith('~$'):
            ext = os.path.splitext(fn)[1].lower()
            if ext in ('.xlsx','.xls','.csv','.zip'):
                source = classify_file(fn)
                if source in ALLOWED_SOURCE_TYPES:
                    files.append({'path': fp, 'name': fn, 'source': source})
    kept, _dropped = dedupe_source_files(files)
    return kept
