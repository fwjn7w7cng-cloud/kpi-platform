"""Редакции «Компания» и «Максимум» на демо: закупки с согласованием, приёмка на складе, «Мой день» водителя,
ГСМ, кадры, функции на заказ; роли по должностям и нижнее меню отделов."""
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path

from core.app import App
from core.ui import screens

TODAY = date(2026, 10, 29)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[1], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pos(self, uid, pos, ed="max"):
        self.app.press(uid, f"sw:ed:{ed}")
        self.app.press(uid, f"sw:pos:{pos}")
        return self.app.ctx(uid)

    def role(self, uid, role, ed="max"):
        self.app.press(uid, f"sw:ed:{ed}")
        self.app.press(uid, f"sw:role:{role}")
        return self.app.ctx(uid)

    def status(self, ctx, oid):
        return ctx.db.one("SELECT status FROM stock_orders WHERE id = ?", oid)["status"]


class Purchasing(Base):
    def _new_order(self, uid):
        ctx = self.app.ctx(uid)
        from modules.stock import Stock
        s = Stock(ctx)
        busy = {o["supplier"] for o in ctx.db.q("SELECT supplier FROM stock_orders WHERE status IN ('draft', 'back')")}
        i, sup = next((n, sup) for n, sup, codes in s.my_suppliers(ctx) if sup not in busy and any(s.need(c) > 0 for c in codes))
        self.app.press(uid, f"son:{i}")
        return ctx.db.one("SELECT id FROM stock_orders WHERE supplier = ? AND status = 'draft'", sup)["id"], s

    def test_buyer_sees_only_own_suppliers(self):
        ctx = self.pos(11, "buyer")
        from modules.stock import Stock, owners
        own = owners(ctx.db)
        mine = {sup for _, sup, _ in Stock(ctx).my_suppliers(ctx)}
        self.assertTrue(mine)
        self.assertEqual(mine, {s for s, e in own.items() if e == ctx.me["id"]})
        self.assertLess(len(mine), len(Stock(ctx).suppliers()))

    def test_small_order_head_approves_then_warehouse_receives_with_shortage(self):
        buyer = self.pos(12, "buyer")
        oid, s = self._new_order(12)
        items = json.loads(buyer.db.one("SELECT items FROM stock_orders WHERE id = ?", oid)["items"])
        code = items[0][0]
        self.app.press(12, f"soe:{oid}")
        self.app.text(12, "\n".join(f"{c} 0" for c, _ in items[1:]) + f"\n{code} 10")   # маленький заказ — до порога
        out = self.app.press(12, f"sop:{oid}")
        self.assertEqual(self.status(buyer, oid), "wait")
        self.assertIn("начальнику закупок", out[0].text)
        head = self.pos(13, "buy_head")
        from modules.stock import waiting
        self.assertIn(oid, waiting(head))
        self.app.press(13, f"soa:{oid}")
        self.assertEqual(self.status(head, oid), "ok")
        self.app.press(12, f"sos:{oid}")
        self.assertEqual(self.status(buyer, oid), "sent")
        wh = self.pos(14, "storekeeper")
        self.assertTrue(any(b.to == f"wra:{oid}" for b in self.app.press(14, f"wrc:{oid}")[0].all_buttons()))
        self.app.press(14, f"wrn:{oid}")
        self.app.text(14, f"{code} 7")
        self.assertEqual(self.status(wh, oid), "part")
        ex = json.loads(wh.db.one("SELECT extra FROM stock_orders WHERE id = ?", oid)["extra"])
        self.assertIn([code, 7.0], ex["got"])

    def test_big_order_goes_to_owner_and_can_be_returned(self):
        buyer = self.pos(15, "buyer")
        oid, s = self._new_order(15)
        code = json.loads(buyer.db.one("SELECT items FROM stock_orders WHERE id = ?", oid)["items"])[0][0]
        self.app.press(15, f"soe:{oid}")
        self.app.text(15, f"{code} {round(400000 / s.price(code)) + 1}")    # выше порога 300 000
        self.assertIn("директору", self.app.press(15, f"sop:{oid}")[0].text)
        head = self.pos(16, "buy_head")
        from modules.stock import waiting
        self.assertNotIn(oid, waiting(head))
        self.assertIn("нечего", self.app.press(16, f"soa:{oid}")[0].text)
        owner = self.role(17, "owner")
        self.assertIn(oid, waiting(owner))
        self.app.press(17, f"sob:{oid}")
        self.app.text(17, "Слишком много, половину")
        self.assertEqual(self.status(owner, oid), "back")
        card = self.app.press(15, f"sko:{oid}")[0]
        self.assertTrue(any("половину" in str(b) for b in card.view.blocks))
        self.assertTrue(any(b.to == f"sop:{oid}" for b in card.all_buttons()))

    def test_seeded_orders_in_every_status(self):
        ctx = self.role(18, "owner")
        st = {r["status"] for r in ctx.db.q("SELECT status FROM stock_orders")}
        self.assertTrue({"draft", "wait", "back", "ok", "sent", "came"} <= st)


