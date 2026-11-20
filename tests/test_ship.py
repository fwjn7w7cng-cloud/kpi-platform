"""🚚 Учёт в Алапе (Адам 20.11): доставка — резерв, не списание; водителя назначает менеджер; маршрутный лист — только из
погруженного; «не доставил» — товар обратно и снова в резерв; перемещение склад → салон; у опта (1С) этого нет."""
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core.app import App
from core.ui.web import reply_json

TODAY = date(2026, 11, 20)
ADMIN = 1


def text(out):
    return " ".join(str(reply_json(r)) for r in out)


class Ship(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.app.press(ADMIN, "sw:ind:furniture")
        self.app.press(ADMIN, "sw:seg:mix")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def db(self):
        return self.app.ctx(ADMIN).db

    def wh(self, code):
        return self.db().one("SELECT qty FROM stock_loc WHERE code = ? AND loc = 'wh'", code)["qty"]

    def sell_delivery(self, drv):
        self.app.press(ADMIN, "sw:pos:seller")
        g = self.db().one("SELECT g.* FROM goods g JOIN stock_loc s ON s.code = g.code AND s.loc = 'wh' WHERE s.qty >= 10 ORDER BY g.code LIMIT 1")
        self.app.press(ADMIN, f"sale:a:{g['code']}")
        self.app.press(ADMIN, "sale:ok", form={"disc": "0", "pay": "card", "take": "deliv", "drv": str(drv), "addr": "ул. Мира 1, кв. 2",
                                               "dday": TODAY.isoformat(), "buyer": "Алиев Хасан"})
        s = self.db().one("SELECT * FROM shop_sales ORDER BY id DESC LIMIT 1")
        return g, s["order_id"]

    def driver(self):
        return next(e for e in self.db().employees() if e["position"] == "driver")

    def test_reserve_then_load_then_sheet(self):
        d = self.driver()
        from modules import ship as SH
        r0 = dict(SH.reserved(self.db()))
        g, oid = self.sell_delivery(d["id"])
        before = self.wh(g["code"])
        r1 = SH.reserved(self.db()).get((g["code"], "wh"), 0)
        self.assertEqual(r1 - r0.get((g["code"], "wh"), 0), 1)                         # продано — в резерве, не списано
        self.app.press(ADMIN, "sw:pos:storekeeper")
        self.app.press(ADMIN, f"ld:t:{d['id']}:{oid}")                                 # ✓ погружено
        out = self.app.press(ADMIN, f"ld:go:{d['id']}")
        self.assertIn("Маршрутный лист", text(out))
        self.assertEqual(self.wh(g["code"]), before - 1)                               # списано при погрузке
        sh = self.db().one("SELECT * FROM route_sheets WHERE driver = ? ORDER BY id DESC LIMIT 1", d["id"])
        self.assertIn(oid, __import__("json").loads(sh["orders"]))
        self.assertTrue(self.db().one("SELECT 1 AS x FROM trip_stops WHERE batch = 0 AND client = ?", f"Z{oid}"))
        self.app.press(ADMIN, f"sw:as:{d['id']}")
        self.app.press(ADMIN, f"rs:{sh['id']}:no:{oid}")                                # не доставил — обратно и в резерв
        self.assertEqual(self.wh(g["code"]), before)
        self.assertEqual(SH.reserved(self.db()).get((g["code"], "wh"), 0), r1)

    def test_not_loaded_stays(self):
        d = self.driver()
        g, oid = self.sell_delivery(d["id"])
        self.app.press(ADMIN, "sw:pos:storekeeper")
        self.app.press(ADMIN, f"ld:t:{d['id']}:{oid}")
        self.app.press(ADMIN, f"ld:t:{d['id']}:{oid}")                                 # ✕ не погружен
        self.app.press(ADMIN, f"ld:go:{d['id']}")
        from modules import ship as SH
        o = SH._order(self.db(), oid)
        self.assertEqual(o["load"], "no")
        self.assertFalse(o.get("sheet"))

    def test_move_to_shop(self):
        self.app.press(ADMIN, "sw:pos:shop_head")
        ctx = self.app.ctx(ADMIN)
        g = self.db().one("SELECT g.* FROM goods g JOIN stock_loc s ON s.code = g.code AND s.loc = 'wh' WHERE s.qty >= 10 ORDER BY g.code LIMIT 1")
        self.app.press(ADMIN, "mvs", form={"lines": f"{g['code']}; 2", "smp": "1"})
        m = self.db().one("SELECT * FROM moves ORDER BY id DESC LIMIT 1")
        self.app.press(ADMIN, "sw:pos:storekeeper")
        before = self.wh(g["code"])
        self.app.press(ADMIN, f"mvo:{m['id']}:go")
        self.assertEqual(self.wh(g["code"]), before - 2)
        self.app.press(ADMIN, "sw:pos:shop_head")
        self.app.press(ADMIN, f"mvy:{m['id']}", form={"l0": "2"})
        r = self.db().one("SELECT * FROM stock_loc WHERE code = ? AND loc = ?", g["code"], ctx.me["team"])
        self.assertGreaterEqual(r["smp"], 2)

    def test_opt_has_no_loading(self):
        self.app.press(ADMIN, "sw:seg:opt")
        self.app.press(ADMIN, "sw:pos:storekeeper")
        self.assertNotIn("Погрузка сегодня", text(self.app.press(ADMIN, "home")))


if __name__ == "__main__":
    unittest.main()
