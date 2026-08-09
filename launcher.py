"""MetoX desktop launcher with automatic local-port selection."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys


HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_SCAN_LIMIT = 100


def package_root() -> Path:
    """Return the directory containing the packaged app and launcher."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def preferred_port(value: str | None = None) -> int:
    """Parse TGTOOL_PORT, falling back to the documented default."""
    raw = os.environ.get("TGTOOL_PORT", "") if value is None else value
    raw = raw.strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def port_is_available(port: int, host: str = HOST) -> bool:
    """Return True only when this process can exclusively bind the port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
    except OSError:
        return False
    return True


def find_available_port(start_port: int, host: str = HOST) -> int:
    """Prefer start_port, then scan upward, finally ask Windows for a free port."""
    for port in range(start_port, min(65535, start_port + PORT_SCAN_LIMIT - 1) + 1):
        if port_is_available(port, host):
            return port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def streamlit_arguments(app_path: Path, port: int) -> list[str]:
    headless = "true" if os.environ.get("METOX_HEADLESS", "").strip() == "1" else "false"
    return [
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        HOST,
        "--server.port",
        str(port),
        "--global.developmentMode",
        "false",
        "--server.headless",
        headless,
        "--server.showEmailPrompt",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]


def validate_runtime_dependencies() -> None:
    """Import Streamlit modules that are loaded dynamically during app execution."""
    import streamlit.runtime.scriptrunner.magic_funcs  # noqa: F401


def prepare_application_environment(root: Path) -> None:
    """Make sibling application modules importable from every launcher mode."""
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.chdir(root)


def run_app_execution_test(app_path: Path) -> int:
    """Execute the real app through Streamlit's script runner and report exceptions."""
    from streamlit.testing.v1 import AppTest

    test = AppTest.from_file(str(app_path))
    test.run(timeout=60)
    exceptions = list(test.exception)
    if exceptions:
        for exception in exceptions:
            message = getattr(exception, "message", None) or getattr(exception, "value", None)
            print(f"METOX_APP_TEST_ERROR={message or exception}", flush=True)
        return 3
    print("METOX_APP_TEST=PASS", flush=True)
    return 0


def main() -> int:
    root = package_root()
    app_path = root / "app.py"
    if not app_path.is_file():
        print(f"Startup failed: app.py not found beside the launcher: {app_path}", flush=True)
        return 2

    prepare_application_environment(root)
    validate_runtime_dependencies()
    if os.environ.get("METOX_APP_TEST", "").strip() == "1":
        return run_app_execution_test(app_path)
    requested = preferred_port()
    selected = find_available_port(requested)
    if selected != requested:
        print(f"Port {requested} is already in use; MetoX selected port {selected}.", flush=True)
    url = f"http://{HOST}:{selected}"
    print(f"MetoX is starting: {url}", flush=True)
    print(f"METOX_URL={url}", flush=True)

    sys.argv = streamlit_arguments(app_path, selected)
    from streamlit.web import cli as streamlit_cli

    result = streamlit_cli.main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
