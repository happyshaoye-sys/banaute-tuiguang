"""Build an immutable, audited MetoX third-party release artifact.

默认输出目录是发版总目录 ``E:\\08-推广``（与历史 MetoX2.x 产物放在一起）；
可用 ``--output-root`` 或环境变量 ``METOX_RELEASE_DIR`` 覆盖。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
#: 发版产物默认落盘目录（用户约定：所有 MetoX 打包产物统一放在 E:\08-推广）
DEFAULT_OUTPUT_ROOT = Path(r"E:\08-推广")
OVERLAY_FILES = (
    ".streamlit/config.toml",
    "app.py",
    "launcher.py",
    "requirements.txt",
    "README.md",
    "README_离线使用说明.txt",
    "第三方使用说明_先看这个.txt",
    "半自动推广工具_数据源与指标说明.md",
    "执行表字段字典_20260710.html",
    "字段字典.xlsx",
    "VERSION.json",
    "VERSION.md",
    "启动MetoX半自动推广工具.bat",
    "启动半自动推广工具.bat",
    "生成日更库存快照.py",
)
REQUIRED_PATHS = (
    "TuiGuangTool.exe",
    "_internal",
    ".streamlit/config.toml",
    "app.py",
    "launcher.py",
    "core/version.py",
    "core/new_product_first_seen.py",
    "core/new_product_lifecycle.py",
    "core/new_product_backtest.py",
    "core/segmentation.py",
    "VERSION.json",
    "VERSION.md",
    "启动MetoX半自动推广工具.bat",
    "第三方使用说明_先看这个.txt",
)
FORBIDDEN_NAMES = {
    ".git",
    ".env",
    ".agents",
    ".perf_cache",
    "cache",
    "cache_result",
    "__pycache__",
    "secrets.toml",
}
FORBIDDEN_SUFFIXES = {".pyc", ".log"}


def _is_forbidden_path(path: Path) -> bool:
    return (
        path.name in FORBIDDEN_NAMES
        or path.name.startswith(".env.")
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    )


def _run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "cannot resolve source commit")
    return result.stdout.strip()


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if _is_forbidden_path(Path(name)):
            ignored.add(name)
    return ignored


def _safe_remove_new_tree(path: Path, output_root: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != output_root.resolve() or not path.name.startswith("."):
        raise RuntimeError(f"refusing to clean unsafe staging path: {path}")
    if path.exists():
        shutil.rmtree(path)


def _safe_remove_published_tree(path: Path, output_root: Path, package_name: str) -> None:
    resolved = path.resolve()
    if resolved.parent != output_root.resolve() or path.name != package_name:
        raise RuntimeError(f"refusing to clean unsafe release path: {path}")
    if path.exists():
        shutil.rmtree(path)


def _write_zip(source: Path, package_name: str, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                relative = path.relative_to(source)
                archive.write(path, Path(package_name) / relative)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit(
    stage: Path,
    version: str,
    operator_manual_name: str,
    training_manual_name: str,
) -> dict[str, int]:
    missing = [relative for relative in REQUIRED_PATHS if not (stage / relative).exists()]
    for manual_name in (operator_manual_name, training_manual_name):
        if not (stage / manual_name).is_file():
            missing.append(manual_name)
    if missing:
        raise RuntimeError("release is missing required paths: " + ", ".join(missing))

    forbidden = []
    for path in stage.rglob("*"):
        if _is_forbidden_path(path):
            forbidden.append(str(path.relative_to(stage)))
    if forbidden:
        raise RuntimeError("release contains forbidden paths: " + ", ".join(forbidden[:20]))

    version_info = json.loads((stage / "VERSION.json").read_text(encoding="utf-8"))
    if str(version_info.get("version")) != version:
        raise RuntimeError("packaged VERSION.json does not match requested version")
    app_source = (stage / "app.py").read_text(encoding="utf-8-sig")
    if "from core.version import APP_VERSION, DISPLAY_VERSION, PRODUCT_NAME" not in app_source:
        raise RuntimeError("packaged app.py is not connected to VERSION.json")
    if (stage / "core" / "v93_algorithm.py").exists():
        raise RuntimeError("obsolete core/v93_algorithm.py leaked into release")
    launcher_source = (stage / "launcher.py").read_text(encoding="utf-8")
    if (
        "find_available_port" not in launcher_source
        or '"--global.developmentMode"' not in launcher_source
        or "streamlit.runtime.scriptrunner.magic_funcs" not in launcher_source
        or "AppTest.from_file" not in launcher_source
    ):
        raise RuntimeError("packaged launcher lacks the automatic-port production-mode contract")
    config_source = (stage / ".streamlit" / "config.toml").read_text(encoding="utf-8-sig")
    if re.search(r"^\s*port\s*=\s*8765\s*$", config_source, flags=re.MULTILINE):
        raise RuntimeError("packaged Streamlit config still forces port 8765")
    for batch_name in ("启动MetoX半自动推广工具.bat", "启动半自动推广工具.bat"):
        batch_source = (stage / batch_name).read_text(encoding="utf-8-sig")
        if "set TGTOOL_PORT=8765" in batch_source or "TuiGuangTool.exe" not in batch_source:
            raise RuntimeError(f"packaged batch entry point violates launcher contract: {batch_name}")

    files = [path for path in stage.rglob("*") if path.is_file()]
    return {"file_count": len(files), "total_bytes": sum(path.stat().st_size for path in files)}


def build_release(baseline: Path, launcher_runtime: Path, output_root: Path) -> dict[str, object]:
    info = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))
    version = str(info["version"])
    release_date = str(info["release_date"])
    compact_date = release_date.replace("-", "")
    package_name = f"MetoX{version}_{compact_date}"
    operator_manual_name = f"MetoX{version}_使用说明与运营操作顺序.md"
    training_manual_name = f"MetoX{version}_新运营培训手册.html"
    destination = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    hash_path = output_root / f"{package_name}.zip.sha256.txt"

    if not baseline.is_dir():
        raise RuntimeError(f"baseline package does not exist: {baseline}")
    runtime_exe = launcher_runtime / "TuiGuangTool.exe"
    runtime_internal = launcher_runtime / "_internal"
    if not runtime_exe.is_file() or not runtime_internal.is_dir():
        raise RuntimeError(f"launcher runtime is incomplete: {launcher_runtime}")
    if not any(runtime_internal.glob("streamlit-*.dist-info")):
        raise RuntimeError("launcher runtime is missing Streamlit package metadata")
    output_root.mkdir(parents=True, exist_ok=True)
    collisions = [path for path in (destination, zip_path, hash_path) if path.exists()]
    if collisions:
        raise FileExistsError("release artifacts are immutable; refusing overwrite: " + ", ".join(map(str, collisions)))

    stage = Path(tempfile.mkdtemp(prefix=f".{package_name}.staging-", dir=output_root))
    temp_zip = output_root / f".{package_name}.{uuid.uuid4().hex}.zip.tmp"
    published_dir = False
    published_zip = False
    try:
        shutil.copytree(baseline, stage, dirs_exist_ok=True, ignore=_ignore)

        shutil.copy2(runtime_exe, stage / "TuiGuangTool.exe")
        packaged_internal = stage / "_internal"
        if packaged_internal.exists():
            shutil.rmtree(packaged_internal)
        shutil.copytree(runtime_internal, packaged_internal, ignore=_ignore)

        packaged_core = stage / "core"
        if packaged_core.exists():
            shutil.rmtree(packaged_core)
        shutil.copytree(ROOT / "core", packaged_core, ignore=_ignore)

        for relative in OVERLAY_FILES:
            source = ROOT / relative
            if not source.is_file():
                raise RuntimeError(f"release overlay file missing: {relative}")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        for old_manual in stage.glob("MetoX*_使用说明与运营操作顺序.md"):
            old_manual.unlink()
        shutil.copy2(ROOT / operator_manual_name, stage / operator_manual_name)

        for old_manual in stage.glob("MetoX*_新运营培训手册.html"):
            old_manual.unlink()
        shutil.copy2(ROOT / training_manual_name, stage / training_manual_name)

        manifest = {
            "product": info["product"],
            "version": version,
            "release_date": release_date,
            "external_strategy_version": info["external_strategy_version"],
            "source_commit": _git_commit(),
            "baseline_package": baseline.name,
            "launcher_runtime": launcher_runtime.name,
            "launcher_sha256": _sha256(stage / "TuiGuangTool.exe"),
            "startup": {
                "preferred_port": 8765,
                "occupied_port_behavior": "select next available local port",
                "first_run_email_prompt": "disabled",
                "entry_points": [
                    "TuiGuangTool.exe",
                    "启动MetoX半自动推广工具.bat",
                    "启动半自动推广工具.bat",
                ],
            },
            "rules": {
                "refund_extreme": ">80%",
                "refund_high": ">70% and <=80%",
                "refund_page_excel_line": ">70%",
                "refund_action": "scale actions downgrade to 待优化",
                "p1_p4_low_refund": "<40% unchanged",
                "sellable_days_status": "<14 urgent; 14-<28 attention; 28-40 sufficient; >40 overstock",
                "execution_top_categories": 15,
                "execution_top_products_per_category": 50,
                "execution_top_products_positive_gmv_only": True,
                "execution_non_status_background": None,
                "execution_status_text_color": "#1F2937",
                "execution_status_high_background": "#DDEBF7",
                "execution_status_low_background": "#FCE4EC",
                "new_product_view": {
                    "tab": "新品视图",
                    "read_only": True,
                    "history_load": {
                        "main_promotion_load": "does not load new-product history",
                        "lazy_tab_button": "加载新品历史 / 刷新新品历史",
                        "unloaded_state": "new-product data is unloaded and does not block other analysis",
                    },
                    "new_product_d1": "first current-online discovery date from 商品-整体",
                    "decision_grain": "style/货号, fallback 宝贝ID with low confidence",
                    "decision_checkpoints": ["D15", "D30"],
                    "decision_checkpoint_semantics": "fixed result snapshots only",
                    "current_decision_range": "D15-D45",
                    "current_decision_frequency": "daily",
                    "decision_window_days": 45,
                    "decision_periods": ["D1-D15", "D16-D30"],
                    "decision_results": ["下架", "继续观察"],
                    "paid_sale_protection": "any paid buyer, paid unit, or GMV above zero from D1 through the analysis date, capped at D45, protects the style",
                    "visitor_gate": 300,
                    "visitor_gate_scope": "rolling cumulative product visitors from D1 through the analysis date, capped at D45",
                    "traffic_validation_basis": "cumulative visitors only; effective-day sufficiency is evaluated separately",
                    "data_min_valid_days": 12,
                    "peer_min_styles": 20,
                    "down_rule": "from D15, cumulative no sale and cumulative visitors>=300, data days>=12, same-layer sample>=20, cart rate below same-layer baseline*80%; at D45 cumulative no sale and visitors<300 forces down after the history-truncation guard",
                    "link_inventory_context_only": True,
                    "status_inventory_context": "link status and inventory are operational display context only; they do not filter peer baselines or decide down versus observe",
                    "context_fields": ["动作可执行", "库存提示", "保护证据", "下架阻断原因"],
                    "removed_decision_columns": ["商品名称", "商品支付转化率", "访客平均价值"],
                    "removed_lifecycle_columns": ["left_censored", "D14/D15/D21 status and stock", "D15_D21_*", "D1_D21_*"],
                    "hidden_columns": ["商品名称", "商品支付转化率", "访客平均价值"],
                    "material_hidden_columns": ["商品名称"],
                    "main_image_ctr_source": "商品-整体.主图整体点击率",
                    "creative_ctr_formula": "无界-创意 点击量 / 展现量",
                    "metox_algorithm_changed": False,
                },
            },
            "performance": {
                "daily_fact_cache": "metox2.39-daily-facts-v3",
                "shared_dimension_cache": "metox2.30-shared-dimensions-v2",
                "source_invalidation": "path+source_type+size+mtime_ns",
                "atomic_write": True,
                "verified_28day_cold_seconds": 352.067,
                "verified_28day_warm_seconds": 14.179,
                "verified_speedup": 24.831,
                "verified_cache_hits": "28/28",
                "verified_result_equivalence": True,
            },
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        (stage / "RELEASE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        audit = _audit(stage, version, operator_manual_name, training_manual_name)
        _write_zip(stage, package_name, temp_zip)
        stage.rename(destination)
        published_dir = True
        temp_zip.rename(zip_path)
        published_zip = True
        digest = _sha256(zip_path)
        hash_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
        return {
            "package_dir": str(destination),
            "zip": str(zip_path),
            "sha256_file": str(hash_path),
            "sha256": digest,
            **audit,
        }
    except Exception:
        if stage.exists():
            _safe_remove_new_tree(stage, output_root)
        if temp_zip.exists():
            temp_zip.unlink()
        if hash_path.exists():
            hash_path.unlink()
        if published_zip and zip_path.exists():
            zip_path.unlink()
        if published_dir and destination.exists():
            _safe_remove_published_tree(destination, output_root, package_name)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-package", required=True, type=Path)
    parser.add_argument("--launcher-runtime", required=True, type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help=f"发版产物输出目录，默认 {DEFAULT_OUTPUT_ROOT}（可用 METOX_RELEASE_DIR 覆盖）",
    )
    args = parser.parse_args()
    configured_root = str(os.environ.get("METOX_RELEASE_DIR", "") or "").strip()
    output_root = args.output_root or (Path(configured_root) if configured_root else DEFAULT_OUTPUT_ROOT)
    # 回填解析结果：build_release 调用形式（args.output_root.absolute()）保持不变
    args.output_root = Path(output_root)
    print(f"release-build output-root: {args.output_root}", flush=True)
    try:
        _run([sys.executable, str(ROOT / "scripts" / "release_check.py"), "--release"])
        _run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"])
        _run([sys.executable, str(ROOT / "_regression_metox_algorithm.py")])
        _run([sys.executable, str(ROOT / "_regression_export_period.py")])
        _run([sys.executable, str(ROOT / "自检_造数套件.py")])
        result = build_release(
            # Keep caller-provided directory junctions intact. Resolving them here
            # reintroduces non-ASCII target paths in legacy Windows subprocesses.
            args.baseline_package.absolute(),
            args.launcher_runtime.absolute(),
            args.output_root.absolute(),
        )
    except Exception as exc:
        print(f"release-build FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
