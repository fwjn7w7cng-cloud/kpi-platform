"""Свои магазины (Адам 20.11): сегмент опт / опт + розница / розница рядом с отраслью, отрасль «Мебель»,
директор фирмы и магазина, продавец, сборщик, заказы с доставкой и рассрочка. Опт без магазинов — как был."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App
from core.ui.web import reply_json

TODAY = date(2026, 11, 20)
ADMIN = 1


def view(out):
    for r in out:
        d = reply_json(r)
        if d.get("view"):
            return d["view"]
    return {}


def blocks(out):
    return {(b["t"], b.get("title", "")): b for b in view(out).get("blocks", []) if b["k"] == "ui"}


def titles(out):
    return {t for t, _ in blocks(out)} | {ti for _, ti in blocks(out)}


class Retail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)
        self.app.press(ADMIN, "sw:ind:furniture")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hero(self, out):
        return next(b["d"] for (t, _), b in blocks(out).items() if t == "hero")

    def test_opt_has_no_shops(self):
        out = self.app.press(ADMIN, "sw:role:owner")
        self.assertNotIn("Магазины", titles(out))
        self.assertIn("Продажи", self.hero(out)["label"])
        ctx = self.app.ctx(ADMIN)
        self.assertFalse(any(e["dept"] == "shops" for e in ctx.db.employees()))

    def test_mix_owner(self):
        self.app.press(ADMIN, "sw:seg:mix")
        out = self.app.press(ADMIN, "sw:role:owner")
        h = self.hero(out)
        self.assertIn("Выручка фирмы", h["label"])
        self.assertEqual([p[0] for p in h["pairs"]], ["Опт", "Розница", "Рассрочка"])
        b = blocks(out)
        shops = b[("rows", "Магазины")]["d"]["items"]
        self.assertEqual(len(shops), 3)
        self.assertTrue(all(x["chip"][1].startswith("rts:") for x in shops))
        self.assertTrue(view(self.app.press(ADMIN, shops[0]["chip"][1])))

    def test_retail_hides_wholesale(self):
        self.app.press(ADMIN, "sw:seg:retail")
        ctx = self.app.ctx(ADMIN)
        self.assertFalse(any(e["dept"] == "sales" for e in ctx.db.employees()))
        out = self.app.press(ADMIN, "sw:role:owner")
        self.assertIn("магазинов", self.hero(out)["label"])
        bar = ctx.app.ctx(ADMIN)
        from core.ui.orient import demo_bar
        d = demo_bar(bar)
        self.assertEqual(d["segment"], "Розница")
        self.assertNotIn("sw:role:rep", [r[1] for r in d["roles"]])
        self.assertIn("sw:pos:seller", [r[1] for r in d["roles"]])

    def test_seller_pay_and_orders(self):
        self.app.press(ADMIN, "sw:seg:mix")
        out = self.app.press(ADMIN, "sw:pos:seller")
        b = blocks(out)
        rows = [r[0] for r in b[("explain", "Из чего зарплата")]["d"]["items"]]
        self.assertIn("Оклад за смены", rows)
        self.assertIn("% от продаж", rows)
        orders = b[("rows", "Мои заказы с доставкой")]["d"]["items"]
        self.assertTrue(orders and orders[0]["steps"])

    def test_shop_head_and_assembler(self):
        self.app.press(ADMIN, "sw:seg:mix")
        out = self.app.press(ADMIN, "sw:pos:shop_head")
        t = titles(out)
        self.assertTrue({"Продавцы", "Смена сегодня"} <= t)
        out = self.app.press(ADMIN, "sw:pos:assembler")
        self.assertIn("Сборка", self.hero(out)["label"])
        out = self.app.press(ADMIN, "rto")
        self.assertIn("🔧 Сборщики", titles(out))

    def test_mix_rep_is_wholesale(self):
        self.app.press(ADMIN, "sw:seg:mix")
        self.app.press(ADMIN, "sw:role:rep")
        ctx = self.app.ctx(ADMIN)
        self.assertEqual(ctx.me["dept"], "sales")


if __name__ == "__main__":
    unittest.main()