class Logistics(Base):
    def test_driver_marks_own_day_and_pay(self):
        ctx = self.pos(21, "driver")
        me = ctx.me
        self.app.press(21, f"dmo:{me['id']}:{TODAY.isoformat()}")
        self.app.press(21, f"dmt:{me['id']}:{TODAY.isoformat()}")
        self.app.text(21, "06:30 19:45")
        from modules.logistics import _get, overtime
        m = _get(self.app.ctx(21), me["id"], TODAY)
        self.assertEqual((m["out"], m["back"]), ("06:30", "19:45"))
        self.assertEqual(overtime(ctx.cfg, m), 90 + 105)
        old = TODAY - timedelta(days=3)
        self.assertIn("нельзя", self.app.press(21, f"dmn:{me['id']}:{old.isoformat()}")[0].text)
        self.app.press(21, f"dmp:{me['id']}:{TODAY.isoformat()}")
        self.app.text(21, "2,5 450 трубы")
        rows = {r[0]: r[1] for r in self.app.ctx(21).pay().calc(me).rows}
        self.assertIn("Переработка, минуты", rows)
        self.assertIn("Привоз от поставщика", rows)

    def test_dispatcher_marks_any_day_and_sees_far_trips(self):
        ctx = self.pos(22, "dispatcher")
        drv = next(e for e in ctx.db.employees() if e["position"] == "driver")
        old = TODAY - timedelta(days=5)
        self.app.press(22, f"dmn:{drv['id']}:{old.isoformat()}")
        from modules.logistics import _get, far_trips
        self.assertEqual(_get(self.app.ctx(22), drv["id"], old).get("night"), 1)
        self.assertTrue(far_trips(self.app.ctx(22)))
        self.assertTrue(self.app.press(22, "lgs")[0].view)

    def test_fuel_card_and_norm(self):
        ctx = self.pos(23, "dispatcher")
        drv = next(e for e in ctx.db.employees() if e["position"] == "driver")
        self.app.press(23, f"fun:{drv['id']}")
        self.app.text(23, "11,5")
        self.app.press(23, f"fuc:{drv['id']}")
        self.app.text(23, "40 000")
        from modules.fuel import rows_of
        r = next(x for x in rows_of(self.app.ctx(23)) if x["emp"]["id"] == drv["id"])
        self.assertEqual((r["norm"], r["card"]), (11.5, 40000))
        self.assertAlmostEqual(r["diff"], 40000 - r["km"] * 11.5 / 100 * 65)


