"""Fail closed when a MetoX business-rule release is not traceable."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION.json"
PROTECTED_FILES = {
    ".streamlit/config.toml",
    "app.py",
    "launcher.py",
    "core/analysis.py",
    "core/dataframe_clipboard.py",
    "core/data_validation.py",
    "core/daily_cache.py",
    "core/exporter.py",
    "core/field_mapping.py",
    "core/jst_refund.py",
    "core/jst_stock.py",
    "core/loader.py",
    "core/merge.py",
    "core/metric_labels.py",
    "core/metox_algorithm.py",
    "core/new_product_backtest.py",
    "core/new_product_first_seen.py",
    "core/new_product_lifecycle.py",
    "core/periods.py",
    "core/schema.py",
    "core/segmentation.py",
    "core/v93_algorithm.py",
    "core/v93_signal.py",
    "scripts/build_launcher.py",
    "scripts/smoke_three_entries.ps1",
    "启动MetoX半自动推广工具.bat",
    "启动半自动推广工具.bat",
}
CURRENT_DOCS = {
    "README.md": "MetoX v{version}",
    "README_离线使用说明.txt": "MetoX v{version}",
    "第三方使用说明_先看这个.txt": "MetoX v{version}",
    "半自动推广工具_数据源与指标说明.md": "MetoX v{version}",
    "MetoX{version}_使用说明与运营操作顺序.md": "MetoX v{version}",
    "MetoX{version}_新运营培训手册.html": "MetoX{version}",
}
VERSION_RE = re.compile(r"^[1-9]\d*\.\d+$")
TAG_RE = re.compile(r"^MetoX([1-9]\d*)\.(\d+)$")


def _git(*args: str, check: bool = True) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={ROOT.as_posix()}",
        "-C",
        str(ROOT),
        *args,
    ]
    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def _version_tuple(value: str) -> tuple[int, int]:
    major, minor = value.split(".")
    return int(major), int(minor)


def _function_source(source: str, function_name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _latest_version_tag() -> tuple[str | None, tuple[int, int] | None]:
    tags = []
    for tag in _git("tag", "--list", "MetoX*").splitlines():
        match = TAG_RE.fullmatch(tag.strip())
        if match:
            tags.append((tag.strip(), (int(match.group(1)), int(match.group(2)))))
    if not tags:
        return None, None
    return max(tags, key=lambda item: item[1])


def check_release(require_release_branch: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        info = json.loads(VERSION_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"VERSION.json cannot be read: {exc}"]

    version = str(info.get("version", ""))
    product = str(info.get("product", ""))
    release_date = str(info.get("release_date", ""))
    if product != "MetoX":
        errors.append("VERSION.json product must be MetoX")
    if not VERSION_RE.fullmatch(version):
        errors.append("VERSION.json version must use major.minor, for example 2.0")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", release_date):
        errors.append("VERSION.json release_date must use YYYY-MM-DD")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("refund_risk"):
        errors.append("VERSION.json must record rule_versions.refund_risk")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("startup_launcher"):
        errors.append("VERSION.json must record rule_versions.startup_launcher")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("runtime_packaging"):
        errors.append("VERSION.json must record rule_versions.runtime_packaging")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("direct_purchase_metrics"):
        errors.append("VERSION.json must record rule_versions.direct_purchase_metrics")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("ai_task_period_alignment"):
        errors.append("VERSION.json must record rule_versions.ai_task_period_alignment")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("ai_task_experience"):
        errors.append("VERSION.json must record rule_versions.ai_task_experience")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("ai_task_no_plan_semantics"):
        errors.append("VERSION.json must record rule_versions.ai_task_no_plan_semantics")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("link_roi_refund_breakeven"):
        errors.append("VERSION.json must record rule_versions.link_roi_refund_breakeven")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("strict_same_group_baseline"):
        errors.append("VERSION.json must record rule_versions.strict_same_group_baseline")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("sellable_days_status"):
        errors.append("VERSION.json must record rule_versions.sellable_days_status")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("first_run_email_prompt"):
        errors.append("VERSION.json must record rule_versions.first_run_email_prompt")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("ai_task_plan_pipeline"):
        errors.append("VERSION.json must record rule_versions.ai_task_plan_pipeline")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("data_contract_validation"):
        errors.append("VERSION.json must record rule_versions.data_contract_validation")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("category_analysis_copy"):
        errors.append("VERSION.json must record rule_versions.category_analysis_copy")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_column_order"):
        errors.append("VERSION.json must record rule_versions.execution_column_order")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("metox_cart_burst_label"):
        errors.append("VERSION.json must record rule_versions.metox_cart_burst_label")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("ai_task_metox_priority"):
        errors.append("VERSION.json must record rule_versions.ai_task_metox_priority")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_cart_rates"):
        errors.append("VERSION.json must record rule_versions.execution_cart_rates")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("category_investment_export"):
        errors.append("VERSION.json must record rule_versions.category_investment_export")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_metric_statuses"):
        errors.append("VERSION.json must record rule_versions.execution_metric_statuses")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("metric_status_rule_export"):
        errors.append("VERSION.json must record rule_versions.metric_status_rule_export")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_table_styler_capacity"):
        errors.append("VERSION.json must record rule_versions.execution_table_styler_capacity")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_top_category_products"):
        errors.append("VERSION.json must record rule_versions.execution_top_category_products")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("execution_top_status_colors"):
        errors.append("VERSION.json must record rule_versions.execution_top_status_colors")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("daily_fact_cache"):
        errors.append("VERSION.json must record rule_versions.daily_fact_cache")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("shared_dimension_cache"):
        errors.append("VERSION.json must record rule_versions.shared_dimension_cache")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_source_ingestion"):
        errors.append("VERSION.json must record rule_versions.new_product_source_ingestion")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_first_seen"):
        errors.append("VERSION.json must record rule_versions.new_product_first_seen")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_lifecycle_rules"):
        errors.append("VERSION.json must record rule_versions.new_product_lifecycle_rules")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_material_ctr"):
        errors.append("VERSION.json must record rule_versions.new_product_material_ctr")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_backtest"):
        errors.append("VERSION.json must record rule_versions.new_product_backtest")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_view"):
        errors.append("VERSION.json must record rule_versions.new_product_view")
    if not isinstance(info.get("rule_versions"), dict) or not info["rule_versions"].get("new_product_lazy_history_load"):
        errors.append("VERSION.json must record rule_versions.new_product_lazy_history_load")

    latest_tag, latest_version = _latest_version_tag()
    changed: set[str] = set()
    if latest_tag:
        changed = {
            line.replace("\\", "/")
            for line in _git("diff", "--name-only", latest_tag, "--").splitlines()
            if line.strip()
        }
        changed.update(
            line.replace("\\", "/")
            for line in _git("ls-files", "--others", "--exclude-standard").splitlines()
            if line.strip()
        )
        protected_changed = sorted(changed.intersection(PROTECTED_FILES))
        if protected_changed and VERSION_RE.fullmatch(version) and _version_tuple(version) <= latest_version:
            errors.append(
                f"protected business files changed since {latest_tag} but version {version} was not bumped: "
                + ", ".join(protected_changed)
            )

    app_source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    if "from core.version import APP_VERSION, DISPLAY_VERSION, PRODUCT_NAME" not in app_source:
        errors.append("app.py must derive the application version from core.version")
    if re.search(r"^APP_VERSION\s*=", app_source, flags=re.MULTILINE):
        errors.append("app.py must not hardcode APP_VERSION")
    if "DISPLAY_VERSION" not in app_source:
        errors.append("app.py must render DISPLAY_VERSION")
    if "新品视图" not in app_source:
        errors.append("app.py must render the read-only 新品视图 tab")
    if "加载新品历史" not in app_source or "刷新新品历史" not in app_source:
        errors.append("新品视图 must expose a lazy history load/refresh button")
    load_folder_source = _function_source(app_source, "_load_folder_cached")
    if "_load_new_product_history(" in load_folder_source:
        errors.append("main promotion load must not eager-load new-product history")

    launcher_source = (ROOT / "launcher.py").read_text(encoding="utf-8")
    for snippet in (
        "find_available_port",
        'HOST = "127.0.0.1"',
        'DEFAULT_PORT = 8765',
        '"--global.developmentMode"',
        "streamlit.runtime.scriptrunner.magic_funcs",
        "AppTest.from_file",
        "METOX_APP_TEST",
    ):
        if snippet not in launcher_source:
            errors.append(f"launcher port-selection contract missing: {snippet}")
    primary_bat = (ROOT / "启动MetoX半自动推广工具.bat").read_text(encoding="utf-8-sig")
    if "set TGTOOL_PORT=8765" in primary_bat:
        errors.append("primary launcher batch file must not force fixed port 8765")

    init_source = (ROOT / "core" / "__init__.py").read_text(encoding="utf-8")
    if "from .segmentation import" not in init_source or "refund_rate" in init_source:
        errors.append("core/__init__.py must re-export segmentation instead of copying business rules")

    segmentation = (ROOT / "core" / "segmentation.py").read_text(encoding="utf-8")
    required_segmentation = (
        "if refund_rate > 0.8:",
        "elif refund_rate > 0.7:",
        "if refund_rate > 0.7 and action in ('放量','适当放量'):",
    )
    for snippet in required_segmentation:
        if snippet not in segmentation:
            errors.append(f"refund segmentation contract missing: {snippet}")
    analysis = (ROOT / "core" / "analysis.py").read_text(encoding="utf-8")
    exporter = (ROOT / "core" / "exporter.py").read_text(encoding="utf-8")
    if "df['退款率'] > 0.7" not in analysis:
        errors.append("page refund-risk line must be strict >0.7")
    if "df['退款率'] > 0.7" not in exporter:
        errors.append("Excel refund-risk line must be strict >0.7")

    schema = (ROOT / "core" / "schema.py").read_text(encoding="utf-8")
    loader = (ROOT / "core" / "loader.py").read_text(encoding="utf-8")
    merge = (ROOT / "core" / "merge.py").read_text(encoding="utf-8")
    daily_cache = (ROOT / "core" / "daily_cache.py").read_text(encoding="utf-8")
    periods = (ROOT / "core" / "periods.py").read_text(encoding="utf-8")
    data_validation = (ROOT / "core" / "data_validation.py").read_text(encoding="utf-8")
    if "'直接购物车数':'直购数量'" not in schema:
        errors.append("direct-purchase quantity must map from 直接购物车数")
    if "'直接成交笔数':'直购数量'" in schema:
        errors.append("direct-purchase quantity must not map from 直接成交笔数")
    if "PARSED_CACHE_VERSION = 'v11'" not in loader:
        errors.append("source-schema validation requires parsed cache v11")
    for snippet in (
        "SOURCE_PRODUCT_OVERALL",
        "SOURCE_WUJIE_CREATIVE",
        "商品-整体",
        "无界-创意",
    ):
        if snippet not in schema and snippet not in loader and snippet not in data_validation:
            errors.append(f"new-product source ingestion contract missing: {snippet}")
    for snippet in (
        "validate_required_columns",
        "有付费但没有PlanID",
        "有付费但计划数据缺失或不一致",
    ):
        if snippet not in data_validation:
            errors.append(f"source-schema validation contract missing: {snippet}")
    if "grouped['直购成本'] = _safe_div_series(" not in merge:
        errors.append("daily direct-purchase cost must be spend divided by quantity")
    if 'grouped["直购成本"] = _ratio_series(spend, grouped["直购数量"])' not in periods:
        errors.append("period direct-purchase cost must be recalculated from period totals")
    for snippet in (
        "metox2.30-daily-facts-v2",
        "metox2.30-shared-dimensions-v2",
        "os.replace(tmp_path, cache_path)",
        "verified_fingerprint",
        "daily fact sources changed before cache write",
        "shared dimension sources changed before cache write",
    ):
        if snippet not in daily_cache:
            errors.append(f"daily cache safety contract missing: {snippet}")
    for relative in (
        "core/new_product_first_seen.py",
        "core/new_product_lifecycle.py",
        "core/new_product_backtest.py",
    ):
        if not (ROOT / relative).is_file():
            errors.append(f"new-product core module missing: {relative}")
    first_seen_source = (ROOT / "core" / "new_product_first_seen.py").read_text(encoding="utf-8") if (ROOT / "core" / "new_product_first_seen.py").is_file() else ""
    lifecycle_source = (ROOT / "core" / "new_product_lifecycle.py").read_text(encoding="utf-8") if (ROOT / "core" / "new_product_lifecycle.py").is_file() else ""
    backtest_source = (ROOT / "core" / "new_product_backtest.py").read_text(encoding="utf-8") if (ROOT / "core" / "new_product_backtest.py").is_file() else ""
    for snippet in (
        "首次在线发现日",
        "是否历史截断",
        "STYLE_KEY",
    ):
        if snippet not in first_seen_source:
            errors.append(f"new-product first-seen contract missing: {snippet}")
    for snippet in (
        "D15",
        "D30",
        "保护状态",
        "流量验证",
        "下架",
        "继续观察",
        "商品加购率",
    ):
        if snippet not in lifecycle_source:
            errors.append(f"new-product lifecycle contract missing: {snippet}")
    for snippet in (
        "decision_visitor_min",
        "period_min_valid_days",
        "min_peer_count",
        "本轮成交",
        "D15累计已有成交，动销保护",
        "D30累计已有成交，动销保护",
        '"sale_prefix": "D1_D30"',
        '"traffic_prefix": "D1_D30"',
        'traffic_visitors = _num(row, f"{traffic_prefix}_商品访客数")',
        "if traffic_visitors >= config.decision_visitor_min",
        "REASON_D15_SALE_PROTECTED",
        "REASON_D30_SALE_PROTECTED",
    ):
        if snippet not in lifecycle_source:
            errors.append(f"new-product v2.37 lifecycle rule missing: {snippet}")
    for snippet in (
        "rule_",
        "precision",
        "false",
        "coverage",
    ):
        if snippet not in backtest_source:
            errors.append(f"new-product backtest contract missing: {snippet}")
    if "result_df['商品加购率'] = _safe_div_series(add_cart, visitors)" not in merge:
        errors.append("daily product cart rate must be add-cart quantity divided by product visitors")
    if "result_df['MetoX推广加购件数'], result_df['MetoX推广访客数']" not in merge:
        errors.append("daily promotion cart rate must be promotion cart quantity divided by promotion visitors")
    if 'grouped["商品加购率"] = _ratio_series(add_cart, visitors)' not in periods:
        errors.append("period product cart rate must be recalculated from period totals")
    if 'grouped["MetoX推广加购件数"], grouped["MetoX推广访客数"]' not in periods:
        errors.append("period promotion cart rate must be recalculated from promotion totals")
    metric_labels = (ROOT / "core" / "metric_labels.py").read_text(encoding="utf-8")
    for snippet in (
        'HISTORY_DAYS = 28',
        'RATE_SAMPLE_MIN_VISITORS = 100',
        'breakeven * 1.2',
        'quantile(0.75)',
        'refund_after_gsv = gmv * (1 - refund_rate)',
        'refund_after_cost = estimated_units * cost',
        'available_gross_profit = refund_after_gsv - refund_after_cost',
        'breakeven = gmv / available_gross_profit.where(available_gross_profit > 0)',
        'sellable_days < 14',
        'sellable_days >= 28',
        'sellable_days <= 40',
        'sellable_days > 40',
        'out["商品加购率状态"]',
        'out["商品支付转化率状态"]',
        'out["推广加购率状态"]',
    ):
        if snippet not in metric_labels:
            errors.append(f"execution metric status contract missing: {snippet}")
    for snippet in (
        'METRIC_STATUS_RULE_SHEET = "指标状态规则"',
        '"状态字段": "链接投产状态"',
        '"状态字段": "商品支付转化率状态"',
        '"状态字段": "商品加购率状态"',
        '"状态字段": "可售天数状态"',
        '"状态字段": "推广加购率状态"',
        '同组无数据时不跨类目或价格段回退',
        '不使用链接实际投产过滤',
    ):
        if snippet not in metric_labels:
            errors.append(f"metric status rule document contract missing: {snippet}")
    streamlit_config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "smoke_three_entries.ps1").read_text(encoding="utf-8")
    if "showEmailPrompt=false" not in streamlit_config:
        errors.append("project Streamlit config must disable the first-run email prompt")
    if '"--server.showEmailPrompt"' not in launcher:
        errors.append("launcher must disable the first-run email prompt")
    if "Test-MetoXFreshProfile" not in smoke or 'Email:' not in smoke:
        errors.append("release smoke must cover a fresh user profile without an email prompt")
    for snippet in (
        'def _write_metric_status_rule_sheet(writer):',
        '_write_metric_status_rule_sheet(writer)',
    ):
        if snippet not in exporter:
            errors.append(f"metric status rule export contract missing: {snippet}")
    if "CATEGORY_INVESTMENT_SHEET = '品类投放占比与ROI'" not in exporter:
        errors.append("Excel exports must include the category investment and ROI sheet")
    for snippet in (
        'TOP_CATEGORY_LIMIT = 15',
        'TOP_PRODUCT_LIMIT = 50',
        "STATUS_HIGH_BACKGROUND = '#DDEBF7'",
        "STATUS_LOW_BACKGROUND = '#FCE4EC'",
        "STATUS_TEXT_COLOR = '#1F2937'",
        'STATUS_BACKGROUND_BY_COLUMN = {',
        'def top_category_product_mask(',
        'def _top_product_row_mask(',
        "work['_gmv'].gt(0)",
        'worksheet.write(',
        'ranking_df=ranking_df',
    ):
        if snippet not in exporter:
            errors.append(f"execution top-category product highlight contract missing: {snippet}")
    for forbidden in (
        'TOP_PRODUCT_BACKGROUND',
        'TOP_PRODUCT_STYLE',
        'worksheet.set_row(excel_row, None, highlight_format)',
    ):
        if forbidden in exporter:
            errors.append(f"execution non-status cells must remain unfilled: {forbidden}")

    for relative_template, marker_template in CURRENT_DOCS.items():
        relative = relative_template.format(version=version)
        marker = marker_template.format(version=version)
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"current operator document missing: {relative}")
            continue
        if marker not in path.read_text(encoding="utf-8-sig"):
            errors.append(f"current operator document lacks version marker {marker}: {relative}")

    if require_release_branch and VERSION_RE.fullmatch(version):
        branch = _git("branch", "--show-current")
        expected_branch = f"release/MetoX{version}"
        tag = f"MetoX{version}"
        tagged_head = _git("tag", "--points-at", "HEAD").splitlines()
        if branch != expected_branch and tag not in tagged_head:
            errors.append(f"release must run from {expected_branch} or a HEAD tagged {tag}; current={branch or 'detached'}")

    if not errors:
        protected_count = len(changed.intersection(PROTECTED_FILES))
        print(
            f"release-check PASS product={product} version={version} latest_tag={latest_tag or 'none'} "
            f"protected_changes={protected_count}"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="also enforce the release branch/tag gate")
    args = parser.parse_args()
    try:
        errors = check_release(require_release_branch=args.release)
    except Exception as exc:
        errors = [f"release check crashed: {exc}"]
    if errors:
        print("release-check FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
