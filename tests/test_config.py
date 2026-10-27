"""Конфиг клиента: demo читается, ошибки ловятся и пишутся понятно, ядро не знает клиентов."""
import copy
import re
import unittest
from pathlib import Path

import yaml

from core.access import editions as E
from core.config import ConfigError, client_dir, load_client, parse

ROOT = Path(__file__).resolve().parent.parent


def demo_raw():
    return yaml.safe_load((client_dir("demo") / "config.yaml").read_text(encoding="utf-8"))


class DemoConfig(unittest.TestCase):
    def test_demo_loads(self):
        cfg = load_client("demo")
        self.assertEqual(cfg.id, "demo")
        self.assertEqual(cfg.edition, "control")
        self.assertEqual(cfg.weekend, ["sun"])
        self.assertEqual(cfg.salary_days, 26)
        self.assertTrue(cfg.dept("sales"))
        self.assertEqual(len(cfg.teams_of("sales")), 3)
        self.assertEqual(cfg.role_title("rep"), "Торговый представитель")
        self.assertEqual(cfg.role_title("sv"), "Супервайзер")     # не переименована — стандартная

    def test_modules_from_edition_and_addons(self):
        cfg = load_client("demo")
        self.assertTrue(cfg.has_module("kpi"))        # старт
        self.assertTrue(cfg.has_module("debts"))      # бизнес
        self.assertTrue(cfg.has_module("approval"))   # контроль
        self.assertTrue(cfg.has_module("ai"))         # докуплен
        self.assertFalse(cfg.has_module("miniapp"))   # не куплен


class Errors(unittest.TestCase):
    def broken(self, change):
        raw = copy.deepcopy(demo_raw())
        change(raw)
        with self.assertRaises(ConfigError) as ctx:
            parse("demo", raw)
        return "\n".join(ctx.exception.problems)

    def test_bad_edition(self):
        self.assertIn("edition", self.broken(lambda r: r.update(edition="max")))

    def test_unknown_addon(self):
        self.assertIn("нет такого модуля", self.broken(lambda r: r.update(addons=["teleport"])))

    def test_addon_already_in_edition(self):
        self.assertIn("уже входит", self.broken(lambda r: r.update(addons=["debts"])))

    def test_team_without_dept(self):
        def ch(r):
            r["teams"][0]["dept"] = "nowhere"
        self.assertIn("нет отдела", self.broken(ch))

    def test_no_sales_dept(self):
        self.assertIn("отдел продаж",
                      self.broken(lambda r: r.update(departments=[{"id": "o", "name": "Офис", "kind": "office"}], teams=[])))

    def test_bad_weekday_and_rate(self):
        def ch(r):
            r["work_week"]["weekend"] = ["friday"]
            r["lines"]["main"]["rate"] = 4
        msg = self.broken(ch)
        self.assertIn("weekend", msg)
        self.assertIn("rate", msg)

    def test_all_problems_at_once(self):
        def ch(r):
            r["edition"] = "?"
            r["company"]["industry"] = "?"
            r["source"]["kind"] = "?"
        msg = self.broken(ch)
        for word in ("edition", "industry", "source.kind"):
            self.assertIn(word, msg)

    def test_start_edition_lacks_payroll(self):
        raw = demo_raw()
        raw.update(edition="start", addons=["debts"])
        cfg = parse("demo", raw)
        self.assertTrue(cfg.has_module("debts"))
        self.assertFalse(cfg.has_module("payroll"))
        self.assertEqual(E.lock_text("payroll"), "Доступно в Бизнес")
        self.assertEqual(E.lock_text("miniapp"), "Подключается отдельно")


class Editions(unittest.TestCase):
    def test_nested(self):
        s, b, c = (E.edition_modules(x) for x in E.ORDER)
        self.assertTrue(s < b < c)
        self.assertFalse(E.ADDON_ONLY & c)

    def test_requirements_inside_editions(self):
        for ed in E.ORDER:
            mods = E.edition_modules(ed)
            for m, need in E.REQUIRES.items():
                if m in mods:
                    self.assertTrue(need <= mods, f"{ed}: {m} без {need}")


class CoreKnowsNoClients(unittest.TestCase):
    """Правило 2 из CLAUDE.md: в ядре и модулях нет упоминаний конкретных клиентов."""
    FORBIDDEN = re.compile(r"теплоком|строй\s*сити|quizbot|clients[./]demo|['\"]demo['\"]", re.I)

    def test_no_client_names_in_core(self):
        bad = []
        for folder in ("core", "modules"):
            for f in (ROOT / folder).rglob("*.py"):
                for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if self.FORBIDDEN.search(line):
                        bad.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(bad, [], "ядро знает о клиенте:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
