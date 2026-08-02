r"""生成增强版聚水潭款式级库存快照。

输出列:
宝贝ID, 货号, 款式主图, 创建时间, 在仓库存, 在途, 成本价（含税）

默认写入:
\\10.1.22.253\天猫mark\mark0706啊\聚水潭\库存快照\YYYY-MM
"""
import argparse
from datetime import date
from pathlib import Path

from core.jst_stock import write_daily_enriched_snapshot


DEFAULT_JST_ROOT = Path(r"\\10.1.22.253\天猫mark\mark0706啊\聚水潭")
DEFAULT_DEV_MAPPING = Path(r"E:\08-推广\天猫运营方法论\聚水潭\店铺商品资料_聚水潭映射.csv")


def _default_source_snapshot(snapshot_root, date_str):
    month_dir = Path(snapshot_root) / date_str[:7]
    candidates = [
        month_dir / f"库存快照_{date_str}.csv",
        month_dir / f"库存快照_{date_str}_款式级.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"未找到 {date_str} 的库存快照: {month_dir}")


def parse_args():
    parser = argparse.ArgumentParser(description="生成日更增强库存快照")
    parser.add_argument("--date", default=date.today().isoformat(), help="日期, 例如 2026-07-15")
    parser.add_argument(
        "--jst-root",
        default=str(DEFAULT_JST_ROOT),
        help="聚水潭根目录, 下方应有 店铺商品资料_聚水潭映射.csv 和 库存快照/",
    )
    parser.add_argument("--source-snapshot", default="", help="可选: 指定源库存快照 CSV")
    parser.add_argument("--mapping", default="", help="可选: 指定店铺商品资料_聚水潭映射.csv")
    parser.add_argument("--snapshot-root", default="", help="可选: 指定库存快照根目录")
    return parser.parse_args()


def main():
    args = parse_args()
    jst_root = Path(args.jst_root)
    snapshot_root = Path(args.snapshot_root) if args.snapshot_root else jst_root / "库存快照"
    mapping = Path(args.mapping) if args.mapping else jst_root / "店铺商品资料_聚水潭映射.csv"
    if not mapping.exists() and DEFAULT_DEV_MAPPING.exists():
        mapping = DEFAULT_DEV_MAPPING
    source_snapshot = (
        Path(args.source_snapshot)
        if args.source_snapshot
        else _default_source_snapshot(snapshot_root, args.date)
    )

    out_path = write_daily_enriched_snapshot(source_snapshot, mapping, snapshot_root, args.date)
    print(out_path)


if __name__ == "__main__":
    main()
