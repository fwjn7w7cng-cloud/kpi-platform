"""Новые модули на демо: «Не работает», клиенты, маршрут, планы, тесты, склад, логистика, возвраты;
сдельные кубики зарплаты; рассылки по дням недели; свободный текст."""
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from core import anketa as A
from core.app import App
from core.config import load_client
from core.jobs import schedule

TODAY = date(2026, 10, 27)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.app = App("demo", today=TODAY, admins=[1], data_dir=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def as_(self, uid, role=None, emp=None):
        self.app.press(uid, "sw:ed:control")
        if emp:
            self.app.press(uid, f"sw:as:{emp}")
        else:
            self.app.press(uid, f"sw:role:{role}")
        return self.app.ctx(uid)


class Feedback(Base):
    def test_bug_report_reaches_admin_and_back(self):
        ctx = self.as_(101, "rep")
        ask = self.app.press(101, "fb")
        self.assertTrue(ask[0].ask)
        out = self.app.text(101, "Не открывается маршрут")
        self.assertTrue(any(r.to == 1 and "маршрут" in r.text for r in out))
        fid = ctx.db.one("SELECT MAX(id) AS i FROM feedback")["i"]
        self.as_(102, "admin")
        done = self.app.press(102, f"fbd:{fid}")
        self.assertTrue(any(r.to == 101 and "Сделано" in r.text for r in done))


class Clients(Base):
    def test_categories_and_card_by_code(self):
        ctx = self.as_(201, "head")
        from modules.clients import history
        res = history(ctx).all(ctx)
        for cat in "sfgb":
            self.assertTrue(res.get(cat), cat)
        rep = self.as_(202, "rep")
        h = history(rep)
        mine = next(c for c, e in h.owner.items() if e == rep.me["id"])
        other = next(c for c, e in h.owner.items() if e != rep.me["id"])
        card = self.app.text(202, mine)
        self.assertIn(f"код {mine}", card[0].view.subtitle)
        self.assertIn("не из ваших", self.app.text(202, other)[0].text)


class Route(Base):
    def test_mark_and_form_report(self):
        ctx = self.as_(301, "rep")
        day = TODAY.isoformat()
        from modules.route import points
        pts = points(ctx, ctx.me["id"], day)
        self.assertTrue(pts)
        free = [c for c in pts if not ctx.db.one("SELECT 1 AS x FROM visit_marks WHERE day = ? AND emp_id = ? AND client = ?",
                                                 day, ctx.me["id"], c)]
        self.assertTrue(self.app.press(301, f"rtp:{day}:{free[0]}:sold")[0].ask)
        self.app.text(301, "12 500")
        self.app.press(301, f"rtp:{day}:{free[1]}:missed:0")
        m = {r["client"]: r for r in ctx.db.q("SELECT * FROM visit_marks WHERE day = ? AND emp_id = ?", day, ctx.me["id"])}
        self.assertEqual(m[free[0]]["amount"], 12500)
        self.assertEqual(m[free[1]]["reason"], "Закрыто")
        out = self.app.press(301, f"rtr:{day}")
        self.assertIn("сформирован", out[0].text)
        self.assertIn("уже", self.app.press(301, f"rtr:{day}")[0].text)
        head = self.as_(302, "head")
        r = self.app.press(302, f"rtd:{day}")
        self.assertIsNotNone(r[0].view)
        self.assertTrue(head.db.one("SELECT 1 AS x FROM visit_reports WHERE day = ? AND emp_id = ?", day, ctx.me["id"]))


class Plans(Base):
    def test_copy_edit_send_approve(self):
        head = self.as_(401, "head")
        nxt = "2026-11"
        self.app.press(401, f"plc:{nxt}:5")
        p = head.db.plans(nxt)
        rep = next(iter(p))
        self.assertGreater(p[rep]["sales"], head.db.plans("2026-10")[rep]["sales"])
        self.app.press(401, f"ple:{nxt}:{rep}")
        self.app.text(401, "1 234 000\nакб 31")
        p = head.db.plans(nxt)[rep]
        self.assertEqual((p["sales"], p["akb"]), (1234000, 31))
        self.app.press(401, f"pls:{nxt}")
        self.as_(402, "owner")
        self.app.press(402, f"pla:{nxt}")
        from modules.plans import state
        self.assertEqual(state(head.db, nxt)["status"], "ok")
        self.assertIn("не меняются", self.app.press(401, f"ple:{nxt}:{rep}")[0].text)


class Quiz(Base):
    def test_pass_weekly_test(self):
        ctx = self.as_(501, "rep")
        from modules.quiz import week_ref
        ref = week_ref(TODAY)
        r = self.app.press(501, f"qzs:test:{ref}")
        n = 0
        while r and r[-1].view is None and n < 20:
            r = self.app.press(501, "qza:0")           # вариант 0 — всегда правильный (на экране перемешан)
            n += 1
        self.assertEqual(n, 10)
        res = ctx.db.one("SELECT * FROM quiz_results WHERE emp_id = ? AND ref = ? ORDER BY id DESC", ctx.me["id"], ref)
        self.assertEqual((res["score"], res["total"]), (10, 10))

    def test_newbie_lessons_unlock(self):
        newbie = next(e for e in self.as_(502, "rep").db.employees() if e["role"] == "newbie")
        ctx = self.as_(503, emp=newbie["id"])
        from modules.quiz import lesson_open
        self.assertTrue(lesson_open(ctx, newbie["id"], 1))
        self.assertFalse(lesson_open(ctx, newbie["id"], 2))
        self.assertIn("закрыт", self.app.press(503, "qzl:3")[0].text)

    def test_add_question(self):
        ctx = self.as_(504, "head")
        self.app.press(504, "qzq")
        self.app.text(504, "Сколько будет 2+2?\n+ 4\n- 5\n- 22")
        q = ctx.db.one("SELECT * FROM quiz_questions ORDER BY id DESC")
        self.assertEqual(json.loads(q["options"])[0], "4")

    def test_weekly_invite_only_on_its_day(self):
        names = lambda now: [j.name for j in schedule.due(self.app, now)]           # noqa: E731
        self.assertIn("quiz_week", names(datetime(2026, 11, 1, 10, 0)))           # воскресенье
        self.assertNotIn("quiz_week", names(datetime(2026, 10, 27, 10, 0)))


class Stock(Base):
    def test_categories_order_and_transit(self):
        ctx = self.as_(601, "owner")
        from modules.stock import Stock as S
        s = S(ctx)
        cats = {c: [x for x in s.items if s.cat(x) == c] for c in ("out", "low", "stale")}
        for c, xs in cats.items():
            self.assertTrue(xs, c)
        sups = s.suppliers()
        i = next(n for n, (_, codes) in enumerate(sups) if any(s.need(c) > 0 for c in codes))
        r = self.app.press(601, f"son:{i}")
        oid = ctx.db.one("SELECT MAX(id) AS i FROM stock_orders")["i"]
        code = json.loads(ctx.db.one("SELECT items FROM stock_orders WHERE id = ?", oid)["items"])[0][0]
        need0 = s.need(code)
        self.app.press(601, f"soe:{oid}")
        self.app.text(601, f"{code} {need0}")
        x = self.app.press(601, f"sox:{oid}")
        self.assertTrue(x[0].files[0][0].endswith(".xlsx"))
        self.app.press(601, f"sos:{oid}")
        self.assertEqual(S(ctx).need(code), 0)
        found = self.app.text(601, code)
        self.assertIn(f"код {code}", found[0].view.blocks[0][1][1][0].value)


class LogisticsReturns(Base):
    def test_driver_piece_pay_and_doubles(self):
        ctx = self.as_(701, "owner")
        from modules.logistics import trips
        t = trips(ctx)
        self.assertTrue(t.doubles())
        drv = next(e for e in ctx.db.employees() if e["position"] == "driver")
        km = t.totals(drv["id"])["km"]
        rows = {r[0]: r[1] for r in ctx.pay().calc(drv).rows}
        self.assertAlmostEqual(rows["Пробег"], km * 4)
        self.assertIn("Точки доставки", rows)

    def test_returns_cut(self):
        ctx = self.as_(702, "owner")
        from modules.returns import load
        rs = load(ctx.db, ctx.month, ctx.kpi().who)
        rep = next(e for e in ctx.db.employees() if any(r["fault"] == "rep" and r["rep"] == e["id"] for r in rs))
        own = sum(r["amount"] for r in rs if r["fault"] == "rep" and r["rep"] == rep["id"])
        rows = {r[0]: r[1] for r in ctx.pay().calc(rep).rows}
        self.assertAlmostEqual(rows["Возвраты по своей вине"], -own * 0.03)
        store = next(e for e in ctx.db.employees() if any(r["guilty"] == e["id"] for r in rs) and e["position"] == "storekeeper")
        n = sum(1 for r in rs if r["guilty"] == store["id"])
        rows = {r[0]: r[1] for r in ctx.pay().calc(store).rows}
        self.assertEqual(rows["Ошибки склада в возвратах"], -500 * n)

    def test_modules_off_in_business_without_addons(self):
        self.app.press(703, "sw:ed:business")
        self.app.press(703, "sw:role:owner")
        r = self.app.press(703, "lg")
        self.assertIn("Подключается отдельно", r[0].view.subtitle)
        ctx = self.app.ctx(703)
        drv = next(e for e in ctx.db.employees() if e["position"] == "driver")
        self.assertNotIn("Пробег", {r[0] for r in ctx.pay().calc(drv).rows})


class Anketa(unittest.TestCase):
    def test_per_unit_rows(self):
        t = A.from_raw(load_client("demo").raw)
        rows = [r for r in t["Зарплата"] if r[1] in ("Сдельно за единицу", "Удержание за единицу")]
        self.assertIn(["Водитель", "Сдельно за единицу", "Пробег, км", "", "4", "", "", "", "", ""], rows)
        cut = next(r for r in rows if r[2] == "Возвраты по своей вине, ₽")
        self.assertEqual((cut[1], cut[4]), ("Удержание за единицу", "3%"))
        cfg, problems = A.check(t)
        self.assertEqual(problems, [])
        drv = cfg.raw["pay_schemes"][cfg.position("driver")["pay"]]
        self.assertIn({"type": "per_unit", "unit": "km", "price": 4}, drv)

    def test_bad_unit(self):
        t = A.from_raw(load_client("demo").raw)
        t["Зарплата"].append(["Водитель", "Сдельно за единицу", "Литры", "", "5", "", "", "", "", ""])
        _, problems = A.check(t)
        self.assertIn("Литры", A.problems_text(problems))


if __name__ == "__main__":
    unittest.main()


class Debts(unittest.TestCase):
    """Дебиторка: любой порог, доля от разной базы, удержания тремя способами, бонус, «собрать до нормы»."""

    def setUp(self):
        from core.data.db import DB
        from core.data.model import Dataset, Debt, Sale
        from core.kpi.engine import Kpi
        from core.pay.engine import Payroll
        from core.config import parse
        self.raw = load_client("demo").raw
        self.db = DB.open(":memory:")
        self.emp = self.db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", keys=["иванов петр"])
        on = date(2026, 10, 21)
        self.db.load(Dataset("sales", date(2026, 10, 1), date(2026, 10, 31), rows=[Sale(date(2026, 10, 5), "иванов петр", "1", "", 100000)]))
        self.db.load(Dataset("debts", on, on, rows=[Debt(on, "иванов петр", "1", 50000, 20000, 12000, 9000, 9000)]))
        self.parse, self.Kpi, self.Payroll = parse, Kpi, Payroll

    def pay(self, comps, kpi=None):
        raw = json.loads(json.dumps(self.raw))
        raw["kpi"].update(kpi or {})
        raw["pay_schemes"]["rep"] = comps
        cfg = self.parse("demo", raw)
        k = self.Kpi(cfg, self.db, "2026-10", TODAY)
        return k, self.Payroll(cfg, self.db, k).calc(self.db.employee(self.emp)).rows

    def test_thresholds_and_bases(self):
        k, _ = self.pay([])
        r = k.debts[self.emp][0]
        self.assertEqual(k.overdue_amount(r, 30), 30000)
        self.assertEqual(k.overdue_amount(r, 45), 24000)        # половина 31–60 + 61–90 + 91+
        self.assertEqual(k.overdue_amount(r, 90), 9000)
        self.assertAlmostEqual(k.debt_share([self.emp], 60, "debt")[2], 18000 / 50000)
        self.assertEqual(k.emp(self.emp)["overdue"].detail["to_norm"], 18000 - 0.10 * 100000)
        k2, _ = self.pay([], {"debt_norm": "20%"})
        self.assertEqual(k2.emp(self.emp)["overdue"].detail["to_norm"], 0)

    def test_penalty_modes_and_bonus(self):
        _, rows = self.pay([{"type": "debt_penalty", "amount": "3 000", "share": "10%"}])
        self.assertEqual(rows[0][1], -3000)                       # 18% > 10%
        _, rows = self.pay([{"type": "debt_penalty", "mode": "percent", "rate": "5%", "max": "500"}])
        self.assertEqual(rows[0][1], -500)                        # 5% от 18 000 = 900, потолок 500
        _, rows = self.pay([{"type": "debt_penalty", "mode": "steps", "steps": [["5%", "1 000"], ["15%", "2 000"], ["30%", "5 000"]]}])
        self.assertEqual(rows[0][1], -2000)
        _, rows = self.pay([{"type": "debt_bonus", "mode": "steps", "days": 90, "steps": [["5%", "3 000"], ["10%", "1 000"]]}])
        self.assertEqual(rows[0][1], 1000)                        # 90+: 9 000 / 100 000 = 9%
        _, rows = self.pay([{"type": "debt_bonus", "amount": "2 000", "share": "5%"}])
        self.assertEqual(rows[0][1], 0)

    def test_ready_overdue_from_1c_wins(self):
        from core.data.model import Dataset, Debt
        on = date(2026, 10, 22)
        self.db.load(Dataset("debts", on, on, rows=[Debt(on, "иванов петр", "1", 50000, 20000, 12000, 9000, 9000, over=7000)]))
        k, _ = self.pay([])
        self.assertEqual(k.emp(self.emp)["overdue"].detail["sum"], 7000)

    def test_anketa_debt_rows_roundtrip(self):
        raw = json.loads(json.dumps(self.raw))
        raw["pay_schemes"]["rep"] = [{"type": "debt_penalty", "mode": "steps", "days": 45, "of": "debt",
                                      "steps": [["5%", "1 000"], ["10%", "3 000"]]},
                                     {"type": "debt_bonus", "amount": "2 000", "share": "3%", "scope": "team"}]
        t = A.from_raw(raw)
        rows = [r for r in t["Зарплата"] if r[0] == "Торговый представитель"]
        self.assertIn(["Торговый представитель", "Удержание за просрочку — ступени", "Просрочка 45+ от долга", "Свои",
                       "5% → 1 000; 10% → 3 000", "", "", "", "", ""], rows)
        cfg, problems = A.check(t)
        self.assertEqual(problems, [])
        rep = cfg.raw["pay_schemes"][cfg.position("rep")["pay"]]
        self.assertEqual(rep[0], {"type": "debt_penalty", "mode": "steps", "days": 45, "of": "debt",
                                  "steps": [[0.05, 1000], [0.1, 3000]]})
        self.assertEqual(rep[1]["scope"], "team")
        comp = {r[0]: r[1] for r in t["Компания"]}
        self.assertEqual((comp["Норма просрочки"], comp["База нормы просрочки"]), ("10%", "Продажи за 30 дней"))


class Ramadan(Base):
    def test_switch_on_fines_schedule_and_plan(self):
        ctx = self.as_(801, "owner")
        self.assertTrue(self.app.press(801, "rmd:on")[0].ask)
        self.app.text(801, "20.10.2026 – 18.11.2026")
        from core.calendar import in_ramadan, ramadan
        self.assertTrue(in_ramadan(ctx.db, TODAY))
        home = self.app.start(801)[0]
        self.assertTrue(any("Рамадан" in b[1][0] for b in home.view.blocks if b[0] == "note"))
        # опоздание в Рамадан — без штрафа, до — со штрафом
        rep = next(e for e in ctx.db.employees() if e["role"] == "rep")
        ctx.db.add_timesheet("2026-10-21", rep["id"], "late", minutes=40)
        ctx.db.add_timesheet("2026-10-05", rep["id"], "late", minutes=40)
        fines = self.app.ctx(801).pay().fines(rep)
        self.assertEqual([n for _, n in fines if "21.10" in n], [])
        self.assertTrue(any("05.10" in n for _, n in fines))
        # рассылки: утро в 10:00 вместо 08:00
        names = lambda now: [j.name for j in schedule.due(self.app, now)]       # noqa: E731
        self.assertNotIn("morning_brief", names(datetime(2026, 10, 27, 8, 0)))
        self.assertIn("morning_brief", names(datetime(2026, 10, 27, 10, 0)))
        # планы на ноябрь «как в октябре»: дни поста — 85%
        self.as_(802, "head")
        self.app.press(802, "plc:2026-11:0")
        p_nov, p_oct = ctx.db.plans("2026-11"), ctx.db.plans("2026-10")
        rid = next(iter(p_nov))
        self.assertLess(p_nov[rid]["sales"], p_oct[rid]["sales"])
        self.app.press(801, "rmd:late")
        self.assertFalse(ramadan(ctx.db)["late_free"])
        self.app.press(801, "rmd:off")
        self.assertFalse(in_ramadan(ctx.db, TODAY))
