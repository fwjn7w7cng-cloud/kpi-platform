"""Анкета-таблица: туда и обратно без потерь, ошибки с листом и строкой, Excel с выпадающими списками."""
import copy
import unittest

from core import anketa as A
from core.config import load_client


def demo_tables():
    return A.from_raw(load_client("demo").raw)


class RoundTrip(unittest.TestCase):
    def test_demo_survives_table_and_back(self):
        before = load_client("demo")
        cfg, problems = A.check(demo_tables())
        self.assertEqual(problems, [])
        self.assertEqual((cfg.id, cfg.name, cfg.edition, cfg.addons, cfg.weekend, cfg.salary_days),
                         (before.id, before.name, before.edition, before.addons, before.weekend, before.salary_days))
        self.assertEqual([d.id for d in cfg.departments], [d.id for d in before.departments])
        self.assertEqual([(t.id, t.dept) for t in cfg.teams], [(t.id, t.dept) for t in before.teams])
        self.assertEqual(cfg.raw["kpi"], before.raw["kpi"])
        self.assertEqual(cfg.raw["timesheet"], before.raw["timesheet"])
        for pos in before.positions:
            old = before.raw["pay_schemes"].get(pos["pay"], [])
            new = cfg.raw["pay_schemes"].get(cfg.position(pos["id"])["pay"], [])
            self.assertEqual(_norm(old), _norm(new), pos["name"])
        self.assertEqual(cfg.raw["formulas"].keys(), before.raw["formulas"].keys())

    def test_xlsx_roundtrip(self):
        data = A.write_xlsx(demo_tables())
        cfg, problems = A.check(A.read_xlsx(data))
        self.assertEqual(problems, [])
        self.assertEqual(cfg.name, "Опт-Партнёр")


class Human(unittest.TestCase):
    def test_salary_rows_read_like_a_person_writes(self):
        rows = demo_tables()["Зарплата"]
        rep = [r for r in rows if r[0] == "Торговый представитель"]
        self.assertIn(["Торговый представитель", "Оклад", "", "", "25 000", "", "", "", "", ""], rep)
        steps = next(r for r in rep if r[1] == "Бонус за план — ступени")
        self.assertEqual(steps[4], "80% → 5 000; 100% → 15 000; 120% → 20 000")
        self.assertIn("Воскресенье", [r[1] for r in demo_tables()["Компания"]])

    def test_codes_optional(self):
        t = demo_tables()
        for sheet in ("Отделы", "Команды", "Должности"):
            col = t[sheet][0].index("Код")
            for r in t[sheet][1:]:
                r[col] = ""
        cfg, problems = A.check(t)
        self.assertEqual(problems, [])
        self.assertEqual(cfg.dept("otdel_prodazh").name, "Отдел продаж")


class Problems(unittest.TestCase):
    def broken(self, change):
        t = copy.deepcopy(demo_tables())
        change(t)
        cfg, problems = A.check(t)
        self.assertIsNone(cfg)
        return problems

    def test_unknown_department_points_to_row(self):
        def ch(t):
            t["Команды"][2][1] = "Отдел продажж"
        p = self.broken(ch)
        self.assertIn(("Команды", 3), [(s, r) for s, r, _ in p])
        self.assertIn("Отдел продажж", A.problems_text(p))

    def test_percent_without_sign_points_to_salary_row(self):
        def ch(t):
            row = next(r for r in t["Зарплата"] if r[1] == "% от продаж")
            row[4] = "0,4"
        p = self.broken(ch)
        sheet, row, text = p[0]
        self.assertEqual(sheet, "Зарплата")
        self.assertGreater(row, 1)
        self.assertIn("со знаком %", text)

    def test_bad_step_and_cube(self):
        def ch(t):
            row = next(r for r in t["Зарплата"] if r[1] == "Бонус за план — ступени")
            row[4] = "80 процентов 5000"
            t["Зарплата"].append(["Водитель", "Премия за улыбку", "", "", "1", "", "", "", "", ""])
        txt = A.problems_text(self.broken(ch))
        self.assertIn("80% → 5 000", txt)
        self.assertIn("Премия за улыбку", txt)

    def test_bad_choice_in_company(self):
        def ch(t):
            for r in t["Компания"]:
                if r[0] == "Редакция":
                    r[1] = "Максимум"
        self.assertIn("Старт, Бизнес, Контроль", A.problems_text(self.broken(ch)))

    def test_check_file_marks_rows(self):
        t = demo_tables()
        t["Команды"][2][1] = "Нет такого"
        _, p = A.check(t)
        tables = A.read_xlsx(A.write_xlsx(t, p))
        head = tables["Команды"][0]
        self.assertIn("⚠️ Проверка", head)
        self.assertIn("Нет такого", tables["Команды"][2][head.index("⚠️ Проверка")])


def _norm(comps):
    out = []
    for c in comps:
        c = dict(c)
        c.pop("days", None)
        if c.get("type") == "plan_bonus":
            c.setdefault("mode", "steps")
            c.setdefault("scope", "self")
        if c.get("type") == "sales_percent":
            c.setdefault("scope", "self")
        if c.get("type") == "salary":
            c.pop("amount", None) if c.get("amount") in (None, "") else None
        if c.get("type") == "fixed_bonus":
            c.setdefault("cut_per_miss", 0)
        out.append({k: (round(v, 6) if isinstance(v, float) else v) for k, v in c.items()})
    return out


if __name__ == "__main__":
    unittest.main()
