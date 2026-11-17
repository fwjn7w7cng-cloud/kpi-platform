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
        bad = self.demo.press(1, "mns", form={"name": ""})
        self.assertIn("название", text_of(bad[0]))


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
