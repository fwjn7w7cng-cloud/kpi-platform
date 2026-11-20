"""💰 Деньги и клиенты (Адам 01.11): пульт управления в демо — все клиенты, оплаты, заявки, неделя, утренняя сводка."""
import logging
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from core import config as C
from core import control
from core.app import App

logging.disable(logging.CRITICAL)
DAY = date(2026, 11, 16)


def text_of(r):
    out = [r.text or ""]
    for _, b in (r.view.blocks if r.view else []):
        out.append(str(b))
        for x in (b if isinstance(b, tuple) else ()):
            for item in (x if isinstance(x, list) else ()):
                if isinstance(item, str):
                    out.append(item)
                    continue
                out += [c.text for c in getattr(item, "cells", [])] + [str(getattr(item, "sub", "") or "")]
                out.append(str(getattr(item, "title", "") or ""))
    return " ".join(out)


class Control(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        clients = self.tmp / "clients"
        clients.mkdir()
        shutil.copytree(C.CLIENTS / "demo", clients / "demo")
        shutil.copytree(C.CLIENTS / "_template", clients / "_template")
        shutil.copytree(C.CLIENTS / "_template", clients / "acme")
        cfg = clients / "acme" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("id: _template", "id: acme", 1), encoding="utf-8")
        self.patch = mock.patch.object(C, "CLIENTS", clients)
        self.patch.start()
        self.data = self.tmp / "data"
        self.demo = App("demo", today=DAY, admins=[1], data_dir=self.data)
        self.acme = App("acme", today=DAY, admins=[1], data_dir=self.data)
        db = self.acme.db()
        db.add_employee("Хозяин Фирмы", "office", position="director", role="owner", tg_id=700)
        db.add_employee("Иванов Пётр", "sales", team="north", position="rep", role="rep", tg_id=701)

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_admin(self):
        self.assertIn("администратора", self.demo.press(5, "mc")[0].text)

    def test_money_card_and_payments(self):
        r = self.demo.press(1, "mc")[0]
        self.assertIn("Сводка", r.view.title)
        self.demo.press(1, "mca:acme:d")
        r = self.demo.text(1, "01.10.2026 05.10.2026")[0]
        self.assertIn("Даты записаны", text_of(r))
        self.assertIn("mcc:acme", text_of(self.demo.press(1, "mc")[0]))             # «просрочка» — делом на сегодня
        self.assertIn("mcc:acme", text_of(self.demo.press(1, "mcl:late")[0]))
        out = control.digest(self.demo, DAY)
        self.assertTrue(out and out[0].to == 1 and "должен" in out[0].text)
        self.demo.press(1, "mca:acme:p")                                  # октябрь оплачен
        self.demo.press(1, "mca:acme:s")
        r = self.demo.text(1, "5 000")[0]                                 # ноябрь — частично
        self.assertIn("Отмечено 5 000", text_of(r))
        self.assertIn("осталось", text_of(self.acme.press(700, "bl")[0]))
        self.assertTrue(self.demo.press(1, "mcw")[0].view)

    def test_requests_approve_and_notify(self):
        self.acme.press(700, "blc")
        self.acme.press(700, "blc:t:stock")
        self.acme.press(700, "blc:ok")
        r = self.demo.press(1, "mcr")[0]
        self.assertIn("mcy:acme:1", [b.to for b in r.all_buttons()])
        self.demo.press(1, "mcy:acme:1")
        self.assertIn("stock", self.acme.ctx(700).cfg.modules)
        box = self.acme.office().get("inbox:700") or []
        self.assertTrue(any("Включено" in (m.get("text") or "") for m in box))

    def test_reject(self):
        self.acme.press(700, "blc")
        self.acme.press(700, "blc:p:5")
        self.acme.press(700, "blc:ok")
        self.demo.press(1, "mcn:acme:1")
        self.demo.text(1, "Сначала оплатите")
        box = self.acme.office().get("inbox:700") or []
        self.assertTrue(any("Сначала оплатите" in (m.get("text") or "") for m in box))

    def test_morning_once_a_day(self):
        self.demo.press(1, "mca:acme:d")
        self.demo.text(1, "01.10.2026 05.10.2026")
        got = []
        m = control.Morning(self.demo, got.extend)
        self.assertEqual(m.tick(datetime(2026, 11, 16, 8, 59)), [])
        self.assertTrue(m.tick(datetime(2026, 11, 16, 9, 0)))
        self.assertEqual(m.tick(datetime(2026, 11, 16, 9, 1)), [])


