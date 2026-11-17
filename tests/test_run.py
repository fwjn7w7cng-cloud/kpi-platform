"""run.py: порт приложения клиента — из его .env (Адам 18.11: новый клиент садился на порт демо 8101)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run


class Port(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("WEB_PORT", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_from_env_file(self):
        (self.tmp / "clients" / "acme").mkdir(parents=True)
        (self.tmp / "clients" / "acme" / ".env").write_text("WEB_PORT=8301\n", encoding="utf-8")
        with mock.patch.object(run, "ROOT", self.tmp):
            self.assertEqual(run.web_port_of("acme"), 8301)

    def test_no_port_refuses(self):
        (self.tmp / "clients" / "acme").mkdir(parents=True)
        with mock.patch.object(run, "ROOT", self.tmp):
            with self.assertRaises(SystemExit):
                run.web_port_of("acme")
            self.assertEqual(run.web_port_of("demo"), 8101)


if __name__ == "__main__":
    unittest.main()
