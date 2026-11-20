"""📄 Правила и оплата (Адам 01.11, оплата в число запуска — 20.11): даты, счета, окно «добавить / убрать» с ценой, одобрение админом."""
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

    def test_dates_and_bills_on_launch_day(self):
        """Адам 20.11: бесплатных дней нет; в день запуска — подключение и месяц, дальше каждый месяц в число запуска."""
        r = self.app.press(700, "bl")[0]
        self.assertIn("Ещё не договорились", texts(r))
        self.app.press(1, "blm:d")
        self.app.text(1, "16.11.2026 20.11.2026")
        r = self.at(date(2026, 11, 18)).press(700, "bl")[0]
        self.assertEqual(tiles(r)["Оплата 20 ноября"], "29 100 ₽")                # 12 500 подключения + 16 600 месяц
        self.assertNotIn("Бесплатно", texts(r))
        app = self.at(date(2026, 11, 20))
        self.assertIn("оплатить сегодня, 20 ноября", texts(app.press(700, "bl")[0]))
        app.press(1, "blm:p")
        r = self.at(date(2026, 11, 25)).press(700, "bl")[0]
        self.assertEqual(tiles(r)["Оплачено до"], "19 декабря")
        self.assertEqual(tiles(r)["Оплата 20 декабря"], "16 600 ₽")
        app = self.at(date(2026, 12, 21))
        self.assertIn("не оплачен. С 25 декабря — только просмотр", texts(app.press(700, "bl")[0]))
        app.press(1, "blm:p")
        r = app.press(700, "bl")[0]
        self.assertEqual(tiles(r)["Оплачено до"], "19 января")
        self.assertIn("Оплата 20 января", tiles(r))

    def test_launch_on_31st(self):
        from core import billing as B
        launch = date(2026, 1, 31)
        self.assertEqual(B.pday(launch, "2026-02"), date(2026, 2, 28))
        self.assertEqual(B.pday(launch, "2026-03"), date(2026, 3, 31))
        self.assertEqual(B.period(launch, "2026-02"), (date(2026, 2, 28), date(2026, 3, 30)))
        self.assertEqual(B.period_of(launch, date(2026, 3, 15)), "2026-02")

    def test_partial_payment_shows_rest(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")
        app = self.at(date(2026, 11, 5))
        app.press(1, "blm:p")                                        # 5 октября: подключение + месяц — весь
        app.press(1, "blm:s")
        app.text(1, "10 000")                                         # 5 ноября — только часть
        r = app.press(700, "bl")[0]
        self.assertIn("оплачено 10 000 ₽ из 16 600 ₽, осталось 6 600 ₽", texts(r))
        app.press(1, "blm:s")
        app.text(1, "6 600")
        self.assertIn("Оплачено до 4 декабря", texts(app.press(700, "bl")[0]))
        app.press(1, "blm:u")                                         # сняли последнюю отметку — снова остаток
        self.assertIn("осталось 6 600", texts(app.press(700, "bl")[0]))

    def test_add_function_needs_approval_and_bills_days_to_next_payment(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")
        app = self.at(date(2026, 11, 16))
        app.press(1, "blm:p")                                        # 5 октября и 5 ноября оплачены
        app.press(1, "blm:p")
        app.press(700, "blc")
        r = app.press(700, "blc:t:stock")[0]
        t = texts(r)
        self.assertIn("С 5 декабря каждый месяц + 3 000 ₽", t)
        self.assertIn("Сейчас, за дни до оплаты 5 декабря (19 дн.) + 1 900 ₽", t)        # 3 000 × 19 / 30
        self.assertEqual(tiles(r)["Оплата 5 декабря, итого"], "21 500 ₽")                  # 19 600 + 1 900
        out = app.press(700, "blc:ok")
        self.assertTrue(any(x.to == 1 and "Одобрить" in str([b.label for b in x.all_buttons()]) for x in out))
        self.assertNotIn("stock", app.ctx(700).cfg.functions or set())                      # до одобрения — не включено
        out = app.press(1, "bly:1")
        self.assertTrue(any(x.to == 700 and "Включено" in x.text and "С 5 декабря" in x.text for x in out))
        self.assertIn("stock", app.ctx(700).cfg.modules)
        r = app.press(700, "bl")[0]
        self.assertEqual(tiles(r)["Оплата 5 декабря"], "21 500 ₽")
        self.assertIn("доплата за дни до оплаты", texts(r))

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
        self.app.press(1, "blm:p")
        self.app.press(1, "blm:p")
        r = self.app.press(700, "pad")[0]
        self.assertIn("По тарифу 2 чел.", r.text)

    def test_rep_and_pdf(self):
        self.assertIn("директор", self.app.press(701, "bl")[0].text)
        self.assertIn("директор", self.app.press(701, "blc")[0].text)
        self.assertTrue(self.app.press(700, "bld")[0].files)
        self.assertTrue(self.app.press(700, "blo")[0].files)
        self.assertEqual(self.app.press(700, "bly:1")[0].text, "Заявку одобряем мы — она уже у нас.")


    def test_unpaid_steps_and_hold(self):
        self.app.press(1, "blm:d")
        self.app.text(1, "01.10.2026 05.10.2026")                    # первая оплата — в день запуска, 5 октября
        app = self.at(date(2026, 10, 6))
        self.assertIsNone(app.press(701, "home")[0].text or None)       # напоминание — всё работает
        app = self.at(date(2026, 10, 10))
        self.assertIn("только просмотр", app.text(701, "что-то")[0].text)
        self.assertTrue(app.press(701, "home")[0].view)                 # смотреть можно
        app = self.at(date(2026, 10, 15))
        self.assertIn("недоступно", app.press(701, "home")[0].text)
        self.assertTrue(app.press(700, "bl")[0].view)                   # директор видит «Правила и оплата»
        self.assertTrue(app.press(1, "home")[0].view)                   # администратор платформы — всегда
        app.press(1, "blm:h")                                           # ⏳ отсрочка на 7 дней
        self.assertTrue(app.press(701, "home")[0].view)
        self.assertIn("отсрочка до 22 октября", texts(app.press(700, "bl")[0]))


class BillingDemo(unittest.TestCase):
    def test_demo_flow(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            app = App("demo", today=date(2026, 11, 1), admins=[1], data_dir=tmp)
            app.press(5, "sw:role:owner")
            r = app.press(5, "bl")[0]
            self.assertEqual(tiles(r)["Оплачено до"], "21 ноября")          # запуск 22 октября, первый месяц оплачен
            app.press(5, "blc")
            app.press(5, "blc:t:processes")
            r = app.press(5, "blc:ok")[-1]
            self.assertIn("bly:1", [b.to for b in r.all_buttons()])
            app.press(5, "bly:1")
            self.assertIn("processes", app.ctx(5).cfg.functions)
            for _ in range(3):
                r = app.press(5, "blt:7")[0]                         # листаем до 22 ноября — день оплаты
            self.assertIn("оплатить сегодня, 22 ноября", texts(r))
            r = app.press(5, "blt:7")[0]                             # 29 ноября — не оплачен
            self.assertIn("не оплачен", texts(r))
            self.assertIn("Пример: даты и суммы выдуманы", texts(r))
            self.assertIn("Оплачено до", tiles(app.press(5, "blt:0")[0]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
