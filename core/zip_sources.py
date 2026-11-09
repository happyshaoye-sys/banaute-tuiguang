"""无界报表压缩包（zip）读取支持 —— v2.60 新增，与旧 xlsx 格式并存。

背景
----
2026-11-05 起，生意参谋「无界」后台改为按报表导出 zip，每个日期目录下是若干
``<包名>_<下载时间戳>.zip``，每个 zip 内只有一个 CSV（GBK 编码）：

    商品总表_20261106_093332.zip      → 商品报表_20261106_095456.csv  （商品×计划×场景，81 列）
    创意报表_20261106_101309.zip      → 创意报表_…csv                  （创意级，74 列）
    计划报表_20261106_095347.zip      → 计划报表_…csv                  （计划级，75 列）
    人群报表_20261106_093537.zip      → 人群报表_…csv                  （商品×计划，人群场景子集）
    关键词报表_20261106_093525.zip    → 关键词报表_…csv                （商品×计划，关键词场景子集）
    素材_创意报表_20261106_101344.zip → 素材_创意报表_…csv             （素材级，77 列）

包 → 源类型映射（只接工具真正消费的两类）
----------------------------------------
* ``商品总表`` → ``无界-商品主体``（核心：宝贝×计划×场景的花费/GMV/访客）
* ``创意报表`` → ``无界-创意``（素材表现页：点击量 ÷ 展现量）
* 其余包返回 ``None``，即"识别但不消费"：
  - ``人群报表`` / ``关键词报表`` 是 ``商品总表`` 按场景拆分的**子集**
    （11-05 实测：1,570 = 1,390 + 180，花费按计划ID+主体ID 对齐差异 0.00 元），
    同时接入会重复计数，因此只认 商品总表；
  - ``计划报表`` 为计划级汇总，工具的计划ID/花费取自商品主体，无需另接；
  - ``素材_创意报表`` 为素材级明细，暂不进入主链路（后续可做素材 A/B）。

注意
----
* 包名前缀匹配，**不能用 zip 内的成员名**：成员名带每次下载的时间戳，
  且 ``商品总表_*.zip`` 里的成员实际叫 ``商品报表_*.csv``。
* ``素材_创意报表`` 含 ``创意报表`` 子串，规则表必须「长的在前」，
  否则会被误判成 ``无界-创意``。
* CSV 为 GBK；``日期`` 列形如 ``20261105至20261105``，由
  ``core.loader.prepare_source`` 归一成 ``2026-11-05``。
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from pathlib import Path

import pandas as pd

#: zip 包名前缀 → 源类型；None = 识别但不消费。**顺序敏感：长的、特殊的在前**
ZIP_PACKAGE_RULES = (
    ("素材_创意报表", None),
    ("素材创意报表", None),
    ("创意报表", "无界-创意"),
    ("商品总表", "无界-商品主体"),
    ("商品报表", "无界-商品主体"),
    ("计划报表", None),
    ("人群报表", None),
    ("关键词报表", None),
)

ZIP_EXTENSIONS = (".zip",)

#: 无界 zip 报表是**单转化窗口**导出：11-05 实测「计划报表」与旧 xlsx 的 15 天转化子集
#: 花费/GMV 比值 0.97/0.82（纯日间波动），与 1 天转化子集完全相同 → 等价于 15 天转化。
#: 下游按 `转化周期 == 15天` 过滤，故为 zip 帧补这一常量，保持链路与旧格式一致。
WUJIE_ZIP_PERIOD = "15天转化"

#: zip 内 CSV 的解码顺序：无界导出固定 GBK，优先 GBK 避免个别字节被误判成 UTF-8
ZIP_CSV_ENCODINGS = ("gbk", "gb18030", "utf-8-sig", "utf-16")


def is_zip_path(path) -> bool:
    """判断是否为 zip 压缩包（按扩展名）。"""
    return os.path.splitext(str(path))[1].lower() in ZIP_EXTENSIONS


def classify_zip_package(filename) -> str | None:
    """按 zip 包名前缀判定源类型；返回 None 表示"识别但不消费"，未匹配也返回 None。"""
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    for prefix, source in ZIP_PACKAGE_RULES:
        if stem.startswith(prefix):
            return source
    # 容错：前缀前可能有店铺/日期等修饰，退化为"包含"
    for prefix, source in ZIP_PACKAGE_RULES:
        if prefix in stem:
            return source
    return None


def is_known_zip_package(filename) -> bool:
    """是否属于已登记的无界报表包（含"识别但不消费"的那几类）。"""
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    return any(prefix in stem for prefix, _ in ZIP_PACKAGE_RULES)


def pick_data_member(zf: zipfile.ZipFile) -> str:
    """在一个 zip 里挑出数据成员：优先单个 CSV，其次 xlsx，最后第一个非目录成员。"""
    members = [info for info in zf.infolist() if not info.is_dir()]
    members = [m for m in members if not os.path.basename(m.filename).startswith(("__MACOSX", "."))]
    if not members:
        raise ValueError("zip 内没有可用成员")

    def _rank(info: zipfile.ZipInfo) -> tuple:
        name = info.filename.lower()
        kind = 0 if name.endswith(".csv") else 1 if name.endswith((".xlsx", ".xls")) else 2
        return (kind, -info.file_size, name)

    return sorted(members, key=_rank)[0].filename


def read_zip_member_bytes(filepath, member: str | None = None) -> tuple[str, bytes]:
    """读取 zip 内数据成员，返回 ``(成员名, 原始字节)``。"""
    with zipfile.ZipFile(str(filepath)) as zf:
        name = member or pick_data_member(zf)
        return name, zf.read(name)


def decode_csv_bytes(data: bytes) -> str:
    """按无界导出的编码习惯解码 CSV 文本。"""
    last_error: Exception | None = None
    for encoding in ZIP_CSV_ENCODINGS:
        try:
            return data.decode(encoding)
        except Exception as exc:  # noqa: BLE001 - 逐个候选编码试错
            last_error = exc
    raise last_error or UnicodeDecodeError("zip-csv", data[:8], 0, 1, "unable to decode")


def read_zip_table(filepath, member: str | None = None) -> pd.DataFrame:
    """把 zip 内的 CSV 读成「全部为字符串、无表头」的 DataFrame。

    返回形式与 ``core.loader.read_table`` 的 CSV 分支一致（header 由调用方探测），
    这样后续的列白名单、别名归一、表头定位全部复用同一套逻辑。
    """
    name, data = read_zip_member_bytes(filepath, member)
    text = decode_csv_bytes(data)
    rows = list(csv.reader(io.StringIO(text)))
    width = max((len(r) for r in rows), default=0)
    if not width:
        return pd.DataFrame()
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)


def zip_member_token(filepath) -> str:
    """返回参与缓存键的 zip 标识：成员名 + zip 大小/mtime（外层已含，这里补成员信息）。"""
    try:
        with zipfile.ZipFile(str(filepath)) as zf:
            name = pick_data_member(zf)
            info = zf.getinfo(name)
        return f"{name}|{info.file_size}|{info.CRC}"
    except Exception:  # noqa: BLE001 - 取不到标识时退化为空，不影响读取
        return ""


def describe_zip_member(filepath) -> dict:
    """数据验收展示用：``{'member': 成员名, 'member_mb': 大小}``。"""
    try:
        with zipfile.ZipFile(str(filepath)) as zf:
            name = pick_data_member(zf)
            info = zf.getinfo(name)
        return {"member": name, "member_bytes": int(info.file_size)}
    except Exception:  # noqa: BLE001
        return {"member": Path(str(filepath)).name, "member_bytes": 0}
