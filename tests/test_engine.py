"""Календарь, KPI и зарплата кубиками — на выдуманной фирме и на маленьких ручных примерах."""
import unittest
from datetime import date

from clients.demo.seed import seed
from core.calendar import Month
from core.config import load_client, parse
from core.data.db import DB
from core.data.model import Dataset, Sale
from core.hooks import Hooks
from core.kpi.engine import Kpi
from core.pay import components as C
from core.pay.engine import Payroll

TODAY = date(2026, 10, 27)


class Calendar(unittest.TestCase):
    def test_workdays_and_need(self):
        cfg = load_client("demo")
        m = Month(cfg, "2026-10", TODAY)
        self.assertEqual(m.total, 27)                  # октябрь 2026 без воскресений
        self.assertEqual(m.passed, 22)                 # до 27-го не включая
        self.assertAlmostEqual(m.need, 22 / 27)
        self.assertTrue(Month(cfg, "2026-09", TODAY).closed)

    def test_holidays(self):
        raw = load_client("demo").raw
        raw["work_week"]["holidays"] = {"2026-10-05": "paid", "2026-10-06": "unpaid"}
        cfg = parse("demo", raw)
        m = Month(cfg, "2026-10", TODAY)
        self.assertEqual(m.total, 25)
        self.assertEqual(len(m.paid_holidays), 1)


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_client("demo")
        cls.db = DB.open(":memory:")
        cls.info = seed(cls.db, cls.cfg, "food", today=TODAY)
        cls.k = Kpi(cls.cfg, cls.db, "2026-10", TODAY)
        cls.p = Payroll(cls.cfg, cls.db, cls.k)

    def test_seed_shape(self):
        self.assertEqual(self.info["months"], ["2026-07", "2026-08", "2026-09", "2026-10"])
        self.assertEqual(self.k.unknown, {})           # все продажи привязаны к людям
        self.assertGreater(len(self.k.members("dept", "sales")), 10)

    def test_dept_is_sum_of_people(self):
        ids = self.k.members("dept", "sales")
        dept = self.k.scope("dept", "sales")
        self.assertAlmostEqual(dept["sales"].fact, sum(self.k.emp(i)["sales"].fact for i in ids), places=2)
        self.assertAlmostEqual(dept["sales"].plan, sum(self.k.emp(i)["sales"].plan for i in ids), places=2)
        teams = sum(self.k.scope("team", t.id)["sales"].fact for t in self.cfg.teams)
        self.assertAlmostEqual(teams, dept["sales"].fact, places=2)

    def test_spread_of_performance(self):
        pcts = [self.k.emp(i)["sales"].pct for i in self.k.members("dept", "sales")]
        self.assertTrue(any(p > 1 for p in pcts) and any(p < 0.7 for p in pcts), pcts)

    def test_newbie_has_own_calendar(self):
        nb = next(e for e in self.db.employees() if e["role"] == "newbie")
        m = self.k.emp(nb["id"])["sales"]
        self.assertLess(m.total_days, self.k.cal.total)
        self.assertTrue(0 < m.need < 1)

    def test_everyone_gets_paid_and_director_not(self):
        for e in self.db.employees():
            slip = self.p.calc(e)
            if e["position"] == "director":
                self.assertEqual(slip.total, 0)
            else:
                self.assertGreater(slip.total, 0, e["fio"])

    def test_fines_from_timesheet(self):
        marked = {r["emp_id"]: r for r in self.db.timesheet("2026-10-01", "2026-10-31")}
        for emp_id, r in marked.items():
            e = self.db.employee(emp_id)
            slip = self.p.calc(e)
            names = [x[0] for x in slip.rows]
            if r["kind"] in ("late", "truancy") and e["role"] != "newbie" and "timesheet" in str(self.cfg.raw["pay_schemes"].get(self.p.scheme_of(e))):
                self.assertIn("Штрафы по табелю", names, e["fio"])
            if r["kind"] in ("absent", "truancy"):
                self.assertEqual(self.p.missed_days(e), 1)


