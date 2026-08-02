"""Fail closed when a MetoX business-rule release is not traceable."""
from __future__ import annotations

import argparse
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
    "core/exporter.py",
    "core/field_mapping.py",
    "core/jst_refund.py",
    "core/jst_stock.py",
    "core/loader.py",
    "core/merge.py",
    "core/metox_algorithm.py",
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

    launcher_source = (ROOT / "launcher.py").read_text(encoding="utf-8")
    for snippet in (
        "find_available_port",
        'HOST = "127.0.0.1"',
        'DEFAULT_PORT = 8765',
        '"--global.developmentMode"',
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
