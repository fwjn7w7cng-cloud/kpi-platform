"""Живые экраны ролей (core/ui/live.py, Адам 17.11): главная директора, руководителя, торгового, склада, водителя;
решения одним нажатием, отметка точки, отчёт дня одной формой, «Не согласен», честная лента без заказов."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App
from core.ui.web import reply_json

TODAY = date(2026, 11, 17)
ADMIN = 1


def blocks(out):
    for r in out:
        d = reply_json(r)
        if d.get("view"):
            return {b["t"]: b for b in d["view"]["blocks"] if b["k"] == "ui"}
    return {}


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_owner_home(self):
        b = blocks(self.app.press(ADMIN, "sw:role:owner"))
        self.assertEqual(set(b) >= {"hero", "feed", "decide", "tiles"}, True)
        self.assertIsNotNone(b["hero"]["d"]["p"])
        feed = " ".join(x[2] for x in b["feed"]["d"]["items"])
        self.assertNotIn("заказ на", feed.lower())                 # заказов «вживую» нет и в данных (Адам 17.11)
        self.assertTrue(b["feed"]["d"]["items"])
        self.assertEqual(len(b["tiles"]["d"]["items"]), 5)
        ok = b["decide"]["d"]["items"][0]["ok"][1]
        b2 = blocks(self.app.press(ADMIN, ok))                     # утвердили — та же главная, карточка «утверждено»
        self.assertIn("hero", b2)
        self.assertTrue(any(x.get("done", "").startswith("✓") for x in b2["decide"]["d"]["items"]))
        db = self.app.ctx(ADMIN).db
        eid = int(ok.split(":")[2])
        self.assertEqual(db.one("SELECT status FROM edits WHERE id = ?", eid)["status"], "ok")

    def test_head_team(self):
        b = blocks(self.app.press(ADMIN, "sw:role:head"))
        self.assertIn("team", b)
        r = b["team"]["d"]["rows"][0]
        self.assertEqual(len(r["cells"]), 4)
        self.assertTrue(r["acts"])

    def test_rep_route_form_and_pay(self):
        b = blocks(self.app.press(ADMIN, "sw:role:rep"))
        self.assertIn("form", b)
        ctx = self.app.ctx(ADMIN)
        from modules import route as RT
        pts = RT.points(ctx, ctx.me["id"], TODAY.isoformat())
        free = next(c for c in pts if c not in RT.marks(ctx, ctx.me["id"], TODAY.isoformat()))
        self.app.press(ADMIN, f"lvr:{TODAY.isoformat()}:{free}")
        self.assertEqual(RT.marks(self.app.ctx(ADMIN), ctx.me["id"], TODAY.isoformat())[free]["status"], "sold")   # первое нажатие — «заказ»
        self.assertIn("с заказом", blocks(self.app.press(ADMIN, "home"))["form"]["d"]["top"][1])                    # счёт по отметкам — сам
        out = self.app.press(ADMIN, f"lvf:{TODAY.isoformat()}", form={"coll": "abc"})
        self.assertIn("Не понял", out[0].text)
        sv = next(x for x in RT._leads_of(ctx, ctx.me))
        ctx.db.x("UPDATE employees SET tg_id = 777001 WHERE id = ?", sv["id"])
        out = self.app.press(ADMIN, f"lvf:{TODAY.isoformat()}", form={"coll": "45 000", "km": "120", "note": "Стройдвор просит отсрочку"})
        ctx = self.app.ctx(ADMIN)
        rep = ctx.db.get(f"dayrep:{ctx.me['id']}:{TODAY.isoformat()}")
        self.assertEqual(rep["coll"], "45 000")
        self.assertEqual(rep["note"], "Стройдвор просит отсрочку")
        lead = [r.text for r in out if r.to]
        self.assertTrue(lead and "собрал 45 000" in lead[0] and "120 км" in lead[0] and "Стройдвор" in lead[0])
        self.assertTrue(RT.formed(ctx, ctx.me["id"], TODAY.isoformat()))
        self.assertTrue(ctx.db.one("SELECT amount FROM timesheet WHERE emp_id = ? AND kind = 'km' AND day = ?", ctx.me["id"], TODAY.isoformat()))
        p = blocks(self.app.press(ADMIN, "pay"))
        self.assertIn("explain", p)
        self.assertIn("form", p)                                   # «Не согласен»
        out = self.app.press(ADMIN, f"lvq:{ctx.me['id']}", form={"text": "Проверьте оклад за 15-е"})
        self.assertTrue(blocks(out)["form"]["d"]["done"])

    def test_staff_and_driver(self):
        b = blocks(self.app.press(ADMIN, "sw:pos:driver"))
        self.assertIn("hero", b)
        acts = b["hero"]["d"]["acts"]
        if acts:
            b2 = blocks(self.app.press(ADMIN, acts[0][1]))
            self.assertIn("hero", b2)
        self.assertIn("hero", blocks(self.app.press(ADMIN, "sw:pos:storekeeper")))
        self.assertIn("hero", blocks(self.app.press(ADMIN, "sw:pos:buyer")))

    def test_fund_rows(self):
        self.app.press(ADMIN, "sw:role:owner")
        b = blocks(self.app.press(ADMIN, "fund"))
        self.assertIn("rows", b)
        self.assertEqual(len(b["rows"]["d"]["items"]), 5)

    def test_home_not_heaped(self):
        """Адам 17.11 «почему всё в кучу»: на живой главной — ни плиток меню, ни месяца, ни демо-надписи; три частых и «Все функции»."""
        for role in ("rep", "owner", "head"):
            out = self.app.press(ADMIN, f"sw:role:{role}")
            r = next(x for x in out if x.view)
            self.assertFalse([b for row in r.buttons for b in row if not b.to.startswith("bk:")], role)
            b = blocks(out)
            q = b["quick"]["d"]["items"]
            self.assertLessEqual(len(q), 4)
            self.assertEqual(q[-1][1], "all")
            self.assertFalse(any(k == "note" and "выдуманы" in str(x) for k, x in r.view.blocks), role)
        lk = blocks(self.app.press(ADMIN, "all"))["links"]["d"]["groups"]
        self.assertTrue(sum(len(bs) for _, bs in lk) >= 5)
        self.app.press(ADMIN, "sw:role:rep")
        p = self.app.press(ADMIN, "pay")
        mon = [b.to for r in p for row in (r.buttons or []) for b in row if b.to.startswith("mon:")]
        self.assertTrue(mon and mon[0].endswith(":pay"))
        back = self.app.press(ADMIN, mon[0])
        self.assertIn("explain", blocks(back))                         # остались в «Зарплате», не выкинуло на главную
        r = next(x for x in self.app.press(ADMIN, "home") if x.view)
        self.assertTrue(r.buttons and r.buttons[0][0].to.startswith("mon:"))   # прошлый месяц — кнопка вернуться

    def test_set_card(self):
        self.app.press(ADMIN, "sw:role:owner")
        ctx = self.app.ctx(ADMIN)
        ctx.session["built"] = 1
        ctx.save()
        b = blocks(self.app.press(ADMIN, "home"))
        self.assertIn("setbar", b)
        self.assertEqual([a[1] for a in b["setbar"]["d"]["acts"]], ["sw:ed", "sw:req"])

    def test_build_rules_everywhere(self):
        """Адам 17.11: «в сборке убрал склад — а в фонде он есть». Что в «Собрать» — то и везде: отделы, люди, роли."""
        from core.access import editions as E
        self.app.press(ADMIN, "sw:role:owner")
        funcs = [f for f, (d, _) in E.FUNCS.items() if d in ("sales", "logistics") and f not in E.ACC_FUNCS]
        self.app.press(ADMIN, "swb:s:q", form={"funcs": funcs, "on": ["sales", "logistics"],
                                               "counts": {"sales": 8, "stock": 0, "logistics": 3, "office": 0}})
        ctx = self.app.ctx(ADMIN)
        kinds = {d.id: d.kind for d in ctx.cfg.departments}
        b = blocks(self.app.press(ADMIN, "fund"))
        names = [x["t"] for x in b["rows"]["d"]["items"]]
        self.assertFalse([d.name for d in ctx.cfg.departments if d.kind in ("warehouse", "purchasing") and d.name in names])
        es = ctx.db.employees()
        sales = [e for e in es if kinds[e["dept"]] == "sales" and e["role"] not in ("owner", "admin", "accountant")]
        self.assertEqual(len(sales), 8)
        self.assertFalse([e for e in es if kinds[e["dept"]] in ("warehouse", "purchasing")])
        tiles = [t[1] for t in blocks(self.app.press(ADMIN, "home"))["tiles"]["d"]["items"]]
        self.assertFalse([d.name for d in ctx.cfg.departments if d.kind in ("warehouse", "purchasing") and d.name in tiles])
        from core.ui import orient
        self.assertFalse([r for r in orient.demo_bar(self.app.ctx(ADMIN))["roles"] if "складом" in r[0] or "закуп" in r[0].lower()])

    def test_billing_hidden_in_demo(self):
        from core.ui.screens import _menu_items
        self.app.press(ADMIN, "sw:role:owner")
        self.assertNotIn("bl", [m.to for m, _ in _menu_items(self.app.ctx(ADMIN))])


if __name__ == "__main__":
    unittest.main()
