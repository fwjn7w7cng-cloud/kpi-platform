"""Одна Алапа для всех (Адам 20.11): у клиента своих функций нет — только настройки, оформление и переводчик 1С."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class OneAlapa(unittest.TestCase):
    def test_no_client_rules(self):
        own = [str(p.relative_to(ROOT)) for p in (ROOT / "clients").glob("*/rules.py")]
        self.assertEqual(own, [], "у клиента не должно быть своих правил — новая функция идёт в общую Алапу")

    def test_update_script_restarts_all(self):
        s = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
        self.assertIn("kpi-web@demo", s)
        self.assertIn('for d in clients/*/', s)
        self.assertIn("ИТОГ", s)


if __name__ == "__main__":
    unittest.main()
