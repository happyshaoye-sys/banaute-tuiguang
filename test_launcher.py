"""Regression tests for the packaged MetoX launcher."""
from __future__ import annotations

from pathlib import Path
import socket
import unittest

import launcher


class LauncherTests(unittest.TestCase):
    def test_default_and_invalid_environment_ports_use_8765(self):
        for value in ("", "abc", "0", "65536"):
            with self.subTest(value=value):
                self.assertEqual(launcher.preferred_port(value), 8765)

    def test_free_preferred_port_is_preserved(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind((launcher.HOST, 0))
            port = int(reservation.getsockname()[1])
        self.assertEqual(launcher.find_available_port(port), port)

    def test_occupied_preferred_port_automatically_moves(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind((launcher.HOST, 0))
            reservation.listen(1)
            occupied = int(reservation.getsockname()[1])
            selected = launcher.find_available_port(occupied)

        self.assertNotEqual(selected, occupied)
        self.assertTrue(launcher.port_is_available(selected))

    def test_streamlit_arguments_pin_selected_port_and_local_address(self):
        args = launcher.streamlit_arguments(Path("app.py"), 8877)
        self.assertEqual(args[0:2], ["streamlit", "run"])
        self.assertIn("--server.address", args)
        self.assertEqual(args[args.index("--server.address") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--server.port") + 1], "8877")
        self.assertEqual(args[args.index("--global.developmentMode") + 1], "false")

    def test_batch_entry_points_do_not_force_fixed_port(self):
        root = Path(__file__).resolve().parent
        primary = (root / "启动MetoX半自动推广工具.bat").read_text(encoding="utf-8-sig")
        compatibility = (root / "启动半自动推广工具.bat").read_text(encoding="utf-8-sig")
        self.assertNotIn("set TGTOOL_PORT=8765", primary)
        self.assertIn("TuiGuangTool.exe", primary)
        self.assertIn("TuiGuangTool.exe", compatibility)
        self.assertNotIn("call ", compatibility.lower())

    def test_build_copies_streamlit_dependency_metadata(self):
        source = (
            Path(__file__).resolve().parent / "scripts" / "build_launcher.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--recursive-copy-metadata"', source)
        self.assertIn('"streamlit"', source)


if __name__ == "__main__":
    unittest.main()
