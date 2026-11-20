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
