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
        self.assertEqual(RT.marks(self.app.ctx(ADMIN), ctx.me["id"], TODAY.isoformat())[free]["status"], "visited")
        self.app.press(ADMIN, f"lvf:{TODAY.isoformat()}", form={"dir": "Город", "sold": "3", "visits": "5", "worked": "3", "coll": "30т", "km": "120"})
        ctx = self.app.ctx(ADMIN)
        rep = ctx.db.get(f"dayrep:{ctx.me['id']}:{TODAY.isoformat()}")
        self.assertEqual(rep["coll"], "30т")
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

    def test_billing_hidden_in_demo(self):
        from core.ui.screens import _menu_items
        self.app.press(ADMIN, "sw:role:owner")
        self.assertNotIn("bl", [m.to for m, _ in _menu_items(self.app.ctx(ADMIN))])


if __name__ == "__main__":
    unittest.main()
