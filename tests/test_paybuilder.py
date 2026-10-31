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

    def items(self, pos="rep"):
        return PB.items_of(self.app.ctx(7), pos)

    def item(self, bid, pos="rep"):
        return next(x for x in self.items(pos) if x["b"] == bid)

    def add(self, *bids):
        for b in bids:
            self.screens(f"pbs:t:{b}")
        self.screens("pbs:ok")

    def test_every_block_and_variant_for_every_position(self):
        for pos in PB.POSITIONS:
            self.screens(f"pb:{pos}")
            self.screens("pbs")
            for bid in PB._blocks_for(pos):
                if not any(x["b"] == bid for x in self.items(pos)):
                    self.screens(f"pbs:t:{bid}")
            self.screens("pbs:ok")
            for bid in PB._blocks_for(pos):
                self.screens(f"pbe:{bid}")
                for var in PB.variants(bid, pos):
                    self.screens(f"pbe:{bid}:t:{var[0]}")
                self.screens(f"pbe:{bid}:ok")
            emp, rows = PB.example(self.app.ctx(7), pos, self.items(pos))
            self.assertTrue(emp, pos)
            self.assertFalse([r for r in rows if r[0].startswith("⚠️")], pos)
        self.assertEqual(sum(1 for x in self.items("rep") if x["b"] == "salary"), 1)       # оклад — один

    def test_select_blocks_ok_and_cancel(self):
        out = self.screens("pb:rep")
        self.assertIn("Для кого схема", out[0].text)
        self.assertEqual(out[1].view.title, "🧱 Конструктор зарплаты")
        self.assertTrue(any(b.label.startswith("‹") for b in out[1].all_buttons()))       # сам экран — «Назад» туда, откуда пришли
        before = [x["b"] for x in self.items()]
        win = self.screens("pbs")[-1]
        self.assertEqual([b.label for b in win.buttons[-2]], ["✅ ОК", "✖️ Отмена"])
        bk = win.buttons[-1][0].to                                              # «‹ Назад» в окне — на страницу конструктора
        self.screens("pbs:t:akb")
        self.assertIn("Для кого схема", self.screens(bk)[0].text)
        self.assertEqual([x["b"] for x in self.items()], before)
        self.screens("pbs:t:akb")
        self.screens("pbs:t:mult")
        self.assertEqual([x["b"] for x in self.items()], before)                # до «ОК» схема не меняется
        self.screens("pbs:no")
        self.assertEqual([x["b"] for x in self.items()], before)                # «Отмена» — всё как было
        self.screens("pbs")
        self.screens("pbs:t:mult")
        self.screens(f"pbs:t:{before[0]}")                                      # сняли галочку — блок уйдёт
        self.screens("pbs:ok")
        got = [x["b"] for x in self.items()]
        self.assertIn("mult", got)
        self.assertNotIn(before[0], got)
        self.assertEqual(got[-1], "mult")                                       # множитель — в конце, как считает движок
        back = self.screens("pbs:ok")[-1]
        self.assertTrue(any((b.to or "").startswith("bk:") for b in back.all_buttons()))

    def test_block_variants_fields_ok_cancel(self):
        self.screens("pb:rep")
        self.screens("pbx")
        self.add("sales")
        win = self.screens("pbe:sales")[-1]
        self.assertTrue(win.buttons[-1][0].label.startswith("‹"))
        self.screens("pbe:sales:t:pct_steps")                                   # процент — ступенями
        self.screens("pbe:sales:t:gate")                                        # и бонус с порога плана
        self.assertEqual([x["v"] for x in self.items()], ["pct"])               # до «ОК» — прежнее
        self.screens("pbe:sales:f:pct_steps:rate_steps")
        self.app.text(7, "80 0,3; 100 0,5")
        self.screens("pbe:sales:f:gate:min")
        self.assertTrue(self.app.text(7, "много")[0].ask)                        # не число — переспросит
        self.screens("pbe:sales:no")
        self.assertEqual([x["v"] for x in self.items()], ["pct"])               # «Отмена» — как было
        self.screens("pbe:sales")
        self.screens("pbe:sales:t:pct_steps")
        self.screens("pbe:sales:t:gate")
        self.screens("pbe:sales:f:pct_steps:rate_steps")
        self.app.text(7, "80 0,3; 100 0,5")
        self.screens("pbe:sales:ok")
        it = self.items()
        self.assertEqual([x["v"] for x in it], ["pct_steps", "gate"])            # процент и бонус — вместе, по одному виду
        self.assertEqual(it[0]["p"]["rate_steps"], [[0.8, 0.003], [1.0, 0.005]])
        self.screens("pbe:sales")
        self.screens("pbe:sales:t:steps")                                       # другой бонус вместо порога
        self.screens("pbe:sales:ok")
        self.assertEqual([x["v"] for x in self.items()], ["pct_steps", "steps"])
        self.screens("pbe:sales")
        self.screens("pbe:sales:t:pct_steps")
        self.screens("pbe:sales:t:steps")
        self.screens("pbe:sales:ok")                                            # ничего не отмечено — блок убран
        self.assertEqual(self.items(), [])
        self.screens("pbr")
        self.assertEqual(len(self.items()), len(PB.DEFAULTS["rep"]))

    def test_piece_units_several(self):
        self.screens("pb:driver")
        self.screens("pbx")
        self.add("piece")
        units = [v[0] for v in PB.variants("piece", "driver")]
        self.assertIn("km", units)
        self.screens("pbe:piece")
        for u in units[:3]:
            if u not in [x["v"] for x in PB._draft(self.app.ctx(7), "piece")["items"]]:
                self.screens(f"pbe:piece:t:{u}")
        self.screens(f"pbe:piece:f:{units[0]}:price")
        self.app.text(7, "7")
        self.screens("pbe:piece:ok")
        it = self.items("driver")
        self.assertEqual(sorted(x["v"] for x in it), sorted(units[:3]))         # за что платить — можно несколько
        self.assertEqual(next(x for x in it if x["v"] == units[0])["p"]["price"], 7)
        ctx = self.app.ctx(7)                                                    # старая запись «unit» переводится
        ctx.session["pb"]["driver"] = [{"b": "piece", "v": "unit", "p": {"unit": "km", "price": 4}}]
        self.assertEqual(PB.items_of(ctx, "driver"), [{"b": "piece", "v": "km", "p": {"price": 4}}])

    def test_goes_to_card_and_client_config(self):
        self.screens("pb:rep")
        self.add("mult", "margin", "focus")                                     # фокусные — своим блоком
        self.screens("pb:driver")
        self.add("cut")
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

    def test_no_brick_word(self):
        for pos in PB.POSITIONS:
            for r in self.screens(f"pb:{pos}") + self.screens("pbs"):
                self.assertNotIn("ирпичик", str(r.text) + str(r.view and r.view.blocks) + str([b.label for b in r.all_buttons()]))

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
        self.assertIn("Схема пустая", str(v.blocks))
        self.add("akb")                                                         # только один показатель, без оклада
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
