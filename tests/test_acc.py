"""📦 Учёт в Алапе и 🔒 закрытые цифры (Адам 20.11): владелец видит себестоимость, кладовщик и продавец — нет,
приход: закупки с ценами → склад по количеству → себестоимость по средней; поддержка открывает только с причиной и
записью в журнал; владелец включает / выключает доступ."""
import shutil
import tempfile
import unittest
from datetime import date
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


class Acc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[ADMIN], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def heads(self, out):
        return next(d["head"] for t, _, d in blocks(out) if t == "team")

    def test_owner_sees_cost_storekeeper_not(self):
        self.app.press(ADMIN, "sw:role:owner")
        self.assertIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        self.app.press(ADMIN, "sw:pos:storekeeper")
        self.assertNotIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        out = self.app.press(ADMIN, "rco:418")
        self.assertNotIn("Цена 🔒", self.heads(out))
        self.assertNotIn("49 320", text(out))

    def test_receipt_flow_updates_cost(self):
        self.app.press(ADMIN, "sw:pos:buyer")                    # первому менеджеру закупок владелец включил доступ
        ctx = self.app.ctx(ADMIN)
        g = ctx.db.one("SELECT * FROM goods ORDER BY code LIMIT 1")
        out = self.app.press(ADMIN, "rcs", form={"supplier": g["supplier"], "no": "77", "day": TODAY.isoformat(), "pay": "debt",
                                                 "lines": f"{g['code']}; {int(g['qty'])}; {g['cost'] * 2}"})
        rid = int(next(d for t, _, d in blocks(out) if t == "hero")["id"][3:])
        self.app.press(ADMIN, f"rco:{rid}:send")
        self.app.press(ADMIN, "sw:pos:storekeeper")
        self.app.press(ADMIN, f"rcy:{rid}", form={"l0": str(int(g["qty"]))})
        g2 = self.app.ctx(ADMIN).db.one("SELECT * FROM goods WHERE code = ?", g["code"])
        self.assertAlmostEqual(g2["qty"], g["qty"] * 2)
        self.assertAlmostEqual(g2["cost"], g["cost"] * 1.5, delta=0.02)        # средняя: половина по старой, половина вдвое дороже

    def test_bad_lines(self):
        self.app.press(ADMIN, "sw:role:owner")
        out = self.app.press(ADMIN, "rcs", form={"supplier": "нет такого", "day": TODAY.isoformat(), "lines": "абракадабра; 1; 2"})
        self.assertIn("не найден", text(out))

    def test_support_needs_reason_and_logs(self):
        self.app.press(ADMIN, "sw:role:admin")
        self.assertNotIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        self.app.press(ADMIN, "seco", form={"why": "хм"})
        self.assertNotIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        self.app.press(ADMIN, "seco", form={"why": "настраиваем приход по просьбе"})
        self.assertIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        db = self.app.ctx(ADMIN).db
        r = db.one("SELECT * FROM secret_log WHERE support = 1 AND what LIKE 'открыла%' ORDER BY id DESC LIMIT 1")
        self.assertEqual(r["why"], "настраиваем приход по просьбе")

    def test_owner_turns_support_off(self):
        self.app.press(ADMIN, "sw:role:owner")
        self.app.press(ADMIN, "sec:t:sup")
        self.app.press(ADMIN, "sw:role:admin")
        self.app.press(ADMIN, "seco", form={"why": "настраиваем приход по просьбе"})
        self.assertNotIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        self.assertIn("выключил доступ поддержки", text(self.app.press(ADMIN, "gd")))

    def test_only_owner_sets_access(self):
        self.app.press(ADMIN, "sw:pos:buyer")
        self.assertIn("только владелец", text(self.app.press(ADMIN, "sec")))

    def test_access_toggle(self):
        self.app.press(ADMIN, "sw:role:owner")
        ctx = self.app.ctx(ADMIN)
        sk = next(e for e in ctx.db.employees() if e["position"] == "wh_head")
        self.app.press(ADMIN, f"sec:t:{sk['id']}")
        self.app.press(ADMIN, f"sw:as:{sk['id']}")
        self.assertIn("Себест. 🔒", self.heads(self.app.press(ADMIN, "gd")))
        self.assertTrue(self.app.ctx(ADMIN).db.one("SELECT 1 AS x FROM secret_log WHERE what = 'товары с себестоимостью' AND support = 0 "
                                                   "AND role = 'Заведующий складом'"))


if __name__ == "__main__":
    unittest.main()
