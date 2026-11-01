"""📄 Договор и оплата (Адам 01.11): даты, бесплатные дни, счета, окно «добавить / убрать» с ценой, одобрение админом."""
import logging
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.app import App

logging.disable(logging.CRITICAL)


def tiles(r):
    return dict((a, b) for a, b, _ in next(b for k, b in r.view.blocks if k == "tiles"))


def texts(r):
    out = []
    for _, b in r.view.blocks:
        out.append(str(b))
        for x in (b if isinstance(b, tuple) else ()):
            for row in (x if isinstance(x, list) else ()):
                out += [c.text for c in getattr(row, "cells", [])]
    return " ".join(out)


class BillingClient(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = self.at(date(2026, 11, 16))
        db = self.app.db()
        db.add_employee("Хозяин Фирмы", "office", position="director", role="owner", tg_id=700)
        db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", tg_id=701)
        db.add_employee("Петров Иван", "sales", team="north", position="rep", role="rep")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def at(self, day):
        return App("_template", today=day, admins=[1], data_dir=self.tmp)

    def test_dates_trial_and_bills(self):
        r = self.app.press(700, "bl")[0]
        self.assertIn("Договор ещё не подписан", texts(r))
        self.app.press(1, "blm:d")
        self.app.text(1, "16.11.2026 20.11.2026")
        app = self.at(date(2026, 11, 25))
        r = app.press(700, "bl")[0]
        self.assertEqual(tiles(r)["Бесплатно, дней"], "9")                       # бесплатно до 3 декабря
        self.assertIn("Счёт 4 декабря", tiles(r))
        m = tiles(r)["В месяц"]
        self.assertEqual(m, "15 600 ₽")
        self.assertEqual(tiles(r)["Счёт 4 декабря"], "14 100 ₽")              # 15 600 × 28 / 31
        self.assertIn("Бесплатно ещё 9", texts(app.press(700, "home")[0]))
        app = self.at(date(2026, 12, 11))
        self.assertIn("не оплачен", texts(app.press(700, "bl")[0]))                        # срок был 9 декабря
        app.press(1, "blm:p")
        r = app.press(700, "bl")[0]
        self.assertIn("Декабрь оплачен", texts(r))
        self.assertIn("Счёт 1 января", tiles(r))

    def test_partial_payment_shows_rest(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")
        app = self.at(date(2026, 11, 3))
        app.press(1, "blm:p")                                        # октябрь (19–31) — оплачен весь
        app.press(1, "blm:s")
        app.text(1, "10 000")                                         # ноябрь — только часть
        r = app.press(700, "bl")[0]
        self.assertIn("оплачено 10 000 ₽ из 15 600 ₽, осталось 5 600 ₽", texts(r))
        app.press(1, "blm:s")
        app.text(1, "5 600")
        self.assertIn("Ноябрь оплачен", texts(app.press(700, "bl")[0]))
        app.press(1, "blm:u")                                         # сняли последнюю отметку — снова остаток
        self.assertIn("осталось 5 600", texts(app.press(700, "bl")[0]))

    def test_add_function_needs_approval_and_bills_rest_of_month(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")
        app = self.at(date(2026, 11, 16))
        app.press(700, "blc")
        r = app.press(700, "blc:t:stock")[0]
        t = texts(r)
        self.assertIn("С декабря каждый месяц + 3 000 ₽", t)
        self.assertIn("Сейчас, за остаток ноября (15 дн.) + 1 500 ₽", t)
        self.assertEqual(tiles(r)["Счёт 1 декабря, итого"], "20 100 ₽")                   # 18 600 + 1 500
        out = app.press(700, "blc:ok")
        self.assertTrue(any(x.to == 1 and "Одобрить" in str([b.label for b in x.all_buttons()]) for x in out))
        self.assertNotIn("stock", app.ctx(700).cfg.functions or set())                      # до одобрения — не включено
        out = app.press(1, "bly:1")
        self.assertTrue(any(x.to == 700 and "Включено" in x.text for x in out))
        self.assertIn("stock", app.ctx(700).cfg.modules)
        r = app.press(700, "bl")[0]
        self.assertEqual(tiles(r)["Счёт 1 декабря"], "20 100 ₽")
        self.assertIn("доплата за ноябрь", texts(r))

    def test_reject_with_reason(self):
        self.app.press(700, "blc")
        self.app.press(700, "blc:p:5")
        self.app.press(700, "blc:ok")
        self.app.press(1, "bln:1")
        out = self.app.text(1, "Сначала оплатите ноябрь")
        self.assertTrue(any(x.to == 700 and "Сначала оплатите" in x.text for x in out))
        self.assertIn("❌", texts(self.app.press(700, "bl")[0]))

    def test_people_limit(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")
        r = self.app.press(700, "pad")[0]
        self.assertIn("По тарифу 2 чел.", r.text)

    def test_rep_and_pdf(self):
        self.assertIn("директор", self.app.press(701, "bl")[0].text)
        self.assertIn("директор", self.app.press(701, "blc")[0].text)
        self.assertTrue(self.app.press(700, "bld")[0].files)
        self.assertTrue(self.app.press(700, "blo")[0].files)
        self.assertEqual(self.app.press(700, "bly:1")[0].text, "Одобряет администратор Алапы.")


class BillingDemo(unittest.TestCase):
    def test_demo_flow(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            app = App("demo", today=date(2026, 11, 1), admins=[1], data_dir=tmp)
            app.press(5, "sw:role:owner")
            r = app.press(5, "bl")[0]
            self.assertEqual(tiles(r)["Бесплатно, дней"], "4")
            app.press(5, "blc")
            app.press(5, "blc:t:processes")
            r = app.press(5, "blc:ok")[-1]
            self.assertIn("bly:1", [b.to for b in r.all_buttons()])
            app.press(5, "bly:1")
            self.assertIn("processes", app.ctx(5).cfg.functions)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