class Pult(Control):
    """🛠 Пульт (Адам 17.11): функции клиента переключателями, скидки со сроком, каталог, акция, новое приложение одной формой."""

    def blocks(self, out):
        r = next(x for x in out if x.view)
        return {(b[0], b[2]): b[1] for k, b in r.view.blocks if k == "ui"}

    def test_funcs_and_history(self):
        c = self.acme.ctx(700)
        from core import billing as B
        before = B.monthly(c)
        self.assertNotIn("stock", B.funcs(c))
        self.demo.press(1, "mcf:acme:warehouse")                          # склад тянет за собой закупки
        c = self.acme.ctx(700)
        self.assertTrue({"stock", "warehouse"} <= set(B.funcs(c)))
        self.assertIn("stock", c.cfg.modules)                               # у клиента сразу
        self.assertGreater(B.monthly(c), before)
        r = self.demo.press(1, "mcc:acme")[0]
        self.assertIn("Включил", text_of(r))                                # история
        self.demo.press(1, "mcf:acme:stock")
        self.assertNotIn("warehouse", B.funcs(self.acme.ctx(700)))
        trail = self.demo.ctx(1).session.get("trail") or []
        self.assertFalse(any(t.startswith("mcf") for t in trail))           # «Назад» не повторит переключение

    def test_card_people_pay_brand(self):
        """Адам 20.11: люди по отделам ± в карточке, цифры зарплаты из пульта, свой бренд бесплатно и с логотипом."""
        from core import billing as B
        from core.access import editions as E
        self.assertNotIn("branding", E.FUNCS)                              # свой бренд — бесплатно у всех
        self.assertIn("branding", self.acme.ctx(700).cfg.modules)
        self.assertNotIn("custom", E.FUNCS)
        base = B.monthly(self.acme.ctx(700))
        c = self.acme.ctx(700)
        rows = B.people_rows(c)                                             # отделы клиента, не разделы цены
        self.assertEqual([k for k, *_ in rows], [d.id for d in c.cfg.departments])
        dc, typical = B.dcounts(c)
        self.assertFalse(typical)                                           # в приложении уже есть торговый
        form = {k: "0" for k, *_ in rows}
        form.update({"sales": "10", "warehouse": "3", "office": "2"})
        self.demo.press(1, "mcps:acme", form=form)
        c = self.acme.ctx(700)
        self.assertEqual(B.people(c), 15)
        self.assertEqual(B.dcounts(c)[0]["warehouse"], 3)
        self.assertNotEqual(B.monthly(c), base)
        bad = self.demo.press(1, "mcps:acme", form={**form, "sales": "много"})
        self.assertIn("Не сохранил", text_of(bad[0]))
        # зарплата: ставка торгового 0,4% → 0,5%
        r = self.demo.press(1, "mce:acme:rep")[0]
        f = next(b[1] for k, b in r.view.blocks if k == "ui" and b[0] == "form")
        key = next(x["k"] for x in f["fields"] if x["l"].startswith("% от продаж"))
        bad = self.demo.press(1, "mcev:acme:rep", form={key: "абв"})
        self.assertIn("Не сохранил", text_of(bad[0]))
        self.demo.press(1, "mcev:acme:rep", form={key: "0,5"})
        c = self.acme.ctx(700)
        comp = next(x for x in c.cfg.raw["pay_schemes"]["rep"] if x["type"] == "sales_percent")
        self.assertAlmostEqual(comp["rate"], 0.005)
        self.demo.press(1, "mcex:acme:rep")
        comp = next(x for x in self.acme.ctx(700).cfg.raw["pay_schemes"]["rep"] if x["type"] == "sales_percent")
        self.assertAlmostEqual(comp["rate"], 0.004)
        # бренд: логотип → цвета сами, приложение клиента пересобирает страницу
        from PIL import Image
        import io
        im = Image.new("RGB", (300, 300), (10, 30, 80))
        for x in range(100, 200):
            for y in range(100, 140):
                im.putpixel((x, y), (230, 190, 90))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        self.demo.press(1, "mcb:acme")
        out = self.demo.upload(1, "logo.png", buf.getvalue())
        self.assertIn("Логотип загружен", text_of(out[0]))
        from core import brand as BR
        b = BR.get(self.acme.db())
        self.assertTrue(b["logo"] and b["c1"].startswith("#"))
        from core.transport.web import WebApi
        api = WebApi(self.acme)
        self.assertIn("brandcss", api.page)
        self.assertIn("logo.png", api.static)
        self.demo.press(1, "mcbs:acme", form={"name": "Барт", "c1": "#0B1F4D", "c2": "E0B04A"})
        api._fresh()
        self.assertIn("#0B1F4D", api.page)
        self.assertIn("Барт", api.page)
        self.demo.press(1, "mcbx:acme")
        api._fresh()
        self.assertNotIn("brandcss", api.page)

    def test_acc_functions(self):
        """Учёт в Алапе — отдельные функции со своей ценой; старому клиенту с учётом в Алапе включаются один раз."""
        from core.access import editions as E
        for f in E.ACC_FUNCS:
            self.assertIn(f, E.FUNCS)
        self.assertFalse(set(E.ACC_FUNCS) & E.functions_of(E.PRESETS["max"]["packages"]))   # в готовые наборы не входят
        self.assertEqual(E.toggle_function(set(), "opt_sale"), {"opt_sale", "goods"})
        self.assertEqual({E.FUNCS[f][0] for f in E.ACC_FUNCS}, {"sales", "stock"})           # в своих отделах (Адам 20.11)
        self.assertTrue(set(E.ACC_FUNCS) <= self.demo.ctx(1).cfg.modules)                   # в демо видны

    def test_discounts(self):
        from core import billing as B
        base = B.monthly(self.acme.ctx(700))
        self.demo.press(1, "mds:acme")
        r = self.demo.press(1, "mdv:acme", form={"pct": "10", "till": "31.12.2026", "why": "первый клиент"})
        self.assertIn("уже в его счёте", text_of(r[0]))
        c = self.acme.ctx(700)
        self.assertEqual(B.monthly(c), B.P.rnd(base * 0.9))
        st = B.state(c)
        self.assertEqual(st["discounts"][0]["till"], "2026-12-31")
        self.assertEqual(B.monthly(c, on=date(2027, 1, 1)), base)          # после срока — сама снята
        self.demo.press(1, "mdp:acme:t:forever")
        self.demo.press(1, "mdp:acme:w:people")
        self.demo.press(1, "mdv:acme", form={"pct": "50"})
        st = B.state(self.acme.ctx(700))
        self.assertIsNone(st["discounts"][1]["till"])
        self.assertEqual(st["discounts"][1]["what"], "people")
        bad = self.demo.press(1, "mdv:acme", form={"pct": "0"})
        self.assertIn("процент", text_of(bad[0]))
        self.demo.press(1, f"mdx:acme:{st['discounts'][0]['id']}")
        self.assertEqual(len(B.state(self.acme.ctx(700))["discounts"]), 1)

    def test_catalog_price_and_toggle(self):
        from core import billing as B
        r = self.demo.press(1, "mfn")
        self.assertTrue(self.blocks(r))
        self.demo.press(1, "mfp:kpi", form={"price": "4 000"})
        from core.access import pricing as P
        self.assertEqual(P.prices(self.acme.ctx(700).office)["f_kpi"], 4000)  # у клиента тоже
        self.demo.press(1, "mfc:hr:acme")
        self.assertIn("hr", B.funcs(self.acme.ctx(700)))

    def test_promo_and_access(self):
        self.demo.press(1, "mpm:on", form={"pct": "50", "till": "31.12.2026"})
        from core.ui.tariff import promo, quote_of
        g = self.demo.ctx(1)
        self.assertEqual(promo(g)["pct"], 50)
        q = quote_of(g)
        self.assertEqual(q["setup"], int(round(q["setup_full"] * 0.5 / 1000)) * 1000)
        self.demo.press(1, "mpm:off")
        self.assertIsNone(promo(self.demo.ctx(1)))
        self.assertTrue(self.demo.press(1, "mcg")[0].view)

    def test_new_app(self):
        self.demo.press(1, "mnw:new")
        self.demo.press(1, "mnp:d:logistics")
        out = self.demo.press(1, "mns", form={"name": "Стройторг"})
        self.assertIn("Создано", text_of(out[0]))
        self.assertTrue((C.CLIENTS / "stroytorg" / "config.yaml").exists())
        from core import pult
        self.assertEqual(pult.duty_state(self.demo, "stroytorg"), "wait")
        cl = control.client_app(self.demo, "stroytorg", DAY).ctx(control.ADMIN)
        from core import billing as B
        self.assertIn("logistics", B.funcs(cl))
        self.assertTrue(B.state(cl).get("people"))
        from core.ui.web import reply_json                                  # «Открыть его приложение» — ссылкой в браузер (Адам 18.11)
        btns = [b for r in self.demo.press(1, "mcc:stroytorg") for row in reply_json(r).get("buttons") or [] for b in row]
        self.assertTrue(any(b[1].startswith("url:https://stroytorg.") for b in btns))
        bad = self.demo.press(1, "mns", form={"name": ""})
        self.assertIn("название", text_of(bad[0]))

    def test_new_app_shops(self):
        """Адам 20.11: сеть магазинов с учётом в Алапе — магазины, склады, загрузка товаров из отчёта «Остатки» 1С."""
        self.demo.press(1, "mnw:new")
        for x in ("mnp:s:company", "mnp:d:stock", "mnp:d:logistics", "mnp:d:office", "mnp:src:alapa", "mnp:seg:mix"):
            self.demo.press(1, x)
        bad = self.demo.press(1, "mns", form={"name": "Сеть Барт"})
        self.assertIn("магазины", text_of(bad[0]).lower())
        out = self.demo.press(1, "mns", form={"name": "Сеть Барт", "shops": "Барт\nБарт Премиум\nФаида",
                                              "whs": "Склад Гойты 1\nСклад Курчалой"})
        self.assertIn("Создано", text_of(out[0]))
        app = App("set_bart", today=DAY, admins=[1], data_dir=self.data)
        self.assertEqual(app.cfg.segment, "mix")
        self.assertEqual(app.cfg.source["kind"], "alapa")
        self.assertEqual([n for _, n in app.cfg.warehouses], ["Склад Гойты 1", "Склад Курчалой"])
        from core.ui import retail as RT
        self.assertEqual([n for _, n in RT.shops(app.cfg)], ["Барт", "Барт Премиум", "Фаида"])
        self.assertEqual(app.ctx(1).segment, "mix")
        from core import billing as B
        from core import control as K
        rows = B.people_rows(K.cctx(self.demo.ctx(1), "set_bart"))            # люди — по его отделам, магазины по одному
        self.assertIn("Магазины · Барт Премиум", [lbl for _, lbl, *_ in rows])
        self.assertTrue(B.dcounts(K.cctx(self.demo.ctx(1), "set_bart"))[1])   # пока типовые числа размера
        self.assertIn("Общий · Склад Гойты 1", [lbl for _, lbl, *_ in rows])     # склад — по каждому складу клиента
        self.assertIn("Общий · Склад Курчалой", [lbl for _, lbl, *_ in rows])
        cb = K.cctx(self.demo.ctx(1), "set_bart")
        self.assertEqual(sum(B.dcounts(cb)[0].values()), B.people(cb))            # поля в сумме = «Всего»
        # загрузка товаров: отчёт «Остатки» из 1С как есть (места хранения — строки-суммы)
        import openpyxl, io
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in [["Место хранения", "Свободный остаток"], ["Номенклатура", None], ["магазин", 5], ["диван ASTON", 3], ["стол CORVETTE", 2],
                  ["СКЛАД ГОЙТЫ 1", 7], ["диван ASTON", 4], ["стул JULIAN", 3], ["СКЛАД АТАГИ", 1], ["пуф", 1], ["Итого", 13]]:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        app.press(1, "gdu")
        app.upload(1, "Остатки барт.xlsx", buf.getvalue())
        pend = app.db().get("goods_import")
        self.assertEqual(pend["places"], ["магазин", "СКЛАД ГОЙТЫ 1", "СКЛАД АТАГИ"])
        self.assertEqual(len(pend["items"]), 4)                                  # диван ASTON в двух местах — один товар
        self.assertEqual(pend["map"]["СКЛАД ГОЙТЫ 1"], "wh")
        self.assertEqual(pend["map"]["СКЛАД АТАГИ"], "new")
        self.assertEqual(pend["map"]["магазин"], "")                              # не угадать — выбирает человек
        out = app.press(1, "gdy", form={"p0": "", "p1": "wh", "p2": "new"})
        self.assertIn("куда", text_of(out[0]))
        out = app.press(1, "gdy", form={"p0": "s1", "p1": "wh", "p2": "new"})
        self.assertIn("Новых товаров", text_of(out[0]))
        db = app.db()
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM goods")["n"], 4)
        sofa = db.one("SELECT code, qty FROM goods WHERE name = 'диван ASTON'")
        self.assertEqual(sofa["qty"], 7)
        self.assertEqual(db.one("SELECT qty FROM stock_loc WHERE code = ? AND loc = 's1'", sofa["code"])["qty"], 3)
        from modules import ship as SH
        self.assertIn("СКЛАД АТАГИ", [n for _, n in SH.whs(app.ctx(1))])
        app.press(1, "gdu")
        app.upload(1, "Остатки барт.xlsx", buf.getvalue())                       # второй раз — те же товары, не дубли
        app.press(1, "gdy", form={"p0": "s1", "p1": "wh", "p2": "new"})
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM goods")["n"], 4)
        self.assertEqual(db.one("SELECT qty FROM goods WHERE name = 'диван ASTON'")["qty"], 7)
        # ✏️ товар: три цены, раздел, архив (Адам 20.11)
        code = sofa["code"]
        db.add_employee("Хозяин Сети", "office", position="director", role="owner", tg_id=800)
        app.press(800, "gde:" + code)
        app.press(800, "gds:" + code, form={"name": "диван ASTON 02", "grp_new": "Диваны", "price": "64 000", "price_opt": "55 000", "cost": "41000"})
        g = db.one("SELECT * FROM goods WHERE code = ?", code)
        self.assertEqual((g["name"], g["grp"], g["price"], g["price_opt"], g["cost"]), ("диван ASTON 02", "Диваны", 64000, 55000, 41000))
        bad = app.press(1, "gds:" + code, form={"name": "стул JULIAN", "price": "1"})
        self.assertIn("уже есть", text_of(bad[0]))
        app.press(1, "gds:new", form={"name": "Новый шкаф", "price": "10000"})
        self.assertTrue(db.one("SELECT 1 AS x FROM goods WHERE name = 'Новый шкаф'"))
        new = db.one("SELECT code FROM goods WHERE name = 'Новый шкаф'")["code"]
        app.press(1, f"gdz:{new}:ok")                                             # без истории — стирается
        self.assertIsNone(db.one("SELECT 1 AS x FROM goods WHERE code = ?", new))
        app.press(1, f"gdz:{code}:ok")                                            # с остатками — в архив
        self.assertEqual(db.one("SELECT off FROM goods WHERE code = ?", code)["off"], 1)
        app.press(1, f"gdz:{code}:back")
        # ⚙️ справочники: раздел, склад, магазин
        from modules import catalog as CT
        app.press(1, f"rfs:g:{CT._key('Диваны')}", form={"name": "Мягкая мебель"})
        self.assertEqual(db.one("SELECT grp FROM goods WHERE code = ?", code)["grp"], "Мягкая мебель")
        app.press(1, "rfs:w:wh2", form={"name": "Склад Курчалой новый"})
        self.assertIn("Склад Курчалой новый", [n for _, n in SH.whs(app.ctx(1))])
        out = app.press(1, "rfd:w:wh:ok")
        self.assertIn("нельзя", text_of(out[0]))                                 # на складе есть товар
        app.press(1, "rfs:s:s3", form={"name": "Фаида Центр"})
        app.press(1, "rfs:s:new", form={"name": "Бааев Крым"})
        self.assertEqual([n for _, n in RT.shops(app.ctx(1).cfg)], ["Барт", "Барт Премиум", "Фаида Центр", "Бааев Крым"])
        app.press(1, "rfd:s:s4:ok")
        self.assertNotIn("Бааев Крым", [n for _, n in RT.shops(app.ctx(1).cfg)])
        # чей склад (Адам 20.11): по названию сам, в справочниках — вручную; продажа магазина берёт сначала свой склад
        db.put(SH.OWNER_KEY, {})
        app.press(1, "rfs:w:wh2", form={"name": "Склад Фаида Центр"})
        db.put(SH.OWNER_KEY, {})
        self.assertEqual(SH.owners(app.ctx(1))["wh2"], "s3")                       # по названию
        self.assertEqual(SH.wh_order(app.ctx(1), "s3")[0], "wh2")
        self.assertEqual(SH.wh_order(app.ctx(1))[0], "wh")                         # опт — сначала общие
        app.press(1, "rfs:w:wh2", form={"name": "Склад Фаида Центр", "own": "s1"})
        self.assertEqual(SH.owners(app.ctx(1))["wh2"], "s1")                       # вручную важнее названия
        lbls = [lbl for _, lbl, *_ in B.people_rows(K.cctx(self.demo.ctx(1), "set_bart"))]
        self.assertIn("Барт · Склад Фаида Центр", lbls)
        self.assertEqual(lbls.index("Барт · Склад Фаида Центр"), lbls.index("Магазины · Барт") + 1)
        # 🏬 видит магазины: руководитель магазинов — только отмеченные
        db.add_employee("Руководитель Один", "shops", position="shops_head", role="head", tg_id=900)
        db.add_employee("Продавец Барт", "shops", team="s1", position="seller", role="rep", tg_id=901)
        db.add_employee("Продавец Фаида", "shops", team="s3", position="seller", role="rep", tg_id=902)
        boss = db.one("SELECT id FROM employees WHERE tg_id = 900")["id"]
        app.press(1, f"shy:{boss}", form={"shops": "s1,s2"})
        c = app.ctx(900)
        self.assertEqual(c.shops_only, {"s1", "s2"})
        self.assertEqual([n for _, n in RT.shops(c.cfg)], ["Барт", "Барт Премиум"])
        seen = {e["fio"] for e in db.employees() if c.sees(e)}
        self.assertIn("Продавец Барт", seen)
        self.assertNotIn("Продавец Фаида", seen)
        self.assertIn("2 магазина", text_of(app.press(900, "home")[0]))
        # 🧾 продажа оптом: менеджер опта → накладная в долг → выдача водителю клиента → долг → оплата (Адам 20.11)
        db.add_employee("Менеджер Опта", "sales", team="opt", position="rep", role="rep", tg_id=910)
        db.add_employee("Кладовщик Один", "warehouse", position="storekeeper", role="staff", tg_id=911)
        db.add_employee("Продавец Два", "shops", team="s2", position="seller", role="rep", tg_id=912)
        db.x("UPDATE goods SET price_opt = 5000 WHERE name = 'стул JULIAN'")
        chair = db.one("SELECT code FROM goods WHERE name = 'стул JULIAN'")["code"]
        self.assertIn("Оптом продают", text_of(app.press(912, "os")[0]))               # продавец магазина оптом не продаёт
        app.press(910, "ocn", form={"name": "Мебель-Юг"})
        app.press(910, f"os:a:{chair}")
        app.press(910, f"os:a:{chair}")
        out = app.press(910, "os:ok", form={"pay": "part", "paid": "4000", "due": "14", "driver": "Исмаилов Р."})
        self.assertIn("ждёт выдачи", text_of(out[0]))
        self.assertTrue(any(r.to == 911 for r in out))                           # кладовщику — «ждёт выдачи»
        self.assertEqual(SH.reserved(db).get((chair, "wh")), 2)
        out = app.press(911, "owy:1", form={"l0": "1"})                          # выдали 1 из 2
        self.assertTrue(any(r.to == 910 and "Не выдали" in r.text for r in out))
        self.assertEqual(db.one("SELECT total FROM opt_orders WHERE id = 1")["total"], 5000)
        self.assertEqual(db.one("SELECT qty FROM stock_loc WHERE code = ? AND loc = 'wh'", chair)["qty"], 2)
        from modules import opt as OP
        debt, late, b, _ = OP.ledger(db, 1, DAY)
        self.assertEqual((debt, late), (1000, 0))
        self.assertEqual(db.one('SELECT total FROM debts WHERE batch = 0 AND client = ?', "O1")["total"], 1000)
        self.assertTrue(db.one("SELECT 1 AS x FROM sales WHERE batch = 0 AND client = 'O1' AND amount = 5000"))
        app.press(910, "opy:1", form={"sum": "1000", "how": "нал"})
        self.assertEqual(OP.ledger(db, 1, DAY)[0], 0)
        self.assertIsNone(db.one('SELECT 1 AS x FROM debts WHERE batch = 0 AND client = ?', "O1"))


