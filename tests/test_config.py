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
        self.assertEqual(cfg.edition, "company")              # демо по умолчанию — средняя фирма, все отделы (Адам 31.10)
        self.assertEqual(cfg.weekend, ["sun"])
        self.assertEqual(cfg.salary_days, 26)
        self.assertTrue(cfg.dept("sales"))
        self.assertEqual(len(cfg.teams_of("sales")), 3)
        self.assertEqual(cfg.role_title("rep"), "Торговый представитель")
        self.assertEqual(cfg.role_title("sv"), "Супервайзер")     # не переименована — стандартная

    def test_modules_from_edition_and_addons(self):
        cfg = load_client("demo")
        self.assertTrue(cfg.has_module("kpi"))        # старт
        self.assertTrue(cfg.has_module("debts"))      # старт (с 29.10)
        self.assertTrue(cfg.has_module("route"))      # бизнес
        self.assertTrue(cfg.has_module("approval"))   # контроль
        self.assertTrue(cfg.has_module("stock"))      # компания
        self.assertFalse(cfg.has_module("custom"))    # максимум — не по умолчанию, кнопкой в «Выбрать пакеты»


class Errors(unittest.TestCase):
    def broken(self, change):
        raw = copy.deepcopy(demo_raw())
        change(raw)
        with self.assertRaises(ConfigError) as ctx:
            parse("demo", raw)
        return "\n".join(ctx.exception.problems)

    def test_bad_edition(self):
        self.assertIn("edition", self.broken(lambda r: r.update(edition="mega")))

    def test_unknown_addon(self):
        self.assertIn("нет такого модуля", self.broken(lambda r: r.update(addons=["teleport"])))

    def test_bad_package(self):
        self.assertIn("нет такого пакета", self.broken(lambda r: r.update(packages={"teleport": True})))
        self.assertIn("packages.sales", self.broken(lambda r: r.update(packages={"sales": "mega"})))
        self.assertIn("ни один пакет", self.broken(lambda r: (r.pop("edition"), r.update(packages={"stock": False}))))

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

    def test_start_edition_has_payroll_and_debts(self):
        raw = demo_raw()
        raw.update(edition="start", addons=["stock"])          # старая запись: докупленный модуль → его пакет
        cfg = parse("demo", raw)
        self.assertTrue(cfg.has_module("payroll"))
        self.assertTrue(cfg.has_module("debts"))
        self.assertTrue(cfg.has_module("stock"))
        self.assertTrue(cfg.has_module("warehouse"))           # весь пакет «Склад и закупки»
        self.assertFalse(cfg.has_module("clients"))
        self.assertEqual(cfg.edition, E.CUSTOM)
        self.assertEqual(cfg.set_title, "Продажи Старт + Склад и закупки")
        self.assertEqual(E.lock_text("clients"), "Функция · 📈 Продажи")                 # платим за функции (31.10)
        self.assertEqual(E.lock_text("custom"), "Функция · ⭐ Дополнительно")

    def test_own_packages_over_preset(self):
        raw = demo_raw()
        raw.update(edition="business", packages={"logistics": True, "sales": False})
        cfg = parse("demo", raw)
        self.assertEqual(cfg.packages, {"sales": None, "stock": False, "logistics": True, "office": False, "max": False})
        self.assertTrue(cfg.has_module("logistics") and cfg.has_module("payroll") and cfg.has_module("staff"))
        self.assertFalse(cfg.has_module("kpi"))
        self.assertTrue(cfg.dept_on("logistics"))
        self.assertFalse(cfg.dept_on("sales"))
        self.assertFalse(cfg.dept_on("warehouse"))


class HumanNumbers(unittest.TestCase):
    """В анкете проценты пишутся как в жизни («0,4%», «80%»), суммы — с пробелами («25 000»)."""

    def test_demo_written_humanly_is_understood(self):
        cfg = load_client("demo")
        rep = cfg.raw["pay_schemes"]["rep"]
        self.assertEqual(rep[0]["amount"], 25000)
        self.assertAlmostEqual(rep[1]["rate"], 0.004)
        self.assertEqual(rep[2]["steps"][0], [0.8, 5000])
        self.assertAlmostEqual(cfg.raw["kpi"]["akb_min"], 5000)

    def test_old_fractions_still_work(self):
        raw = demo_raw()
        raw["pay_schemes"]["rep"][1]["rate"] = 0.004
        self.assertAlmostEqual(parse("demo", raw).raw["pay_schemes"]["rep"][1]["rate"], 0.004)

    def test_percent_without_sign_is_refused(self):
        raw = demo_raw()
        raw["pay_schemes"]["rep"][1]["rate"] = "0,4"
        with self.assertRaises(ConfigError) as ctx:
            parse("demo", raw)
        self.assertIn("со знаком %", str(ctx.exception))

    def test_bad_money(self):
        raw = demo_raw()
        raw["pay_schemes"]["rep"][0]["amount"] = "двадцать тысяч"
        with self.assertRaises(ConfigError) as ctx:
            parse("demo", raw)
        self.assertIn("нужна сумма", str(ctx.exception))


class Editions(unittest.TestCase):
    def test_nested(self):
        eds = [E.edition_modules(x) for x in E.ORDER]
        for a, b in zip(eds, eds[1:]):
            self.assertTrue(a < b)
        self.assertEqual(eds[-1], set(E.MODULES))           # в «Максимум» входит всё
        self.assertEqual(E.preset_of({"sales": "control", "stock": True, "logistics": True, "office": True}), "company")
        self.assertEqual(E.preset_of({"stock": True}), E.CUSTOM)
        self.assertEqual(E.title_of({}), "ничего не подключено")

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
