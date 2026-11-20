"""🧾 Продажа в магазине (Адам 20.11, кассы нет): корзина, скидка по праву, рассрочка и доставка, списание со склада,
продажа попадает в план продавца, директору — маржа 🔒 и «ниже себестоимости», продавец себестоимости не видит."""
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core.app import App
from core.ui.web import reply_json

TODAY = date(2026, 11, 20)
ADMIN = 1


def blocks(out):
    for r in out:
        d = reply_json(r)
        if d.get("view"):
            return [(b["t"], b.get("title", ""), b["d"]) for b in d["view"]["blocks"] if b["k"] == "ui"]
    return []


def text(out):
    return " ".join(str(reply_json(r)) for r in out)


class Sale(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.app.press(ADMIN, "sw:ind:furniture")
        self.app.press(ADMIN, "sw:seg:mix")
        self.app.press(ADMIN, "sw:pos:seller")
        self.db = lambda: self.app.ctx(ADMIN).db

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def good(self, here=True):
        """Товар, свободный в салоне продавца (сам забирает) или на складе (доставка)."""
        team = self.app.ctx(ADMIN).me["team"]
        loc = team if here else "wh"
        return self.db().one("SELECT g.* FROM goods g JOIN stock_loc s ON s.code = g.code AND s.loc = ? "
                             "WHERE s.qty - s.smp >= 3 ORDER BY g.code LIMIT 1", loc)

    def test_sale_writes_off_and_counts_in_plan(self):
        g = self.good()
        me = self.app.ctx(ADMIN).me
        before = self.app.ctx(ADMIN).kpi().emp(me["id"])["sales"].fact
        self.app.press(ADMIN, f"sale:a:{g['code']}")
        self.app.press(ADMIN, f"sale:a:{g['code']}")
        out = self.app.press(ADMIN, "sale:ok", form={"disc": "5", "pay": "card", "take": "self"})
        self.assertIn("Продано", text(out))
        self.assertNotIn("Маржа", text(out))                       # продавец маржу не видит
        self.assertEqual(self.db().one("SELECT qty FROM goods WHERE code = ?", g["code"])["qty"], g["qty"] - 2)
        team = self.app.ctx(ADMIN).me["team"]
        self.assertTrue(self.db().one("SELECT 1 AS x FROM stock_loc WHERE code = ? AND loc = ?", g["code"], team))
        after = self.app.ctx(ADMIN).kpi().emp(me["id"])["sales"].fact
        self.assertAlmostEqual(after - before, g["price"] * 2 * 0.95, delta=1)

    def test_discount_limit(self):
        self.app.press(ADMIN, f"sale:a:{self.good()['code']}")
        out = self.app.press(ADMIN, "sale:ok", form={"disc": "15", "pay": "cash", "take": "self"})
        self.assertIn("Скидка — от 0 до 5%", text(out))

    def test_installment_and_delivery(self):
        self.app.press(ADMIN, f"sale:a:{self.good(here=False)['code']}")
        out = self.app.press(ADMIN, "sale:ok", form={"disc": "0", "pay": "inst", "take": "deliv", "buyer": ""})
        self.assertIn("напишите покупателя", text(out))
        out = self.app.press(ADMIN, "sale:ok", form={"disc": "0", "pay": "inst", "months": "10", "first": "25", "take": "deliv",
                                                     "addr": "ул. Мира 14, кв. 8", "dday": (TODAY + timedelta(days=2)).isoformat(),
                                                     "buyer": "Алиев Хасан"})
        s = self.db().one("SELECT * FROM shop_sales ORDER BY id DESC LIMIT 1")
        self.assertTrue(s["order_id"] and s["inst_id"])
        o = next(x for x in self.db().get("rt_orders")["items"] if x["id"] == s["order_id"])
        self.assertEqual(o["pay"], "inst")
        self.assertTrue(any(x["id"] == s["inst_id"] for x in self.db().get("rt_inst")))

    def test_owner_sees_margin_and_below_cost(self):
        self.app.press(ADMIN, "sw:role:owner")
        out = self.app.press(ADMIN, "home")
        t = {ti for _, ti, _ in blocks(out)}
        self.assertIn("Продано сегодня", t)
        self.assertTrue(any("ниже себестоимости" in x for x in t))
        self.assertNotIn("Касса", text(out))


if __name__ == "__main__":
    unittest.main()
