import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import max_web


class MaxWebQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_dir = Path(self.temp_dir.name)

    def test_start_command_is_written_atomically(self):
        command_path = self.data_dir / "command.json"
        with patch.object(max_web, "DATA_DIR", self.data_dir), patch.object(
            max_web, "COMMAND_PATH", command_path
        ):
            max_web.queue_browser_start()
        with command_path.open("r", encoding="utf-8") as stream:
            command = json.load(stream)
        self.assertEqual(command["action"], "open")
        self.assertIn("created_at", command)

    def test_inspection_command_is_written(self):
        command_path = self.data_dir / "command.json"
        with patch.object(max_web, "DATA_DIR", self.data_dir), patch.object(
            max_web, "COMMAND_PATH", command_path
        ):
            max_web.queue_browser_inspection()
        with command_path.open("r", encoding="utf-8") as stream:
            command = json.load(stream)
        self.assertEqual(command["action"], "inspect")

    def test_vnc_password_is_created_and_reused(self):
        password_path = self.data_dir / "vnc-password.txt"
        with patch.object(max_web, "DATA_DIR", self.data_dir), patch.object(
            max_web, "VNC_PASSWORD_PATH", password_path
        ):
            first = max_web.get_vnc_password()
            second = max_web.get_vnc_password()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)
        self.assertEqual(password_path.stat().st_mode & 0o777, 0o600)

    def test_missing_status_returns_safe_default(self):
        with patch.object(max_web, "STATUS_PATH", self.data_dir / "missing.json"):
            status = max_web.read_browser_status()
        self.assertFalse(status["running"])
        self.assertIsNone(status["error"])

    def test_status_and_diagnostics_are_read(self):
        status_path = self.data_dir / "status.json"
        diagnostics_path = self.data_dir / "diagnostics.json"
        status_path.write_text(
            json.dumps({"running": True, "authenticated_guess": False}),
            encoding="utf-8",
        )
        diagnostics_path.write_text(
            json.dumps({"controls": [{"tag": "button"}]}),
            encoding="utf-8",
        )
        with patch.object(max_web, "STATUS_PATH", status_path), patch.object(
            max_web, "DIAGNOSTICS_PATH", diagnostics_path
        ):
            self.assertTrue(max_web.read_browser_status()["running"])
            self.assertEqual(len(max_web.read_browser_diagnostics()["controls"]), 1)


if __name__ == "__main__":
    unittest.main()
