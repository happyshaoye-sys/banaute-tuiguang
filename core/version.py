"""Read the application identity from the repository-level VERSION.json."""
from __future__ import annotations

import json
from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION.json"


def _load_version_info() -> dict:
    with VERSION_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    required = {"product", "version", "release_date", "external_strategy_version"}
    missing = sorted(required.difference(data))
    if missing:
        raise RuntimeError(f"VERSION.json missing fields: {', '.join(missing)}")
    return data


VERSION_INFO = _load_version_info()
PRODUCT_NAME = str(VERSION_INFO["product"])
APP_VERSION = str(VERSION_INFO["version"])
RELEASE_DATE = str(VERSION_INFO["release_date"])
EXTERNAL_STRATEGY_VERSION = str(VERSION_INFO["external_strategy_version"])
DISPLAY_VERSION = f"{PRODUCT_NAME} v{APP_VERSION}"
