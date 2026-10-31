"""Конструктор и цены: пакеты по отделам, цена за пакет и за человека, подключение и в месяц, скидки;
«🧩 Собрать» в демо, заявка, КП в PDF; «💳 Тариф» у директора клиента; «💰 Цены» правит только администратор."""
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.access import editions as E
from core.access import pricing as P
from core.app import App
from core.ui import screens

TODAY = date(2026, 10, 29)


class Quote(unittest.TestCase):
    """Цены за функции (Адам 31.10): отдел — папка, платим за функции + люди; скидка за 4 отдела; дополнительное — без скидки."""
    def test_small_firm(self):
        fs = E.preset_functions("start") | {"stock"}
        q = P.quote(fs, {"sales": 5, "stock": 3}, P.DEFAULT)
        self.assertEqual([round(a) for _, a, _ in q["lines"]], [3000, 3000 + 2000, 3000, 8 * 300])   # все, продажи, склад, люди
        self.assertEqual(q["monthly"], 13400)
        self.assertEqual(q["setup"], 20000)                                     # месяц × 1,5 меньше минимума
        self.assertEqual(q["discount"], 0)
        self.assertEqual(q["people"], 8)

    def test_presets_and_max(self):
        want = {"start": 9800, "business": 19500, "control": 31000, "company": 45000, "max": 106300}
        for code, monthly in want.items():
            q = P.quote(E.preset_functions(code), E.PRESETS[code]["people"], P.DEFAULT)
            self.assertEqual(q["monthly"], monthly, code)
        q = P.quote(E.preset_functions("max"), E.PRESETS["max"]["people"], P.DEFAULT, year=True)
        self.assertTrue(q["all_depts"])
        self.assertEqual(q["monthly"], P.rnd((34000 + 127 * 300) * 0.85 + 45000))   # дополнительное — без скидки
        self.assertEqual(q["setup"], 159000)
        self.assertEqual(q["monthly_year"], P.rnd(q["monthly"] * 0.9))

    def test_empty_old_packages_and_only_extra(self):
        self.assertTrue(P.quote(set(), {}, P.DEFAULT)["empty"])
        self.assertEqual(P.quote({"sales": "start"}, {"sales": 2}, P.DEFAULT)["monthly"], 8000 + 600)   # старый вид — тоже понимает
        q = P.quote({"branding"}, {}, P.DEFAULT)
        self.assertEqual((q["monthly"], q["setup"]), (10000, 20000))

    def test_round_numbers_by_default(self):
        for code, value in P.DEFAULT.items():
            if code != "setup_mult":
                self.assertEqual(value % (1 if code in ("disc_all", "disc_year", "trial_days") else 100), 0, code)
        self.assertEqual({c for c in P.DEFAULT if c.startswith("f_")}, {f"f_{f}" for f in E.FUNCS})   # цена у каждой функции


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("demo", today=TODAY, admins=[1], data_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def owner(self, uid):
        self.app.press(uid, "sw:ed:max")
        self.app.press(uid, "sw:role:owner")
        return self.app.ctx(uid)

    @staticmethod
    def tiles(r):
        return dict((a, b) for a, b, _ in next(b for k, b in r.view.blocks if k == "tiles"))


def pick(app, uid, *acts):
    """Окно «☑️ Выбрать пакеты»: отметить и нажать «ОК»."""
    app.press(uid, "swe")
    for a in acts:
        app.press(uid, f"swe:{a}")
    return app.press(uid, "swe:ok")


class Builder(Base):
    def test_window_ok_and_cancel(self):
        self.owner(16)
        before = dict(self.app.ctx(16).cfg.packages)
        self.app.press(16, "sw:ed")
        w = self.app.press(16, "swe")[0]
        self.assertEqual([b.label for b in w.buttons[-2]], ["✅ ОК", "✖️ Отмена"])
        w2 = self.app.press(16, "swe:t:stock")[0]                  # в окне сумма уже пересчитана
        self.assertNotEqual(self.tiles(w)["Дальше, в месяц"], self.tiles(w2)["Дальше, в месяц"])
        self.assertEqual(self.app.ctx(16).cfg.packages, before)     # до «ОК» ничего не поменялось
        self.app.press(16, "swe:no")
        self.assertEqual(self.app.ctx(16).cfg.packages, before)     # «Отмена» — как было
        back = w.buttons[-1][0].to
        self.assertTrue(back.startswith("bk:"))
        self.app.press(16, "swe")
        self.app.press(16, "swe:t:stock")
        self.assertEqual(self.app.press(16, self.app.press(16, "swe:t:office")[0].buttons[-1][0].to)[0].view.title, "🧩 Собрать бота")   # стрелка — назад, без изменений
        self.assertEqual(self.app.ctx(16).cfg.packages, before)

    def test_toggle_functions_and_price(self):
        self.owner(11)
        r = self.app.press(11, "sw:ed")[0]
        self.assertEqual(r.view.title, "🧩 Собрать бота")
        t = self.tiles(r)
        r2 = pick(self.app, 11, "t:warehouse")[0]            # сняли одну функцию — склад есть, приёмки нет
        ctx = self.app.ctx(11)
        self.assertFalse(ctx.cfg.has_module("warehouse"))
        self.assertTrue(ctx.cfg.has_module("stock"))
        self.assertLess(int(self.tiles(r2)["Дальше, в месяц"].replace(" ", "").rstrip("₽")),
                        int(t["Дальше, в месяц"].replace(" ", "").rstrip("₽")))
        pick(self.app, 11, "b:start", "t:stock")                # набор «Старт» + закупки
        ctx = self.app.ctx(11)
        self.assertFalse(ctx.cfg.has_module("clients"))
        self.assertTrue(ctx.cfg.has_module("stock") and ctx.cfg.dept_on("purchasing") if ctx.cfg.dept("purchasing") else True)
        self.assertIn("«Старт + 1 функц.»", self.app.press(11, "sw:ed")[0].view.subtitle)
        self.assertEqual(self.app.ctx(11).session["counts"]["sales"], 6)    # набор — типовые люди
        r3 = self.app.press(11, "sw:ed:company")[0]
        self.assertIn("«Компания»", r3.view.subtitle)

    def test_people_counts_and_year(self):
        self.owner(12)
        self.app.press(12, "sw:pn")
        r = self.app.text(12, "8 3 4 2")[0]
        self.assertIn("людей в фирме 17", r.view.subtitle)
        q = P.quote(self.app.ctx(12).cfg.packages, {"sales": 8, "stock": 3, "logistics": 4, "office": 2}, P.DEFAULT)
        self.assertEqual(self.tiles(r)["Дальше, в месяц"], f"{q['monthly']:,} ₽".replace(",", " "))
        r = pick(self.app, 12, "y")[0]
        self.assertIn("За год: в месяц", self.tiles(r))
        bad = self.app.text(12, "много")                     # не числа — переспросит
        self.app.press(12, "sw:pn")
        self.assertTrue(self.app.text(12, "x y")[0].ask)
        self.app.press(12, "sw:pn:reset")
        self.assertNotIn("counts", self.app.ctx(12).session)
        self.assertTrue(bad)

    def test_pdf_and_request(self):
        self.owner(13)
        f = self.app.press(13, "sw:pdf")[0]
        self.assertTrue(f.files and f.files[0][0].endswith(".pdf") and f.files[0][1].startswith(b"%PDF"))
        out = self.app.press(13, "sw:req")
        self.assertTrue(any(r.to == 1 and "Заявка" in r.text for r in out))
        self.assertIn("Заявка отправлена", out[-1].text)
        self.assertEqual(len(self.app.office().get("tariff_req")), 1)

    def test_lock_shows_package_price(self):
        self.app.press(14, "sw:ed:start")
        self.app.press(14, "sw:role:owner")
        r = self.app.press(14, "lg")[0]
        lines = next(b for k, b in r.view.blocks if k == "lines")[1]
        self.assertTrue(any("3 000 ₽ в месяц" in x and "настройки внутри бесплатно" in x for x in lines))
        self.assertIn("sw:ed", [b.to for b in r.all_buttons()])

    def test_department_off_for_its_people(self):
        self.app.press(15, "sw:ed:control")                   # только продажи
        self.app.press(15, "sw:pos:storekeeper")
        home = self.app.start(15)
        self.assertTrue(any("не подключён" in str(b) for r in home if r.view for b in r.view.blocks))
        self.assertEqual([t for row in screens.bottom_rows(self.app.ctx(15)) for _, t in row if not t.startswith("sw")],
                         ["home", "fb"])
        pick(self.app, 15, "t:stock")                          # подключили склад — экраны вернулись
        self.assertIn("wrc", [t for row in screens.bottom_rows(self.app.ctx(15)) for _, t in row])


class Prices(Base):
    def test_admin_edits_and_resets(self):
        self.app.press(1, "sw:role:admin")
        r = self.app.press(1, "pr")[0]
        self.assertEqual(r.view.subtitle, "стандартные")
        self.app.press(1, "pre:f_stock")
        self.app.text(1, "6 000")
        self.assertEqual(P.prices(self.app.office())["f_stock"], 6000)
        self.assertEqual(P.changed(self.app.office()), {"f_stock"})
        self.app.press(1, "pre:disc_all")
        self.assertTrue(self.app.text(1, "95")[0].ask)        # скидка 95% — не бывает
        self.owner(21)
        pick(self.app, 21, "t:stock")                         # сняли закупки — в замке уже новая цена
        lines = next(b for k, b in self.app.press(21, "sk")[0].view.blocks if k == "lines")[1]
        self.assertTrue(any("6 000 ₽ в месяц" in x for x in lines))
        self.app.press(1, "prx")
        self.assertEqual(P.changed(self.app.office()), set())

    def test_guest_as_admin_cannot_edit(self):
        self.app.press(31, "sw:role:admin")                   # гость играет роль админа — цены только смотрит
        r = self.app.press(31, "pr")[0]
        self.assertNotIn("pre:f_stock", [b.to for b in r.all_buttons()])
        self.app.press(31, "pre:f_stock")
        out = self.app.text(31, "1")
        self.assertEqual(P.prices(self.app.office())["f_stock"], 3000)
        self.assertTrue(out)


class ClientTariff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = App("_template", today=TODAY, admins=[1], data_dir=self.tmp)
        db = self.app.db()
        self.boss = db.add_employee("Хозяин Фирмы", "office", position="director", role="owner", tg_id=700)
        db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", tg_id=701)
        db.add_employee("Петров Иван", "sales", team="north", position="rep", role="rep")
        self.store = db.add_employee("Складов Семён", "warehouse", position="storekeeper", role="staff", tg_id=702)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_owner_sees_tariff_and_requests_package(self):
        cfg = self.app.cfg
        self.assertEqual(cfg.edition, "business")
        r = self.app.press(700, "tf")[0]
        self.assertEqual(r.view.title, "💳 Тариф")
        tiles = dict((a, b) for a, b, _ in next(b for k, b in r.view.blocks if k == "tiles"))
        self.assertEqual(tiles["Дальше, в месяц"], "15 600 ₽")          # функции «Бизнеса» 15 000 + 2 торговых × 300
        self.assertIn("tfr:stock", [b.to for b in r.all_buttons()])
        out = self.app.press(700, "tfr:stock")
        self.assertTrue(any(x.to == 1 and "Закупки" in x.text for x in out))
        self.assertIn("Станет", out[-1].text)
        self.assertTrue(self.app.office().get("tariff_req"))

    def test_staff_of_unbought_department_is_outside(self):
        home = self.app.start(702)[0]
        self.assertTrue(any("не подключён" in str(b) for b in home.view.blocks))
        self.assertTrue(self.app.start(701)[0].view)                      # торговый работает как обычно

    def test_rep_cannot_open_tariff_or_prices(self):
        self.assertIn("директор", self.app.press(701, "tf")[0].text)
        self.assertIn("администратор", self.app.press(701, "pr")[0].text)


if __name__ == "__main__":
    unittest.main()