class Hr(Base):
    def test_hire_wizard_checklist_apply_and_order(self):
        ctx = self.pos(31, "hr")
        before = len(ctx.db.employees())
        self.app.press(31, "hrn:hire")
        self.app.text(31, "Тестов Тест Тестович")
        self.app.press(31, "hrw:pos:storekeeper")
        self.app.press(31, f"hrw:day:{TODAY.isoformat()}")
        self.app.text(31, "40 000")
        from modules.hr import procs
        p = procs(self.app.ctx(31).db)[-1]
        self.assertEqual((p["fio"], p["salary"], p["status"]), ("Тестов Тест Тестович", 40000, "open"))
        self.assertIn("пункты", self.app.press(31, f"hra:{p['id']}")[0].text)      # не всё закрыто — провести нельзя
        self.role(32, "owner")
        self.app.press(32, f"hrc:{p['id']}")
        for i in range(len(p["items"])):
            self.app.press(32, f"hrc:{p['id']}:t:{i}")
        self.app.press(32, f"hrc:{p['id']}:ok")                                      # отмеченные — закрыты разом
        self.assertIn("Отменить потом нельзя", self.app.press(32, f"hrq:{p['id']}")[0].text)   # необратимо — «Точно?»
        out = self.app.press(32, f"hrq:{p['id']}:ok")
        f = next(r for r in out if r.files)
        self.assertTrue(f.files[0][0].endswith(".docx"))
        doc = zipfile.ZipFile(io.BytesIO(f.files[0][1])).read("word/document.xml").decode()
        self.assertIn("Тестов Тест Тестович", doc)
        es = self.app.ctx(32).db.employees()
        self.assertEqual(len(es), before + 1)
        new = next(e for e in es if e["fio"] == "Тестов Тест Тестович")
        self.assertEqual((new["position"], new["salary"]), ("storekeeper", 40000))

    def test_only_responsible_closes_item(self):
        ctx = self.pos(33, "storekeeper")
        self.assertIn("Кадры", self.app.press(33, "hr")[0].text)       # кладовщику кадры не положены
        self.role(34, "accountant")
        from modules.hr import procs
        fire = next(p for p in procs(self.app.ctx(34).db) if p["kind"] == "fire")
        i_acc = next(i for i, it in enumerate(fire["items"]) if it[1] == "accountant")
        i_hr = next(i for i, it in enumerate(fire["items"]) if it[1] == "hr" and not it[2])
        w = self.app.press(34, f"hrc:{fire['id']}")[0]
        tos = [b.to for b in w.all_buttons()]
        self.assertNotIn(f"hrc:{fire['id']}:t:{i_hr}", tos)                         # пункт кадров бухгалтеру не дают
        self.app.press(34, f"hrc:{fire['id']}:t:{i_hr}")
        self.app.press(34, f"hrc:{fire['id']}:t:{i_acc}")
        self.app.press(34, f"hrc:{fire['id']}:ok")
        fire = next(p for p in procs(self.app.ctx(34).db) if p["kind"] == "fire")
        self.assertTrue(fire["items"][i_acc][2])
        self.assertFalse(fire["items"][i_hr][2])


class Maximum(Base):
    def test_custom_requests_limit_and_admin_notified(self):
        self.role(41, "owner")
        out = []
        for n in range(2):                                   # в демо одна заявка этого месяца уже есть
            self.app.press(41, "cuq")
            out += self.app.text(41, f"Новая функция номер {n} для склада")
        self.assertTrue(any(r.to == 1 for r in out))
        self.assertIn("уже 3", self.app.press(41, "cuq")[0].text)

    def test_locks_below_editions(self):
        self.role(42, "owner", ed="control")
        r = self.app.press(42, "custom")
        self.assertIn("Максимум", r[0].view.subtitle)
        self.role(43, "owner", ed="business")
        self.assertIn("Кадры и офис", self.app.press(43, "hr")[0].view.subtitle)


class Roles(Base):
    def test_role_picker_lists_positions(self):
        self.role(51, "owner")
        tos = {b.to for b in self.app.press(51, "sw:role")[0].all_buttons()}
        for p in ("buy_head", "buyer", "wh_head", "storekeeper", "log_head", "dispatcher", "driver", "hr"):
            self.assertIn(f"sw:pos:{p}", tos)

    def test_bottom_menu_by_department(self):
        cases = {"driver": "dm", "dispatcher": "lgk", "buyer": "sol", "buy_head": "sow", "storekeeper": "wrc", "hr": "hr",
                 "log_head": "fu"}
        for i, (pos, to) in enumerate(cases.items()):
            ctx = self.pos(60 + i, pos)
            self.assertIn(to, {t for row in screens.bottom_rows(ctx) for _, t in row}, pos)
            self.assertTrue(self.app.press(60 + i, "help")[0].view)


if __name__ == "__main__":
    unittest.main()
