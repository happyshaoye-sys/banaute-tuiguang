"""Build the Windows TuiGuangTool launcher runtime with PyInstaller."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-root", type=Path, default=ROOT / "build" / "launcher")
    args = parser.parse_args()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "TuiGuangTool",
        "--distpath",
        str(args.dist_root.resolve()),
        "--workpath",
        str(args.work_root.resolve()),
        "--specpath",
        str(args.work_root.resolve()),
        "--collect-data",
        "streamlit",
        "--recursive-copy-metadata",
        "streamlit",
        "--hidden-import",
        "pandas",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "xlsxwriter",
        str(ROOT / "launcher.py"),
    ]
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        return completed.returncode

    runtime = args.dist_root.resolve() / "TuiGuangTool"
    required = (runtime / "TuiGuangTool.exe", runtime / "_internal")
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("launcher build missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    print(runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