class Panel(Control):
    """Отдельное приложение управления (admin.<домен>): тот же демо-процесс, свой значок, только для администратора."""

    def test_panel_app(self):
        from core.transport.web import WebApi
        demo_api = WebApi(self.demo, admin_key="ключ")
        api = WebApi(self.demo, admin_key="ключ", panel=True, lock=demo_api.lock)
        code, h, raw = api.handle("GET", "/manifest.webmanifest", {}, {}, None)
        self.assertIn("Управление", raw.decode())
        code, h, raw = api.handle("POST", "/api/start", {}, {"admin": "ключ"}, None)
        import json
        data = json.loads(raw)
        self.assertEqual(data["company"], "Управление")
        self.assertIn("Сводка", json.dumps(data["msgs"], ensure_ascii=False))
        self.assertEqual([t[1] for t in data["tabs"]], ["home", "mcl:all", "mfn", "mcg", "mcm"])   # пульт — пять вкладок (Адам 17.11)
        self.assertTrue(self.demo.office().get("panel_admins"))
        code, h, raw = api.handle("POST", "/api/start", {}, {}, None)          # чужой телефон без ключа
        self.assertIn("приложение управления", raw.decode())
        code, h, raw = demo_api.handle("POST", "/api/press", {}, {"to": "home"}, None)   # демо — без «Пульта» внизу
        self.assertNotIn('"adm"', raw.decode())


if __name__ == "__main__":
    unittest.main()
