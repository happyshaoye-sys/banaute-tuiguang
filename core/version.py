"""Read the application identity from the repository-level VERSION.json."""
from __future__ import annotations

import json
from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION.json"


def _load_version_info() -> dict:
    with VERSION_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    required = {"product", "version", "release_date"}
    missing = sorted(required.difference(data))
    if missing:
        raise RuntimeError(f"VERSION.json missing fields: {', '.join(missing)}")
    return data


VERSION_INFO = _load_version_info()
PRODUCT_NAME = str(VERSION_INFO["product"])
# 显示名（v2.73 起工具更名为「MetoX推广参谋」）；product 字段保持 MetoX 以兼容 git tag/包目录/manifest
DISPLAY_NAME = str(VERSION_INFO.get("display_name") or PRODUCT_NAME)
APP_VERSION = str(VERSION_INFO["version"])
RELEASE_DATE = str(VERSION_INFO["release_date"])
DISPLAY_VERSION = f"{DISPLAY_NAME} v{APP_VERSION}"