class Components(unittest.TestCase):
    """Кубики зарплаты на одном торговом с ручными цифрами."""

    def make(self, scheme, sales=900_000, plan=1_000_000):
        raw = load_client("demo").raw
        raw["pay_schemes"]["rep"] = scheme
        cfg = parse("demo", raw)
        db = DB.open(":memory:")
        e = db.add_employee("Тестов Тест", "sales", team="north", position="rep", role="rep", keys=["тестов тест"])
        db.load(Dataset("sales", date(2026, 9, 1), date(2026, 9, 30), rows=[Sale(date(2026, 9, 10), "тестов тест", "1", "G", sales)]))
        db.set_plan("2026-09", e, "sales", plan)
        k = Kpi(cfg, db, "2026-09", TODAY)
        return Payroll(cfg, db, k).calc(e)

    def amounts(self, slip):
        return {r[0]: round(r[1]) for r in slip.rows}

    def test_steps(self):
        steps = [[0.8, 5000], [1.0, 15000], [1.2, 20000]]
        comp = [{"type": "plan_bonus", "metric": "sales", "mode": "steps", "steps": steps}]
        self.assertEqual(self.make(comp, 900_000).total, 5000)
        self.assertEqual(self.make(comp, 1_000_000).total, 15000)
        self.assertEqual(self.make(comp, 700_000).total, 0)
        self.assertEqual(self.make(comp, 1_300_000).total, 20000)

    def test_linear_with_threshold_and_cap(self):
        comp = [{"type": "plan_bonus", "metric": "sales", "mode": "linear", "amount": 10000, "min": 0.7, "cap": 1.2}]
        self.assertEqual(self.make(comp, 900_000).total, 9000)
        self.assertEqual(self.make(comp, 600_000).total, 0)
        self.assertEqual(self.make(comp, 1_500_000).total, 12000)

    def test_percent_only_after_plan_and_above_plan(self):
        comp = [{"type": "sales_percent", "rate": 0.01, "min_plan": 0.9}]
        self.assertEqual(self.make(comp, 850_000).total, 0)
        self.assertEqual(self.make(comp, 950_000).total, 9500)
        comp = [{"type": "sales_percent", "rate": 0.01, "rate_above": 0.02}]
        self.assertEqual(self.make(comp, 1_200_000).total, 10000 + 4000)

    def test_salary_full_month(self):
        slip = self.make([{"type": "salary", "amount": 26000}])
        self.assertEqual(slip.total, round(26000 / 26 * slip.days))
        self.assertEqual(slip.days, 26)                # сентябрь 2026: 26 рабочих дней без воскресений

    def test_component_checks(self):
        self.assertTrue(C.check({"type": "plan_bonus", "metric": "nope", "steps": [[1, 1]]}))
        self.assertTrue(C.check({"type": "sales_percent", "rate": 4}))
        self.assertFalse(C.check({"type": "plan_bonus", "metric": "grp:B01", "mode": "linear", "amount": 1}))

    def test_client_component_via_hook(self):
        hooks = Hooks()
        hooks.component("per_point", lambda ctx, emp, c: [("За точку", c["rate"] * 3)], check=lambda c: [])
        raw = load_client("demo").raw
        raw["pay_schemes"]["rep"] = [{"type": "per_point", "rate": 100}]
        cfg = parse("demo", raw, hooks)                 # без крючка такая анкета не прошла бы проверку
        db = DB.open(":memory:")
        e = db.add_employee("Тестов Тест", "sales", team="north", position="rep", role="rep")
        k = Kpi(cfg, db, "2026-09", TODAY)
        self.assertEqual(Payroll(cfg, db, k, hooks).calc(e).total, 300)
        with self.assertRaises(Exception):
            parse("demo", raw)


if __name__ == "__main__":
    unittest.main()
