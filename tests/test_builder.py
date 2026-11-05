"""🧩 «Собрать бота» одной страницей (core/ui/builder.py): экран отдаёт приложению всё сразу, выбор сохраняется одним JSON,
мусор отбрасывается, цена на телефоне совпадает с pricing.quote, пример расчёта считается настоящим движком."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App
from core.transport.web import WebApi
from core.ui.web import reply_json

TODAY = date(2026, 11, 5)
ADMIN = 1


def builder_of(out):
    for r in out:
        d = reply_json(r)
        for b in (d.get("view") or {}).get("blocks", []):
            if b["k"] == "builder":
                return b["data"]
    return None


class Builder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_opens_with_everything(self):
        d = builder_of(self.app.press(ADMIN, "sw:ed"))
        self.assertIsNotNone(d)
        self.assertEqual([x["k"] for x in d["depts"]], ["sales", "stock", "logistics", "office"])
        self.assertEqual(len(d["sizes"]), 5)
        self.assertIn("salary", d["bricks"])
        self.assertEqual(len(d["bricks"]), 32)
        self.assertEqual({r["pos"] for r in d["roles"]}, {"rep", "sv", "head", "wh", "buyer", "driver", "office"})
        rep = next(r for r in d["roles"] if r["pos"] == "rep")
        self.assertTrue(rep["items"] and rep["quick"])

    def test_save_and_clean(self):
        form = {"funcs": ["kpi", "debts", "approval", "nonsense"], "counts": {"sales": "12", "stock": -5, "logistics": 99999},
                "year": True, "base": "start",
                "pb": {"rep": [{"b": "salary", "v": "days", "p": {"amount": "25 000", "own": True}},
                               {"b": "sales", "v": "pct", "p": {"rate": 0.015}},
                               {"b": "piece", "v": "km", "p": {"price": 7}},          # «за км» — не для торгового
                               {"b": "zzz", "v": "x"}],
                       "hacker": [{"b": "salary", "v": "days"}]}, "ui": {"tab": 1}}
        d = builder_of(self.app.press(ADMIN, "swb:s:ex:rep", form=form))
        self.assertEqual(d["funcs"], ["approval", "debts", "kpi", "payroll"])        # «Утверждение» тянет «Зарплату»
        self.assertEqual(d["counts"]["sales"], 12)
        self.assertEqual(d["counts"]["stock"], 0)
        self.assertEqual(d["counts"]["logistics"], 5000)
        self.assertTrue(d["year"])
        self.assertEqual(d["ui"], {"tab": 1})
        rep = next(r for r in d["roles"] if r["pos"] == "rep")
        self.assertEqual([(i["b"], i["v"]) for i in rep["items"]], [("salary", "days"), ("sales", "pct")])
        self.assertEqual(rep["items"][0]["p"], {"amount": 25000, "own": True})
        ex = d["example"]["rep"]
        self.assertTrue(ex["who"] and ex["rows"])
        self.assertTrue(any(r[0] == "Оклад" for r in ex["rows"]))
        ctx = self.app.ctx(ADMIN)                                                     # набор зрителя — тот же, что видит демо
        self.assertEqual(set(ctx.cfg.functions), {"approval", "debts", "kpi", "payroll"})

    def test_price_matches_quote(self):
        """Цена, которую считает телефон, — по тем же правилам: проверяем те же числа, что отдаёт сервер."""
        d = builder_of(self.app.press(ADMIN, "sw:ed"))
        pr, funcs, counts = d["prices"], set(d["funcs"]), d["counts"]
        s, on = 0, set()
        for f, _, p, _ in d["all"]:
            s += p if f in funcs else 0
        for x in d["depts"]:
            for f, _, p, _ in x["fs"]:
                if f in funcs:
                    s += p
                    on.add(x["k"])
        s += sum(counts[k] for k in on) * pr["user"]
        disc = s * pr["disc_all"] / 100 if len(on) == 4 else 0
        ext = sum(p for f, _, p, _ in d["extra"] if f in funcs)
        monthly = round((s - disc + ext) / pr["round"]) * pr["round"]
        self.assertEqual(monthly, d["check"]["monthly"])

    def test_dept_without_functions(self):
        """Адам 05.11: отдел без функций (водители на окладе) остаётся в боте и в цене — люди × цена за человека."""
        from core.access import pricing as P
        form = {"funcs": ["kpi"], "on": ["logistics", "stock", "office", "zzz"],
                "counts": {"sales": 5, "logistics": 15, "stock": 0, "office": 0}}
        d = builder_of(self.app.press(ADMIN, "swb:s:q2", form=form))
        self.assertEqual(d["on"], ["logistics", "office", "sales", "stock"])
        ctx = self.app.ctx(ADMIN)
        self.assertTrue(ctx.cfg.packages["logistics"])
        self.assertIn("staff", ctx.cfg.modules)                 # кабинет сотрудника есть
        self.assertNotIn("logistics", ctx.cfg.functions)         # «Рейсы» — нет
        pr = P.prices(ctx.office)
        q = P.quote({"kpi"}, {"sales": 5, "logistics": 15}, pr, depts={"sales", "stock", "logistics", "office"})
        self.assertEqual(q["people"], 20)
        self.assertFalse(q["all_depts"])                         # 4 отдела без платных функций — без скидки
        self.assertTrue(any("только зарплата" in x[2] for x in q["lines"]))
        self.assertEqual(q["monthly"], d["check"]["monthly"])
        self.assertEqual(P.quote(set(), {"logistics": 10}, pr, depts={"logistics"})["monthly"], P.rnd(10 * pr["user"]))

    def test_look_then_want(self):
        """Адам 05.11: «👀 Посмотреть мой бот» — сохраняет набор и открывает главную демо, «📨 Хочу так» — уже там."""
        d = builder_of(self.app.press(ADMIN, "sw:ed"))
        self.assertFalse(d["partner"])
        out = self.app.press(ADMIN, "swb:s:look", form={"funcs": ["kpi", "debts"], "counts": {"sales": 8}})
        self.assertTrue(self.app.ctx(ADMIN).session.get("built"))
        btns = [b.to for r in out for row in (r.buttons or []) for b in row]
        self.assertIn("sw:req", btns)
        self.assertIn("sw:ed", btns)
        self.assertEqual(set(self.app.ctx(ADMIN).cfg.functions), {"debts", "kpi"})

    def test_quiet_save_over_web(self):
        api = WebApi(self.app)
        _, h, _ = api.handle("POST", "/api/start", "", {}, "")
        tok = h["Set-Cookie"].split("kid=")[1].split(";")[0]
        uid = api._device(tok, False)[1]
        self.app.admins.add(uid)
        code, _, raw = api.handle("POST", "/api/press", "", {"to": "swb:s:q", "form": {"funcs": ["kpi"], "counts": {"sales": 3}}}, tok)
        self.assertEqual(code, 200)
        self.assertIn(b'"ok"', raw)
        self.assertEqual(self.app.ctx(uid).session["counts"]["sales"], 3)


if __name__ == "__main__":
    unittest.main()
