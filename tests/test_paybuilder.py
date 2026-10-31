"""🧱 Конструктор зарплаты: новые кубики ядра (множитель, пределы, новые клиенты, скидки, рейтинг, стаж, режимы оклада,
% по группам и от сбора) и страница конструктора (должности, варианты, ввод цифр, перенос в анкету и настройку клиента)."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import onboard as O
from core import paybuilder as PB
from core.app import App
from core.config import parse
from core.pay import components as C
from core.pay.engine import Payslip

TODAY = date(2026, 10, 31)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[1], data_dir=self.tmp)
        self.app.press(7, "sw:role:owner")
        self.ctx = self.app.ctx(7)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rep(self):
        k = self.ctx.kpi()
        reps = [e for e in self.ctx.db.employees() if e["role"] == "rep" and k.emp(e["id"])["sales"].plan]
        return sorted(reps, key=lambda e: k.emp(e["id"])["sales"].pct or 0)[len(reps) // 2]

    def rows(self, emp, scheme):
        slip = Payslip(emp)
        self.ctx.pay().slip_rows(emp, scheme, slip)
        return slip.rows


class Components(Base):
    def test_salary_modes(self):
        e = self.rep()
        fixed = self.rows(e, [{"type": "salary", "amount": 26000, "mode": "fixed"}])
        self.assertEqual(fixed[0][1], 26000)
        cal = self.rows(e, [{"type": "salary", "amount": 31000, "mode": "calendar"}])
        self.assertIn("календ", cal[0][2])
        self.assertLessEqual(cal[0][1], 31000)

    def test_multiplier_scales_bonuses_only(self):
        e = self.rep()
        pct = self.ctx.kpi().emp(e["id"])["sales"].pct
        base = [{"type": "salary", "amount": 20000, "mode": "fixed"}, {"type": "plan_bonus", "metric": "sales", "mode": "fixed",
                                                                        "amount": 10000, "min": 0.0}]
        coef = 1.5 if pct >= 0.5 else 0.5
        rs = self.rows(e, base + [{"type": "multiplier", "metric": "sales", "steps": [[0.0, 0.5], [0.5, 1.5]], "applies": "bonus"}])
        mult = next(r for r in rs if r[0].startswith("Множитель"))
        self.assertAlmostEqual(mult[1], 10000 * (coef - 1))                     # оклад не множится
        self.assertTrue(all(C.check(c) == [] for c in base))
        self.assertEqual(C.check({"type": "multiplier", "metric": "sales", "steps": "x"})[0][:16], "multiplier.steps")

    def test_limits_floor_and_cap(self):
        e = self.rep()
        rs = self.rows(e, [{"type": "salary", "amount": 10000, "mode": "fixed"}, {"type": "limits", "min_total": 50000}])
        self.assertEqual(sum(r[1] for r in rs), 50000)
        rs = self.rows(e, [{"type": "salary", "amount": 10000, "mode": "fixed"},
                           {"type": "plan_bonus", "metric": "sales", "mode": "fixed", "amount": 90000, "min": 0.0},
                           {"type": "limits", "max_variable": 30000}])
        self.assertEqual(sum(r[1] for r in rs), 40000)

    def test_collect_groups_newclients_discount_rank_seniority(self):
        e = self.rep()
        k = self.ctx.kpi()
        col = self.rows(e, [{"type": "sales_percent", "base": "collect", "rate": 0.01}])
        self.assertAlmostEqual(col[0][1], k.emp(e["id"])["collect"].fact * 0.01)
        groups = k.raw(e["id"])["groups"]
        g = max(groups, key=groups.get)
        gr = self.rows(e, [{"type": "sales_percent", "rate": 0.001, "groups": {g: 0.01}}])
        self.assertAlmostEqual(sum(r[1] for r in gr), groups[g] * 0.01 + (sum(groups.values()) - groups[g]) * 0.001, places=2)
        nc = self.rows(e, [{"type": "new_clients", "amount": 1000, "months": 3}])
        self.assertEqual(nc[0][0], "Новые клиенты")
        dp = self.rows(e, [{"type": "discount_penalty", "norm": 0.0, "amount": 2000}])
        self.assertTrue(dp == [] or dp[0][1] in (0, -2000))
        rk = self.rows(e, [{"type": "rank_bonus", "amounts": [5000, 3000, 1000]}])
        self.assertIn("место", rk[0][2])
        e2 = dict(e, hired="2020-03-01")
        sn = self.rows(e2, [{"type": "seniority", "amount": 1000, "max": 5000}])
        self.assertEqual(sn[0][1], 5000)                                        # 6 лет × 1 000, потолок 5 000
        mg = self.rows(e, [{"type": "sales_percent", "base": "margin", "rate": 0.05}])
        self.assertIn("себестоимость", mg[0][2])


class Builder(Base):
    def screens(self, data):
        return self.app.press(7, data)

    def view(self, data):
        return next(r for r in self.screens(data) if r.view)

    def test_every_brick_for_every_position(self):
        for pos in PB.POSITIONS:
            self.screens(f"pb:{pos}")
            for bid, b in PB.BRICKS.items():
                if pos in b[3]:
                    self.screens(f"pbn:{bid}")
                    items = PB.items_of(self.app.ctx(7), pos)
                    i = next(n for n, x in enumerate(items) if x["b"] == bid)
                    for var in b[4]:
                        self.screens(f"pbv:{i}:{var[0]}")
            emp, rows = PB.example(self.app.ctx(7), pos, PB.items_of(self.app.ctx(7), pos))
            self.assertTrue(emp, pos)
            self.assertFalse([r for r in rows if r[0].startswith("⚠️")], pos)
        self.assertEqual(sum(1 for x in PB.items_of(self.app.ctx(7), "rep") if x["b"] == "salary"), 1)   # оклад — один

    def test_page_edit_and_input(self):
        out = self.screens("pb:rep")
        self.assertIn("Для кого схема", out[0].text)                           # должность — сверху
        v = out[1].view
        self.assertEqual(v.title, "🧱 Конструктор зарплаты")
        self.assertIn("Пример за месяц", [t[0] for t in next(b for k, b in v.blocks if k == "tiles")])
        self.screens("pbf:2:steps")
        self.app.text(7, "80 5 000; 100 15000; 120 25 000")
        self.assertEqual(PB.items_of(self.app.ctx(7), "rep")[2]["p"]["steps"], [[0.8, 5000], [1.0, 15000], [1.2, 25000]])
        self.screens("pbf:1:rate")
        self.app.text(7, "0,5")
        self.assertAlmostEqual(PB.items_of(self.app.ctx(7), "rep")[1]["p"]["rate"], 0.005)
        self.screens("pbf:1:rate")
        self.assertTrue(self.app.text(7, "много")[0].ask)                         # не число — переспросит
        self.screens("pbv:2:gate")                                              # продажи: фикс с порога плана
        self.assertEqual(PB.bname(PB.items_of(self.app.ctx(7), "rep")[2]), "Продажи: фикс с порога")
        self.screens("pbv:1:pct_steps")                                         # % — ставка растёт с планом
        self.screens("pbf:1:rate_steps")
        self.app.text(7, "80 0,3; 100 0,5")
        self.assertEqual(PB.items_of(self.app.ctx(7), "rep")[1]["p"]["rate_steps"], [[0.8, 0.003], [1.0, 0.005]])
        self.screens("pbu:3")
        self.assertEqual(PB.items_of(self.app.ctx(7), "rep")[2]["b"], "akb")
        self.screens("pbd:1")
        self.screens("pbr")
        self.assertEqual(len(PB.items_of(self.app.ctx(7), "rep")), len(PB.DEFAULTS["rep"]))

    def test_goes_to_card_and_client_config(self):
        self.screens("pb:rep")
        self.screens("pbn:mult")
        self.screens("pbn:margin")
        self.screens("pbn:focus")                                               # фокусные — своим блоком
        self.screens("pb:driver")
        self.screens("pbn:cut")
        c = O.ensure(self.app.ctx(7), "7")
        O.snapshot(self.app.ctx(7), c)
        self.assertTrue(any(x.startswith("Торговый:") and "множитель" in x for x in c["set"]["pay_lines"]))
        self.assertTrue(any("наценки" in x for x in O.flags(c)))                 # нужна себестоимость — нестандарт
        c["f"]["company"] = "Проверка"
        raw = O.build_raw(c)
        self.assertTrue(any(x["type"] == "multiplier" for x in raw["pay_schemes"]["rep"]))
        self.assertTrue(any(x.get("base") == "focus" for x in raw["pay_schemes"]["rep"]))
        cfg = parse(raw["id"], raw)                                             # настройка клиента разбирается
        self.assertIn("driver", cfg.raw["pay_schemes"])

    def test_each_indicator_is_own_block(self):
        for m in ("sales", "focus", "collect", "akb", "sku", "visits"):
            self.assertEqual(PB.BRICKS[m][0], "Показатели")
            kinds = [v[0] for v in PB.BRICKS[m][4]]
            self.assertTrue({"fix", "gate", "steps", "linear"} <= set(kinds), m)
        self.assertIn("pct_steps", [v[0] for v in PB.BRICKS["focus"][4]])
        self.assertNotIn("pct", [v[0] for v in PB.BRICKS["akb"][4]])                # у АКБ процента нет
        ctx = self.app.ctx(7)                                                    # старая схема — переводится сама
        ctx.session["pb"] = {"rep": [{"b": "percent", "v": "gate", "p": {"rate": 0.01, "min_plan": 1.0}},
                                     {"b": "plan", "v": "fixed", "p": {"metric": "akb", "amount": 7000}}]}
        items = PB.items_of(ctx, "rep")
        self.assertEqual([(i["b"], i["v"]) for i in items], [("sales", "pct_gate"), ("akb", "fix")])
        self.assertEqual(items[0]["p"]["rate"], 0.01)

    def test_empty_and_partial_schemes(self):
        self.screens("pb:rep")
        out = self.screens("pbx")
        v = next(r for r in out if r.view).view
        self.assertEqual(PB.items_of(self.app.ctx(7), "rep"), [])
        self.assertIn("с нуля", str(v.blocks))
        self.screens("pbn:akb")                                                 # только один показатель, без оклада
        emp, rows = PB.example(self.app.ctx(7), "rep", PB.items_of(self.app.ctx(7), "rep"))
        self.assertTrue(rows and not any(r[0].startswith("⚠️") for r in rows))
        c = O.ensure(self.app.ctx(7), "7")
        O.snapshot(self.app.ctx(7), c)
        c["f"]["company"] = "Пустая"
        parse(*(lambda r: (r["id"], r))(O.build_raw(c)))                        # и такая схема — рабочая настройка клиента

    def test_rep_cannot_open(self):
        app = App("_template", today=TODAY, admins=[1], data_dir=self.tmp)
        app.db().add_employee("Иванов Пётр", "sales", position="rep", role="rep", tg_id=500)
        self.assertIn("руководителя", app.press(500, "pb")[0].text)


if __name__ == "__main__":
    unittest.main()
