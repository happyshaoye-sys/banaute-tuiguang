"""Build an immutable, audited MetoX third-party release artifact."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OVERLAY_FILES = (
    "app.py",
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
    "app.py",
    "core/version.py",
    "core/segmentation.py",
    "VERSION.json",
    "VERSION.md",
    "启动MetoX半自动推广工具.bat",
    "第三方使用说明_先看这个.txt",
)
FORBIDDEN_NAMES = {".git", ".env", "cache", "cache_result", "__pycache__"}


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
        if name in FORBIDDEN_NAMES or name.endswith(".pyc"):
            ignored.add(name)
    return ignored


def _safe_remove_new_tree(path: Path, output_root: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != output_root.resolve() or not path.name.startswith("."):
        raise RuntimeError(f"refusing to clean unsafe staging path: {path}")
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


def _audit(stage: Path, version: str, manual_name: str) -> dict[str, int]:
    missing = [relative for relative in REQUIRED_PATHS if not (stage / relative).exists()]
    if not (stage / manual_name).is_file():
        missing.append(manual_name)
    if missing:
        raise RuntimeError("release is missing required paths: " + ", ".join(missing))

    forbidden = []
    for path in stage.rglob("*"):
        if path.name in FORBIDDEN_NAMES or path.suffix == ".pyc":
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

    files = [path for path in stage.rglob("*") if path.is_file()]
    return {"file_count": len(files), "total_bytes": sum(path.stat().st_size for path in files)}


def build_release(baseline: Path, output_root: Path) -> dict[str, object]:
    info = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))
    version = str(info["version"])
    release_date = str(info["release_date"])
    compact_date = release_date.replace("-", "")
    package_name = f"MetoX半自动推广工具_v{version}_第三方版_{compact_date}"
    manual_name = f"MetoX{version}_新运营培训手册.html"
    destination = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    hash_path = output_root / f"{package_name}.zip.sha256.txt"

    if not baseline.is_dir():
        raise RuntimeError(f"baseline package does not exist: {baseline}")
    output_root.mkdir(parents=True, exist_ok=True)
    collisions = [path for path in (destination, zip_path, hash_path) if path.exists()]
    if collisions:
        raise FileExistsError("release artifacts are immutable; refusing overwrite: " + ", ".join(map(str, collisions)))

    stage = Path(tempfile.mkdtemp(prefix=f".{package_name}.staging-", dir=output_root))
    temp_zip = output_root / f".{package_name}.{uuid.uuid4().hex}.zip.tmp"
    try:
        shutil.copytree(baseline, stage, dirs_exist_ok=True, ignore=_ignore)

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

        for old_manual in stage.glob("MetoX*_新运营培训手册.html"):
            old_manual.unlink()
        shutil.copy2(ROOT / manual_name, stage / manual_name)

        manifest = {
            "product": info["product"],
            "version": version,
            "release_date": release_date,
            "external_strategy_version": info["external_strategy_version"],
            "source_commit": _git_commit(),
            "baseline_package": baseline.name,
            "rules": {
                "refund_extreme": ">80%",
                "refund_high": ">70% and <=80%",
                "refund_page_excel_line": ">70%",
                "refund_action": "scale actions downgrade to 待优化",
                "p1_p4_low_refund": "<40% unchanged",
            },
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        (stage / "RELEASE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        audit = _audit(stage, version, manual_name)
        _write_zip(stage, package_name, temp_zip)
        stage.rename(destination)
        temp_zip.rename(zip_path)
        digest = _sha256(zip_path)
        hash_path.write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
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
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-package", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        _run([sys.executable, str(ROOT / "scripts" / "release_check.py"), "--release"])
        _run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"])
        _run([sys.executable, str(ROOT / "_regression_metox_algorithm.py")])
        _run([sys.executable, str(ROOT / "_regression_export_period.py")])
        _run([sys.executable, str(ROOT / "自检_造数套件.py")])
        result = build_release(args.baseline_package.resolve(), args.output_root.resolve())
    except Exception as exc:
        print(f"release-build FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
